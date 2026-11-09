"""发布记录模型：只有已审批脚本版本才允许发布（服务层强制）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import PublicationPlatform
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.script import Script


class Publication(Base, TimestampMixin):
    """一次平台发布 + 其后采集的效果数据。

    script_version 冗余保存，是为了让"发布的是哪一版脚本"成为不可变事实：
    即使脚本后续被修改，历史发布记录依然指向当时审批通过的版本号。
    """

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint(
            "script_id", "script_version", "platform", name="uq_publication_per_platform"
        ),
        UniqueConstraint("platform", "platform_item_id", name="uq_platform_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_version: Mapped[int] = mapped_column(Integer, nullable=False)
    platform: Mapped[PublicationPlatform] = enum_column(
        PublicationPlatform, name="platform", index=True
    )
    platform_item_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- 效果数据（可空，随时间采集更新） ---
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    watch_time_seconds: Mapped[float | None] = mapped_column(Float)
    completion_rate: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    notes: Mapped[str | None] = mapped_column(Text)

    script: Mapped[Script] = relationship(back_populates="publications")
