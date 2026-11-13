"""报告"卡在哪、下一步做什么"的接口模型（`GET /api/research-reports/{id}/pipeline`）。

用途：人在「语义核查」页确认完结论之后，页面要能立刻告诉他"现在可以去点继续跑了"，
而不是让他自己去「研究任务」页翻是哪一条任务、卡在哪个阶段。
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


__all__ = ["PipelineSubtaskOut", "ReportPipelineOut"]
