"""纯 ASGI 中间件：为每个请求生成/透传 ``X-Request-Id`` 并记录一行访问日志。

为什么不用 ``BaseHTTPMiddleware``：
1. 它会包一层任务，异常与流式响应行为与原生 ASGI 不一致；
2. 我们只需要"进入时打标签、退出时写日志"，纯 ASGI 足够且零依赖。

写日志的字段固定为：``method path status duration_ms request_id``，
后续接 ``task_events`` 时可以直接解析这一行。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.logging import get_logger
from backend.app.core.request_context import (
    REQUEST_ID_HEADER,
    new_request_id,
    reset_request_id,
    sanitize_request_id,
    set_request_id,
)

logger = get_logger(__name__)


class RequestContextMiddleware:
    """注入请求 ID、写访问日志、并在异常时保证 ID 出现在日志里。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = sanitize_request_id(headers.get(REQUEST_ID_HEADER)) or new_request_id()
        token = set_request_id(request_id)
        method = scope.get("method", "-")
        path = scope.get("path", "-")
        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "%s %s -> 500 %.1fms request_id=%s", method, path, duration_ms, request_id
            )
            raise
        finally:
            reset_request_id(token)

        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "%s %s -> %s %.1fms request_id=%s",
            method,
            path,
            status_code if status_code is not None else "-",
            duration_ms,
            request_id,
        )


MiddlewareFactory = Callable[[ASGIApp], Any]


def install_middleware(app: Any) -> None:
    """把请求上下文中间件挂到 FastAPI 应用上（幂等，测试可重复调用）。"""
    existing = {m.cls for m in app.user_middleware}
    if RequestContextMiddleware in existing:
        return
    app.add_middleware(RequestContextMiddleware)


__all__ = ["RequestContextMiddleware", "install_middleware"]
