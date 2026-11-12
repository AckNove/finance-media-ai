"""研究任务相关的 API 模型（编排/执行/事件）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import ORMBase


class ResearchTaskCreate(BaseModel):
    """建一个研究任务：只写"要研究什么"，怎么拆由流水线决定。"""

    title: str = Field(min_length=1, max_length=200, description="任务标题")
    objective: str = Field(default="", max_length=2000, description="研究目标（写清要回答什么）")
    pipeline: str = Field(default="research_to_script", description="config/pipelines.yaml 里的流水线名")
    topic_id: int | None = Field(default=None, ge=1)
    queries: list[str] = Field(default_factory=list, description="显式检索词；为空则由关键词规则拆分")
    files: list[str] = Field(default_factory=list, description="已登记的本地文件路径")
    skip: list[str] = Field(
        default_factory=list,
        description="显式跳过的阶段 id（例如没有模型时跳过 topic_candidate/research_plan）",
    )
    skip_reason: str = Field(default="", max_length=500, description="为什么跳过，会写进任务计划")
    budget_max_calls: int | None = Field(default=None, ge=1)
    budget_max_cost_cny: Decimal | None = Field(default=None, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    start: bool = Field(default=True, description="建完是否立刻启动")


class SubtaskOut(ORMBase):
    id: int
    sequence: int
    question: str
    task_type: str
    assignee: str | None = None
    status: str
    attempts: int
    max_attempts: int
    failure_reason: str | None = None
    output: Any | None = None


class ResearchTaskOut(ORMBase):
    id: int
    title: str
    objective: str
    status: str
    topic_id: int | None = None
    planner: str | None = Field(default=None, description="计划由谁产出：pipeline/model/keywords")
    calls_spent: int = 0
    tokens_spent: int = 0
    cost_spent_cny: Decimal = Decimal("0")
    failure_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlanningOut(BaseModel):
    """``research.plan`` 阶段到底是怎么规划的（模型还是关键词规则）。"""

    model_config = ConfigDict(extra="ignore")

    planner: Literal["model", "keywords"] | str = "keywords"
    model_used: bool = False
    objective: str = ""
    query_count: int = 0
    subtask_count: int = 0
    core_questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    proposed_subtasks: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str = ""
    degraded_message: str = ""
    note: str = ""


class ResearchTaskDetailOut(ResearchTaskOut):
    subtasks: list[SubtaskOut] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    planning: PlanningOut | None = Field(
        default=None, description="规划模式与降级原因（来自 research.plan 阶段的产出）"
    )


class TaskEventOut(ORMBase):
    id: int
    event_seq: int
    event_type: str
    subtask_id: int | None = None
    actor: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime | None = None


class RunRequest(BaseModel):
    max_iterations: int = Field(default=1, ge=1, le=10, description="最多推进几格")
    poll_seconds: float = Field(default=0.0, ge=0, le=30)


class RunReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    iterations: int
    completed: list[int] = Field(default_factory=list)
    blocked: list[int] = Field(default_factory=list)
    retried: list[int] = Field(default_factory=list)
    failed: list[int] = Field(default_factory=list)
    reaped: list[int] = Field(default_factory=list)
    added_subtasks: list[int] = Field(
        default_factory=list, description="本轮规划动态追加的采集子任务 id"
    )
    idle: bool = False
    stopped_reason: str = ""
    task: ResearchTaskDetailOut | None = None


class CostBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    calls: int = 0
    tokens: int = 0
    cost_cny: Decimal = Decimal("0")


class CostSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_calls: int = 0
    model_tokens: int = 0
    model_cost_cny: Decimal = Decimal("0")
    tool_calls: int = 0
    tool_cost_cny: Decimal = Decimal("0")
    task_cost_cny: Decimal = Decimal("0")
    by_model: list[CostBucket] = Field(default_factory=list)
    by_tool: list[CostBucket] = Field(default_factory=list)
    by_day: list[CostBucket] = Field(default_factory=list)
    pricing_unknown_calls: int = 0
    note: str = ""


class ProviderStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str = ""
    status: Literal["ready", "missing_api_key", "provider_disabled", "unknown_provider", "missing_base_url"]
    key_env: str = ""
    base_url: str = ""
    enabled: bool = False
    note: str = ""
