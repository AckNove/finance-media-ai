"""选题分类域模型（M2.0 迁移 B1：tags / topic_collections / topic_tags / topic_score_snapshots）。

设计依据：``docs/research_hub/database_design.md`` §4.1–§4.4。

三条要点：
- **合集复用 ``topic_columns``**，不建第二张合集表（M1.2 决策 D2）；
- 一个选题可以属于多个合集，但**恰好一个** ``is_primary=1``，用部分唯一索引表达；
- 评分快照只追加，``is_current=1`` 的那一条用部分唯一索引保证唯一。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import ScoredBy, TagNamespace
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.topic import Topic, TopicColumn


class Tag(Base, TimestampMixin):
    """标签：跨合集的细粒度标注（多标签规范，``config/content_taxonomy.yaml``）。"""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_tag_namespace_name"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace: Mapped[TagNamespace] = enum_column(
        TagNamespace, name="tag_namespace", default=TagNamespace.OTHER, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # 使用计数：由服务层维护，用于"热门标签"排序；0 表示尚未被引用
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    topic_tags: Mapped[list[TopicTag]] = relationship(
        back_populates="tag", cascade="save-update, merge"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tag {self.namespace.value}:{self.name!r}>"


class TopicCollection(Base, TimestampMixin):
    """选题 ↔ 合集（多对多）。合集主数据仍然是 ``topic_columns``。"""

    __tablename__ = "topic_collections"
    __table_args__ = (
        UniqueConstraint("topic_id", "column_id", name="uq_topic_collection"),
        # 一个选题最多一个主合集——用部分唯一索引表达"条件唯一"
        Index(
            "uq_topic_primary_collection",
            "topic_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    column_id: Mapped[int] = mapped_column(
        ForeignKey("topic_columns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # 该选题在此合集里的权重（0–1），用于同一选题多合集时的排序
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1.0")

    topic: Mapped[Topic] = relationship(back_populates="collections")
    topic_column: Mapped[TopicColumn] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TopicCollection topic={self.topic_id} column={self.column_id}>"


class TopicTag(Base):
    """选题 ↔ 标签（多对多 + 权重）。"""

    __tablename__ = "topic_tags"
    __table_args__ = (
        UniqueConstraint("topic_id", "tag_id", name="uq_topic_tag"),
        CheckConstraint("weight >= 0 AND weight <= 1", name="weight_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    tag: Mapped[Tag] = relationship(back_populates="topic_tags")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TopicTag topic={self.topic_id} tag={self.tag_id}>"


class TopicScoreSnapshot(Base):
    """选题评分快照（只追加）。六维分数取 0–1，总分也是 0–1 量纲。"""

    __tablename__ = "topic_score_snapshots"
    __table_args__ = (
        CheckConstraint(
            "importance >= 0 AND importance <= 1 AND research_value >= 0 AND research_value <= 1"
            " AND timeliness >= 0 AND timeliness <= 1 AND reach_potential >= 0"
            " AND reach_potential <= 1 AND commercial_value >= 0 AND commercial_value <= 1"
            " AND source_availability >= 0 AND source_availability <= 1",
            name="dimensions_range",
        ),
        CheckConstraint("score_total >= 0 AND score_total <= 1", name="score_total_range"),
        Index(
            "uq_topic_current_score",
            "topic_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    research_value: Mapped[float] = mapped_column(Float, nullable=False)
    timeliness: Mapped[float] = mapped_column(Float, nullable=False)
    reach_potential: Mapped[float] = mapped_column(Float, nullable=False)
    commercial_value: Mapped[float] = mapped_column(Float, nullable=False)
    source_availability: Mapped[float] = mapped_column(Float, nullable=False)
    score_total: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    #: 本次评分采用的权重（六键 JSON），便于事后复现
    weights: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rationale: Mapped[str | None] = mapped_column(Text)
    scored_by: Mapped[ScoredBy] = enum_column(
        ScoredBy, name="scored_by", default=ScoredBy.RULE, index=True
    )
    # 关联的模型调用（M2.0 B3 才建 model_calls，因此这里只存 id、刻意不加外键）
    model_call_id: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TopicScoreSnapshot topic={self.topic_id} total={self.score_total}>"
