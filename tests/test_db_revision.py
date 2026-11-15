"""工作库版本检查（`backend/scripts/db_revision.py`）的测试。

这个模块存在的理由是踩过的坑：`start.ps1` 原来只在"库不存在"时才建表，
库一旦存在就直接用——换分支、拉新代码、搬机器之后工作库可能还停在旧版本，
服务照样起得来，只是碰新表的页面会在查询时报 `no such column`。
所以这里把"库版本 vs 代码版本"的判定钉死，供一键启动判断"要不要先升级/该不该拒绝启动"。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config

from backend.scripts import db_revision

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = SimpleNamespace(x=[f"db_url={db_url}"])
    return config


def upgrade_to(url: str, target: str) -> None:
    command.upgrade(make_alembic_config(url), target)


@pytest.fixture()
def temp_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'work.db').as_posix()}"


def test_head_revision_matches_the_newest_migration_on_disk() -> None:
    head = db_revision.head_revision()
    assert head, "读不到迁移头，alembic.ini 或迁移目录有问题"
    latest = max(p.name for p in (PROJECT_ROOT / "backend" / "migrations" / "versions").glob("*.py"))
    assert head in latest, f"迁移头 {head} 不是目录里最新的那个文件（{latest}），说明链断了"


def test_missing_file_counts_as_fresh_not_as_an_error(temp_url: str) -> None:
    state = db_revision.inspect(temp_url)
    assert state.status == db_revision.STATUS_FRESH
    assert state.needs_upgrade is True
    assert state.refuses is False


def test_empty_file_also_counts_as_fresh(temp_url: str, tmp_path: Path) -> None:
    (tmp_path / "work.db").write_bytes(b"")
    assert db_revision.inspect(temp_url).status == db_revision.STATUS_FRESH


def test_freshly_upgraded_database_is_current(temp_url: str) -> None:
    upgrade_to(temp_url, "head")
    state = db_revision.inspect(temp_url)
    assert state.status == db_revision.STATUS_CURRENT
    assert state.current == state.head
    assert state.needs_upgrade is False


def test_database_left_behind_names_the_missing_migrations(temp_url: str) -> None:
    upgrade_to(temp_url, "b6_v6_operations")
    state = db_revision.inspect(temp_url)
    assert state.status == db_revision.STATUS_BEHIND
    assert state.needs_upgrade is True
    assert "b7_topic_origin" in state.detail and "b8_calendar_tags" in state.detail
    assert "缺 2 个迁移" in state.detail


def test_newer_database_refuses_automatic_downgrade() -> None:
    """拿着旧代码连新库：必须拒绝，绝不自动降级。"""
    state = db_revision.classify("b8_calendar_tags", "b6_v6_operations")
    assert state.status == db_revision.STATUS_AHEAD
    assert state.refuses is True
    assert state.needs_upgrade is False
    assert "不要自动降级" in state.detail


def test_unknown_revision_is_reported_not_guessed() -> None:
    state = db_revision.classify("from_another_branch", db_revision.head_revision())
    assert state.status == db_revision.STATUS_UNKNOWN
    assert state.refuses is True
    assert "不在代码的迁移链上" in state.detail


def test_unreadable_head_is_unknown_not_current() -> None:
    state = db_revision.classify("b6_v6_operations", None)
    assert state.status == db_revision.STATUS_UNKNOWN
    assert state.refuses is True


def test_cli_json_is_one_parsable_line_and_exit_code_tells_the_story(
    temp_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    upgrade_to(temp_url, "head")
    assert db_revision.main(["--database-url", temp_url, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "current"

    behind_url = temp_url.replace("work.db", "behind.db")
    upgrade_to(behind_url, "b6_v6_operations")
    assert db_revision.main(["--database-url", behind_url, "--json"]) == 3
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "behind"
    assert payload["needs_upgrade"] is True


def test_default_url_is_the_work_database_never_the_protected_one() -> None:
    url = db_revision.default_database_url()
    assert url.endswith("data/finance_media.work.db")
    assert not url.endswith("data/finance_media.db")
