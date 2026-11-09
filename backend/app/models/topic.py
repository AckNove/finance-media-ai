"""选题域模型：内容栏目与选题。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import AudienceLevel, ContentStatus, KnowledgeDomain, ResearchStatus
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.research import ResearchReport
    from backend.app.models.script import Script


class TopicColumn(Base, TimestampMixin):
    """内容栏目：选题的分组（对应内容矩阵中的固定栏目）。"""

    __tablename__ = "topic_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    topics: Mapped[list[Topic]] = relationship(back_populates="topic_column")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TopicColumn code={self.code!r}>"


class Topic(Base, TimestampMixin):
    """选题：内容生产的起点。"""

    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("priority >= 1 AND priority <= 5", name="priority_range"),
        CheckConstraint(
            "estimated_duration IS NULL OR estimated_duration >= 0",
            name="estimated_duration_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    column_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic_columns.id", ondelete="SET NULL"), nullable=True, index=True
    )
    knowledge_domain: Mapped[KnowledgeDomain] = enum_column(
        KnowledgeDomain, name="knowledge_domain", default=KnowledgeDomain.GENERAL, index=True
    )
    audience_level: Mapped[AudienceLevel] = enum_column(
        AudienceLevel, name="audience_level", default=AudienceLevel.BEGINNER
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    football_analogy_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    research_status: Mapped[ResearchStatus] = enum_column(
        ResearchStatus, name="research_status", default=ResearchStatus.PENDING, index=True
    )
    content_status: Mapped[ContentStatus] = enum_column(
        ContentStatus, name="content_status", default=ContentStatus.IDEA, index=True
    )
    # 预计成片时长（秒）
    estimated_duration: Mapped[int | None] = mapped_column(Integer)

    topic_column: Mapped[TopicColumn | None] = relationship(back_populates="topics")
    research_reports: Mapped[list[ResearchReport]] = relationship(
        back_populates="topic", cascade="save-update, merge"
    )
    scripts: Mapped[list[Script]] = relationship(
        back_populates="topic", cascade="save-update, merge"
    )
