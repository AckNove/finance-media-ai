"""统一模型网关（V5.0 M2）。

一次 ``complete()`` 的固定顺序（每一步失败都有稳定错误码，绝不静默降级）：

1. 功能开关 ``features.model``；
2. provider 解析（密钥只来自环境变量，缺失即 ``not_configured``）；
3. 模型登记与启用状态、价目解析（价目未知按 Q8=B 直接拒绝）；
4. 预算三道闸门（单次 / 任务 / 当日），判定里**包含预留**；
5. 请求缓存（同 request_hash 且在 TTL 内 → 命中不重复计费）；
6. 真实 HTTP 调用（OpenAI 兼容 ``/chat/completions``，只对可重试错误重试）；
7. 用响应里的 ``usage`` 复算成本，写 ``model_calls`` 审计行。

**没有 Fake 分支**：任何"跑不通"都以 ``not_configured`` / ``budget_blocked`` /
``error`` 返回，调用方必须把任务标 BLOCKED。这是 V5.0 §十 的硬要求。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Sequence

import httpx

from backend.app.capabilities.budget import evaluate_model_call
from backend.app.capabilities.model.providers import (
    ProviderError,
    ResolvedProvider,
    resolve_provider,
)
from backend.app.capabilities.model.tokens import cost_cny, estimate_prompt_tokens
from backend.app.core.caller import CallerContext
from backend.app.core.config import (
    get_cost_settings,
    get_features_settings,
    get_models_config,
)
from backend.app.core.config_schema import ModelsConfig

logger = logging.getLogger(__name__)

GatewayStatus = Literal[
    "ok",
    "not_configured",
    "disabled",
    "budget_blocked",
    "rate_limited",
    "timeout",
    "error",
]

#: 可重试的 HTTP 状态码（限流与服务端抖动）；4xx 的认证/参数错误一律不重试
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """一次模型调用的完整描述。"""

    provider: str
    model: str
    messages: Sequence[Mapping[str, Any]]
    purpose: str = "general"
    temperature: float = 0.3
    max_tokens: int = 1024
    top_p: float | None = None
    response_format: Literal["text", "json_object"] = "text"
    stop: Sequence[str] | None = None
    #: 关掉缓存时同参数每次都会真发请求（用于需要多样性的写作类调用）
    cacheable: bool = True

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "messages": [dict(item) for item in self.messages],
            "purpose": self.purpose,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "response_format": self.response_format,
            "stop": list(self.stop) if self.stop else None,
        }

    @property
    def request_hash(self) -> str:
        blob = json.dumps(self.canonical_payload(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def prompt_preview(self) -> str:
        from backend.app.capabilities.model.tokens import message_text

        joined = "\n".join(message_text(item) for item in self.messages)
        return joined[:2000]


@dataclass(frozen=True, slots=True)
class CallRecord:
    """落库后的审计摘要（id 为 NULL 表示这次调用没能记账）。"""

    id: int | None
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float | None
    cache_hit: bool
    latency_ms: int
    status: str


@dataclass(frozen=True, slots=True)
class ModelReply:
    """网关返回值。``text`` 只在 status=ok 时可信。"""

    status: GatewayStatus
    text: str = ""
    error_code: str = ""
    error_message: str = ""
    record: CallRecord | None = None
    raw_usage: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class BudgetContext:
    """一次调用所属任务的预算上下文（缺省时按单次调用处理）。"""

    task_id: int | None = None
    subtask_id: int | None = None
    calls_spent: int = 0
    cost_spent_cny: float = 0.0
    calls_reserved: int = 0
    cost_reserved_cny: float = 0.0
    max_calls: int | None = None
    max_cost_cny: float | None = None
    daily_spent_cny: float | None = None


class _ReplyCache:
    """进程内 TTL 缓存：键是 request_hash，命中不重复计费。"""

    def __init__(self, ttl_hours: float) -> None:
        self._ttl = ttl_hours * 3600
        self._data: dict[str, tuple[float, str, dict[str, Any]]] = {}

    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        if self._ttl <= 0:
            return None
        item = self._data.get(key)
        if item is None:
            return None
        stored_at, text, usage = item
        if time.time() - stored_at > self._ttl:
            self._data.pop(key, None)
            return None
        return text, usage

    def put(self, key: str, text: str, usage: dict[str, Any]) -> None:
        if self._ttl <= 0:
            return
        if len(self._data) > 512:  # 简单上限，避免长期运行内存膨胀
            self._data.clear()
        self._data[key] = (time.time(), text, usage)

    def clear(self) -> None:
        self._data.clear()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _redact(text: str, api_key: str | None) -> str:
    """把服务端错误体里可能回显的密钥片段抹掉。

    实测 DeepSeek 的鉴权失败响应会带 ``Your api key: ****0000``——尾部 4 位。
    这种片段写进错误日志就等于把密钥的一部分留在了数据里，所以这里按
    "出现完整密钥" 与 "出现尾部 4 位" 两种形态一起清掉。
    """
    if not text:
        return text
    if api_key:
        if api_key in text:
            return "[服务端响应含密钥，已隐去]"
        tail = api_key[-4:]
        if len(api_key) >= 8 and tail in text:
            return text.replace(tail, "****")
    return text


class LocalRateLimitError(RuntimeError):
    """本地限流拦下的调用：没发出去，可以稍后重试，但不是"服务端拒绝"。"""

    def __init__(self, reason: str, message: str, *, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retry_after_seconds = retry_after_seconds

    def describe(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "message": self.message,
            "retry_after_seconds": round(self.retry_after_seconds, 3),
        }


class _LocalLimiter:
    """进程内节流：每分钟次数 + 同时在途数量。

    官方有并发上限（DeepSeek 是 2500 / 500），但那是账号级的天花板，不是"我该多快"。
    这里按模型配速，是为了在拿到 429 之前自己先减速——429 之后重试会放大延迟，
    而且一次失败调用同样要写审计行，没必要制造这种噪声。

    刻意做得保守：只统计本进程的调用，不做跨进程协调。
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._recent: dict[str, deque[float]] = {}
        self._inflight: dict[str, int] = {}

    def acquire(
        self, key: str, *, requests_per_minute: int | None, max_concurrency: int | None
    ) -> None:
        now = self._clock()
        if max_concurrency is not None and self._inflight.get(key, 0) >= max_concurrency:
            raise LocalRateLimitError(
                "local_concurrency_exceeded",
                f"{key} 同时进行的请求已达上限 {max_concurrency}",
                retry_after_seconds=1.0,
            )
        if requests_per_minute is not None and requests_per_minute > 0:
            window = 60.0 / requests_per_minute
            marks = self._recent.setdefault(key, deque())
            while marks and now - marks[0] >= 60.0:
                marks.popleft()
            if len(marks) >= requests_per_minute:
                retry_after = max(0.0, 60.0 - (now - marks[0]))
                raise LocalRateLimitError(
                    "local_rate_limited",
                    f"{key} 每分钟最多 {requests_per_minute} 次，请 {retry_after:.1f} 秒后再试",
                    retry_after_seconds=retry_after,
                )
            marks.append(now)
            del window  # 间隔口径写在这里是为了让读者看到 60/rpm 的关系
        self._inflight[key] = self._inflight.get(key, 0) + 1

    def release(self, key: str) -> None:
        current = self._inflight.get(key, 0)
        if current <= 1:
            self._inflight.pop(key, None)
        else:
            self._inflight[key] = current - 1

    def reset(self) -> None:
        self._recent.clear()
        self._inflight.clear()


class ModelGateway:
    """真实模型调用的唯一入口。"""
    def __init__(
        self,
        *,
        models_config: ModelsConfig | None = None,
        transport: httpx.BaseTransport | None = None,
        session_factory: Callable[[], Any] | None = None,
        environ: Mapping[str, str] | None = None,
        retries: int = 2,
        backoff_seconds: float = 1.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = models_config or get_models_config()
        self._transport = transport
        self._session_factory = session_factory
        self._environ = environ
        self._retries = max(0, retries)
        self._backoff = backoff_seconds
        self._cache = _ReplyCache(self._config.cost.cache_ttl_hours)
        self._limiter = _LocalLimiter(clock=clock)

    # -- 只读辅助 ---------------------------------------------------------

    @property
    def models_config(self) -> ModelsConfig:
        return self._config

    def ready_providers(self) -> list[str]:
        """本网关**这套配置与密钥**下真正可用的 provider。

        为什么不直接问全局 ``provider_status()``：那个函数读的是进程级 ``os.environ``，
        而网关可能被注入了别的 environ（测试、多账号、CI）。两边不一致时，
        "以为有模型、实际没有"会让调用方做错决定。
        """
        from backend.app.capabilities.model.providers import provider_status

        status = provider_status(
            models_config=self._config,
            environ=self._environ if self._environ is not None else None,
        )
        return [
            name
            for name, info in status.items()
            if isinstance(info, dict) and info.get("status") == "ready"
        ]

    def _session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()
        from backend.app.database.session import get_session_factory

        return get_session_factory()()

    def daily_spent_cny(self, *, now: datetime | None = None) -> float:
        """当天已花的模型费用（来自 model_calls，价目未知的行不计入）。"""
        from sqlalchemy import func, select

        from backend.app.models.metering import ModelCall

        moment = now or _utcnow()
        start = _day_start(moment)
        session = self._session()
        try:
            stmt = select(func.coalesce(func.sum(ModelCall.cost_estimate), 0)).where(
                ModelCall.created_at >= start,
                ModelCall.pricing_unknown.is_(False),
            )
            value = session.execute(stmt).scalar()
            return float(value or 0.0)
        finally:
            session.close()

    # -- 主流程 -----------------------------------------------------------

    def complete(
        self,
        caller: CallerContext,
        request: ModelRequest,
        *,
        budget: BudgetContext | None = None,
    ) -> ModelReply:
        started = time.perf_counter()
        context = budget or BudgetContext()

        # 1) 功能开关
        if not get_features_settings().model:
            return self._fail(
                "disabled",
                "feature_disabled",
                "features.model=false：模型能力未开启（config/settings.yaml）",
            )

        # 2) provider
        try:
            provider = resolve_provider(
                request.provider, models_config=self._config, environ=self._environ
            )
        except ProviderError as error:
            return self._fail("not_configured", error.reason, error.message)

        # 3) 价目 + 4) 预算闸门
        entry = self._config.find_model(request.provider, request.model)
        est_prompt = estimate_prompt_tokens(request.messages)
        est_cost = 0.0
        if entry is not None and entry.input_price_per_1k_cny is not None:
            est_cost = cost_cny(
                prompt_tokens=est_prompt,
                completion_tokens=request.max_tokens,
                input_price_per_1k_cny=entry.input_price_per_1k_cny,
                output_price_per_1k_cny=entry.output_price_per_1k_cny or 0.0,
            )
        settings = get_cost_settings()
        daily = (
            context.daily_spent_cny
            if context.daily_spent_cny is not None
            else self._safe_daily_spent()
        )
        price, decision = evaluate_model_call(
            provider=request.provider,
            model=request.model,
            est_cost_cny=est_cost,
            calls_spent=context.calls_spent,
            cost_spent_cny=context.cost_spent_cny,
            calls_reserved=context.calls_reserved,
            cost_reserved_cny=context.cost_reserved_cny,
            max_calls=context.max_calls or settings.default_task_calls,
            max_cost_cny=context.max_cost_cny or settings.default_task_cost_cny,
            daily_spent_cny=daily,
            models_config=self._config,
        )
        if not decision.allowed:
            self._log(
                caller,
                request,
                context,
                status="budget_blocked",
                prompt_tokens=est_prompt,
                completion_tokens=0,
                cost=None,
                pricing_unknown=price.status != "known",
                cache_hit=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=decision.message,
            )
            return self._fail("budget_blocked", decision.reason, decision.message)

        # 5) 缓存
        request_hash = request.request_hash
        if request.cacheable:
            hit = self._cache.get(request_hash)
            if hit is not None:
                text, usage = hit
                latency = int((time.perf_counter() - started) * 1000)
                record = self._log(
                    caller,
                    request,
                    context,
                    status="ok",
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost=0.0,
                    pricing_unknown=False,
                    cache_hit=True,
                    latency_ms=latency,
                )
                return ModelReply(
                    status="ok",
                    text=text,
                    record=record,
                    raw_usage={"cached": True, **usage},
                )

        # 6) 真实调用
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [dict(item) for item in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        # 6.5) 本地限流：在发请求之前先摁住节奏，429 不是唯一的减速方式
        limit_key = f"{request.provider}/{request.model}"
        try:
            self._limiter.acquire(
                limit_key,
                requests_per_minute=getattr(entry, "requests_per_minute", None),
                max_concurrency=getattr(entry, "max_concurrency", None),
            )
        except LocalRateLimitError as error:
            self._log(
                caller,
                request,
                context,
                status="error",
                prompt_tokens=est_prompt,
                completion_tokens=0,
                cost=0.0,
                pricing_unknown=False,
                cache_hit=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error.message,
            )
            return self._fail("rate_limited", error.reason, error.message)

        try:
            text, usage, error = self._post(provider, payload)
        finally:
            self._limiter.release(limit_key)
        latency = int((time.perf_counter() - started) * 1000)
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)

        if error is not None:
            status, code, message = error
            self._log(
                caller,
                request,
                context,
                status="timeout" if status == "timeout" else "error",
                prompt_tokens=prompt_tokens or est_prompt,
                completion_tokens=completion_tokens,
                cost=None,
                pricing_unknown=price.status != "known",
                cache_hit=False,
                latency_ms=latency,
                error=message,
            )
            return self._fail(status, code, message)

        actual_cost: float | None = None
        if price.status == "known" and entry is not None:
            actual_cost = cost_cny(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                input_price_per_1k_cny=entry.input_price_per_1k_cny or 0.0,
                output_price_per_1k_cny=entry.output_price_per_1k_cny or 0.0,
            )

        record = self._log(
            caller,
            request,
            context,
            status="ok",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=actual_cost,
            pricing_unknown=price.status != "known",
            cache_hit=False,
            latency_ms=latency,
        )
        if request.cacheable:
            self._cache.put(request_hash, text, dict(usage))
        return ModelReply(
            status="ok",
            text=text,
            record=record,
            raw_usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        )

    # -- 内部 -------------------------------------------------------------

    def _safe_daily_spent(self) -> float:
        try:
            return self.daily_spent_cny()
        except Exception as error:  # pragma: no cover - 数据库不可用时保守按 0 处理
            logger.warning("读取当日已花费用失败，按 0 处理：%s", error)
            return 0.0

    def _post(
        self,
        provider: ResolvedProvider,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any], tuple[GatewayStatus, str, str] | None]:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        url = provider.chat_completions_url
        attempt = 0
        last: tuple[GatewayStatus, str, str] | None = None
        while attempt <= self._retries:
            attempt += 1
            try:
                with httpx.Client(
                    timeout=provider.timeout_seconds, transport=self._transport
                ) as client:
                    response = client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as error:
                last = ("timeout", "timeout", f"模型请求超时：{error}")
            except httpx.HTTPError as error:
                last = ("error", "transport_error", f"模型请求失败：{error}")
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                        text = self._extract_text(body)
                        usage = body.get("usage") or {}
                        if not isinstance(usage, Mapping):
                            usage = {}
                        usage = dict(usage)
                        # finish_reason 是"这次回答有没有被截断"的唯一权威信号：
                        # ``length`` 表示撞到 max_tokens，正文不完整（JSON 尤其致命）。
                        choices = body.get("choices")
                        if isinstance(choices, list) and choices:
                            first = choices[0]
                            if isinstance(first, Mapping):
                                reason = first.get("finish_reason")
                                if isinstance(reason, str) and reason:
                                    usage["finish_reason"] = reason
                        return text, usage, None
                    except (ValueError, KeyError) as error:
                        return "", {}, ("error", "bad_response", f"响应不是合法 JSON：{error}")
                status = response.status_code
                detail = _redact(response.text[:300], provider.api_key)
                if status in (401, 403):
                    return "", {}, (
                        "error",
                        "auth_failed",
                        f"鉴权失败（HTTP {status}）：请检查 {provider.name} 的密钥",
                    )
                if status == 429:
                    last = ("rate_limited", "rate_limited", f"被限流（HTTP 429）：{detail}")
                elif status in RETRYABLE_STATUS:
                    last = ("error", "server_error", f"服务端错误（HTTP {status}）：{detail}")
                else:
                    return "", {}, (
                        "error",
                        "bad_request",
                        f"请求被拒绝（HTTP {status}）：{detail}",
                    )
            if attempt <= self._retries:
                time.sleep(self._backoff * attempt)
        return "", {}, last

    @staticmethod
    def _extract_text(body: Mapping[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, Sequence) or not choices:
            raise KeyError("choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise KeyError("choices[0]")
        message = first.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, Sequence):
                parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str)
                ]
                if parts:
                    return "".join(parts)
        text = first.get("text")
        if isinstance(text, str):
            return text
        raise KeyError("message.content")

    def _fail(self, status: GatewayStatus, code: str, message: str) -> ModelReply:
        return ModelReply(status=status, error_code=code, error_message=message)

    def _log(
        self,
        caller: CallerContext,
        request: ModelRequest,
        context: BudgetContext,
        *,
        status: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float | None,
        pricing_unknown: bool,
        cache_hit: bool,
        latency_ms: int,
        error: str | None = None,
    ) -> CallRecord:
        """写 model_calls 审计行；记账失败不掩盖业务结果，但会打警告日志。"""
        total = prompt_tokens + completion_tokens
        record = CallRecord(
            id=None,
            provider=request.provider,
            model=request.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_cny=cost,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            status=status,
        )
        try:
            from backend.app.models.enums import CallStatus
            from backend.app.models.metering import ModelCall

            session = self._session()
            try:
                row = ModelCall(
                    research_task_id=context.task_id,
                    subtask_id=context.subtask_id,
                    provider=request.provider,
                    model=request.model,
                    purpose=request.purpose,
                    request_hash=request.request_hash,
                    response_hash=None,
                    prompt_preview=request.prompt_preview,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total,
                    cost_estimate=cost,
                    pricing_unknown=pricing_unknown,
                    cache_hit=cache_hit,
                    status=CallStatus(status if status in {"ok", "error", "timeout", "budget_blocked"} else "error"),
                    error_message=error,
                    duration_ms=latency_ms,
                    created_at=_utcnow(),
                )
                session.add(row)
                session.commit()
                record = CallRecord(
                    id=row.id,
                    provider=record.provider,
                    model=record.model,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    total_tokens=record.total_tokens,
                    cost_cny=record.cost_cny,
                    cache_hit=record.cache_hit,
                    latency_ms=record.latency_ms,
                    status=record.status,
                )
            finally:
                session.close()
        except Exception as db_error:  # pragma: no cover - 数据库故障路径
            logger.warning("写 model_calls 失败（%s）：%s", caller.name, db_error)
        logger.info(
            "model_call provider=%s model=%s purpose=%s status=%s tokens=%s cost=%s latency=%sms",
            request.provider,
            request.model,
            request.purpose,
            status,
            total,
            cost,
            latency_ms,
        )
        return record


_default_gateway: ModelGateway | None = None


def get_model_gateway() -> ModelGateway:
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = ModelGateway()
    return _default_gateway


def reset_model_gateway() -> None:
    """测试用：丢弃进程内单例（含缓存）。"""
    global _default_gateway
    _default_gateway = None


__all__ = [
    "BudgetContext",
    "CallRecord",
    "GatewayStatus",
    "ModelGateway",
    "ModelReply",
    "ModelRequest",
    "RETRYABLE_STATUS",
    "get_model_gateway",
    "reset_model_gateway",
]
