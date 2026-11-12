"""新实现的五个工具（`db_query`/`db_write`/`task_update`/`handoff`/`fact_check`）的行为测试。

这些用例的共同主题是**"越权与伪造必须失败"**：

* `db_query` 只能查 ORM 里真实存在的表与列，且必须带 LIMIT；
* `db_write` 必须同时满足表白名单、Agent 表级作用域、列黑名单三道条件；
* `task_update`/`handoff` 必须过租约令牌（旧令牌一律 `stale_attempt`）；
* `fact_check` 只认落盘快照，快照被改过就报哈希不符，找不到就说需人工复核。

数据库用 conftest 的会话级临时库；任务运行时用独立的临时库（模块级 engine），
避免与 API 层的表互相干扰。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.capabilities.storage.tool import make_db_query, make_db_write
from backend.app.capabilities.verification.tool import check_quote, make_fact_check
from backend.app.core.caller import CallerContext
from backend.app.database.base import Base
from backend.app.execution.runtime import TaskRuntime
from backend.app.execution.tools import make_handoff, make_task_update
from backend.app.models.enums import SourceType
from backend.app.models.source import Source

# --------------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def runtime_db() -> Iterator[sessionmaker]:
    from backend.app.database.session import get_engine

    engine = get_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory


@pytest.fixture()
def runtime(runtime_db: sessionmaker) -> TaskRuntime:
    return TaskRuntime(session_factory=runtime_db)


def agent_caller(*, agent: str = "financial_researcher", scopes: set[str] | None = None) -> CallerContext:
    return CallerContext.for_executor(
        agent=agent,
        task_id="1",
        attempt_token="tok-test",
        subtask_id="1",
        db_scopes=scopes if scopes is not None else {"sources", "evidence"},
    )


# ------------------------------------------------------------------- db_query


def test_db_query_returns_rows_with_a_hard_limit(db_session) -> None:
    db_session.add(
        Source(
            title="测试来源",
            url="https://example.com/a",
            source_type=SourceType.OTHER,
        )
    )
    db_session.commit()

    query = make_db_query()
    result = query(CallerContext.human(), table="sources", limit=5)
    assert result.ok is True, result.error_message
    assert result.data["table"] == "sources"
    assert result.data["limit"] == 5
    assert result.data["returned"] >= 1
    assert any(row["url"] == "https://example.com/a" for row in result.data["rows"])


def test_db_query_rejects_unknown_table_and_column(db_session) -> None:
    query = make_db_query()
    bad_table = query(CallerContext.human(), table="not_a_table")
    assert bad_table.error_code == "unknown_table"

    bad_column = query(CallerContext.human(), table="sources", columns=["no_such_column"])
    assert bad_column.error_code == "unknown_column"

    bad_filter = query(CallerContext.human(), table="sources", filters={"nope": 1})
    assert bad_filter.error_code == "unknown_column"


def test_db_query_refuses_unbounded_and_oversized_limits(db_session) -> None:
    query = make_db_query()
    assert query(CallerContext.human(), table="sources", limit=0).error_code == "bad_arguments"
    assert query(CallerContext.human(), table="sources", limit=10_000).error_code == "bad_arguments"
    assert query(CallerContext.human(), table="sources", limit="abc").error_code == "bad_arguments"


def test_db_query_filters_and_orders(db_session) -> None:
    db_session.add(Source(title="排序B", url="https://example.com/b", source_type=SourceType.OTHER))
    db_session.add(Source(title="排序A", url="https://example.com/c", source_type=SourceType.OTHER))
    db_session.commit()

    query = make_db_query()
    result = query(
        CallerContext.human(),
        table="sources",
        columns=["title"],
        filters={"url": "https://example.com/c"},
        order_by="title",
        limit=3,
    )
    assert result.ok is True
    assert result.data["rows"] == [{"title": "排序A"}]


# ------------------------------------------------------------------- db_write


def test_db_write_requires_agent_identity(db_session) -> None:
    writer = make_db_write()
    result = writer(
        CallerContext.human(),
        table="sources",
        values={"title": "x", "source_type": "other"},
    )
    assert result.status == "denied"
    assert result.error_code == "scope_denied"


def test_db_write_rejects_ledger_and_scheduler_tables(db_session) -> None:
    writer = make_db_write()
    for table in ("model_calls", "tool_calls", "research_tasks", "task_events", "agent_runs"):
        result = writer(agent_caller(), table=table, values={"id": 1})
        assert result.error_code == "table_not_writable", table


def test_db_write_rejects_tables_outside_agent_scope(db_session) -> None:
    writer = make_db_write()
    # scriptwriter 的作用域是 scripts/script_evidence_links/report_findings
    result = writer(
        agent_caller(agent="scriptwriter", scopes={"scripts"}),
        table="sources",
        values={"title": "越权", "source_type": "other"},
    )
    assert result.status == "denied"
    assert result.error_code == "scope_denied"


def test_db_write_rejects_money_columns(db_session, monkeypatch) -> None:
    """金额列即使落在白名单表里也不许 Agent 写（防"被审计者改账"）。

    当前没有任何可写表带金额列，所以这里临时把 `research_tasks` 塞进白名单，
    专门验证"列黑名单"这一层闸门真的会拦。
    """
    from backend.app.capabilities.storage import tool as storage_tool

    monkeypatch.setattr(
        storage_tool, "WRITABLE_TABLES", (*storage_tool.WRITABLE_TABLES, "research_tasks")
    )
    writer = make_db_write()
    result = writer(
        agent_caller(agent="video_producer", scopes={"research_tasks"}),
        table="research_tasks",
        values={"cost_spent": 1.5},
    )
    assert result.error_code == "read_only"


def test_db_write_inserts_within_scope(db_session) -> None:
    writer = make_db_write()
    result = writer(
        agent_caller(agent="financial_researcher", scopes={"sources"}),
        table="sources",
        values={"title": "写入来源", "url": "https://example.com/w", "source_type": "other"},
    )
    assert result.ok is True, result.error_message
    assert result.data["id"] is not None
    stored = db_session.scalars(select(Source).where(Source.title == "写入来源")).first()
    assert stored is not None


# --------------------------------------------------------------- task_update


def _claim_a_subtask(runtime: TaskRuntime) -> tuple[int, object]:
    task = runtime.create_task(title="工具测试任务", objective="验证 task_update")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="采集资料",
        task_type="research.collect",
        assignee="financial_researcher",
    )
    runtime.start_task(task.id)
    # 指定 task_id：共享测试库里可能有别的用例留下的 running 任务占着租约，
    # 不指定的话 claim_next 会在第一格上直接返回 None。
    claim = runtime.claim_next(worker="worker-1", task_id=task.id)
    assert claim is not None
    return task.id, claim


def test_task_update_heartbeat_and_complete(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
        db_scopes={"sources"},
    )
    update = make_task_update(runtime=runtime)

    beat = update(caller, action="heartbeat")
    assert beat.ok is True, beat.error_message

    done = update(caller, action="complete", output={"ok": True})
    assert done.ok is True, done.error_message
    assert done.data["task_status"] == "completed"


def test_task_update_rejects_stale_token(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    stale = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token="dead-beef",
        subtask_id=str(claim.subtask_id),
        db_scopes={"sources"},
    )
    result = make_task_update(runtime=runtime)(stale, action="complete")
    assert result.status == "error"
    assert result.error_code == "stale_attempt"


def test_task_update_rejects_unknown_action_and_missing_reason(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
    )
    update = make_task_update(runtime=runtime)
    assert update(caller, action="pause").error_code == "bad_arguments"
    assert update(caller, action="fail").error_code == "bad_arguments"


def test_task_update_denies_human_caller(runtime: TaskRuntime) -> None:
    result = make_task_update(runtime=runtime)(CallerContext.human(), action="heartbeat")
    assert result.status == "denied"


# ------------------------------------------------------------------- handoff


def test_handoff_need_human_pauses_the_task(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
    )
    result = make_handoff(runtime=runtime)(
        caller, kind="need_human", reason="需要人确认选题范围"
    )
    assert result.ok is True, result.error_message
    assert result.data["task_status"] == "paused"
    events = runtime.events(task_id)
    assert any(item["type"] == "paused" for item in events)


def test_handoff_records_direction_change_without_touching_status(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
    )
    result = make_handoff(runtime=runtime)(
        caller, kind="insufficient_evidence", reason="只有两家银行的数据", payload={"banks": 2}
    )
    assert result.ok is True
    assert result.data["recorded"] is True
    events = runtime.events(task_id)
    changed = [item for item in events if item["type"] == "direction_changed"]
    assert changed and changed[-1]["payload"]["kind"] == "insufficient_evidence"
    # 状态没被偷偷改：子任务仍是 running
    snapshot = runtime.task(task_id)
    assert getattr(snapshot.status, "value", snapshot.status) == "running"


def test_handoff_request_subtask_creates_a_new_stage(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
        db_scopes={"sources"},
    )
    result = make_handoff(runtime=runtime)(
        caller,
        kind="request_subtask",
        reason="需要补一份财报解析",
        question="解析工商银行 2024 年报",
        task_type="research.extract",
    )
    assert result.ok is True, result.error_message
    assert result.data["created_sequence"] == 2
    snapshot = runtime.task(task_id)
    assert len(snapshot.subtasks) == 2


def test_handoff_rejects_unknown_kind_and_missing_question(runtime: TaskRuntime) -> None:
    task_id, claim = _claim_a_subtask(runtime)
    caller = CallerContext.for_executor(
        agent="financial_researcher",
        task_id=str(task_id),
        attempt_token=claim.attempt_token,
        subtask_id=str(claim.subtask_id),
    )
    handler = make_handoff(runtime=runtime)
    assert handler(caller, kind="please_do_something", reason="x").error_code == "bad_arguments"
    assert handler(caller, kind="request_subtask", reason="补一格").error_code == "bad_arguments"


# ---------------------------------------------------------------- fact_check


def _write_snapshot(root: Path, content_hash: str, text: str) -> Path:
    directory = root / "example.com"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{content_hash}.html"
    path.write_text(text, encoding="utf-8")
    return path


def _add_source(db_session, *, body: str, url: str = "https://example.com/report") -> Source:
    """登记一条来源，并让 content_hash 等于**将要写进快照的那段字节**的哈希。"""
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    source = Source(
        title="带快照的来源",
        url=url,
        source_type=SourceType.OFFICIAL_STATISTICS,
        content_hash=content_hash,
        accessed_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(source)
    db_session.commit()
    return source


def test_fact_check_finds_quote_in_snapshot(db_session, tmp_path: Path) -> None:
    body = "<html><body>工商银行 2024 年实现营业收入 8218.03 亿元，同比下降 2.5%。</body></html>"
    source = _add_source(db_session, body=body)
    _write_snapshot(tmp_path, source.content_hash, body)

    checker = make_fact_check(snapshot_root=tmp_path)
    result = checker(
        CallerContext.for_executor(
            agent="fact_checker",
            task_id="1",
            attempt_token="tok",
            db_scopes={"evidence"},
        ),
        quote="实现营业收入 8218.03 亿元，同比下降 2.5%",
        source_id=source.id,
    )
    assert result.ok is True, result.error_message
    assert result.data["found"] is True
    assert result.data["searched_web"] is False
    assert result.data["offset"] >= 0


def test_fact_check_reports_missing_quote_without_accusing(db_session, tmp_path: Path) -> None:
    body = "净利润同比增长 0.8%。"
    source = _add_source(db_session, body=body, url="https://example.com/two")
    _write_snapshot(tmp_path, source.content_hash, body)

    checker = make_fact_check(snapshot_root=tmp_path)
    result = checker(
        CallerContext.for_executor(
            agent="fact_checker", task_id="1", attempt_token="tok", db_scopes={"evidence"}
        ),
        quote="净利润同比增长 12.8%",
        source_id=source.id,
    )
    assert result.ok is True
    assert result.data["found"] is False
    assert "不等于判定造假" in result.data["note"]


def test_fact_check_detects_tampered_snapshot(db_session, tmp_path: Path) -> None:
    body = "原始内容 ABCDEF"
    source = _add_source(db_session, body=body, url="https://example.com/three")
    path = _write_snapshot(tmp_path, source.content_hash, body)
    path.write_text("原始内容 ABCDEF（被改过）", encoding="utf-8")

    checker = make_fact_check(snapshot_root=tmp_path)
    result = checker(
        CallerContext.for_executor(
            agent="fact_checker", task_id="1", attempt_token="tok", db_scopes={"evidence"}
        ),
        quote="原始内容 ABCDEF",
        source_id=source.id,
    )
    assert result.error_code == "snapshot_hash_mismatch"


def test_fact_check_without_snapshot_says_so(db_session, tmp_path: Path) -> None:
    source = _add_source(db_session, body="没有快照", url="https://example.com/four")
    checker = make_fact_check(snapshot_root=tmp_path)
    result = checker(
        CallerContext.for_executor(
            agent="fact_checker", task_id="1", attempt_token="tok", db_scopes={"evidence"}
        ),
        quote="随便一段引文内容",
        source_id=source.id,
    )
    assert result.error_code == "no_snapshot"


def test_fact_check_rejects_short_quote_and_missing_locator(db_session, tmp_path: Path) -> None:
    checker = make_fact_check(snapshot_root=tmp_path)
    caller = CallerContext.for_executor(
        agent="fact_checker", task_id="1", attempt_token="tok", db_scopes={"evidence"}
    )
    assert checker(caller, quote="短", source_id=1).error_code == "bad_arguments"
    assert checker(caller, quote="足够长的一段引文").error_code == "bad_arguments"


def test_check_quote_ignores_whitespace_only() -> None:
    assert check_quote(quote="下降了 1.05%", snapshot_text="下降了\n 1.05%")["found"] is True
    assert check_quote(quote="下降了 1.5%", snapshot_text="下降了 1.05%")["found"] is False
