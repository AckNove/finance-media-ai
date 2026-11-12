"""``llm_complete`` 工具适配器（V5.0 M2）。

把统一网关包装成 ``ToolRegistry`` 能派发的工具：

- 入参支持 ``prompt``（单轮）或 ``messages``（多轮，OpenAI 兼容结构）；
- 出参是 ``ToolResult`` 信封，``model_call_id`` 指向 ``model_calls`` 审计行；
- **状态映射诚实**：没有密钥 → ``not_configured``；价目未知/超预算 →
  ``error`` + ``budget_blocked``；超时 → ``error`` + ``timeout``。
  绝不把失败包装成 "ok"。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from backend.app.capabilities.model.gateway import (
    BudgetContext,
    ModelGateway,
    ModelReply,
    ModelRequest,
    get_model_gateway,
)
from backend.app.capabilities.tools import ToolResult
from backend.app.core.caller import CallerContext

#: 网关状态 → 工具信封状态（网关不产生 "denied" 与 "not_implemented"）
_STATUS_MAP: dict[str, str] = {
    "ok": "ok",
    "not_configured": "not_configured",
    "disabled": "not_configured",
    "budget_blocked": "error",
    "rate_limited": "error",
    "timeout": "error",
    "error": "error",
}


def _build_messages(
    prompt: str | None,
    messages: Sequence[Mapping[str, Any]] | None,
    system: str | None,
) -> list[dict[str, Any]]:
    if messages:
        return [dict(item) for item in messages]
    if not prompt:
        raise ValueError("llm_complete 需要 prompt 或 messages（至少一个）")
    built: list[dict[str, Any]] = []
    if system:
        built.append({"role": "system", "content": system})
    built.append({"role": "user", "content": prompt})
    return built


def make_llm_complete(gateway: ModelGateway | None = None) -> Any:
    """构造一个 ``llm_complete`` provider（闭包捕获网关，便于测试注入）。"""

    def llm_complete(
        *,
        caller: CallerContext,
        prompt: str | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        system: str | None = None,
        provider: str = "deepseek",
        model: str = "deepseek-chat",
        purpose: str = "general",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: str = "text",
        budget: BudgetContext | None = None,
        **_: Any,
    ) -> ToolResult:
        engine = gateway or get_model_gateway()
        try:
            built = _build_messages(prompt, messages, system)
        except ValueError as error:
            return ToolResult(
                status="error", error_code="bad_arguments", error_message=str(error)
            )
        request = ModelRequest(
            provider=provider,
            model=model,
            messages=built,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format="json_object" if response_format == "json_object" else "text",
        )
        reply: ModelReply = engine.complete(caller, request, budget=budget)
        if not reply.ok:
            return ToolResult(
                status=_STATUS_MAP.get(reply.status, "error"),  # type: ignore[arg-type]
                error_code=reply.error_code or reply.status,
                error_message=reply.error_message,
            )
        record = reply.record
        cost = record.cost_cny if record is not None else None
        return ToolResult(
            status="ok",
            data={
                "text": reply.text,
                "provider": provider,
                "model": model,
                "purpose": purpose,
                "usage": dict(reply.raw_usage),
                "prompt_tokens": record.prompt_tokens if record else 0,
                "completion_tokens": record.completion_tokens if record else 0,
                "cache_hit": record.cache_hit if record else False,
                "latency_ms": record.latency_ms if record else 0,
                "cost_cny": cost,
            },
            billable=bool(cost),
            cost_cny=cost,
            model_call_id=record.id if record else None,
        )

    llm_complete.__name__ = "llm_complete"
    return llm_complete


def register_model_adapters(registry: Any, *, gateway: ModelGateway | None = None) -> None:
    """把模型工具挂到注册表上（幂等：重复调用只是覆盖同名 provider）。"""
    registry.register("llm_complete", make_llm_complete(gateway))


__all__ = ["make_llm_complete", "register_model_adapters"]
