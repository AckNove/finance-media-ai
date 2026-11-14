"""``bootstrap_check``：从一个空库验到"能建选题、能建任务、能跑一格"。

这些用例真跑迁移与真起一个 app（不是造假的步骤），所以每个用例几秒钟；
但正因为如此，"新手第一脚会不会绊住"才验得出来。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.app.execution.executor import AgentExecutor
from backend.app.execution.runtime import Claim
from backend.app.capabilities.tools import ToolResult
from backend.scripts import bootstrap_check as boot


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"


def test_is_protected_matches_only_the_development_database() -> None:
    assert boot.is_protected("sqlite:///C:/repo/data/finance_media.db") is True
    assert boot.is_protected("sqlite:///C:\\repo\\data\\finance_media.db") is True
    assert boot.is_protected("sqlite:///C:/repo/data/finance_media.work.db") is False
    assert boot.is_protected("sqlite:///C:/tmp/demo.db") is False


def test_a_fresh_database_becomes_usable(tmp_path: Path) -> None:
    """九步全过：迁移 → 种子 → 自检 → 接口 → 选题 → 任务 → worker → 如实停下 → 不花钱。"""

    report = boot.run_bootstrap(_url(tmp_path))

    assert report.ok, report.summary()
    assert [step.name for step in report.steps] == [
        "migrate",
        "seed",
        "startup",
        "api",
        "topic",
        "task",
        "worker",
        "honest_stop",
        "no_spending",
    ]
    migrate = next(step for step in report.steps if step.name == "migrate")
    assert "业务表 31 张" in migrate.detail
    task = next(step for step in report.steps if step.name == "task")
    assert "9 个子任务" in task.detail


def test_the_run_does_not_spend_any_money(tmp_path: Path) -> None:
    report = boot.run_bootstrap(_url(tmp_path))
    spending = next(step for step in report.steps if step.name == "no_spending")
    assert spending.ok is True
    assert "model_calls=0 行" in spending.detail


def test_without_seed_the_topic_step_says_the_column_list_is_empty(tmp_path: Path) -> None:
    """不种栏目就必须如实报"栏目列表是空的"，而不是让建选题莫名其妙 422。"""

    report = boot.run_bootstrap(_url(tmp_path), seed=False)

    assert report.ok is False
    assert any("栏目列表是空的" in step.detail for step in report.steps)


def test_worker_can_be_skipped(tmp_path: Path) -> None:
    report = boot.run_bootstrap(_url(tmp_path), drive_worker=False)

    assert report.ok is True
    names = [step.name for step in report.steps]
    assert "honest_stop" not in names
    worker = next(step for step in report.steps if step.name == "worker")
    assert worker.detail.startswith("跳过")


def test_the_run_leaves_the_callers_environment_alone(tmp_path: Path, monkeypatch) -> None:
    """这是别人的进程：验完 ``DATABASE_URL`` 与模型密钥都要原样放回去。"""

    monkeypatch.setenv("DATABASE_URL", "sqlite:///C:/sentinel/keepme.db")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-sentinel-value")

    boot.run_bootstrap(_url(tmp_path))

    assert os.environ["DATABASE_URL"] == "sqlite:///C:/sentinel/keepme.db"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-sentinel-value"


def test_cli_refuses_the_protected_database() -> None:
    assert boot.main(["--database-url", "sqlite:///C:/repo/data/finance_media.db"]) == 2


def test_the_run_does_not_write_into_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """产物要落在库文件旁边，不能掉进仓库的 ``content/research``（那是真研究的目录）。

    conftest 默认把 ``DEFAULT_ARTIFACTS_ROOT`` 指到临时目录，这条用例**故意指回仓库**，
    这样"靠 conftest 兜住"不算数——只有 ``bootstrap_check`` 自己传对目录才能过。
    """

    from backend.app.core.config import PROJECT_ROOT
    from backend.app.execution import executor as executor_module

    research = PROJECT_ROOT / "content" / "research"
    monkeypatch.setattr(executor_module, "DEFAULT_ARTIFACTS_ROOT", research, raising=False)
    before = {item.name for item in research.iterdir()} if research.exists() else set()

    report = boot.run_bootstrap(_url(tmp_path))

    after = {item.name for item in research.iterdir()} if research.exists() else set()
    assert report.ok, report.summary()
    assert after - before == set()
    assert (tmp_path / "research").exists()


def test_cli_refuses_a_non_sqlite_target() -> None:
    assert boot.main(["--database-url", "postgresql://user@host/db"]) == 2


def test_cli_emits_json_that_can_be_read_back(tmp_path: Path, capsys) -> None:
    code = boot.main(
        ["--database-url", _url(tmp_path), "--keep", "--no-worker", "--json"]
    )
    captured = capsys.readouterr().out
    payload = json.loads(captured)

    assert code == 0
    assert payload["ok"] is True
    assert payload["steps"][0]["name"] == "migrate"
    assert "全部通过" in payload["summary"]


class _DisabledTools:
    """所有工具都返回 ``feature_disabled``——模拟 ``features.search=false``。"""

    def dispatch(self, caller: Any, tool: str, **kwargs: Any) -> ToolResult:
        return ToolResult(
            status="error",
            error_code="feature_disabled",
            error_message=f"{tool} 需要联网检索，但 features.search=false",
            data={"feature": "search", "enabled": False},
        )


def _claim() -> Claim:
    return Claim(
        task_id=1,
        subtask_id=1,
        task_type="research.collect",
        question="商业银行靠什么赚钱",
        assignee="financial_researcher",
        sequence=1,
        max_attempts=2,
        attempt=1,
        attempt_token="token-1",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        input_payload={"queries": ["商业银行 收入结构"]},
    )


def test_a_disabled_capability_blocks_instead_of_burning_retries(tmp_path: Path) -> None:
    """开关关掉是**配置问题**，不该耗重试把任务判死——要停在"等人去开开关"。"""

    from backend.app.database.session import get_session_factory

    executor = AgentExecutor(
        tools=_DisabledTools(),
        session_factory=get_session_factory(),
        artifacts_root=tmp_path / "research",
    )
    outcome = executor.execute(_claim(), worker="test")

    assert outcome.status == "blocked"
    assert outcome.reason == "feature_disabled"
    assert "features.search" in str((outcome.output or {}).get("note", ""))
    assert (outcome.output or {}).get("next_step")
