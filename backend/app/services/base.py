"""服务层公共工具。

设计约定：
- 路由层只做参数解析与响应序列化，所有数据库读写必须落在服务层；
- 服务层不吞异常：数据库唯一约束冲突统一翻译成 ConflictError(409)，
  未找到统一抛 NotFoundError(404)，业务规则违反抛 DomainValidationError(422)。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import ConflictError
from backend.app.core.utils import slugify


class BaseService:
    """服务层基类：持有 Session，提供提交与分页助手。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _commit(self, instance: Any, conflict_message: str) -> Any:
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(conflict_message, details={"reason": str(exc.orig)}) from exc
        self.db.refresh(instance)
        return instance

    def _paginate(
        self,
        stmt: Any,
        *,
        limit: int,
        offset: int,
        order_by: Any,
    ) -> tuple[list[Any], int]:
        """执行分页查询，返回 (当前页记录, 满足条件总数)。"""
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(self.db.scalars(stmt.order_by(*order_by).limit(limit).offset(offset)))
        return rows, int(total)


def make_unique_slug(db: Session, model: Any, base_text: str, *, max_length: int = 120) -> str:
    """按标题生成唯一 slug；冲突时追加 -2、-3 等序号。"""
    root = slugify(base_text)[: max_length - 8]
    candidate = root
    suffix = 2
    while db.scalar(select(func.count()).select_from(model).where(model.slug == candidate)):
        candidate = f"{root}-{suffix}"
        suffix += 1
    return candidate
