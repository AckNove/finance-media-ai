"""报告往下走的进度（只读事实，不替人做决定）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JobProgressOut(BaseModel):
    """一个视频任务在这份报告下的状态。"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    title: str = ""
    readiness_status: str | None = None
    readiness_label: str | None = None
    usable: bool = False
    film_path: str = ""
    next_step: str = ""


class ReportProgressOut(BaseModel):
    """一份报告：脚本几条、视频任务几个、下一步该干什么。"""

    model_config = ConfigDict(extra="forbid")

    report_id: int
    script_count: int = 0
    latest_script_version: int | None = None
    latest_script_id: int | None = None
    job_count: int = 0
    jobs: list[JobProgressOut] = Field(default_factory=list)
    next_step: str = ""


__all__ = ["JobProgressOut", "ReportProgressOut"]
