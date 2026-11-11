"""成本闸门判定（M2.0 T04，纯函数，不触碰数据库与网络）。

三道闸门（见 system_architecture.md §8.2）：

1. **单次闸门**：``est_tokens`` / ``est_cost`` 不得超过单次上限；
2. **任务闸门**：``calls_spent + calls_reserved + est_calls ≤ max_calls``，
   金额同理（``spent + reserved + est ≤ max_cost``）；
3. **当日闸门**：``今日已花 + est ≤ daily_cap``（超限只拒绝新任务）。

关键点：判定式里必须**包含预留**（reserved）。只比较"已花"会让并发/重试把预算冲破，
这正是 M1.1 审查的 P0-2 要修的问题。

价目缺失的处理遵循 M1.2 决策 **Q8=B：默认拒绝**（``pricing_policy=reject_unknown``），
只有 ``config/models.yaml`` 的 ``approved_exceptions`` 里显式登记的模型
才允许按上界预留调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.core.config import get_cost_settings, get_models_config
from backend.app.core.config_schema import ModelsConfig

BudgetReason = Literal[
    "allowed",
    "single_call_exceeded",
    "task_calls_exceeded",
    "task_cost_exceeded",
    "daily_cap_exceeded",
    "pricing_missing",
    "model_disabled",
    "model_not_registered",
]

PricingStatus = Literal["known", "missing", "approved_exception"]


@dataclass(frozen=True, slots=True)
class PriceResolution:
    """一次调用的价格解析结果。"""

    status: PricingStatus
    input_price_per_1k_cny: float | None = None
    output_price_per_1k_cny: float | None = None
    upper_bound_per_call_cny: float | None = None
    exception_reason: str | None = None

    @property
    def billable(self) -> bool:
        return self.status == "known"


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: BudgetReason
    projected_calls: int
    projected_cost_cny: float
    message: str = ""


def resolve_price(
    provider: str,
    model: str,
    *,
    models_config: ModelsConfig | None = None,
) -> PriceResolution:
    """解析某模型的价格；缺失时按 Q8=B 返回 missing，**绝不返回 0 元**。"""
    config = models_config or get_models_config()
    entry = config.find_model(provider, model)
    if entry is None:
        return PriceResolution(status="missing", exception_reason="model_not_registered")
    if entry.pricing_status == "known":
        return PriceResolution(
            status="known",
            input_price_per_1k_cny=entry.input_price_per_1k_cny,
            output_price_per_1k_cny=entry.output_price_per_1k_cny,
        )
    # 价目未知：只有人工例外登记过才放行
    for exception in config.cost.approved_exceptions:
        if exception.provider == provider and exception.model == model:
            return PriceResolution(
                status="approved_exception",
                upper_bound_per_call_cny=exception.max_cost_per_call_cny,
                exception_reason=f"approved_by={exception.approved_by}",
            )
    return PriceResolution(status="missing", exception_reason="pricing_unknown")


def evaluate(
    *,
    est_calls: int = 1,
    est_cost_cny: float,
    calls_spent: int,
    cost_spent_cny: float,
    calls_reserved: int = 0,
    cost_reserved_cny: float = 0.0,
    max_calls: int,
    max_cost_cny: float,
    daily_spent_cny: float = 0.0,
    daily_cap_cny: float | None = None,
    max_cost_per_call_cny: float | None = None,
) -> BudgetDecision:
    """判定一次调用是否被允许。纯函数：输入全部显式传入，便于测试与审计复算。

    ``max_cost_per_call_cny`` 对应"单次闸门"：价目已知时按实际价目算出的
    单次费用、价目未知时按人工例外的上界，都不得突破它。None 表示不限制。
    """
    if est_calls <= 0:
        raise ValueError("est_calls 必须为正整数")
    if est_cost_cny < 0:
        raise ValueError("est_cost_cny 不能为负")

    projected_calls = calls_spent + calls_reserved + est_calls
    projected_cost = cost_spent_cny + cost_reserved_cny + est_cost_cny

    if max_cost_per_call_cny is not None and est_cost_cny > max_cost_per_call_cny:
        return BudgetDecision(
            allowed=False,
            reason="single_call_exceeded",
            projected_calls=projected_calls,
            projected_cost_cny=projected_cost,
            message=(
                f"单次预估费用 {est_cost_cny:.4f} 超过单次上限 "
                f"{max_cost_per_call_cny:.4f}"
            ),
        )
    if projected_calls > max_calls:
        return BudgetDecision(
            allowed=False,
            reason="task_calls_exceeded",
            projected_calls=projected_calls,
            projected_cost_cny=projected_cost,
            message=f"任务调用次数将达到 {projected_calls}，超过上限 {max_calls}",
        )
    if projected_cost > max_cost_cny:
        return BudgetDecision(
            allowed=False,
            reason="task_cost_exceeded",
            projected_calls=projected_calls,
            projected_cost_cny=projected_cost,
            message=f"任务费用将达到 {projected_cost:.4f}，超过上限 {max_cost_cny:.4f}",
        )
    cap = daily_cap_cny if daily_cap_cny is not None else get_cost_settings().daily_cap_cny
    if daily_spent_cny + est_cost_cny > cap:
        return BudgetDecision(
            allowed=False,
            reason="daily_cap_exceeded",
            projected_calls=projected_calls,
            projected_cost_cny=projected_cost,
            message=f"当日费用将达到 {daily_spent_cny + est_cost_cny:.4f}，超过日上限 {cap:.4f}",
        )
    return BudgetDecision(
        allowed=True,
        reason="allowed",
        projected_calls=projected_calls,
        projected_cost_cny=projected_cost,
    )


def evaluate_model_call(
    *,
    provider: str,
    model: str,
    est_cost_cny: float,
    calls_spent: int,
    cost_spent_cny: float,
    calls_reserved: int,
    cost_reserved_cny: float,
    max_calls: int,
    max_cost_cny: float,
    daily_spent_cny: float = 0.0,
    models_config: ModelsConfig | None = None,
) -> tuple[PriceResolution, BudgetDecision]:
    """把"价目解析"与"预算判定"串起来：价目未知先拒（Q8=B），再算三道闸门。"""
    config = models_config or get_models_config()
    price = resolve_price(provider, model, models_config=config)
    entry = config.find_model(provider, model)
    if entry is None:
        return price, BudgetDecision(
            allowed=False,
            reason="model_not_registered",
            projected_calls=calls_spent + calls_reserved + 1,
            projected_cost_cny=cost_spent_cny + cost_reserved_cny + est_cost_cny,
            message=f"模型 {provider}/{model} 未在 config/models.yaml 登记",
        )
    if not entry.enabled:
        return price, BudgetDecision(
            allowed=False,
            reason="model_disabled",
            projected_calls=calls_spent + calls_reserved + 1,
            projected_cost_cny=cost_spent_cny + cost_reserved_cny + est_cost_cny,
            message=f"模型 {provider}/{model} 未启用",
        )
    if price.status == "missing":
        return price, BudgetDecision(
            allowed=False,
            reason="pricing_missing",
            projected_calls=calls_spent + calls_reserved + 1,
            projected_cost_cny=cost_spent_cny + cost_reserved_cny + est_cost_cny,
            message="价目缺失且无人工例外登记：按 Q8=B 拒绝调用（不发请求、不计费）",
        )
    decision = evaluate(
        est_calls=1,
        est_cost_cny=est_cost_cny,
        calls_spent=calls_spent,
        cost_spent_cny=cost_spent_cny,
        calls_reserved=calls_reserved,
        cost_reserved_cny=cost_reserved_cny,
        max_calls=max_calls,
        max_cost_cny=max_cost_cny,
        daily_spent_cny=daily_spent_cny,
        max_cost_per_call_cny=price.upper_bound_per_call_cny,
    )
    return price, decision


__all__ = [
    "BudgetDecision",
    "BudgetReason",
    "PriceResolution",
    "PricingStatus",
    "evaluate",
    "evaluate_model_call",
    "resolve_price",
]
