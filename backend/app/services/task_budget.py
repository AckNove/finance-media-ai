"""一条研究任务的预算：上限多少、已经花了多少、还剩多少、下一次调用会不会被拦。

为什么单独写一个模块：预算闸门已经在 `backend/app/capabilities/budget.py` 里守着了，
但那道闸门只在"调用被拒"时留下一条事件。人真正想问的是三件更朴素的事——**这条任务
上限多少、现在花了多少、还剩多少**；以及撞线之后**下一次会被哪条规则拦**。
把它做成纯函数（只吃一个任务行），接口与页面才可能说同一句话。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# 与 backend/app/capabilities/budget.py 的原因码保持一致；这里只负责翻译成人话
REASON_TEXT = {
    "task_calls_exceeded": "调用次数到上限了",
    "task_cost_exceeded": "钱花到上限了",
    "single_call_exceeded": "单次调用本身就超过单次上限",
    "daily_cap_exceeded": "今天的总额度已经用完",
    "model_not_registered": "这个模型没在 config/models.yaml 里登记",
    "model_disabled": "这个模型被关掉了",
    "pricing_missing": "价目没核实——不确定价格就不许调用",
}


@dataclass(frozen=True)
class BudgetStatus:
    """一条任务的预算快照。`*_limit` 为 None 表示那一项没设上限。"""

    calls_limit: int | None = None
    tokens_limit: int | None = None
    cost_limit_cny: Decimal | None = None
    calls_used: int = 0
    tokens_used: int = 0
    cost_used_cny: Decimal = Decimal("0")
    calls_remaining: int | None = None
    tokens_remaining: int | None = None
    cost_remaining_cny: Decimal | None = None
    exhausted: bool = False
    blocked_reason: str = ""
    note: str = ""

    @property
    def has_limit(self) -> bool:
        return any(
            value is not None
            for value in (self.calls_limit, self.tokens_limit, self.cost_limit_cny)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls_limit": self.calls_limit,
            "tokens_limit": self.tokens_limit,
            "cost_limit_cny": None if self.cost_limit_cny is None else str(self.cost_limit_cny),
            "calls_used": self.calls_used,
            "tokens_used": self.tokens_used,
            "cost_used_cny": str(self.cost_used_cny),
            "calls_remaining": self.calls_remaining,
            "tokens_remaining": self.tokens_remaining,
            "cost_remaining_cny": (
                None if self.cost_remaining_cny is None else str(self.cost_remaining_cny)
            ),
            "exhausted": self.exhausted,
            "blocked_reason": self.blocked_reason,
            "has_limit": self.has_limit,
            "note": self.note,
        }


def _decade(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _remaining(limit: Any, used: Decimal) -> Decimal | None:
    if limit is None:
        return None
    left = _decade(limit) - used
    return left if left > 0 else Decimal("0")


def budget_of(task: Any) -> BudgetStatus:
    """从任务行算预算状态。

    只读 `budget_max_*` 与 `*_spent` 六个字段；`calls_reserved` / `cost_reserved`
    是"调用路上的占位"，不在这里体现（它们随调用落账就会退回 0）。
    """
    calls_limit = getattr(task, "budget_max_calls", None)
    tokens_limit = getattr(task, "budget_max_tokens", None)
    cost_limit = getattr(task, "budget_max_cost", None)
    calls_used = int(getattr(task, "calls_spent", 0) or 0)
    tokens_used = int(getattr(task, "tokens_spent", 0) or 0)
    cost_used = _decade(getattr(task, "cost_spent", None))

    calls_remaining = None if calls_limit is None else max(0, int(calls_limit) - calls_used)
    tokens_remaining = (
        None if tokens_limit is None else max(0, int(tokens_limit) - tokens_used)
    )
    cost_remaining = _remaining(cost_limit, cost_used)

    blocked_reason = ""
    if calls_limit is not None and calls_used >= int(calls_limit):
        blocked_reason = "task_calls_exceeded"
    elif cost_limit is not None and cost_used >= _decade(cost_limit):
        blocked_reason = "task_cost_exceeded"

    exhausted = bool(blocked_reason)
    if exhausted:
        note = (
            f"{REASON_TEXT[blocked_reason]}：这条任务的下一次模型调用会被预算闸门拒绝"
            "（返回 budget_blocked，不发请求、不花钱）。想继续就得新建任务或先确认还有没有必要。"
        )
    elif not any(
        value is not None for value in (calls_limit, tokens_limit, cost_limit)
    ):
        note = (
            "这条任务没设预算上限：只要日额度、价目、模型开关都正常，它就能一直调下去。"
            "要卡住它，建任务时填 budget_max_calls 或 budget_max_cost_cny。"
        )
    else:
        note = "还没撞线：下一次调用会照常走预算闸门（单次上限、任务上限、当日额度三道都要过）。"

    return BudgetStatus(
        calls_limit=None if calls_limit is None else int(calls_limit),
        tokens_limit=None if tokens_limit is None else int(tokens_limit),
        cost_limit_cny=None if cost_limit is None else _decade(cost_limit),
        calls_used=calls_used,
        tokens_used=tokens_used,
        cost_used_cny=cost_used,
        calls_remaining=calls_remaining,
        tokens_remaining=tokens_remaining,
        cost_remaining_cny=cost_remaining,
        exhausted=exhausted,
        blocked_reason=blocked_reason,
        note=note,
    )


__all__ = ["BudgetStatus", "REASON_TEXT", "budget_of"]
