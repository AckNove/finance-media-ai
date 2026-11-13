"""导出一条排期（真写文件 + 留记录）的测试。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.models.enums import CalendarStatus, PublishState, ScriptStatus
from backend.app.models.operations import ContentCalendar, PlatformMetric
from backend.app.models.script import Script
from backend.app.models.topic import Topic
from backend.app.services.export_service import ExportError, export_entry


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    db_session.query(PlatformMetric).delete()
    db_session.query(ContentCalendar).delete()
    db_session.query(Script).delete()
    db_session.query(Topic).delete()
    db_session.commit()
    yield


def _topic(db_session) -> Topic:
    tag = uuid.uuid4().hex[:8]
    topic = Topic(title=f"利率与银行-{tag}", slug=f"rates-{tag}")
    db_session.add(topic)
    db_session.commit()
    return topic


def _script(db_session, *, body: str = "净息差降到1.52%，利息收入这块基本盘在被压缩。") -> Script:
    script = Script(
        topic_id=_topic(db_session).id,
        title=f"商业银行靠什么赚钱-{uuid.uuid4().hex[:6]}",
        body=body,
        status=ScriptStatus.APPROVED.value,
        version=2,
    )
    db_session.add(script)
    db_session.commit()
    return script


def _entry(db_session, *, script_id: int | None, platform: str = "douyin") -> ContentCalendar:
    entry = ContentCalendar(
        title=f"排期-{uuid.uuid4().hex[:6]}",
        platform=platform,
        status=CalendarStatus.SCRIPTED.value,
        publish_state=PublishState.NOT_PUBLISHED.value,
        script_id=script_id,
        notes="银行 利率",
    )
    db_session.add(entry)
    db_session.commit()
    return entry


def test_export_writes_three_platform_files_and_records_the_path(db_session, tmp_path: Path) -> None:
    script = _script(db_session)
    entry = _entry(db_session, script_id=script.id)

    result = export_entry(db_session, entry.id, root=tmp_path, now=datetime(2026, 11, 13, 9, 30, tzinfo=UTC))

    assert result["ok"] is True
    assert result["platforms"] == ["douyin", "xiaohongshu", "wechat"]
    base = Path(result["export_path"])
    assert base.parent == tmp_path / f"entry-{entry.id}" and base.name == "20261113-093000"
    for platform in ("douyin", "xiaohongshu", "wechat"):
        target = base / f"{platform}.md"
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert text.startswith("# ") and "净息差" in text
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    assert meta["script_id"] == script.id
    assert meta["script_version"] == 2
    assert meta["platforms"] == ["douyin", "xiaohongshu", "wechat"]
    assert "发布仍需人工" in meta["note"]

    db_session.refresh(entry)
    assert entry.export_path == str(base)
    assert entry.status == CalendarStatus.EXPORTED.value
    # 导出不等于发布
    assert entry.publish_state == PublishState.NOT_PUBLISHED.value
    assert entry.published_url in (None, "")


def test_export_refuses_an_entry_without_a_script(db_session, tmp_path: Path) -> None:
    entry = _entry(db_session, script_id=None)
    with pytest.raises(ExportError) as caught:
        export_entry(db_session, entry.id, root=tmp_path)
    assert caught.value.reason == "no_script"
    assert not (tmp_path / f"entry-{entry.id}").exists()


def test_export_rejects_an_unknown_platform(db_session, tmp_path: Path) -> None:
    script = _script(db_session)
    entry = _entry(db_session, script_id=script.id)
    with pytest.raises(ExportError) as caught:
        export_entry(db_session, entry.id, root=tmp_path, platforms=["weibo"])
    assert caught.value.reason == "unknown_platform"


def test_export_api_returns_paths_and_404s_for_unknown_entry(db_session, tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.services import export_service

    monkeypatch.setattr(export_service, "EXPORT_ROOT", tmp_path)
    script = _script(db_session)
    entry = _entry(db_session, script_id=script.id)

    with TestClient(create_app()) as client:
        response = client.post(f"/api/calendar/{entry.id}/export", json={"platforms": ["wechat"]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["platforms"] == ["wechat"]
        assert Path(body["export_path"], "wechat.md").is_file()

        missing = client.post("/api/calendar/999999/export")
        assert missing.status_code == 404
        assert missing.json()["error"]["details"]["reason"] == "unknown_entry"

        no_script = _entry(db_session, script_id=None)
        refused = client.post(f"/api/calendar/{no_script.id}/export")
        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["reason"] == "no_script"
