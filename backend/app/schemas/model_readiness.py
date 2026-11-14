"""模型可用性快照：每个模型现在能不能调、缺什么、上次调用怎么样。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LastCallOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = ""
    error_message: str = ""
    created_at: str = ""
    ok: bool = False


class ModelReadinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    display_name: str = ""
    enabled: bool = False
    provider_status: str = "unknown"
    provider_message: str = ""
    pricing_status: str = ""
    price_checked_at: str = ""
    input_price_per_1k_cny: float | None = None
    output_price_per_1k_cny: float | None = None
    has_price: bool = False
    ready: bool = False
    actionable: bool = Field(
        default=False, description="能用；且上一次调用不是失败（失败也值得看一眼）"
    )
    blocked_reason: str = ""
    next_step: str = ""
    last_call: LastCallOut | None = None


class ModelsReadinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_feature: bool = True
    items: list[ModelReadinessOut] = Field(default_factory=list)
    ready_count: int = 0
    note: str = ""


def to_out(items: list[Any]) -> list[ModelReadinessOut]:
    return [ModelReadinessOut(**item.as_dict()) for item in items]


__all__ = ["LastCallOut", "ModelReadinessOut", "ModelsReadinessOut", "to_out"]
