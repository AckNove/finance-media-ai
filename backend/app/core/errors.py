"""统一错误类型与异常处理器。

约定：所有业务错误返回统一的 JSON 结构
    {"error": {"code": "...", "message": "...", "details": ...}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

logger = logging.getLogger(__name__)

# 直接用整数字面量，避免不同 Starlette 版本对
# HTTP_422_UNPROCESSABLE_ENTITY / HTTP_422_UNPROCESSABLE_CONTENT 的命名差异。
HTTP_400_BAD_REQUEST = 400
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_422_UNPROCESSABLE = 422
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_503_SERVICE_UNAVAILABLE = 503


class AppError(Exception):
    """业务错误基类。"""

    status_code: int = HTTP_400_BAD_REQUEST
    code: str = "app_error"
    default_message: str = "请求无法完成"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        self.code = code or type(self).code
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = HTTP_404_NOT_FOUND
    code = "not_found"
    default_message = "资源不存在"


class ConflictError(AppError):
    status_code = HTTP_409_CONFLICT
    code = "conflict"
    default_message = "资源状态冲突"


class DomainValidationError(AppError):
    status_code = HTTP_422_UNPROCESSABLE
    code = "domain_validation_error"
    default_message = "数据未通过业务校验"


class DependencyUnavailableError(AppError):
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"
    default_message = "依赖服务不可用"


def _error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return JSONResponse(status_code=status_code, content={"error": payload})


def install_exception_handlers(app: FastAPI) -> None:
    """把所有异常收敛成同一种响应结构。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.info(
            "business error: %s %s -> %s(%s)",
            request.method,
            request.url.path,
            exc.code,
            exc.message,
        )
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            HTTP_422_UNPROCESSABLE,
            "request_validation_error",
            "请求参数校验失败",
            jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(IntegrityError)
    async def _handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("database integrity error on %s: %s", request.url.path, exc)
        return _error_response(
            HTTP_409_CONFLICT,
            "database_integrity_error",
            "数据库约束冲突（可能是唯一键或外键约束）",
        )

    @app.exception_handler(SQLAlchemyError)
    async def _handle_sqlalchemy_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database error on %s", request.url.path)
        return _error_response(
            HTTP_500_INTERNAL_SERVER_ERROR,
            "database_error",
            "数据库操作失败",
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return _error_response(
            HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "服务器内部错误",
        )
