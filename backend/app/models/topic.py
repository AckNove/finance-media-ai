"""选题域模型：内容栏目与选题。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import (
    AudienceLevel,
    ContentStatus,
    KnowledgeDomain,
    ResearchStatus,
    TopicOrigin,
    TopicStatus,
)
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.research import ResearchReport
    from backend.app.models.script import Script
    from backend.app.models.taxonomy import TopicCollection


class TopicColumn(Base, TimestampMixin):
    """内容栏目：选题的分组（对应内容矩阵中的固定栏目）。

    M2.0 B1 追加三列：``is_active``（下线但保留历史）、``is_system``（六大长期合集，
    不允许删除）、``taxonomy_version``（分类体系版本，便于整体换版）。
    """

    __tablename__ = "topic_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )
    taxonomy_version: Mapped[int | None] = mapped_column(Integer)

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

    # --- M2.0 B1 追加：选题生命周期主状态机与多合集/去重所需字段 ---
    status: Mapped[TopicStatus] = enum_column(
        TopicStatus,
        name="topic_status",
        default=TopicStatus.CANDIDATE,
        index=True,
        # 数据库侧默认值：B1 迁移要给已有行一个值，SQLite 的
        # ADD COLUMN ... NOT NULL 没有默认值会直接失败
        server_default=TopicStatus.CANDIDATE,
    )
    origin_type: Mapped[TopicOrigin | None] = enum_column(
        TopicOrigin, name="topic_origin", nullable=True, index=True
    )
    discovered_by: Mapped[str | None] = mapped_column(String(64))
    #: 去重键（标题归一化后的 hash），由服务层生成；不需要唯一（历史重复要先人工合并）
    dedupe_key: Mapped[str | None] = mapped_column(String(64), index=True)
    merged_into_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: 最近一次六维评分的总分（0–1）；明细在 topic_score_snapshots
    score_total: Mapped[float | None] = mapped_column(Float, index=True)
    score_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    topic_column: Mapped[TopicColumn | None] = relationship(back_populates="topics")
    collections: Mapped[list[TopicCollection]] = relationship(
        back_populates="topic", cascade="save-update, merge"
    )
    research_reports: Mapped[list[ResearchReport]] = relationship(
        back_populates="topic", cascade="save-update, merge"
    )
    scripts: Mapped[list[Script]] = relationship(
        back_populates="topic", cascade="save-update, merge"
    )
