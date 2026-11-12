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


class VideoJobIn(BaseModel):
    """从报告开视频任务时的可选参数。

    默认目录名由报告 id + 日期生成，同一天对同一份报告再点一次就会撞名；
    这里允许调用方指定 ``job_id``，重试时换个名字即可（目录存在时仍然拒绝覆盖）。
    """

    job_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="任务目录名，省略则按 report_<id>_<日期> 生成",
    )


__all__ = ["VideoJobIn", "VideoJobOut"]
