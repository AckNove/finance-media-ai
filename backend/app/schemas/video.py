"""视频交接的响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VideoJobOut(BaseModel):
    """从报告开出的视频任务目录。"""

    job_id: str = Field(description="任务目录名（在 content/jobs/ 下）")
    path: str = Field(description="任务目录绝对路径")
    stage: str = Field(description="已完成到哪一步")
    next_stage: str = Field(description="下一步该做什么（阶段名）")
    blocked: bool = Field(description="是否卡住（例如没有可用模型）")
    blocked_reason: str = Field(default="", description="卡住的稳定原因码")
    next_step: str = Field(description="给人看的具体下一步")
    report_id: int
    evidence_count: int
    source_count: int
    files: list[str] = Field(default_factory=list, description="本次写出的文件")
    outline: dict[str, Any] = Field(
        default_factory=dict,
        description="规则分镜结果（ok/shot_count/total_seconds/reason），排不出来时给原因",
    )


__all__ = ["VideoJobOut"]
