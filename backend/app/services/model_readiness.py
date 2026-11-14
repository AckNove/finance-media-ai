"""每个模型现在到底能不能调用，不能的话缺什么。

模型网关的拒绝码散在三处：功能开关（`features.model`）、服务商解析（缺密钥 / 缺地址）、
价目（未核价不许调用）。运营时最常问的一句是「为什么这个模型没用上」——
这个模块把它压成一行：`ready` + 一个原因码 + 一句能照做的下一步。

顺带把「上一次真调用」的结果带上：价目配错、密钥过期这类事，看历史比看配置快。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import select

from backend.app.capabilities.model.providers import provider_status
from backend.app.core.config import get_features_settings, get_models_config
from backend.app.core.config_schema import ModelSettings, ModelsConfig
from backend.app.models.metering import ModelCall

#: 原因码 → 一句人话（顺序即判断顺序，见 `readiness_of`）
REASON_TEXT: dict[str, str] = {
    "feature_disabled": "总开关 features.model 是关的：所有模型调用都会被拒绝（不会退化成假数据）",
    "provider_disabled": "服务商没启用：在 config/models.yaml 的 providers 段把 enabled 改成 true",
    "missing_api_key": "缺密钥：设置对应的环境变量（.env 或系统环境变量），本页不显示密钥内容",
    "missing_base_url": "缺接口地址：设置对应的环境变量，或在该 provider 下写默认地址",
    "model_disabled": "模型没启用：在 config/models.yaml 把这个模型的 enabled 改成 true",
    "pricing_missing": "价目没配：未知价目的模型会被直接拒绝调用（不按 0 元记账），补上输入/输出价再试",
    "ready": "",
}

#: 这些"上一次调用"的状态说明那次调用没成功，值得看一眼
BAD_CALL_STATUSES = frozenset({"error", "timeout", "budget_blocked"})


@dataclass(frozen=True)
class LastCall:
    """上一次真调用（同一 provider/model 里最新的一条）。"""

    status: str
    error_message: str = ""
    created_at: datetime | None = None
    ok: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "ok": self.ok,
        }


@dataclass(frozen=True)
class ModelReadiness:
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
    ready: bool = False
    blocked_reason: str = ""
    next_step: str = ""
    last_call: LastCall | None = None

    @property
    def has_price(self) -> bool:
        return self.input_price_per_1k_cny is not None and self.output_price_per_1k_cny is not None

    @property
    def actionable(self) -> bool:
        """能不能用，以及用得顺不顺——`last_call` 失败也算"要看一眼"。"""
        if not self.ready:
            return False
        return not (self.last_call and not self.last_call.ok)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "provider_status": self.provider_status,
            "provider_message": self.provider_message,
            "pricing_status": self.pricing_status,
            "price_checked_at": self.price_checked_at,
            "input_price_per_1k_cny": self.input_price_per_1k_cny,
            "output_price_per_1k_cny": self.output_price_per_1k_cny,
            "has_price": self.has_price,
            "ready": self.ready,
            "actionable": self.actionable,
            "blocked_reason": self.blocked_reason,
            "next_step": self.next_step,
            "last_call": self.last_call.as_dict() if self.last_call else None,
        }


def last_calls(session: Any) -> dict[tuple[str, str], LastCall]:
    """每个 provider/model 最近一次调用；一次查询后在内存里挑最新，避免 N+1。"""
    rows = session.execute(
        select(
            ModelCall.provider,
            ModelCall.model,
            ModelCall.status,
            ModelCall.error_message,
            ModelCall.created_at,
        ).order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
    ).all()
    seen: dict[tuple[str, str], LastCall] = {}
    for provider, model, status, error_message, created_at in rows:
        key = (str(provider), str(model))
        if key in seen:
            continue
        status_text = str(getattr(status, "value", status))
        seen[key] = LastCall(
            status=status_text,
            error_message=str(error_message or ""),
            created_at=created_at,
            ok=status_text not in BAD_CALL_STATUSES,
        )
    return seen


def readiness_of(
    settings: ModelSettings,
    *,
    providers: Mapping[str, Mapping[str, object]],
    model_feature: bool = True,
) -> tuple[bool, str]:
    """按判断顺序给一个模型下结论；返回 `(可用, 原因码)`。"""
    if not model_feature:
        return False, "feature_disabled"
    provider = providers.get(settings.provider) or {}
    if not provider.get("enabled", False):
        return False, "provider_disabled"
    status = str(provider.get("status") or "unknown")
    if status != "ready":
        return False, status if status in REASON_TEXT else "provider_disabled"
    if not settings.enabled:
        return False, "model_disabled"
    if settings.input_price_per_1k_cny is None or settings.output_price_per_1k_cny is None:
        return False, "pricing_missing"
    return True, "ready"


def _pricing_label(settings: ModelSettings) -> str:
    """价目出处压成一句话：有来源 + 有核对日期才算"核过价"。"""
    provenance = settings.price_provenance
    source = str(provenance.get("source") or "").strip()
    checked = str(provenance.get("checked_at") or "").strip()
    if not source:
        return "没记出处"
    if not checked:
        return "记了出处但没核对日期"
    return f"已核价 {checked}"


def collect(
    session: Any,
    *,
    models_config: ModelsConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[ModelReadiness]:
    config = models_config or get_models_config()
    try:
        model_feature = bool(get_features_settings().model)
    except Exception:  # pragma: no cover - 配置读不出来时按开处理，别把错误说成"关掉了"
        model_feature = True
    providers = provider_status(models_config=config, environ=environ)
    calls = last_calls(session)

    items: list[ModelReadiness] = []
    for settings in config.models:
        ready, reason = readiness_of(settings, providers=providers, model_feature=model_feature)
        provider = providers.get(settings.provider) or {}
        items.append(
            ModelReadiness(
                provider=settings.provider,
                model=settings.model,
                display_name=settings.display_name,
                enabled=settings.enabled,
                provider_status=str(provider.get("status") or "unknown"),
                provider_message=str(provider.get("message") or ""),
                pricing_status=_pricing_label(settings),
                price_checked_at=settings.price_checked_at or "",
                input_price_per_1k_cny=settings.input_price_per_1k_cny,
                output_price_per_1k_cny=settings.output_price_per_1k_cny,
                ready=ready,
                blocked_reason="" if ready else reason,
                next_step=REASON_TEXT.get(reason, ""),
                last_call=calls.get((settings.provider, settings.model)),
            )
        )
    # 能用的排前面，其余按服务商/模型名稳定排序
    items.sort(key=lambda item: (not item.ready, item.provider, item.model))
    return items
