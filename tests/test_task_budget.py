"""一条研究任务的预算看得见：上限多少、花了多少、还剩多少、下一次会不会被拦。

预算闸门本身早就有（backend/app/capabilities/budget.py），但人只能在"被拒"之后
从事件流里看到一行 budget_exceeded。任务详情该回答的是三件更朴素的事。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.services.task_budget import REASON_TEXT, budget_of


def _task(*, calls=None, tokens=None, cost=None, used_calls=0, used_tokens=0, used_cost="0"):
    return SimpleNamespace(
        budget_max_calls=calls,
        budget_max_tokens=tokens,
        budget_max_cost=None if cost is None else Decimal(str(cost)),
        calls_spent=used_calls,
        tokens_spent=used_tokens,
        cost_spent=Decimal(str(used_cost)),
    )


def test_a_task_without_limits_says_so_instead_of_pretending_it_is_fine() -> None:
    status = budget_of(_task())
    assert status.has_limit is False
    assert status.exhausted is False
    assert status.calls_remaining is None and status.cost_remaining_cny is None
    assert "没设预算上限" in status.note
    # 得告诉人怎么卡住它，而不是只说"没上限"
    assert "budget_max_calls" in status.note


def test_remaining_is_computed_for_each_limit_that_exists() -> None:
    status = budget_of(_task(calls=5, cost="0.5", used_calls=2, used_cost="0.1"))
    assert status.calls_remaining == 3
    assert status.cost_remaining_cny == Decimal("0.4")
    assert status.tokens_remaining is None  # token 没设上限就不编一个数
    assert status.exhausted is False
    assert "还没撞线" in status.note


def test_running_out_of_calls_is_reported_with_the_gate_reason() -> None:
    status = budget_of(_task(calls=3, used_calls=3, used_cost="0.2"))
    assert status.exhausted is True
    assert status.blocked_reason == "task_calls_exceeded"
    assert status.calls_remaining == 0
    assert "下一次模型调用会被预算闸门拒绝" in status.note
    assert status.blocked_reason in REASON_TEXT


def test_running_out_of_money_is_a_different_reason() -> None:
    status = budget_of(_task(cost="0.05", used_calls=1, used_cost="0.05"))
    assert status.blocked_reason == "task_cost_exceeded"
    assert status.cost_remaining_cny == Decimal("0")


def test_overspending_never_shows_a_negative_remainder() -> None:
    """已经超了也不能显示 -2 次——那种数字只会让人以为还能再花。"""
    status = budget_of(_task(calls=1, cost="0.01", used_calls=4, used_cost="0.03"))
    assert status.calls_remaining == 0
    assert status.cost_remaining_cny == Decimal("0")


def test_token_limit_becomes_visible_when_it_is_set() -> None:
    status = budget_of(_task(tokens=1000, used_tokens=250))
    assert status.tokens_remaining == 750
    assert status.has_limit is True


def test_as_dict_is_json_ready() -> None:
    payload = budget_of(_task(calls=2, cost="0.1", used_calls=1, used_cost="0.02")).as_dict()
    assert payload["calls_limit"] == 2
    assert payload["cost_used_cny"] == "0.02"
    assert payload["cost_remaining_cny"] == "0.08"
    assert isinstance(payload["exhausted"], bool)


def test_unknown_task_shape_does_not_explode() -> None:
    """接口层会拿 ORM 行喂它；字段缺失时按 0 算，不抛异常。"""
    status = budget_of(SimpleNamespace())
    assert status.calls_used == 0 and status.cost_used_cny == Decimal("0")
    assert status.exhausted is False


@pytest.mark.parametrize("reason", [
    "task_calls_exceeded",
    "task_cost_exceeded",
    "single_call_exceeded",
    "daily_cap_exceeded",
    "model_not_registered",
    "model_disabled",
    "pricing_missing",
])
def test_every_gate_reason_has_a_plain_chinese_line(reason: str) -> None:
    assert REASON_TEXT[reason].strip()
