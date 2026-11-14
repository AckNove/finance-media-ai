"""流水线相关的接口模型。

这里有两个用途不同的东西，都叫"pipeline"，但一个是"报告卡在哪"，另一个是"有哪些产线可选"：

1. ``ReportPipelineOut``：`GET /api/research-reports/{id}/pipeline`，告诉页面
   "这份报告现在能不能继续跑、卡在哪一格"。人在「语义核查」页确认完结论之后，
   页面要能立刻告诉他"现在可以去点继续跑了"，而不是让他自己去「研究任务」页翻。
2. ``PipelinesOut``：`GET /api/pipelines`，列出 `config/pipelines.yaml` 里登记的全部产线，
   供新建研究任务时选择（以前产线在界面上是写死的，想"只研究不写脚本"没有入口）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PipelineSubtaskOut(BaseModel):
    """卡住的阶段（只有确实卡住时才出现）。"""

    model_config = ConfigDict(extra="ignore")

    subtask_id: int
    sequence: int = 0
    task_type: str = ""
    attempts: int = 0
    failure_reason: str = Field("", description="稳定原因码，例如 no_confirmed_findings")


class ReportPipelineOut(BaseModel):
    """这份报告对应的研究任务现在的状态。"""

    model_config = ConfigDict(extra="ignore")

    report_id: int
    research_task_id: int | None = Field(None, description="没有关联任务时为 null")
    task_status: str = Field("", description="created/planning/running/paused/completed/failed/cancelled")
    paused: bool = Field(False, description="任务是否停在等人放行")
    blocked: PipelineSubtaskOut | None = Field(None, description="卡住的阶段（没有就是 null）")
    pending_checks: int = Field(0, description="这份报告还有几条核查等人确认")
    confirmed_checks: int = Field(0)
    can_resume: bool = Field(False, description="现在能不能点「继续跑」（任务暂停且已满足放行条件）")
    next_step: str = Field("", description="给人看的下一步，一句话")
    note: str = Field("", description="为什么还不能继续（不能继续时才有）")


class PipelineStageOut(BaseModel):
    """流水线里的一格。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: str = Field(description="task（要干活）或 gate（等人）")
    task_type: str = Field(default="", description="task 阶段对应的 task_type；gate 阶段为空")
    agent: str = Field(default="", description="由谁执行；gate 阶段为空")
    human_gate: bool = Field(default=False, description="这一格是否需要人（含 task 上挂 human_gate 的情况）")


class PipelineSummaryOut(BaseModel):
    """一条产线的概览 + 完整阶段表。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str = ""
    stage_count: int
    gates: list[str] = Field(default_factory=list, description="需要人的格 id，按顺序")
    ends_with: str = Field(default="", description="最后一格 id：跑到哪里就停")
    stages: list[PipelineStageOut] = Field(default_factory=list)


class PipelinesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = Field(default="", description="界面新建任务时默认选的产线")
    items: list[PipelineSummaryOut] = Field(default_factory=list)
    note: str = ""


__all__ = [
    "PipelineStageOut",
    "PipelineSubtaskOut",
    "PipelineSummaryOut",
    "PipelinesOut",
    "ReportPipelineOut",
]
