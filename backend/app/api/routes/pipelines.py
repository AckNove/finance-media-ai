"""流水线目录：告诉界面"现在有哪些产线可以选"。

为什么要有这个端点
------------------
`config/pipelines.yaml` 一直是唯一真源，但只有代码知道它里面有什么：界面上新建研究任务时
产线是写死的 `research_to_script`，想"只要一份报告、不写脚本"的人没有入口（真跑过才知道：
那样会一路走到 `content.script`，在"结论还没确认"上停下——白占一道人工闸门）。

这里只读配置、不做任何判断：`stages` 的数量、哪些是人工闸门、最后一格是什么，
都直接来自 YAML。看的人据此决定选哪条，而不是靠猜。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.app.api.deps import COMMON_RESPONSES, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.config import get_pipelines_config
from backend.app.schemas.pipeline import (
    PipelineStageOut,
    PipelineSummaryOut,
    PipelinesOut,
)

router = APIRouter(
    prefix="/pipelines",
    tags=["pipelines"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


def _is_gate(stage: Any) -> bool:
    """人工闸门有两种写法：`kind: gate`，或 `kind: task` 上挂 `human_gate: true`。

    两种都在流水线里"等人"（前者等人放行，后者等人做完核查再放行），
    所以目录里统一算作闸门——只报一种会让人以为漏了一道。
    """

    if getattr(stage, "kind", "") == "gate":
        return True
    return bool(getattr(stage, "human_gate", False))


def _summary(name: str, spec: Any) -> PipelineSummaryOut:
    stages = list(spec.stages)
    gates = [stage.id for stage in stages if _is_gate(stage)]
    last = stages[-1].id if stages else ""
    return PipelineSummaryOut(
        name=name,
        display_name=spec.display_name or name,
        description=spec.description or "",
        stage_count=len(stages),
        gates=gates,
        ends_with=last,
        stages=[
            PipelineStageOut(
                id=stage.id,
                name=stage.name or stage.id,
                kind=stage.kind,
                task_type=getattr(stage, "task_type", "") or "",
                agent=getattr(stage, "agent", "") or "",
                human_gate=_is_gate(stage),
            )
            for stage in stages
        ],
    )


@router.get("", response_model=PipelinesOut, summary="流水线目录（可选产线）")
def list_pipelines() -> PipelinesOut:
    """列出 `config/pipelines.yaml` 里登记的全部产线，连同每一格的用途与闸门位置。"""

    config = get_pipelines_config()
    items = [_summary(name, spec) for name, spec in config.pipelines.items()]
    default = "research_to_script" if "research_to_script" in config.pipelines else (items[0].name if items else "")
    return PipelinesOut(
        default=default,
        items=items,
        note=(
            "产线来自 config/pipelines.yaml（唯一真源）。想只拿一份研究报告就选 research_only；"
            "想一直做到脚本就选 research_to_script。换产线要新建任务，已建任务的阶段不会跟着变。"
        ),
    )


__all__ = ["router"]
