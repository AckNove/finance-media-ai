"""排期里那条片子到底能不能发。

这些用例守的是一条纪律：**片子在不算数**。文件存在只能证明渲染过，
不能证明它引用的结论现在还成立——真实数据里就有这么一条（结论被撤回确认，
`output/final.mp4` 还躺在那里）。所以判定顺序是先看闸门、再看文件。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.models.enums import Platform, PublishState, CalendarStatus
from backend.app.services import calendar_readiness
from backend.app.services.calendar_readiness import job_readiness


def _make_job(root: Path, job_id: str, *, gate: bool | None, film: bool, stage: str = "storyboard_ready") -> Path:
    job_dir = root / job_id
    (job_dir / "output").mkdir(parents=True, exist_ok=True)
    payload: dict = {"job_id": job_id, "stage": stage}
    if gate is not None:
        payload["draft_gate"] = {
            "usable": gate,
            "invalidated_at": "2026-11-13T12:00:00+00:00" if gate is False else "",
            "invalidated_reason": "结论被撤回确认" if gate is False else "",
        }
    (job_dir / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if film:
        (job_dir / "output" / "final.mp4").write_bytes(b"\x00" * 2048)
    return job_dir


def test_no_job_id_means_nothing_to_check(tmp_path):
    result = job_readiness(None, jobs_root=tmp_path)
    assert result["status"] == "no_job"
    assert result["usable"] is False
    assert "先给它建一个视频任务" in result["next_step"]


def test_unknown_job_directory_is_reported_not_guessed(tmp_path):
    result = job_readiness("nope", jobs_root=tmp_path)
    assert result["status"] == "job_missing"
    assert result["usable"] is False


def test_a_film_without_a_job_json_is_not_called_ready(tmp_path):
    """目录和片子都在、却没有 job.json：查不到结论当时是否被确认，不能说成就绪。

    真事：``content/jobs/v5_research_film`` 就是这样——片子（final.mp4）在，
    但没有交接记录。以前这里会报"任务目录不在了"，把人引到错误的方向。
    """

    job_dir = tmp_path / "legacy"
    (job_dir / "output").mkdir(parents=True)
    (job_dir / "output" / "final.mp4").write_bytes(b"\x00" * 1024)

    result = job_readiness("legacy", jobs_root=tmp_path)

    assert result["status"] == "no_gate"
    assert result["usable"] is False
    assert result["film"]["relative_path"] == "output/final.mp4", "要如实说明片子还在"
    assert "job.json" in result["next_step"]


def test_traversal_is_refused(tmp_path):
    _make_job(tmp_path, "real", gate=None, film=True)
    result = job_readiness("../real", jobs_root=tmp_path)
    assert result["status"] == "job_missing"


def test_job_without_a_film_is_not_ready(tmp_path):
    _make_job(tmp_path, "only_storyboard", gate=None, film=False)
    result = job_readiness("only_storyboard", jobs_root=tmp_path)
    assert result["status"] == "film_missing"
    assert result["usable"] is False


def test_a_rendered_film_with_a_valid_gate_is_ready(tmp_path):
    _make_job(tmp_path, "good", gate=True, film=True)
    result = job_readiness("good", jobs_root=tmp_path)
    assert result["status"] == "ready"
    assert result["usable"] is True
    assert result["film"]["relative_path"] == "output/final.mp4"
    assert result["film"]["byte_size"] == 2048


def test_a_film_whose_conclusions_were_withdrawn_is_not_ready(tmp_path):
    """文件还在，但结论不作数了——这是最该拦住的一种。"""
    _make_job(tmp_path, "stale", gate=False, film=True)
    result = job_readiness("stale", jobs_root=tmp_path)
    assert result["status"] == "draft_invalid"
    assert result["usable"] is False
    assert result["film"]["relative_path"] == "output/final.mp4", "要如实说明片子还在"
    assert result["invalidated_reason"] == "结论被撤回确认"
    assert "重新确认结论" in result["next_step"]


def test_render_receipt_is_surfaced_when_present(tmp_path):
    job_dir = _make_job(tmp_path, "with_receipt", gate=True, film=True)
    (job_dir / "qc").mkdir()
    (job_dir / "qc" / "render_run.json").write_text(
        json.dumps({"status": "done", "stage": "rendered", "updated_at": "2026-11-14T01:00:00+00:00"}),
        encoding="utf-8",
    )
    result = job_readiness("with_receipt", jobs_root=tmp_path)
    assert result["status"] == "ready"
    assert result["render"]["status"] == "done"


def test_every_status_has_a_label_and_a_next_step():
    for status in calendar_readiness.READINESS_LABELS:
        result = calendar_readiness._result(status)
        assert result["label"]
        assert result["next_step"]
        assert result["usable"] is (status == "ready")


@pytest.fixture()
def jobs_root(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    return root


def test_calendar_detail_and_list_carry_readiness(client: TestClient, jobs_root, monkeypatch):
    monkeypatch.setattr(calendar_readiness, "DEFAULT_JOBS_ROOT", jobs_root)
    _make_job(jobs_root, "ready_job", gate=True, film=True)
    _make_job(jobs_root, "stale_job", gate=False, film=True)

    created = client.post(
        "/api/calendar",
        json={"title": "测试排期", "platform": "douyin", "video_job_id": "ready_job"},
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["readiness"]["status"] == "ready"
    assert entry["readiness"]["usable"] is True

    patched = client.patch(f"/api/calendar/{entry['id']}", json={"video_job_id": "stale_job"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["readiness"]["status"] == "draft_invalid"

    listed = client.get("/api/calendar?limit=10")
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["id"] == entry["id"])
    assert row["readiness"]["status"] == "draft_invalid"

    detail = client.get(f"/api/calendar/{entry['id']}")
    assert detail.status_code == 200
    assert detail.json()["readiness"]["status"] == "draft_invalid"

    cleared = client.patch(f"/api/calendar/{entry['id']}", json={"video_job_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["readiness"]["status"] == "no_job"
