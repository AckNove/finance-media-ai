"""SQLAlchemy 声明式基类、命名约定与通用 Mixin。

设计要点：
- 统一命名约定，保证 SQLite 与 PostgreSQL 上约束名可预测（便于 Alembic 迁移）；
- 不使用 SQLite 专有类型（如需 JSON 时统一用 SQLAlchemy 的 JSON 类型，PostgreSQL 上可平滑替换为 JSONB）；
- 所有时间字段统一存储 UTC。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.core.utils import utcnow

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class TimestampMixin:
    """created_at / updated_at 通用时间戳。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
