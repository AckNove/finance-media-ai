"""请求上下文：一个请求一个可追踪 ID。

为什么需要它：出错时日志里只有时间戳和堆栈，无法把"用户点了什么"与
"哪一行日志"对上。请求 ID 是排障的最小成本手段，也是未来
``task_events`` / ``tool_calls`` 关联外部请求的锚点。

规则：
- 客户端可以用 ``X-Request-Id`` 指定，但只接受 ``[A-Za-z0-9._-]{1,64}``；
- 不合法或缺失时由服务端生成 ``req-<16位 hex>``；
- 该 ID 只用于日志与响应头，**不作为任何权限依据**（可伪造，与 ``X-Caller`` 同理）。
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-Id"

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_request_id: ContextVar[str] = ContextVar("finance_media_ai_request_id", default="")


def new_request_id() -> str:
    """生成服务端请求 ID。"""
    return f"req-{uuid4().hex[:16]}"


def sanitize_request_id(raw: str | None) -> str | None:
    """校验客户端传入的 ID；不合法返回 ``None``（由调用方生成新的）。"""
    if not raw:
        return None
    candidate = raw.strip()
    if not _VALID_REQUEST_ID.match(candidate):
        return None
    return candidate


def set_request_id(value: str) -> Token[str]:
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    """当前请求的 ID；不在请求上下文里时返回空字符串。"""
    return _request_id.get()


__all__ = [
    "REQUEST_ID_HEADER",
    "get_request_id",
    "new_request_id",
    "reset_request_id",
    "sanitize_request_id",
    "set_request_id",
]
