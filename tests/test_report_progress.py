"""报告进度：脚本几条、视频任务几个、下一步该干什么。

这些用例只读文件系统与测试库，不渲染、不联网、不调模型。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.app.api.routes import research_reports as reports_route
from backend.app.services import report_progress
from backend.app.services.report_progress import progress_map, progress_of


def _report(db_session: Any, *, title: str = "进度用例报告") -> Any:
    from backend.app.models.research import ResearchReport

    report = ResearchReport(
        title=f"{title}-{uuid.uuid4().hex[:6]}",
        summary="一句话结论",
        findings=[],
        limitations="仅供测试",
        verification_status="unverified",
        jurisdiction="cn",
        version=1,
    )
    db_session.add(report)
    db_session.commit()
    return report


def _topic_id(db_session: Any) -> int:
    from backend.app.models.topic import Topic

    topic = Topic(title="报告进度", slug=f"report-progress-{uuid.uuid4().hex[:6]}")
    db_session.add(topic)
    db_session.commit()
    return topic.id


def _script(db_session: Any, report: Any, *, version: int = 1) -> Any:
    from backend.app.models.script import Script

    script = Script(
        topic_id=_topic_id(db_session),
        research_report_id=report.id,
        title=f"脚本 v{version}",
        version=version,
        body="正文",
        segments=[{"text": "一句", "role": "evidence", "source_ids": ["S01"], "numbers": []}],
        disclaimer="本片为资料整理，不构成投资建议。",
    )
    db_session.add(script)
    db_session.commit()
    return script


def _make_job(root: Path, job_id: str, *, report_id: int, usable: bool | None, film: bool) -> Path:
    """造一个视频任务目录：``job.json`` + 可选的 ``output/final.mp4``。"""

    job_dir = root / job_id
    (job_dir / "output").mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "job_id": job_id,
        "report": {"report_id": report_id, "title": f"{job_id} 的来源报告"},
        "outline": {"ok": True, "source": "script", "shot_count": 4},
    }
    if usable is not None:
        payload["draft_gate"] = {
            "usable": usable,
            "reason": "" if usable else "人工把核查记录 1 的确认撤回到 rejected",
        }
    (job_dir / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if film:
        (job_dir / "output" / "final.mp4").write_bytes(b"fake-mp4")
    return job_dir


def test_a_report_without_script_or_job_says_start_with_the_script(
    db_session, tmp_path: Path
) -> None:
    report = _report(db_session)
    progress = progress_of(db_session, report.id, jobs_root=tmp_path / "jobs")

    assert progress.script_count == 0
    assert progress.job_count == 0
    assert progress.latest_script_version is None
    assert "还没有脚本" in progress.next_step


def test_existing_scripts_are_counted_with_the_latest_version(db_session, tmp_path: Path) -> None:
    report = _report(db_session)
    _script(db_session, report, version=1)
    _script(db_session, report, version=2)
    progress = progress_of(db_session, report.id, jobs_root=tmp_path / "jobs")

    assert progress.script_count == 2
    assert progress.latest_script_version == 2
    assert progress.latest_script_id is not None
    assert "v2" in progress.next_step
    assert "按这份脚本排" in progress.next_step


def test_jobs_are_matched_by_the_report_id_inside_job_json(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    first = _report(db_session, title="第一份")
    second = _report(db_session, title="第二份")
    _make_job(jobs, "job_for_first", report_id=first.id, usable=True, film=True)
    # 第二个任务建了但还没渲出片子 → film_missing，不能算"能发"
    _make_job(jobs, "job_for_second", report_id=second.id, usable=True, film=False)

    progress = progress_map(db_session, [first.id, second.id], jobs_root=jobs)

    assert progress[first.id].job_count == 1
    assert progress[first.id].jobs[0].job_id == "job_for_first"
    assert progress[first.id].jobs[0].usable is True
    assert progress[first.id].jobs[0].film_path == "output/final.mp4"
    assert "已有能发的成片" in progress[first.id].next_step
    assert progress[second.id].jobs[0].readiness_status == "film_missing"
    assert progress[second.id].jobs[0].usable is False
    assert "都不能发" in progress[second.id].next_step


def test_a_revoked_film_is_reported_as_not_usable(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    report = _report(db_session)
    _make_job(jobs, "revoked_job", report_id=report.id, usable=False, film=True)
    progress = progress_of(db_session, report.id, jobs_root=jobs)

    assert progress.jobs[0].readiness_status == "draft_invalid"
    assert progress.jobs[0].usable is False
    assert "结论确认被撤回" in (progress.jobs[0].readiness_label or "")
    assert "先回「语义核查」确认结论" in progress.next_step


def test_broken_job_json_does_not_break_the_whole_page(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    (jobs / "broken_job").mkdir(parents=True)
    (jobs / "broken_job" / "job.json").write_text("{ 这不是 JSON", encoding="utf-8")
    report = _report(db_session)
    _make_job(jobs, "good_job", report_id=report.id, usable=True, film=True)

    progress = progress_of(db_session, report.id, jobs_root=jobs)

    assert progress.job_count == 1
    assert progress.jobs[0].job_id == "good_job"


def test_a_missing_jobs_directory_is_not_an_error(db_session, tmp_path: Path) -> None:
    report = _report(db_session)
    progress = progress_of(db_session, report.id, jobs_root=tmp_path / "no_such_dir")

    assert progress.job_count == 0
    assert progress.report_id == report.id


def test_the_list_endpoint_carries_progress_for_every_report(
    db_session, client, tmp_path: Path, monkeypatch
) -> None:
    jobs = tmp_path / "jobs"
    report = _report(db_session)
    _script(db_session, report, version=3)
    _make_job(jobs, "job_from_script", report_id=report.id, usable=True, film=True)
    monkeypatch.setattr(report_progress, "DEFAULT_JOBS_ROOT", jobs)

    response = client.get("/api/research-reports")
    assert response.status_code == 200
    items = response.json()["items"]
    item = next(row for row in items if row["id"] == report.id)

    assert item["progress"]["script_count"] == 1
    assert item["progress"]["latest_script_version"] == 3
    assert item["progress"]["job_count"] == 1
    assert item["progress"]["jobs"][0]["job_id"] == "job_from_script"
    assert item["progress"]["jobs"][0]["usable"] is True

    # 详情接口保持原样：不带 progress，避免每次看详情都扫一遍目录
    detail = client.get(f"/api/research-reports/{report.id}")
    assert detail.status_code == 200
    assert "progress" not in detail.json()


def test_the_list_route_scans_the_jobs_directory_once_per_page(
    db_session, client, tmp_path: Path, monkeypatch
) -> None:
    """一页多份报告只扫一次目录——用计数器钉住，避免以后退化成 N+1。"""

    calls: list[str] = []
    original = reports_route.progress_map

    def counted(session, report_ids, *, jobs_root=None):
        calls.append(",".join(str(value) for value in report_ids))
        return original(session, report_ids, jobs_root=jobs_root)

    _report(db_session)
    monkeypatch.setattr(report_progress, "DEFAULT_JOBS_ROOT", tmp_path / "jobs")
    monkeypatch.setattr(reports_route, "progress_map", counted)

    assert client.get("/api/research-reports").status_code == 200
    assert len(calls) == 1, f"期望整页只调用一次，实际 {calls}"


def test_progress_of_uses_the_same_judgement_as_the_page(db_session, tmp_path: Path) -> None:
    """单份与整页必须是同一套判定（不能两处各写一份）。"""

    report = _report(db_session)
    _script(db_session, report, version=1)
    single = progress_of(db_session, report.id, jobs_root=tmp_path / "jobs")
    page = progress_map(db_session, [report.id], jobs_root=tmp_path / "jobs")

    assert single.model_dump() == page[report.id].model_dump()
