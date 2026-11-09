"""脚本域模型：研究报告 -> 视频脚本。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import ScriptStatus, VideoTemplate
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.media import MediaAsset
    from backend.app.models.publication import Publication
    from backend.app.models.research import ResearchReport
    from backend.app.models.topic import Topic


class Script(Base, TimestampMixin):
    """内容脚本。

    版本规则：版本号在 (topic_id, template) 维度内自增；
    只有 status == approved 的版本才允许建立发布记录（服务层强制）。
    """

    __tablename__ = "scripts"
    __table_args__ = (
        UniqueConstraint("topic_id", "template", "version", name="uq_script_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "estimated_duration IS NULL OR estimated_duration >= 0",
            name="estimated_duration_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    research_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    template: Mapped[VideoTemplate] = enum_column(
        VideoTemplate, name="template", default=VideoTemplate.FINANCIAL_MECHANISM, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[ScriptStatus] = enum_column(
        ScriptStatus, name="status", default=ScriptStatus.DRAFT, index=True
    )

    hook: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # 分镜/分段：[{"index":1,"heading":"...","narration":"...","duration_seconds":12,"visual_note":"..."}]
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    disclaimer: Mapped[str | None] = mapped_column(Text)

    word_count: Mapped[int | None] = mapped_column(Integer)
    estimated_duration: Mapped[int | None] = mapped_column(Integer)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))

    topic: Mapped[Topic] = relationship(back_populates="scripts")
    research_report: Mapped[ResearchReport | None] = relationship(back_populates="scripts")
    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )
    publications: Mapped[list[Publication]] = relationship(
        back_populates="script", cascade="all, delete-orphan"
    )
