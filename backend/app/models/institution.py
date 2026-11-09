"""金融机构域模型：银行、券商、保险、交易所、监管机构、足球俱乐部等。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import InstitutionType
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.finance import FinancialProduct


class Institution(Base, TimestampMixin):
    """机构主表：金融行业主体 + 足球产业主体（俱乐部的财务分析与银行同表管理）。

    选择理由：
    - 俱乐部、联赛组织在"资金流向/收入结构"上的分析与金融机构同构，
      共用一张机构表可以复用知识关联与来源追溯；
    - institution_type 枚举显式区分，避免把商业实体混为一谈。
    """

    __tablename__ = "institutions"
    __table_args__ = (
        UniqueConstraint("name", "country", name="uq_institution_name_country"),
        CheckConstraint(
            "founded_year IS NULL OR (founded_year > 1500 AND founded_year <= 2200)",
            name="founded_year_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    en_name: Mapped[str | None] = mapped_column(String(200))
    institution_type: Mapped[InstitutionType] = enum_column(
        InstitutionType, name="institution_type", default=InstitutionType.OTHER, index=True
    )
    country: Mapped[str | None] = mapped_column(String(64))
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    founded_year: Mapped[int | None] = mapped_column(Integer)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    website: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)

    # 与知识条目的主关联（一个机构可对应一个主知识条目）
    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    parent: Mapped[Institution | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Institution]] = relationship(back_populates="parent")
    products: Mapped[list[FinancialProduct]] = relationship(back_populates="issuer")
