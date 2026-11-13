"""人工撤回确认后，已建视频任务必须失效（F 模块的"审核撤销与失效处理"）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.video import handoff as handoff_module
from backend.app.video.handoff import INVALIDATION_FILE, invalidate_jobs, jobs_using_checks

REPORT_ID = 91


def _make_job(root: Path, job_id: str, check_ids: list[int]) -> Path:
    directory = root / job_id
    (directory / "research").mkdir(parents=True)
    (directory / "job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "stage": "storyboard_ready",
                "draft_gate": {
                    "rule": "只有 human_review_status=confirmed 的结论可以进入成稿",
                    "check_ids": check_ids,
                    "confirmed_findings": list(range(len(check_ids))),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return directory


def _seed(db_session) -> int:
    """写一份报告 + 一条已确认的核查行，返回核查行 id。"""

    existing = db_session.get(ResearchReport, REPORT_ID)
    if existing is None:
        db_session.add(
            ResearchReport(
                id=REPORT_ID,
                title="测试报告",
                summary="价格战通过三条渠道吃掉现金流。",
                findings=[{"text": "组件价格跌到 0.68 元/W", "kind": "fact", "source_ids": ["S01"]}],
                limitations="样本只覆盖几家龙头。",
                research_task_id=None,
                author_agent="model+rules",
                verification_status="unverified",
            )
        )
        db_session.commit()
    row = FindingCheck(
        research_report_id=REPORT_ID,
        finding_index=0,
        claim="组件价格跌到 0.68 元/W",
        verdict="supported",
        reason="原文逐字一致",
        evidence_ids=[],
        source_ids=["S01"],
        human_review_status="confirmed",
        human_note="对照 S01 原文确认",
    )
    db_session.add(row)
    db_session.commit()
    return int(row.id)


def test_jobs_using_checks_matches_only_the_referencing_jobs(tmp_path: Path):
    _make_job(tmp_path, "job-a", [1, 2])
    _make_job(tmp_path, "job-b", [3])
    (tmp_path / "no-manifest").mkdir()

    assert [item.name for item in jobs_using_checks([2], root=tmp_path)] == ["job-a"]
    assert [item.name for item in jobs_using_checks([3, 1], root=tmp_path)] == ["job-a", "job-b"]
    assert jobs_using_checks([], root=tmp_path) == []
    assert jobs_using_checks([9], root=tmp_path) == []


def test_invalidate_marks_the_job_without_deleting_anything(tmp_path: Path):
    directory = _make_job(tmp_path, "job-a", [7])
    produced = directory / "output" / "final.mp4"
    produced.parent.mkdir(parents=True)
    produced.write_bytes(b"fake-mp4")

    affected = invalidate_jobs(
        [7],
        reason="人工把核查记录 7 的确认撤回到 rejected",
        root=tmp_path,
        now=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
    )

    assert [item["job_id"] for item in affected] == ["job-a"]
    assert affected[0]["check_ids"] == [7]
    assert affected[0]["invalidated_at"].startswith("2026-05-20T09:00")

    payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    gate = payload["draft_gate"]
    assert gate["usable"] is False
    assert gate["invalidated_check_ids"] == [7]
    assert "撤回到 rejected" in gate["invalidated_reason"]

    marker = (directory / INVALIDATION_FILE).read_text(encoding="utf-8")
    assert "这个视频任务已经失效" in marker
    assert "产物文件没有被删除" in marker
    assert produced.read_bytes() == b"fake-mp4"


def test_invalidate_only_touches_matching_jobs(tmp_path: Path):
    first = _make_job(tmp_path, "job-a", [1])
    second = _make_job(tmp_path, "job-b", [2])
    invalidate_jobs([1], reason="撤回", root=tmp_path)

    again = invalidate_jobs([1], reason="撤回", root=tmp_path)
    assert [item["job_id"] for item in again] == ["job-a"]
    assert "usable" not in json.loads(
        (second / "job.json").read_text(encoding="utf-8")
    )["draft_gate"]
    assert not (second / INVALIDATION_FILE).exists()
    assert (first / INVALIDATION_FILE).is_file()


def test_withdrawing_a_confirmation_invalidates_the_job_through_the_api(
    client, db_session, tmp_path, monkeypatch
):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(handoff_module, "JOBS_ROOT", jobs_root)

    check_id = _seed(db_session)
    _make_job(jobs_root, "job-keep", [check_id])

    response = client.post(
        f"/api/research-reports/{REPORT_ID}/checks/{check_id}/review",
        json={"status": "rejected", "note": "复核原文后发现口径不一致"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["human_review_status"] == "rejected"

    marker = jobs_root / "job-keep" / INVALIDATION_FILE
    assert marker.is_file(), "撤回确认后必须给已建任务留失效标记"
    payload = json.loads((jobs_root / "job-keep" / "job.json").read_text(encoding="utf-8"))
    assert payload["draft_gate"]["usable"] is False
    assert payload["draft_gate"]["invalidated_check_ids"] == [check_id]


def test_confirming_again_does_not_invalidate(client, db_session, tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(handoff_module, "JOBS_ROOT", jobs_root)

    check_id = _seed(db_session)
    _make_job(jobs_root, "job-keep", [check_id])

    response = client.post(
        f"/api/research-reports/{REPORT_ID}/checks/{check_id}/review",
        json={"status": "confirmed", "note": "对照 S01 原文，口径一致"},
    )
    assert response.status_code == 200, response.text
    assert not (jobs_root / "job-keep" / INVALIDATION_FILE).exists()
