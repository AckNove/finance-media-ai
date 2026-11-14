"""导出前的"片子这一关" + 发布清单：发出去就收不回来，所以这里宁可拦。

背景（真实的坑）：排期绑了视频任务，任务依据的结论被人工撤回，片子的文件还在
（`output/final.mp4`，9 MB）。旧版导出不看这一层，照样把文案写出来——人拿着一个
已经作废的片子去发。所以导出必须先按磁盘事实核对一遍，再决定写不写文件。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.models.enums import CalendarStatus, PublishState, ScriptStatus
from backend.app.models.operations import ContentCalendar
from backend.app.models.script import Script
from backend.app.models.topic import Topic
from backend.app.services.export_service import CHECKLIST_NAME, ExportError, export_entry

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    db_session.query(ContentCalendar).delete()
    db_session.query(Script).delete()
    db_session.query(Topic).delete()
    db_session.commit()
    yield


def _topic(db_session) -> Topic:
    tag = uuid.uuid4().hex[:8]
    topic = Topic(title=f"储能-{tag}", slug=f"storage-{tag}")
    db_session.add(topic)
    db_session.commit()
    return topic


def _script(db_session) -> Script:
    script = Script(
        topic_id=_topic(db_session).id,
        title=f"储能为什么打价格战-{uuid.uuid4().hex[:6]}",
        body="2023年组件产能920GW，价格战把毛利压到成本线以下。",
        status=ScriptStatus.APPROVED.value,
        version=1,
    )
    db_session.add(script)
    db_session.commit()
    return script


def _entry(db_session, *, script_id: int, video_job_id: str | None) -> ContentCalendar:
    entry = ContentCalendar(
        title=f"排期-{uuid.uuid4().hex[:6]}",
        platform="douyin",
        status=CalendarStatus.RENDERED.value,
        publish_state=PublishState.NOT_PUBLISHED.value,
        script_id=script_id,
        video_job_id=video_job_id,
        notes="储能",
    )
    db_session.add(entry)
    db_session.commit()
    return entry


def _make_job(
    root: Path,
    job_id: str,
    *,
    gate: dict | None = None,
    film: bytes | None = b"fake-mp4",
    render: dict | None = None,
) -> Path:
    job_dir = root / job_id
    (job_dir / "output").mkdir(parents=True, exist_ok=True)
    (job_dir / "qc").mkdir(parents=True, exist_ok=True)
    payload = {"job_id": job_id, "stage": "rendered"}
    if gate is not None:
        payload["draft_gate"] = gate
        (job_dir / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if film is not None:
        (job_dir / "output" / "final.mp4").write_bytes(film)
    if render is not None:
        (job_dir / "qc" / "render_run.json").write_text(
            json.dumps(render, ensure_ascii=False), encoding="utf-8"
        )
    return job_dir


def _render_report(*, failed: list[str] | None = None) -> dict:
    checks = {"no_black_frames": True, "duration_matches_audio": True, "audio_present": True}
    for name in failed or []:
        checks[name] = False
    return {
        "status": "done",
        "report": {
            "frames": 1197,
            "qc": {
                "media": {"duration": 39.9, "width": 1080, "height": 1920, "has_audio": True},
                "checks": checks,
            },
        },
    }


def test_export_refuses_a_film_whose_conclusions_were_retracted(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _make_job(
        jobs,
        "film_invalid",
        gate={"usable": False, "invalidated_at": "2026-11-13T05:59:30+00:00", "invalidated_reason": "人工把核查记录 1 的确认撤回到 rejected"},
        render=_render_report(),
    )
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="film_invalid")

    with pytest.raises(ExportError) as caught:
        export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW, jobs_root=jobs)

    assert caught.value.reason == "draft_invalid"
    assert "撤回" in caught.value.message
    # 关键：一个字都没写出去
    assert not (tmp_path / "exports" / f"entry-{entry.id}").exists()
    db_session.refresh(entry)
    assert entry.export_path in (None, "")


def test_export_refuses_when_no_film_was_rendered(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _make_job(jobs, "film_absent", gate={"usable": True}, film=None)
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="film_absent")

    with pytest.raises(ExportError) as caught:
        export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW, jobs_root=jobs)

    assert caught.value.reason == "film_missing"
    assert not (tmp_path / "exports" / f"entry-{entry.id}").exists()


def test_export_allows_a_film_without_a_gate_record_but_says_so(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _make_job(jobs, "legacy_film", gate=None, render=_render_report())
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="legacy_film")

    result = export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW, jobs_root=jobs)

    assert result["readiness"] == "no_gate"
    checklist = Path(result["checklist_path"]).read_text(encoding="utf-8")
    assert "查不到成稿闸门记录" in checklist
    # 允许导出，但清单里必须写明"查不到"，不能装作没问题
    assert "人工核对" in checklist


def test_checklist_tells_where_the_film_is_and_whether_qc_passed(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _make_job(jobs, "film_ok", gate={"usable": True}, film=b"x" * 1024, render=_render_report())
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="film_ok")

    result = export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW, jobs_root=jobs)
    checklist = Path(result["checklist_path"]).read_text(encoding="utf-8")

    assert result["readiness"] == "ready"
    assert "output" in checklist and "final.mp4" in checklist
    assert "0.00 MB（1024 字节）" in checklist
    assert "1080×1920" in checklist and "39.9 秒" in checklist
    assert "技术质检：通过" in checklist
    assert "不填等于没发" in checklist
    assert CHECKLIST_NAME in result["checklist_path"]

    meta = json.loads(Path(result["meta_path"]).read_text(encoding="utf-8"))
    assert meta["readiness"] == "ready"
    assert meta["video_job_id"] == "film_ok"
    assert meta["film"]["frames"] == 1197
    assert meta["checklist"].endswith(CHECKLIST_NAME)


def test_checklist_reports_failed_technical_checks(db_session, tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _make_job(jobs, "film_noaudio", gate={"usable": True}, render=_render_report(failed=["audio_present"]))
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="film_noaudio")

    result = export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW, jobs_root=jobs)
    checklist = Path(result["checklist_path"]).read_text(encoding="utf-8")

    assert "技术质检：**未通过**" in checklist
    assert "audio_present" in checklist


def test_export_without_a_video_job_says_there_is_only_copy(db_session, tmp_path: Path) -> None:
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id=None)

    result = export_entry(db_session, entry.id, root=tmp_path / "exports", now=NOW)
    checklist = Path(result["checklist_path"]).read_text(encoding="utf-8")

    assert result["readiness"] == ""
    assert "没绑视频任务" in checklist
    assert Path(result["export_path"], "douyin.md").is_file()


def test_export_api_returns_422_and_the_readiness_reason(db_session, tmp_path: Path, monkeypatch) -> None:
    """接口层也要把"为什么不让发"说清楚，否则页面上只有一句 422。"""

    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.services import calendar_readiness, export_service

    jobs = tmp_path / "jobs"
    _make_job(
        jobs,
        "film_invalid",
        gate={"usable": False, "invalidated_at": "2026-11-13T05:59:30+00:00", "invalidated_reason": "人工把核查记录 1 的确认撤回到 rejected"},
        render=_render_report(),
    )
    monkeypatch.setattr(calendar_readiness, "DEFAULT_JOBS_ROOT", jobs)
    monkeypatch.setattr(export_service, "EXPORT_ROOT", tmp_path / "exports")
    entry = _entry(db_session, script_id=_script(db_session).id, video_job_id="film_invalid")

    with TestClient(create_app()) as client:
        response = client.post(f"/api/calendar/{entry.id}/export", json={})

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "draft_invalid"
    assert "不能发" in response.json()["error"]["message"]
    assert not (tmp_path / "exports" / f"entry-{entry.id}").exists()
