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


class ScriptFilmIn(BaseModel):
    """按脚本开视频任务时的可选参数（与 ``VideoJobIn`` 同一条理由）。"""

    job_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="任务目录名，省略则按 film_s<script_id>_<日期> 生成",
    )


class ScriptFilmOut(BaseModel):
    """按脚本开出来的视频任务目录。

    ``blocked`` 为真表示分镜没排出来（句子太少之类），原因在 ``blocked_reason``；
    这时目录仍然留着，脚本快照与说明都在，方便人改完稿重开一条。
    """

    job_id: str = Field(description="任务目录名（在 content/jobs/ 下）")
    path: str = Field(description="任务目录绝对路径")
    script_id: int
    report_id: int | None = Field(default=None, description="脚本挂着的报告（可能为空）")
    stage: str = Field(description="已完成到哪一步")
    next_stage: str = Field(description="下一步该做什么（阶段名）")
    blocked: bool = Field(description="分镜是否没排出来")
    blocked_reason: str = Field(default="", description="卡住的稳定原因码")
    next_step: str = Field(description="给人看的具体下一步")
    shot_count: int = Field(default=0, description="排出的镜头数")
    total_seconds: float = Field(default=0.0, description="分镜总时长（秒）")
    files: list[str] = Field(default_factory=list, description="本次写出的文件")
    storyline: dict[str, Any] = Field(default_factory=dict, description="分镜结果摘要")


__all__ = ["ScriptFilmIn", "ScriptFilmOut", "VideoJobIn", "VideoJobOut"]
