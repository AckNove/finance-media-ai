"""运行监控：成本、模型服务商、Agent 名册。

这些端点只读，供前端"成本统计 / 模型设置 / 服务商设置 / 系统设置"页面使用。
数据一律来自真实调用记录（``model_calls`` / ``tool_calls`` / ``research_tasks``），
没有记录就显示 0，不用示例数字填充。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, require_caller
from backend.app.capabilities.model.providers import provider_status
from backend.app.core.caller import CallerKind
from backend.app.core.config import get_agent_routing
from backend.app.models.execution import ResearchTask
from backend.app.models.metering import ModelCall, ToolCall
from backend.app.schemas.research_task import CostBucket, CostSummaryOut, ProviderStatusOut
from backend.app.services.agent_registry import load_roster

router = APIRouter(
    prefix="/system",
    tags=["monitoring"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


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
