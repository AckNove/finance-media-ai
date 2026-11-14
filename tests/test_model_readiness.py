"""每个模型现在能不能用：结论、原因、下一步。

这条链上"不能用"有五个不同来源，页面以前只显示其中一个（服务商状态），
于是配错价目时人会以为系统坏了。这里把判断顺序和话术钉住。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.core.config_schema import ModelSettings
from backend.app.models.metering import ModelCall
from backend.app.services import model_readiness as readiness


def _settings(**overrides: object) -> ModelSettings:
    base = dict(
        provider="deepseek",
        model="deepseek-flash",
        display_name="Flash",
        enabled=True,
        input_price_per_1k_cny=0.00213,
        output_price_per_1k_cny=0.00852,
        price_checked_at="2026-11-14",
    )
    base.update(overrides)
    return ModelSettings(**base)  # type: ignore[arg-type]


def _providers(status: str = "ready", *, enabled: bool = True) -> dict[str, dict[str, object]]:
    return {"deepseek": {"enabled": enabled, "status": status, "message": "缺密钥" if status != "ready" else ""}}


def test_a_model_with_key_and_price_is_ready() -> None:
    ready, reason = readiness.readiness_of(_settings(), providers=_providers())
    assert ready is True
    assert reason == "ready"
    assert readiness.REASON_TEXT[reason] == ""


@pytest.mark.parametrize(
    ("kwargs", "providers", "feature", "expected"),
    [
        ({"enabled": False}, _providers(), True, "model_disabled"),
        ({}, _providers(), False, "feature_disabled"),
        ({}, _providers(enabled=False), True, "provider_disabled"),
        ({}, _providers("missing_api_key"), True, "missing_api_key"),
        ({}, _providers("missing_base_url"), True, "missing_base_url"),
        ({"output_price_per_1k_cny": None}, _providers(), True, "pricing_missing"),
        ({"input_price_per_1k_cny": None}, _providers(), True, "pricing_missing"),
    ],
)
def test_every_blocking_reason_is_named(
    kwargs: dict[str, object],
    providers: dict[str, dict[str, object]],
    feature: bool,
    expected: str,
) -> None:
    ready, reason = readiness.readiness_of(
        _settings(**kwargs), providers=providers, model_feature=feature
    )
    assert ready is False
    assert reason == expected
    # 每个原因码都得有一句能照做的下一步，不能只有代号
    assert readiness.REASON_TEXT[reason]


def test_feature_switch_beats_everything_else() -> None:
    """总开关关掉时，别再去数缺哪个密钥——先说总开关。"""
    ready, reason = readiness.readiness_of(
        _settings(enabled=False), providers=_providers("missing_api_key"), model_feature=False
    )
    assert (ready, reason) == (False, "feature_disabled")


def _call(
    session: Session,
    *,
    status: str,
    error: str = "",
    when: int = 0,
    provider: str = "deepseek",
    model: str = "deepseek-flash",
) -> ModelCall:
    stamp = datetime(2026, 5, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None)
    stamp = stamp.replace(minute=when)
    row = ModelCall(
        provider=provider,
        model=model,
        purpose="readiness-test",
        prompt_tokens=10,
        completion_tokens=10,
        total_tokens=20,
        cost_estimate=Decimal("0.0001"),
        status=status,
        error_message=error,
        created_at=stamp,
    )
    session.add(row)
    session.commit()
    return row


def test_last_call_keeps_the_newest_row_per_model(db_session: Session) -> None:
    # 用独立的 provider/model 名，避免和别的用例插进同一个库的调用混在一起
    # （测试库是 session 级共享的，真实模型名会被别的用例占用）
    _call(db_session, status="ok", when=0, provider="probe-provider", model="probe-model")
    _call(
        db_session,
        status="error",
        error="401",
        when=5,
        provider="probe-provider",
        model="probe-model",
    )
    calls = readiness.last_calls(db_session)
    latest = calls[("probe-provider", "probe-model")]
    assert latest.status == "error"
    assert latest.error_message == "401"
    assert latest.ok is False


def test_collect_reports_ready_and_blocked_side_by_side(db_session: Session) -> None:
    config = SimpleNamespace(
        providers={
            "deepseek": SimpleNamespace(
                display_name="DeepSeek",
                enabled=True,
                api_key_env="DEEPSEEK_API_KEY",
                base_url_env="DEEPSEEK_BASE_URL",
            )
        },
        models=[
            _settings(),
            _settings(model="deepseek-v4-pro", output_price_per_1k_cny=None),
        ],
    )
    items = readiness.collect(
        db_session,
        models_config=config,
        environ={"DEEPSEEK_API_KEY": "test-key-not-a-real-secret"},
    )
    by_model = {item.model: item for item in items}
    assert by_model["deepseek-flash"].ready is True
    assert by_model["deepseek-v4-pro"].ready is False
    assert by_model["deepseek-v4-pro"].blocked_reason == "pricing_missing"
    # 能用的排前面
    assert items[0].model == "deepseek-flash"


def test_actionable_is_false_when_the_last_call_failed() -> None:
    item = readiness.ModelReadiness(
        provider="deepseek",
        model="deepseek-flash",
        ready=True,
        last_call=readiness.LastCall(status="error", error_message="401"),
    )
    assert item.actionable is False
    assert item.as_dict()["last_call"]["status"] == "error"


def test_models_endpoint_is_read_only_and_names_why(client: TestClient) -> None:
    response = client.get("/api/system/models")
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"model_feature", "items", "ready_count", "note"}
    assert isinstance(data["items"], list)
    assert data["ready_count"] == sum(1 for item in data["items"] if item["ready"])
    for item in data["items"]:
        assert set(item) >= {
            "provider",
            "model",
            "ready",
            "actionable",
            "blocked_reason",
            "next_step",
            "has_price",
            "last_call",
        }
        if item["ready"]:
            assert item["blocked_reason"] == ""
        else:
            assert item["blocked_reason"] in readiness.REASON_TEXT
            assert item["next_step"]
