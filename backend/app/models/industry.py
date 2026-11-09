"""产业域模型：产业链 / 行业。

设计选择：用自引用树 (parent_id) 表达"产业链上下游"，而不是独立的上游/下游表。
理由：
- 产业链本质是树/网结构，深度可变（一级行业 → 二级行业 → 细分环节）；
- chain_stage 枚举标注环节属性，便于横向筛选"上游资源品"或"下游渠道"；
- 跨链关系（某行业既是 A 链下游又是 B 链上游）通过 parent_id 的多重挂载 + 知识关联表达。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import IndustryChainStage
from backend.app.models.types import enum_column


class Industry(Base, TimestampMixin):
    __tablename__ = "industries"
    __table_args__ = (
        CheckConstraint("level >= 1", name="level_positive"),
        CheckConstraint(
            "parent_industry_id IS NULL OR parent_industry_id <> id", name="no_self_parent"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 自引用外键：产业链父节点
    parent_industry_id: Mapped[int | None] = mapped_column(
        ForeignKey("industries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chain_stage: Mapped[IndustryChainStage] = enum_column(
        IndustryChainStage, name="chain_stage", default=IndustryChainStage.MIDSTREAM, index=True
    )
    description: Mapped[str | None] = mapped_column(Text)
    key_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parent: Mapped[Industry | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Industry]] = relationship(back_populates="parent")
