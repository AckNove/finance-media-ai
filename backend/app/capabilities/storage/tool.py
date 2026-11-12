"""存储类工具：`db_query`（只读）与 `db_write`（受作用域约束的写入）。

设计取舍（为什么不是拼 SQL）：

* 查询与写入都走 SQLAlchemy Core 的 `select()` / `insert()`，表与列对象来自
  `Base.metadata`。这样表名、列名只能是"代码里真实存在的对象"，外部传入的字符串
  永远进不了解析器，注入面直接消失；也不需要自己写字符串转义。
* `db_query` 只允许 `SELECT`，并且强制 `LIMIT`（默认 50，上限 200）。理由：
  工具是给 Agent 用的，一个忘了加 LIMIT 的查询会把整张 `model_calls` 拉进上下文。
* `db_write` 必须同时满足三层条件才写：
  1. 目标表在 `WRITABLE_TABLES` 里（白名单，`model_calls` / `tool_calls` /
     `agent_runs` / `research_tasks` 这些"账本与调度"表永远不开放给 Agent）；
  2. Agent 的 `db_scopes` 包含该表名（`config/agent_routing.yaml` 声明的表级作用域，
     例如 scriptwriter 只能写 `scripts` / `script_evidence_links` / `report_findings`）；
  3. 列名必须真实存在于该表，且不在 `FORBIDDEN_COLUMNS`（金额、任务状态机字段）。
  `model_calls.cost_estimate` 这类金额列绝不允许 Agent 直接写——那等于让被审计者改账。
* 两个工具都不接受原始 SQL 字符串参数（没有 `sql=` 这种入口）。想加能力就加显式参数。

返回值统一是 `ToolResult`，错误码稳定：`bad_arguments` / `unknown_table` /
`unknown_column` / `table_not_writable` / `scope_denied` / `read_only` / `write_failed`。
"""

from __future__ import annotations

import datetime as _dt
import decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import Table, insert, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, sessionmaker

from backend.app.capabilities.tools import ToolResult
from backend.app.core.caller import CallerContext, CallerKind
from backend.app.database.base import Base
from backend.app.database.session import get_session_factory

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: 允许 Agent 写入的表。刻意不含账本表（`model_calls`/`tool_calls`）、调度表
#: （`research_tasks`/`research_subtasks`/`task_events`）与审计表（`agent_runs`）。
WRITABLE_TABLES: tuple[str, ...] = (
    "sources",
    "source_snapshots",
    "evidence",
    "report_findings",
    "finding_evidence",
    "research_reports",
    "knowledge_items",
    "knowledge_relations",
    "topics",
    "topic_tags",
    "topic_collections",
    "scripts",
    "script_evidence_links",
    "media_assets",
    "financial_cases",
    "football_cases",
    "industries",
    "institutions",
    "financial_instruments",
    "financial_products",
    "review_records",
    "publications",
)

#: 永远不允许通过工具写的列（金额与状态机）。
FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "cost_estimate",
        "cost_spent",
        "cost_reserved",
        "cost_delta",
        "budget_max_cost",
        "budget_max_calls",
        "calls_spent",
        "calls_reserved",
        "tokens_spent",
        "attempt_token",
        "lease_until",
        "lease_heartbeat_at",
        "status_changed_by_tool",
    }
)


def _session_factory(factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    return factory if factory is not None else get_session_factory()


def _table(name: str) -> Table | None:
    if not isinstance(name, str) or not name:
        return None
    return Base.metadata.tables.get(name)


def _jsonable(value: Any) -> Any:
    """把行值转成能进 JSON 的东西（Decimal/datetime/bytes 都不行）。"""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


def _row_to_dict(row: Row[Any]) -> dict[str, Any]:
    return {key: _jsonable(val) for key, val in row._mapping.items()}


def _coerce_limit(raw: Any) -> tuple[int, str]:
    if raw is None:
        return DEFAULT_LIMIT, ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0, "limit 必须是整数"
    if value < 1:
        return 0, "limit 必须大于 0"
    if value > MAX_LIMIT:
        return 0, f"limit 不能超过 {MAX_LIMIT}"
    return value, ""


def _resolve_columns(table: Table, requested: Sequence[str] | None) -> tuple[list[Any], str]:
    """把列名解析成列对象；未指定时返回全部列。"""
    if not requested:
        return list(table.columns), ""
    if not isinstance(requested, (list, tuple)):
        return [], "columns 必须是字符串数组"
    resolved: list[Any] = []
    for name in requested:
        if not isinstance(name, str):
            return [], "columns 必须是字符串数组"
        if name not in table.columns:
            return [], f"unknown_column: {table.name}.{name}"
        resolved.append(table.columns[name])
    return resolved, ""


def _resolve_filters(table: Table, filters: Any) -> tuple[list[Any], str]:
    """只支持列 = 值 的等值过滤（不做字符串拼装，也不做范围以外的东西）。"""
    if filters is None:
        return [], ""
    if not isinstance(filters, dict):
        return [], "filters 必须是对象（列名 → 值）"
    clauses: list[Any] = []
    for name, value in filters.items():
        if not isinstance(name, str) or name not in table.columns:
            return [], f"unknown_column: {table.name}.{name}"
        if isinstance(value, (dict, list)):
            return [], "filters 的值只能是标量（不支持嵌套条件）"
        clauses.append(table.columns[name] == value)
    return clauses, ""


def make_db_query(*, session_factory: sessionmaker[Session] | None = None) -> Any:
    """构造 `db_query` 的提供方（只读）。"""

    factory = _session_factory(session_factory)

    def db_query(
        caller: CallerContext,
        *,
        table: str,
        columns: Sequence[str] | None = None,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> ToolResult:
        target = _table(table)
        if target is None:
            return ToolResult(
                status="error",
                error_code="unknown_table",
                error_message=f"没有这张表：{table}",
            )

        resolved, problem = _resolve_columns(target, columns)
        if problem:
            code = "unknown_column" if problem.startswith("unknown_column") else "bad_arguments"
            return ToolResult(status="error", error_code=code, error_message=problem)

        clauses, problem = _resolve_filters(target, filters)
        if problem:
            code = "unknown_column" if problem.startswith("unknown_column") else "bad_arguments"
            return ToolResult(status="error", error_code=code, error_message=problem)

        size, problem = _coerce_limit(limit)
        if problem:
            return ToolResult(status="error", error_code="bad_arguments", error_message=problem)

        start = 0
        if offset is not None:
            try:
                start = int(offset)
            except (TypeError, ValueError):
                return ToolResult(
                    status="error",
                    error_code="bad_arguments",
                    error_message="offset 必须是整数",
                )
            if start < 0:
                return ToolResult(
                    status="error",
                    error_code="bad_arguments",
                    error_message="offset 不能为负",
                )

        statement = select(*resolved)
        if clauses:
            statement = statement.where(*clauses)
        if order_by:
            if order_by not in target.columns:
                return ToolResult(
                    status="error",
                    error_code="unknown_column",
                    error_message=f"unknown_column: {target.name}.{order_by}",
                )
            column = target.columns[order_by]
            statement = statement.order_by(column.desc() if descending else column.asc())
        statement = statement.limit(size).offset(start)

        with factory() as session:
            rows = [_row_to_dict(row) for row in session.execute(statement)]

        return ToolResult(
            status="ok",
            data={
                "table": target.name,
                "columns": [col.name for col in resolved],
                "rows": rows,
                "returned": len(rows),
                "limit": size,
                "offset": start,
                "caller": caller.name,
            },
        )

    return db_query


def _agent_scopes(caller: CallerContext) -> frozenset[str]:
    return frozenset(caller.db_scopes or ())


def make_db_write(*, session_factory: sessionmaker[Session] | None = None) -> Any:
    """构造 `db_write` 的提供方（受白名单 + 作用域 + 列黑名单约束）。"""
    from backend.app.core.config import get_agent_routing

    factory = _session_factory(session_factory)

    def db_write(
        caller: CallerContext,
        *,
        table: str,
        values: dict[str, Any],
    ) -> ToolResult:
        target = _table(table)
        if target is None:
            return ToolResult(
                status="error",
                error_code="unknown_table",
                error_message=f"没有这张表：{table}",
            )
        if target.name not in WRITABLE_TABLES:
            return ToolResult(
                status="error",
                error_code="table_not_writable",
                error_message=f"表 {target.name} 不允许由工具写入",
            )
        if not isinstance(values, dict) or not values:
            return ToolResult(
                status="error",
                error_code="bad_arguments",
                error_message="values 必须是非空对象",
            )

        # 作用域：Agent 的身份里带的 db_scopes 决定它能碰哪些表。
        if caller.kind != CallerKind.AGENT:
            return ToolResult(
                status="denied",
                error_code="scope_denied",
                error_message="只有 Agent 身份可以写库",
            )
        scopes = _agent_scopes(caller)
        if not scopes:
            routing = get_agent_routing()
            scopes = frozenset(routing.db_scopes_for(caller.name) or ())
        if target.name not in scopes:
            return ToolResult(
                status="denied",
                error_code="scope_denied",
                error_message=(
                    f"Agent {caller.name} 的作用域里没有 {target.name}"
                    f"（当前：{sorted(scopes)}）"
                ),
            )

        for name in values:
            if not isinstance(name, str):
                return ToolResult(
                    status="error",
                    error_code="bad_arguments",
                    error_message="values 的键必须是列名",
                )
            if name not in target.columns:
                return ToolResult(
                    status="error",
                    error_code="unknown_column",
                    error_message=f"unknown_column: {target.name}.{name}",
                )
            if name in FORBIDDEN_COLUMNS:
                return ToolResult(
                    status="error",
                    error_code="read_only",
                    error_message=f"列 {name} 不允许由工具写入（金额/调度字段）",
                )

        row = {name: _jsonable_inverse(val) for name, val in values.items()}
        try:
            with factory() as session:
                result = session.execute(insert(target).values(**row))
                session.commit()
                new_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
        except Exception as error:  # noqa: BLE001 - 写库失败要如实回报
            return ToolResult(
                status="error",
                error_code="write_failed",
                error_message=f"{type(error).__name__}: {error}",
            )

        return ToolResult(
            status="ok",
            data={
                "table": target.name,
                "id": new_id,
                "columns": sorted(row),
                "caller": caller.name,
            },
        )

    return db_write


def _jsonable_inverse(value: Any) -> Any:
    """写库前的轻校验：只挡明显不该出现的类型（dict/list 交给 SQLAlchemy 判断）。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return value


def register_storage_adapters(registry: Any) -> None:
    """把 `db_query` / `db_write` 注册进工具注册表。"""
    registry.register("db_query", make_db_query())
    registry.register("db_write", make_db_write())


def implemented_names() -> Iterable[str]:
    return ("db_query", "db_write")


__all__ = [
    "DEFAULT_LIMIT",
    "FORBIDDEN_COLUMNS",
    "MAX_LIMIT",
    "WRITABLE_TABLES",
    "make_db_query",
    "make_db_write",
    "register_storage_adapters",
]
