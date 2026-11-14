"""一条任务的时间构成（见 `backend/app/services/task_timing.py`）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubtaskTimingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtask_id: int
    sequence: int
    task_type: str
    #: 跑了几次（重试会累加）
    runs: int = 0
    seconds: float = 0.0
    #: 只有开始事件、没等到结束事件
    running: bool = False
    note: str = ""


class WaitWindowOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    ended_at: datetime | None = None
    seconds: float = 0.0
    #: 任务还停着，等到"现在"为止
    ongoing: bool = False


class TaskTimingOut(BaseModel):
    """墙钟、干活、等人、模型调用四笔账分开列。"""

    model_config = ConfigDict(extra="forbid")

    task_id: int
    title: str
    status: str
    first_at: datetime | None = None
    last_at: datetime | None = None
    total_seconds: float = 0.0
    work_seconds: float = 0.0
    wait_seconds: float = 0.0
    #: 模型调用耗时（与"干活"重叠，单独列，不参与加减）
    model_seconds: float = 0.0
    #: 总时长里没归到"干活/等人"的部分（任务创建到第一格开始这类空档）
    unpaired_seconds: float = 0.0
    subtasks: list[SubtaskTimingOut] = Field(default_factory=list)
    waits: list[WaitWindowOut] = Field(default_factory=list)
    note: str = ""
