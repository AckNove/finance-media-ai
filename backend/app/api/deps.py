"""FastAPI 依赖与公共响应声明。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.database.session import get_db
from backend.app.schemas.common import PaginationParams

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

# 与 config/settings.yaml 的 pagination.page_max_limit 保持一致
MAX_PAGE_LIMIT = 100


def pagination_params(
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_LIMIT, description="单页条数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


Pagination = Annotated[PaginationParams, Depends(pagination_params)]

# 供各路由声明统一错误响应（与 core/errors.py 的响应体一致）
COMMON_RESPONSES: dict[int | str, dict] = {
    404: {"description": "资源不存在"},
    409: {"description": "违反唯一约束 / 状态冲突"},
    422: {"description": "请求参数或业务规则校验失败"},
    503: {"description": "依赖服务不可用"},
}
