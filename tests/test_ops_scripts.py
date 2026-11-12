"""启动预检与启动恢复的测试。

这两个脚本的价值在于「如实汇报」：预检把缺什么说清楚，恢复把卡住的任务列出来。
测试覆盖的也正是这一点——档位判定真实、``--apply`` 才真的动数据、空库不编造任务。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.scripts import preflight, recover_tasks


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


# --- 预检 -------------------------------------------------------------------


def test_report_separates_blocked_from_errors() -> None:
    report = preflight.Report()
    report.add("config", "ok", "fine")
    report.add("models", "blocked", "没有启用模型", "改 config/models.yaml")
    payload = report.as_dict()
    assert payload["ok"] is True  # blocked 不算失败：它是如实告知
    assert payload["blocked"] == ["models"]
    assert payload["errors"] == []
    report.add("startup", "error", "坏了")
    assert report.as_dict()["ok"] is False


def test_config_check_reports_real_values() -> None:
    report = preflight.Report()
    preflight.check_config(report)
    config = next(item for item in report.checks if item.name == "config")
    assert config.status == "ok"
    assert "features(model=" in config.detail


def test_database_check_flags_missing_tables(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    db_path = tmp_path / "empty.db"
    create_engine(f"sqlite:///{db_path.as_posix()}").dispose()  # 建一个空文件

    report = preflight.Report()
    original = preflight.check_database

    # 用环境变量指向空库，再走真实检查逻辑
    import os

    from backend.app.core.config import clear_settings_cache
    from backend.app.database.session import dispose_engine

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    clear_settings_cache()
    dispose_engine()
    try:
        original(report)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        clear_settings_cache()
        dispose_engine()

    entry = next(item for item in report.checks if item.name == "database")
    assert entry.status == "error"
    assert "缺表" in entry.detail
    assert "research_tasks" in entry.detail


def test_toolchain_check_uses_the_pinned_build() -> None:
    report = preflight.Report()
    preflight.check_toolchain(report)
    entry = next(item for item in report.checks if item.name == "ffmpeg")
    assert entry.status == "ok"
    assert entry.detail.startswith("config")


def test_preflight_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = preflight.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "checks", "blocked", "errors"}
    assert isinstance(exit_code, int)


# --- 启动恢复 ----------------------------------------------------------------


@pytest.fixture()
def runtime_on_temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sqlalchemy import create_engine

    from backend.app.core.config import clear_settings_cache
    from backend.app.database.base import Base
    from backend.app.database.session import dispose_engine

    import backend.app.models  # noqa: F401

    db_path = tmp_path / "recovery.db"
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)

    monkeypatch.setenv("DATABASE_URL", url)
    clear_settings_cache()
    dispose_engine()
    yield url
    dispose_engine()
    clear_settings_cache()
    engine.dispose()


def test_collect_reports_paused_tasks_with_reasons(runtime_on_temp_db: str) -> None:
    from backend.app.execution.runtime import TaskRuntime
    from backend.app.database.session import get_session_factory

    clock = FakeClock()
    runtime = TaskRuntime(session_factory=get_session_factory(), clock=clock)
    task = runtime.create_task(title="恢复测试：商业银行", objective="验证恢复脚本", )
    empty = runtime.add_subtask(
        task.id, sequence=1, question="采集资料", task_type="research.collect", assignee="financial_researcher"
    )
    runtime.start_task(task.id)
    claim = runtime.claim_next(worker="w1")
    runtime.request_human_review(claim, reason="model_not_configured", output={"next_step": "配密钥"})

    found = recover_tasks.collect(runtime)
    assert [item["id"] for item in found] == [task.id]
    entry = found[0]
    assert entry["status"] == "paused"
    blocked = [item for item in entry["open_subtasks"] if item["status"] == "blocked"]
    assert blocked and blocked[0]["task_type"] == "research.collect"
    assert blocked[0]["sequence"] == empty.sequence


def test_recovery_does_not_write_without_apply(runtime_on_temp_db: str) -> None:
    from backend.app.database.session import get_session_factory
    from backend.app.execution.runtime import TaskRuntime

    clock = FakeClock()
    runtime = TaskRuntime(session_factory=get_session_factory(), lease_seconds=60, clock=clock)
    task = runtime.create_task(title="恢复测试：租约", objective="过期租约", )
    runtime.add_subtask(task.id, sequence=1, question="采集", task_type="research.collect", assignee="financial_researcher")
    runtime.start_task(task.id)
    runtime.claim_next(worker="w1")
    clock.advance(600)

    before = runtime.task(task.id)
    assert before.counts().get("running") == 1

    payload = recover_tasks.main(["--database-url", runtime_on_temp_db, "--json"])
    assert payload == 0
    after_read_only = runtime.task(task.id)
    assert after_read_only.counts().get("running") == 1  # 只看不改

    assert recover_tasks.main(["--database-url", runtime_on_temp_db, "--apply", "--json"]) == 0
    after_reap = runtime.task(task.id)
    assert after_reap.counts().get("running") in (None, 0)
    assert after_reap.counts().get("ready") == 1


def test_recovery_on_empty_database_says_so(runtime_on_temp_db: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert recover_tasks.main(["--database-url", runtime_on_temp_db]) == 0
    output = capsys.readouterr().out
    assert "没有需要恢复的任务。" in output
    assert "只看不改" in output


def test_preflight_never_leaks_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    preflight.main(["--json"])
    output = capsys.readouterr().out
    for marker in ("sk-", "api_key=", "Bearer "):
        assert marker not in output
