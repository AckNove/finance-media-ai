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
