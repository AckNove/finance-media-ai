"""Pydantic 公共 Schema 与错误模型。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMBase(BaseModel):
    """可从 ORM 实例构造的响应模型基类。"""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """统一分页响应。"""

    items: list[T]
    total: int = Field(ge=0, description="满足过滤条件的总数（不受 limit/offset 影响）")
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)


class ErrorDetail(BaseModel):
    code: str = Field(description="稳定的机器可读错误码")
    message: str
    details: dict | list | None = None


class ErrorResponse(BaseModel):
    """全局错误响应体：{"error": {"code", "message", "details"}}。"""

    error: ErrorDetail


class PaginationParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
