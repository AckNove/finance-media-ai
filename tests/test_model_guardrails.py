"""V5.0 模型网关的护栏：本地限流、密钥不外泄、错误可追。

这一组和 ``test_model_gateway.py`` 的分工：那边测"决策顺序对不对"，这里测
"出事的时候会不会把不该露的东西露出去、会不会把 429 当正常继续砸请求"。

全部零网络、零费用：HTTP 用 ``httpx.MockTransport`` 注入。
"""

from __future__ import annotations

import logging

import httpx
import pytest

from backend.app.capabilities.model import gateway as gateway_module
from backend.app.capabilities.model.gateway import (
    BudgetContext,
    LocalRateLimitError,
    ModelGateway,
    ModelRequest,
    _LocalLimiter,
)
from backend.app.core.config_schema import FeaturesSettings, ModelsConfig

FAKE_KEY = "sk-test-do-not-leak-9f3a2c"


def build_config(
    *,
    requests_per_minute: int | None = None,
    max_concurrency: int | None = None,
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
                    "enabled": True,
                }
            },
            "models": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "input_price_per_1k_cny": 0.00213,
                    "output_price_per_1k_cny": 0.00852,
                    "enabled": True,
                    "requests_per_minute": requests_per_minute,
                    "max_concurrency": max_concurrency,
                }
            ],
        }
    )


class Recorder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "银行靠息差和中间业务"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            },
        )


def make_gateway(recorder: Recorder, config: ModelsConfig, *, clock=None) -> ModelGateway:
    return ModelGateway(
        models_config=config,
        transport=httpx.MockTransport(recorder),
        environ={"DEEPSEEK_API_KEY": FAKE_KEY},
        retries=0,
        clock=clock,
    )


def request(*, content: str = "用一句话解释商业银行怎么赚钱") -> ModelRequest:
    return ModelRequest(
        provider="deepseek",
        model="deepseek-flash",
        messages=[{"role": "user", "content": content}],
        cacheable=False,
    )


def model_rows() -> list:
    from backend.app.database.session import get_session_factory
    from backend.app.models.metering import ModelCall

    session = get_session_factory()()
    try:
        return session.query(ModelCall).order_by(ModelCall.id).all()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_model_calls(test_database):  # noqa: ANN001 - pytest fixture 注入
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


# --- 本地限流 ---------------------------------------------------------------


def test_limiter_counts_requests_per_minute_and_says_when_to_retry():
    now = [1000.0]
    limiter = _LocalLimiter(clock=lambda: now[0])
    limiter.acquire("deepseek/x", requests_per_minute=2, max_concurrency=None)
    limiter.release("deepseek/x")
    now[0] += 1.0
    limiter.acquire("deepseek/x", requests_per_minute=2, max_concurrency=None)
    limiter.release("deepseek/x")
    now[0] += 1.0
    with pytest.raises(LocalRateLimitError) as raised:
        limiter.acquire("deepseek/x", requests_per_minute=2, max_concurrency=None)
    assert raised.value.reason == "local_rate_limited"
    assert raised.value.retry_after_seconds > 0
    # 时间窗过去之后应该重新放行，而不是永久拉黑
    now[0] += 61.0
    limiter.acquire("deepseek/x", requests_per_minute=2, max_concurrency=None)
    limiter.release("deepseek/x")


def test_limiter_enforces_concurrency_and_releases_on_release():
    limiter = _LocalLimiter(clock=lambda: 0.0)
    limiter.acquire("deepseek/x", requests_per_minute=None, max_concurrency=1)
    with pytest.raises(LocalRateLimitError) as raised:
        limiter.acquire("deepseek/x", requests_per_minute=None, max_concurrency=1)
    assert raised.value.reason == "local_concurrency_exceeded"
    limiter.release("deepseek/x")
    limiter.acquire("deepseek/x", requests_per_minute=None, max_concurrency=1)
    limiter.release("deepseek/x")


def test_gateway_refuses_when_local_limit_reached_and_logs_the_attempt(
    model_enabled, monkeypatch: pytest.MonkeyPatch
):
    """被自己拦下来的调用：不发 HTTP，但要留下审计行（否则成本对不上）。"""
    recorder = Recorder()
    config = build_config(requests_per_minute=1)
    gateway = make_gateway(recorder, config)

    first = gateway.complete(_caller(), request(content="第一个问题"))
    assert first.ok is True
    assert recorder.calls == 1

    second = gateway.complete(_caller(), request(content="第二个问题"))
    assert second.status == "rate_limited"
    assert second.error_code == "local_rate_limited"
    assert recorder.calls == 1, "被本地限流之后不应该再发请求"

    rows = model_rows()
    assert len(rows) == 2
    assert rows[-1].status.value == "error"
    assert "每分钟最多" in (rows[-1].error_message or "")


def test_gateway_does_not_count_cache_hits_against_the_rate_limit(
    model_enabled,
):
    """缓存命中没有出网，不该吃掉限流额度。"""
    recorder = Recorder()
    config = build_config(requests_per_minute=1)
    gateway = make_gateway(recorder, config)

    payload = ModelRequest(
        provider="deepseek",
        model="deepseek-flash",
        messages=[{"role": "user", "content": "同一个问题"}],
        cacheable=True,
    )
    assert gateway.complete(_caller(), payload).ok is True
    again = gateway.complete(_caller(), payload)
    assert again.ok is True
    assert again.raw_usage.get("cached") is True
    assert recorder.calls == 1


# --- 密钥不外泄 -------------------------------------------------------------


def test_api_key_never_lands_in_audit_rows(model_enabled):
    recorder = Recorder()
    gateway = make_gateway(recorder, build_config())

    reply = gateway.complete(_caller(), request())
    assert reply.ok is True

    rows = model_rows()
    assert len(rows) == 1
    row = rows[0]
    for value in (
        row.request_hash,
        row.prompt_preview,
        row.error_message,
        row.provider,
        row.model,
        row.purpose,
    ):
        assert FAKE_KEY not in str(value or "")


def test_api_key_never_lands_in_error_messages(model_enabled):
    """4xx/5xx 的错误体可能包含诡异的回显，网关只保留 300 字并不得拼接密钥。"""

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    gateway = ModelGateway(
        models_config=build_config(),
        transport=httpx.MockTransport(unauthorized),
        environ={"DEEPSEEK_API_KEY": FAKE_KEY},
        retries=0,
    )
    reply = gateway.complete(_caller(), request())
    assert reply.ok is False
    assert reply.error_code == "auth_failed"
    assert FAKE_KEY not in (reply.error_message or "")
    rows = model_rows()
    assert all(FAKE_KEY not in str(row.error_message or "") for row in rows)


def test_api_key_never_lands_in_logs(model_enabled, caplog: pytest.LogCaptureFixture):
    """日志里出现过密钥等于把它写进了磁盘——这条必须守住。"""
    recorder = Recorder()
    gateway = make_gateway(recorder, build_config())
    with caplog.at_level(logging.DEBUG, logger="backend.app.capabilities.model"):
        gateway.complete(_caller(), request())
        gateway.complete(_caller(), request(content="第二个问题"))
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_KEY not in joined


def test_provider_description_masks_the_key():
    from backend.app.capabilities.model.providers import resolve_provider

    provider = resolve_provider(
        "deepseek", models_config=build_config(), environ={"DEEPSEEK_API_KEY": FAKE_KEY}
    )
    described = provider.describe()
    assert FAKE_KEY not in str(described)
    assert described["api_key"].startswith("sk-")
    assert "*" in described["api_key"]


def _caller():
    from backend.app.core.caller import CallerContext

    return CallerContext.for_executor(
        agent="financial_analyst", task_id="t-test", attempt_token="token-1"
    )


def test_server_error_echoing_the_key_tail_is_redacted(model_enabled):
    """实测 DeepSeek 的 401 会回显 ``****0000``（密钥尾 4 位），不能原样入库。"""

    def echoing(request: httpx.Request) -> httpx.Response:
        tail = FAKE_KEY[-4:]
        return httpx.Response(
            429,
            text=f'{{"error":{{"message":"rate limited for api key ****{tail}"}}}}',
        )

    gateway = ModelGateway(
        models_config=build_config(),
        transport=httpx.MockTransport(echoing),
        environ={"DEEPSEEK_API_KEY": FAKE_KEY},
        retries=0,
    )
    reply = gateway.complete(_caller(), request())
    assert reply.ok is False
    assert reply.status == "rate_limited"
    assert FAKE_KEY[-4:] not in (reply.error_message or "")
    rows = model_rows()
    assert all(FAKE_KEY[-4:] not in str(row.error_message or "") for row in rows)


def test_redact_helper_handles_absent_and_short_keys():
    from backend.app.capabilities.model.gateway import _redact

    assert _redact("", "sk-whatever") == ""
    assert _redact("all good", None) == "all good"
    assert _redact("all good", "sk-whatever") == "all good"
    assert _redact("key sk-secret-value leaked", "sk-secret-value").startswith("[服务端响应含密钥")


def test_budget_context_of_zero_does_not_block(model_enabled):
    """BudgetContext 的 0 是"没花"，不是"已超"——别把默认值当成拒绝理由。"""
    recorder = Recorder()
    gateway = make_gateway(recorder, build_config())
    reply = gateway.complete(_caller(), request(), budget=BudgetContext())
    assert reply.ok is True
