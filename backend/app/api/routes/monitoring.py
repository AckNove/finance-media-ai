"""运行监控：成本、模型服务商、Agent 名册。

这些端点只读，供前端"成本统计 / 模型设置 / 服务商设置 / 系统设置"页面使用。
数据一律来自真实调用记录（``model_calls`` / ``tool_calls`` / ``research_tasks``），
没有记录就显示 0，不用示例数字填充。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, require_caller
from backend.app.capabilities.model.providers import provider_status
from backend.app.core.caller import CallerKind
from backend.app.core.config import get_agent_routing
from backend.app.models.enums import ResearchTaskStatus, TaskEventType
from backend.app.models.execution import ResearchTask, TaskEvent
from backend.app.models.metering import ModelCall, ToolCall
from backend.app.schemas.monitoring import HistoryDayOut, HistoryOut
from backend.app.schemas.research_task import CostBucket, CostSummaryOut, ProviderStatusOut
from backend.app.services.agent_registry import load_roster

router = APIRouter(
    prefix="/system",
    tags=["monitoring"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)

#: 历史曲线最多看 90 天（再多也没什么信息量，只会把页面拉得很长）
MAX_HISTORY_DAYS = 90


def _cost_note(model_calls: int, pricing_unknown: int) -> str:
    if model_calls == 0:
        return (
            "还没有任何模型调用记录。真实模型调用需要先在 .env 里配 api_key，"
            "并在 config/models.yaml 里启用模型与填写价目；未填价目的模型会被拒绝调用。"
        )
    if pricing_unknown:
        return f"有 {pricing_unknown} 次调用按'价格未知'处理，成本被低估，需要补价目。"
    return "成本按已配置价目与实际 token 用量计算。"


@router.get("/costs", response_model=CostSummaryOut, summary="成本统计（真实调用记录）")
def get_costs(db: DbSession) -> CostSummaryOut:
    model_row = db.execute(
        select(
            func.count(ModelCall.id),
            func.coalesce(func.sum(ModelCall.total_tokens), 0),
            func.coalesce(func.sum(ModelCall.cost_estimate), 0),
            func.coalesce(func.sum(ModelCall.pricing_unknown), 0),
        )
    ).one()
    tool_row = db.execute(
        select(
            func.count(ToolCall.id),
            func.coalesce(func.sum(ToolCall.cost_estimate), 0),
        )
    ).one()
    task_cost = db.execute(
        select(func.coalesce(func.sum(ResearchTask.cost_spent), 0))
    ).scalar_one()

    by_model = [
        CostBucket(key=f"{row[0]}/{row[1]}", calls=row[2], tokens=row[3], cost_cny=row[4])
        for row in db.execute(
            select(
                ModelCall.provider,
                ModelCall.model,
                func.count(ModelCall.id),
                func.coalesce(func.sum(ModelCall.total_tokens), 0),
                func.coalesce(func.sum(ModelCall.cost_estimate), 0),
            )
            .group_by(ModelCall.provider, ModelCall.model)
            .order_by(func.count(ModelCall.id).desc())
        ).all()
    ]
    by_tool = [
        CostBucket(key=row[0], calls=row[1], cost_cny=row[2])
        for row in db.execute(
            select(
                ToolCall.tool_name,
                func.count(ToolCall.id),
                func.coalesce(func.sum(ToolCall.cost_estimate), 0),
            )
            .group_by(ToolCall.tool_name)
            .order_by(func.count(ToolCall.id).desc())
        ).all()
    ]
    day_expr = func.strftime("%Y-%m-%d", ModelCall.created_at)
    by_day = [
        CostBucket(key=row[0] or "", calls=row[1], tokens=row[2], cost_cny=row[3])
        for row in db.execute(
            select(
                day_expr,
                func.count(ModelCall.id),
                func.coalesce(func.sum(ModelCall.total_tokens), 0),
                func.coalesce(func.sum(ModelCall.cost_estimate), 0),
            )
            .group_by(day_expr)
            .order_by(day_expr.desc())
            .limit(30)
        ).all()
    ]

    model_calls = int(model_row[0] or 0)
    pricing_unknown = int(model_row[3] or 0)
    return CostSummaryOut(
        model_calls=model_calls,
        model_tokens=int(model_row[1] or 0),
        model_cost_cny=Decimal(str(model_row[2] or 0)),
        tool_calls=int(tool_row[0] or 0),
        tool_cost_cny=Decimal(str(tool_row[1] or 0)),
        task_cost_cny=Decimal(str(task_cost or 0)),
        by_model=by_model,
        by_tool=by_tool,
        by_day=by_day,
        pricing_unknown_calls=pricing_unknown,
        note=_cost_note(model_calls, pricing_unknown),
    )


@router.get("/providers", response_model=list[ProviderStatusOut], summary="模型服务商状态")
def get_providers() -> list[ProviderStatusOut]:
    """可用性取决于环境变量里的密钥与 ``config/models.yaml`` 的 enabled/价目。"""
    report = provider_status()
    items: list[ProviderStatusOut] = []
    for name, entry in report.items():
        items.append(
            ProviderStatusOut(
                name=name,
                display_name=str(entry.get("display_name", "")),
                status=str(entry.get("status", "unknown_provider")),
                key_env=str(entry.get("api_key_env", "")),
                base_url=str(entry.get("base_url", "")),
                enabled=bool(entry.get("enabled", False)),
                note=str(entry.get("message", "")),
            )
        )
    return items


@router.get("/agents", summary="Agent 名册（agents/roster.yaml + 路由表）")
def get_agents() -> dict[str, object]:
    routing = get_agent_routing()
    roster = []
    for spec in load_roster():
        allowed = sorted(
            task_type
            for task_type in routing.task_types
            if spec.key in routing.allowed_agents_for(task_type)
        )
        roster.append(
            {
                "key": spec.key,
                "name": spec.display_name,
                "role": spec.role,
                "prompt": spec.prompt_path.name,
                "task_types": allowed,
                "tools": sorted(routing.tools_for(spec.key)),
                "db_scopes": sorted(routing.db_scopes_for(spec.key)),
            }
        )
    return {"count": len(roster), "agents": roster}


def _days_window(days: int, *, now: datetime | None = None) -> list[str]:
    """最近 N 天的日期串，从最早排到今天（画曲线要按时间正序）。"""
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _history_note(items: list[HistoryDayOut]) -> str:
    if not any(item.model_calls or item.tasks_created for item in items):
        return (
            "这段时间没有任何记录：不是统计坏了，而是确实还没跑过任务或调用过模型。"
            "空白的一天补 0，不画示例曲线。"
        )
    failed = sum(item.tasks_failed for item in items)
    if failed:
        return f"这段时间有 {failed} 个任务失败，先看失败原因再决定要不要重跑。"
    return "这段时间没有失败任务；曲线只反映真实记录，不代表内容质量。"


@router.get("/history", response_model=HistoryOut, summary="按天的运行历史（调用/任务/失败）")
def get_history(
    db: DbSession,
    days: int = Query(default=14, ge=1, le=MAX_HISTORY_DAYS, description="看最近多少天"),
) -> HistoryOut:
    """按天回答「花了多少、跑了几条、失败几次、卡了几次闸门」。

    模型调用含被预算或限流拦下的（它们也真的发生过，同样计入）；任务按完成时间
    落到那一天；闸门次数取 `paused` 事件。**缺的那天补 0**，不让前端自己猜。
    """
    window = _days_window(days)
    rows: dict[str, dict[str, int | Decimal]] = {
        day: {
            "model_calls": 0,
            "tokens": 0,
            "cost_cny": Decimal("0"),
            "tasks_created": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "subtasks_failed": 0,
            "gates": 0,
        }
        for day in window
    }

    call_day = func.strftime("%Y-%m-%d", ModelCall.created_at)
    for day, calls, tokens, cost in db.execute(
        select(
            call_day,
            func.count(ModelCall.id),
            func.coalesce(func.sum(ModelCall.total_tokens), 0),
            func.coalesce(func.sum(ModelCall.cost_estimate), 0),
        ).group_by(call_day)
    ).all():
        cell = rows.get(str(day or ""))
        if cell is None:
            continue
        cell["model_calls"] = int(calls or 0)
        cell["tokens"] = int(tokens or 0)
        cell["cost_cny"] = Decimal(str(cost or 0))

    created_day = func.strftime("%Y-%m-%d", ResearchTask.created_at)
    for day, count in db.execute(
        select(created_day, func.count(ResearchTask.id)).group_by(created_day)
    ).all():
        cell = rows.get(str(day or ""))
        if cell is not None:
            cell["tasks_created"] = int(count or 0)

    updated_day = func.strftime("%Y-%m-%d", ResearchTask.updated_at)
    for status, key in (
        (ResearchTaskStatus.COMPLETED, "tasks_completed"),
        (ResearchTaskStatus.FAILED, "tasks_failed"),
    ):
        for day, count in db.execute(
            select(updated_day, func.count(ResearchTask.id))
            .where(ResearchTask.status == status.value)
            .group_by(updated_day)
        ).all():
            cell = rows.get(str(day or ""))
            if cell is not None:
                cell[key] = int(count or 0)

    event_day = func.strftime("%Y-%m-%d", TaskEvent.created_at)
    for event_type, key in (
        (TaskEventType.SUBTASK_FAILED, "subtasks_failed"),
        (TaskEventType.PAUSED, "gates"),
    ):
        for day, count in db.execute(
            select(event_day, func.count(TaskEvent.id))
            .where(TaskEvent.event_type == event_type)
            .group_by(event_day)
        ).all():
            cell = rows.get(str(day or ""))
            if cell is not None:
                cell[key] = int(count or 0)

    items = [
        HistoryDayOut(
            day=day,
            model_calls=int(cell["model_calls"]),
            tokens=int(cell["tokens"]),
            cost_cny=Decimal(str(cell["cost_cny"])),
            tasks_created=int(cell["tasks_created"]),
            tasks_completed=int(cell["tasks_completed"]),
            tasks_failed=int(cell["tasks_failed"]),
            subtasks_failed=int(cell["subtasks_failed"]),
            gates=int(cell["gates"]),
        )
        for day in window
        for cell in (rows[day],)
    ]
    return HistoryOut(
        days=days,
        items=items,
        total_cost_cny=sum((item.cost_cny for item in items), Decimal("0")),
        total_model_calls=sum(item.model_calls for item in items),
        total_tasks_completed=sum(item.tasks_completed for item in items),
        total_tasks_failed=sum(item.tasks_failed for item in items),
        note=_history_note(items),
    )
