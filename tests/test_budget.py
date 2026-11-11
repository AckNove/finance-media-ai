"""M2.0 T04：成本闸门（纯函数）。

要点：
- 价目缺失 → ``missing``，**绝不当成 0 元**（Q8=B：默认拒绝）；
- 三道闸门（单次 / 任务 / 当日）都要把 reserved 算进判定式；
- 拒绝理由必须可区分，便于审计与前端提示。
"""

from __future__ import annotations

import pytest

from backend.app.capabilities.budget import (
    evaluate,
    evaluate_model_call,
    resolve_price,
)


# --- price ----------------------------------------------------------------


def test_unknown_model_price_is_missing_not_zero():
    price = resolve_price("deepseek", "no-such-model")
    assert price.status == "missing"
    assert price.billable is False
    assert price.exception_reason == "model_not_registered"
    assert price.input_price_per_1k_cny is None and price.output_price_per_1k_cny is None


def test_registered_model_without_pricing_is_missing():
    price = resolve_price("deepseek", "deepseek-chat")
    assert price.status == "missing"
    assert price.billable is False
    assert price.exception_reason == "pricing_unknown"


def test_known_price_from_models_config():
    from backend.app.core.config_schema import ModelsConfig

    config = ModelsConfig(
        version=1,
        updated_at="2026-11-10",
        cost={"currency": "CNY", "pricing_policy": "reject_unknown"},
        providers={"deepseek": {"display_name": "DeepSeek", "enabled": True}},
        models=[
            {
                "provider": "deepseek",
                "model": "priced",
                "input_price_per_1k_cny": 0.001,
                "output_price_per_1k_cny": 0.002,
                "enabled": True,
            }
        ],
    )
    price = resolve_price("deepseek", "priced", models_config=config)
    assert price.status == "known" and price.billable is True
    assert price.input_price_per_1k_cny == pytest.approx(0.001)


def test_approved_exception_allows_missing_price_with_upper_bound():
    from backend.app.core.config_schema import ModelsConfig

    config = ModelsConfig(
        version=1,
        updated_at="2026-11-10",
        cost={
            "currency": "CNY",
            "pricing_policy": "reject_unknown",
            "approved_exceptions": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "max_cost_per_call_cny": 0.05,
                    "total_budget_cny": 1.0,
                    "approved_by": "owner",
                    "approved_at": "2026-11-10",
                }
            ],
        },
        providers={"deepseek": {"display_name": "DeepSeek", "enabled": True}},
        models=[{"provider": "deepseek", "model": "deepseek-chat", "enabled": True}],
    )
    price = resolve_price("deepseek", "deepseek-chat", models_config=config)
    assert price.status == "approved_exception"
    assert price.billable is False, "例外只允许按上界预留，不能算作已知价"
    assert price.upper_bound_per_call_cny == pytest.approx(0.05)


# --- evaluate -------------------------------------------------------------


def test_evaluate_allows_within_budget():
    decision = evaluate(
        est_cost_cny=0.01,
        calls_spent=1,
        cost_spent_cny=0.01,
        max_calls=10,
        max_cost_cny=1.0,
        daily_spent_cny=0.1,
        daily_cap_cny=5.0,
    )
    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.projected_calls == 2


def test_evaluate_rejects_single_call_above_per_call_cap():
    decision = evaluate(
        est_cost_cny=0.2,
        calls_spent=0,
        cost_spent_cny=0.0,
        max_calls=10,
        max_cost_cny=1.0,
        max_cost_per_call_cny=0.05,
    )
    assert decision.allowed is False
    assert decision.reason == "single_call_exceeded"


def test_evaluate_rejects_when_calls_exceeded():
    decision = evaluate(
        est_cost_cny=0.0,
        calls_spent=9,
        cost_spent_cny=0.0,
        calls_reserved=1,
        max_calls=10,
        max_cost_cny=1.0,
    )
    assert decision.allowed is False
    assert decision.reason == "task_calls_exceeded"
    assert decision.projected_calls == 11


def test_evaluate_counts_reserved_cost_towards_task_limit():
    decision = evaluate(
        est_cost_cny=0.3,
        calls_spent=0,
        cost_spent_cny=0.5,
        cost_reserved_cny=0.4,
        max_calls=10,
        max_cost_cny=1.0,
    )
    assert decision.allowed is False
    assert decision.reason == "task_cost_exceeded"
    assert decision.projected_cost_cny == pytest.approx(1.2)


def test_evaluate_rejects_when_daily_cap_exceeded():
    decision = evaluate(
        est_cost_cny=0.5,
        calls_spent=0,
        cost_spent_cny=0.0,
        max_calls=100,
        max_cost_cny=10.0,
        daily_spent_cny=4.8,
        daily_cap_cny=5.0,
    )
    assert decision.allowed is False
    assert decision.reason == "daily_cap_exceeded"


def test_evaluate_uses_configured_daily_cap_by_default():
    decision = evaluate(
        est_cost_cny=100.0,
        calls_spent=0,
        cost_spent_cny=0.0,
        max_calls=100,
        max_cost_cny=1000.0,
    )
    assert decision.allowed is False
    assert decision.reason == "daily_cap_exceeded", "未显式给 daily_cap 时必须读配置"


def test_evaluate_rejects_invalid_arguments():
    with pytest.raises(ValueError):
        evaluate(est_calls=0, est_cost_cny=0.0, calls_spent=0, cost_spent_cny=0.0, max_calls=1, max_cost_cny=1.0)
    with pytest.raises(ValueError):
        evaluate(est_cost_cny=-1.0, calls_spent=0, cost_spent_cny=0.0, max_calls=1, max_cost_cny=1.0)


# --- evaluate_model_call --------------------------------------------------


def test_model_call_rejected_because_model_disabled():
    price, decision = evaluate_model_call(
        provider="deepseek",
        model="deepseek-chat",
        est_cost_cny=0.01,
        calls_spent=0,
        cost_spent_cny=0.0,
        calls_reserved=0,
        cost_reserved_cny=0.0,
        max_calls=10,
        max_cost_cny=1.0,
    )
    assert price.status == "missing"
    assert decision.allowed is False
    assert decision.reason == "model_disabled"


def test_model_call_rejected_because_not_registered():
    _, decision = evaluate_model_call(
        provider="openai",
        model="gpt-unknown",
        est_cost_cny=0.01,
        calls_spent=0,
        cost_spent_cny=0.0,
        calls_reserved=0,
        cost_reserved_cny=0.0,
        max_calls=10,
        max_cost_cny=1.0,
    )
    assert decision.allowed is False
    assert decision.reason == "model_not_registered"


def test_model_call_rejected_because_pricing_missing_when_enabled():
    from backend.app.core.config_schema import ModelsConfig

    config = ModelsConfig(
        version=1,
        updated_at="2026-11-10",
        cost={"currency": "CNY", "pricing_policy": "reject_unknown"},
        providers={"deepseek": {"display_name": "DeepSeek", "enabled": True}},
        models=[{"provider": "deepseek", "model": "no-price", "enabled": True}],
    )
    price, decision = evaluate_model_call(
        provider="deepseek",
        model="no-price",
        est_cost_cny=0.01,
        calls_spent=0,
        cost_spent_cny=0.0,
        calls_reserved=0,
        cost_reserved_cny=0.0,
        max_calls=10,
        max_cost_cny=1.0,
        models_config=config,
    )
    assert price.status == "missing"
    assert decision.allowed is False
    assert decision.reason == "pricing_missing"


def test_model_call_allowed_when_priced_and_within_budget():
    from backend.app.core.config_schema import ModelsConfig

    config = ModelsConfig(
        version=1,
        updated_at="2026-11-10",
        cost={"currency": "CNY", "pricing_policy": "reject_unknown"},
        providers={"deepseek": {"display_name": "DeepSeek", "enabled": True}},
        models=[
            {
                "provider": "deepseek",
                "model": "priced",
                "input_price_per_1k_cny": 0.001,
                "output_price_per_1k_cny": 0.002,
                "enabled": True,
            }
        ],
    )
    price, decision = evaluate_model_call(
        provider="deepseek",
        model="priced",
        est_cost_cny=0.01,
        calls_spent=2,
        cost_spent_cny=0.02,
        calls_reserved=0,
        cost_reserved_cny=0.0,
        max_calls=60,
        max_cost_cny=2.0,
        daily_spent_cny=0.5,
        models_config=config,
    )
    assert price.status == "known"
    assert decision.allowed is True
    assert decision.projected_calls == 3
