"""V5.0 M2：模型网关的行为契约（真实 HTTP 语义，零网络、零费用）。

这些用例全部用 ``httpx.MockTransport`` 注入假的 HTTP 响应，所以**不发真实请求、
不产生费用**；被验证的是网关自己的决策逻辑：
功能开关 → provider 解析 → 价目 + 预算 → 缓存 → HTTP → 复算成本 → 写审计行。

关键诚实性断言：
- 没有密钥 / 价目未知 / 超预算时**必须**返回失败状态，绝不返回 ok；
- 401 不重试、5xx 才重试、超时映射成 ``timeout``；
- 每次真实调用都写一行 ``model_calls``，缓存命中写 ``cache_hit=True`` 且成本为 0。
"""

from __future__ import annotations

import httpx
import pytest

from backend.app.capabilities.model import gateway as gateway_module
from backend.app.capabilities.model.gateway import (
    BudgetContext,
    ModelGateway,
    ModelRequest,
)
from backend.app.capabilities.model.tokens import (
    cost_cny,
    estimate_prompt_tokens,
    estimate_tokens,
)
from backend.app.capabilities.tools import ToolRegistry
from backend.app.core.caller import CallerContext
from backend.app.core.config_schema import FeaturesSettings, ModelsConfig

FETCHER = {"DEEPSEEK_API_KEY": "sk-test-not-real"}


def build_config(
    *,
    price: tuple[float | None, float | None] = (0.001, 0.002),
    model_enabled: bool = True,
    provider_enabled: bool = True,
) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "version": 1,
            "updated_at": "2026-11-12",
            "cost": {
                "currency": "CNY",
                "pricing_policy": "reject_unknown",
                "approved_exceptions": [],
                "cache_ttl_hours": 24,
            },
            "providers": {
                "deepseek": {
                    "display_name": "DeepSeek",
                    "base_url_env": "DEEPSEEK_BASE_URL",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "enabled": provider_enabled,
                }
            },
            "models": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "input_price_per_1k_cny": price[0],
                    "output_price_per_1k_cny": price[1],
                    "enabled": model_enabled,
                }
            ],
        }
    )


class Recorder:
    """记录每次 HTTP 请求，并给出预设响应。"""

    def __init__(self, *responses: httpx.Response, exc: Exception | None = None) -> None:
        self.responses = list(responses)
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        self.calls.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content.decode("utf-8")),
            }
        )
        if self.exc is not None:
            raise self.exc
        if not self.responses:
            return httpx.Response(500, text="no response queued")
        return self.responses.pop(0)


def ok_response(text: str = "你好", *, prompt: int = 10, completion: int = 5) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


def make_gateway(recorder: Recorder, config: ModelsConfig | None = None, **kwargs) -> ModelGateway:
    kwargs.setdefault("retries", 0)
    return ModelGateway(
        models_config=config or build_config(),
        transport=httpx.MockTransport(recorder),
        environ=FETCHER,
        **kwargs,
    )


def simple_request(**overrides) -> ModelRequest:
    payload = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "用一句话解释国债收益率"}],
    }
    payload.update(overrides)
    return ModelRequest(**payload)


@pytest.fixture(autouse=True)
def clean_model_calls(test_database):  # noqa: ANN001 - pytest fixture 注入
    """每个用例前清空 model_calls，避免当日累计金额互相影响。"""
    from backend.app.database.session import get_session_factory
    from backend.app.models.metering import ModelCall

    session = get_session_factory()()
    try:
        session.query(ModelCall).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def model_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gateway_module,
        "get_features_settings",
        lambda: FeaturesSettings(model=True, search=False, execution=False),
    )


def model_rows() -> list:
    from backend.app.database.session import get_session_factory
    from backend.app.models.metering import ModelCall

    session = get_session_factory()()
    try:
        return session.query(ModelCall).order_by(ModelCall.id).all()
    finally:
        session.close()


# --- token 估算与成本 --------------------------------------------------------


def test_cjk_costs_more_tokens_than_ascii_for_same_length():
    # 估算是整数且偏保守：中文 10 字 ≈ 7 token，同长度 ASCII ≈ 3 token
    assert estimate_tokens("国" * 10) == 7
    assert estimate_tokens("a" * 10) == 3
    assert estimate_tokens("") == 0
    messages = [{"role": "user", "content": "国债收益率"}]
    assert estimate_prompt_tokens(messages) > estimate_tokens("国债收益率")


def test_cost_uses_separate_input_and_output_prices():
    value = cost_cny(
        prompt_tokens=1000,
        completion_tokens=1000,
        input_price_per_1k_cny=0.001,
        output_price_per_1k_cny=0.002,
    )
    assert value == pytest.approx(0.003)


# --- 前置闸门（一个都不能放行） ----------------------------------------------


def test_feature_flag_off_blocks_before_any_provider_work():
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder)  # features.model 默认 false
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "disabled"
    assert reply.error_code == "feature_disabled"
    assert recorder.calls == []
    assert model_rows() == []


def test_missing_api_key_is_not_configured(model_enabled):
    recorder = Recorder(ok_response())
    gateway = ModelGateway(
        models_config=build_config(),
        transport=httpx.MockTransport(recorder),
        environ={},
        retries=0,
    )
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "not_configured"
    assert reply.error_code == "missing_api_key"
    assert recorder.calls == []


def test_disabled_provider_is_not_configured(model_enabled):
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder, build_config(provider_enabled=False))
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "not_configured"
    assert reply.error_code == "provider_disabled"


def test_unknown_model_price_is_rejected_not_assumed_free(model_enabled):
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder, build_config(price=(None, None)))
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "budget_blocked"
    assert reply.error_code == "pricing_missing"
    assert recorder.calls == [], "价目未知时绝不允许发请求"


def test_disabled_model_is_rejected(model_enabled):
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder, build_config(model_enabled=False))
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "budget_blocked"
    assert reply.error_code == "model_disabled"


def test_task_call_budget_blocks_before_request(model_enabled):
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder)
    budget = BudgetContext(calls_spent=3, max_calls=3, max_cost_cny=10.0, daily_spent_cny=0.0)
    reply = gateway.complete(CallerContext.human(), simple_request(), budget=budget)
    assert reply.status == "budget_blocked"
    assert reply.error_code == "task_calls_exceeded"
    assert recorder.calls == []


def test_daily_cap_blocks_when_already_spent(model_enabled):
    recorder = Recorder(ok_response())
    gateway = make_gateway(recorder)
    budget = BudgetContext(calls_spent=0, max_calls=10, max_cost_cny=10.0, daily_spent_cny=1e6)
    reply = gateway.complete(CallerContext.human(), simple_request(), budget=budget)
    assert reply.status == "budget_blocked"
    assert reply.error_code == "daily_cap_exceeded"


# --- 真实调用路径 ------------------------------------------------------------


def test_successful_call_returns_text_and_writes_audit_row(model_enabled):
    recorder = Recorder(ok_response("国债收益率就是借钱给政府的利息", prompt=100, completion=50))
    gateway = make_gateway(recorder)
    reply = gateway.complete(CallerContext.human(), simple_request(purpose="research"))
    assert reply.ok is True
    assert "国债收益率" in reply.text
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["auth"] == "Bearer sk-test-not-real"
    assert call["body"]["model"] == "deepseek-chat"

    rows = model_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.provider == "deepseek"
    assert row.purpose == "research"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 50
    assert row.total_tokens == 150
    assert row.pricing_unknown is False
    assert float(row.cost_estimate) == pytest.approx(100 / 1000 * 0.001 + 50 / 1000 * 0.002)
    assert row.status.value == "ok"
    assert reply.record is not None and reply.record.id == row.id


def test_auth_failure_is_reported_and_not_retried(model_enabled):
    recorder = Recorder(httpx.Response(401, text="bad key"), ok_response())
    gateway = make_gateway(recorder, retries=2)
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "error"
    assert reply.error_code == "auth_failed"
    assert len(recorder.calls) == 1, "401 说明密钥错了，重试没有意义"


def test_server_error_is_retried_then_reported(model_enabled):
    recorder = Recorder(
        httpx.Response(503, text="busy"),
        httpx.Response(503, text="busy"),
        httpx.Response(503, text="busy"),
    )
    gateway = make_gateway(recorder, retries=2, backoff_seconds=0.0)
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "error"
    assert reply.error_code == "server_error"
    assert len(recorder.calls) == 3


def test_timeout_maps_to_timeout_status(model_enabled):
    recorder = Recorder(exc=httpx.ConnectTimeout("timed out"))
    gateway = make_gateway(recorder, retries=1, backoff_seconds=0.0)
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "timeout"
    assert reply.error_code == "timeout"


def test_bad_json_is_reported_as_bad_response(model_enabled):
    recorder = Recorder(httpx.Response(200, text="<html>oops</html>"))
    gateway = make_gateway(recorder)
    reply = gateway.complete(CallerContext.human(), simple_request())
    assert reply.status == "error"
    assert reply.error_code == "bad_response"


def test_cache_hit_skips_http_and_costs_nothing(model_enabled):
    recorder = Recorder(ok_response("第一次"), ok_response("第二次"))
    gateway = make_gateway(recorder)
    first = gateway.complete(CallerContext.human(), simple_request())
    second = gateway.complete(CallerContext.human(), simple_request())
    assert first.text == "第一次"
    assert second.text == "第一次", "同 request_hash 必须命中缓存"
    assert len(recorder.calls) == 1
    rows = model_rows()
    assert len(rows) == 2
    assert rows[0].cache_hit is False
    assert rows[1].cache_hit is True
    assert float(rows[1].cost_estimate or 0) == 0.0


def test_non_cacheable_request_always_calls_out(model_enabled):
    recorder = Recorder(ok_response("a"), ok_response("b"))
    gateway = make_gateway(recorder)
    gateway.complete(CallerContext.human(), simple_request(cacheable=False))
    gateway.complete(CallerContext.human(), simple_request(cacheable=False))
    assert len(recorder.calls) == 2


def test_json_response_format_is_forwarded(model_enabled):
    recorder = Recorder(ok_response('{"answer": 1}'))
    gateway = make_gateway(recorder)
    reply = gateway.complete(
        CallerContext.human(), simple_request(response_format="json_object", cacheable=False)
    )
    assert reply.ok
    assert recorder.calls[0]["body"]["response_format"] == {"type": "json_object"}


# --- 工具适配器 --------------------------------------------------------------


def agent_caller() -> CallerContext:
    return CallerContext.for_executor(
        agent="financial_researcher", task_id="task-1", attempt_token="tok-1"
    )


def test_llm_complete_tool_reports_not_configured_without_key(model_enabled):
    from backend.app.capabilities.model.tool import register_model_adapters

    registry = ToolRegistry()
    register_model_adapters(
        registry,
        gateway=ModelGateway(
            models_config=build_config(),
            transport=httpx.MockTransport(Recorder(ok_response())),
            environ={},
            retries=0,
        ),
    )
    result = registry.dispatch(agent_caller(), "llm_complete", prompt="你好")
    assert result.status == "not_configured"
    assert result.error_code == "missing_api_key"
    assert result.data is None


def test_llm_complete_tool_returns_envelope_with_cost(model_enabled):
    from backend.app.capabilities.model.tool import register_model_adapters

    recorder = Recorder(ok_response("答案", prompt=200, completion=100))
    registry = ToolRegistry()
    register_model_adapters(registry, gateway=make_gateway(recorder))
    result = registry.dispatch(agent_caller(), "llm_complete", prompt="解释 M2", system="你是研究员")
    assert result.status == "ok"
    assert result.data["text"] == "答案"
    assert result.billable is True
    assert result.cost_cny == pytest.approx(200 / 1000 * 0.001 + 100 / 1000 * 0.002)
    assert result.model_call_id is not None
    assert recorder.calls[0]["body"]["messages"] == [
        {"role": "system", "content": "你是研究员"},
        {"role": "user", "content": "解释 M2"},
    ]


def test_llm_complete_tool_rejects_empty_prompt(model_enabled):
    from backend.app.capabilities.model.tool import register_model_adapters

    registry = ToolRegistry()
    register_model_adapters(registry, gateway=make_gateway(Recorder(ok_response())))
    result = registry.dispatch(agent_caller(), "llm_complete")
    assert result.status == "error"
    assert result.error_code == "bad_arguments"
