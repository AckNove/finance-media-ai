"""知识库域模型：知识条目与知识关联。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import KnowledgeDomain, RelationType, VerificationStatus
from backend.app.models.types import enum_column


class KnowledgeItem(Base, TimestampMixin):
    """知识条目：平台的核心知识资产。

    字段分层：
    - 定义层：definition / mechanism / participants
    - 商业与定价层：profit_model / pricing_logic
    - 风险层：risk_structure
    - 类比层：football_analogy / analogy_limitations（必须成对出现才有意义）
    - 元数据层：jurisdiction / as_of_date / currency / unit，用于记录"数据口径"
    - 治理层：verification_status / last_verified_at / version / is_current
    """

    __tablename__ = "knowledge_items"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    knowledge_domain: Mapped[KnowledgeDomain] = enum_column(
        KnowledgeDomain, name="knowledge_domain", default=KnowledgeDomain.GENERAL, index=True
    )

    definition: Mapped[str | None] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[str | None] = mapped_column(Text)
    profit_model: Mapped[str | None] = mapped_column(Text)
    pricing_logic: Mapped[str | None] = mapped_column(Text)
    risk_structure: Mapped[str | None] = mapped_column(Text)
    football_analogy: Mapped[str | None] = mapped_column(Text)
    analogy_limitations: Mapped[str | None] = mapped_column(Text)

    # --- 数据口径 ---
    jurisdiction: Mapped[str] = mapped_column(
        String(64), nullable=False, default="global", server_default="global"
    )
    as_of_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32))

    # --- 治理 ---
    verification_status: Mapped[VerificationStatus] = enum_column(
        VerificationStatus,
        name="verification_status",
        default=VerificationStatus.UNVERIFIED,
        index=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)

    relations_from: Mapped[list[KnowledgeRelation]] = relationship(
        foreign_keys="KnowledgeRelation.source_item_id",
        back_populates="source_item",
        cascade="all, delete-orphan",
    )
    relations_to: Mapped[list[KnowledgeRelation]] = relationship(
        foreign_keys="KnowledgeRelation.target_item_id",
        back_populates="target_item",
    )


class KnowledgeRelation(Base, TimestampMixin):
    """知识关联：把知识条目织成图谱（第一阶段仍是关系型，不是图数据库）。"""

    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_item_id", "target_item_id", "relation_type", name="uq_relation_triple"
        ),
        CheckConstraint("source_item_id <> target_item_id", name="no_self_relation"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_item_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[RelationType] = enum_column(
        RelationType, name="relation_type", default=RelationType.RELATED, index=True
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)

    source_item: Mapped[KnowledgeItem] = relationship(
        foreign_keys=[source_item_id], back_populates="relations_from"
    )
    target_item: Mapped[KnowledgeItem] = relationship(
        foreign_keys=[target_item_id], back_populates="relations_to"
    )
