"""来源域模型：原始资料来源（sources）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.utils import utcnow
from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import CredibilityLevel, LicenseStatus, SourceType
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.evidence import Evidence


class Source(Base, TimestampMixin):
    """原始资料来源：一切可追溯性的根。

    关键约束：
    - accessed_at 必填（研究必须记录访问时间，防止"未来数据"与时效性混淆）；
    - content_hash 用于去重与变更检测（不存全文，避免版权问题）。
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(200), index=True)
    source_type: Mapped[SourceType] = enum_column(
        SourceType, name="source_type", default=SourceType.OTHER, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    credibility_level: Mapped[CredibilityLevel] = enum_column(
        CredibilityLevel, name="credibility_level", default=CredibilityLevel.UNKNOWN, index=True
    )
    license_status: Mapped[LicenseStatus] = enum_column(
        LicenseStatus, name="license_status", default=LicenseStatus.UNCLEAR
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh")
    notes: Mapped[str | None] = mapped_column(Text)

    evidence_items: Mapped[list[Evidence]] = relationship(
        back_populates="source", cascade="save-update, merge"
    )
