"""运行监控的响应模型：按天的历史曲线。

字段都对应真实记录（``model_calls`` / ``research_tasks`` / ``task_events``），
所以某一天全 0 就真的表示那天没跑，不是统计缺失。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class HistoryDayOut(BaseModel):
    """一天的一格。"""

    day: str = Field(description="UTC 日期，YYYY-MM-DD")
    model_calls: int = 0
    tokens: int = 0
    cost_cny: Decimal = Decimal("0")
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    subtasks_failed: int = 0
    gates: int = Field(default=0, description="人工闸门（任务暂停）触发次数")


class HistoryOut(BaseModel):
    days: int
    items: list[HistoryDayOut] = Field(default_factory=list)
    total_cost_cny: Decimal = Decimal("0")
    total_model_calls: int = 0
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0
    note: str = ""


class DailyTaskItemOut(BaseModel):
    """一个暂停或失败的研究任务（只列关键字段，点进去看详情）。"""

    task_id: int
    title: str
    updated_at: str = ""
    failure_reason: str = ""


class DailySubtaskItemOut(BaseModel):
    subtask_id: int
    task_id: int | None = None
    task_type: str
    attempts: int = 0
    failure_reason: str = ""


class DailyJobItemOut(BaseModel):
    job_id: str
    invalidated_at: str = ""
    reason: str = ""


class DailyCostOut(BaseModel):
    calls: int = 0
    tokens: int = 0
    cost_cny: Decimal = Decimal("0")


class DailyTasksOut(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)
    paused: list[DailyTaskItemOut] = Field(default_factory=list)
    failed_in_window: list[DailyTaskItemOut] = Field(default_factory=list)


class DailySubtasksOut(BaseModel):
    failed_in_window: list[DailySubtaskItemOut] = Field(default_factory=list)
    count: int = 0


class DailyChecksOut(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)
    pending: int = 0
    pending_reports: list[int] = Field(default_factory=list)


class DailyJobsOut(BaseModel):
    jobs: int = 0
    invalidated: list[DailyJobItemOut] = Field(default_factory=list)
    invalidated_count: int = 0


class DailyBackupOut(BaseModel):
    dir: str = ""
    exists: bool = False
    newest: str = ""
    age_days: float | None = None
    count: int | None = None


class DailyDiskOut(BaseModel):
    ok: bool = False
    free_gb: float | None = None
    total_gb: float | None = None
    error: str = ""


class DailySummaryOut(BaseModel):
    """每日体检。字段全部来自真实记录，没有数据就是 0，不填示例值。"""

    generated_at: str
    database_url: str = ""
    protected_database: bool = False
    window_days: int = 1
    tasks: DailyTasksOut
    subtasks: DailySubtasksOut
    checks: DailyChecksOut
    jobs: DailyJobsOut
    cost: dict[str, DailyCostOut] = Field(default_factory=dict)
    backup: DailyBackupOut
    disk: DailyDiskOut
    sections_unavailable: dict[str, str] = Field(
        default_factory=dict, description="查不了的模块（老库缺表时出现），值里写原因"
    )
    attention: list[str] = Field(
        default_factory=list, description="需要人处理的事项；空列表表示今天干净"
    )
    ok: bool = True
