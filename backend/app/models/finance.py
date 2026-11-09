"""金融工具与金融产品域模型。

设计选择：工具（instrument）与产品（product）分表，而不是一张表 + type 字段。
理由：
- 工具是市场层面的合约/证券类别（股票、期权、互换……），有代码、标的、交易所、合约乘数；
- 产品是面向投资者的封装（公募基金、信托、结构性票据……），有发行机构、费率、起投门槛；
- 二者生命周期、字段集合与查询模式完全不同，合并会造成大量可空列与语义混淆。
扩展方式：新增工具类别时扩展 InstrumentClass 枚举并补充该类别特有字段（或建扩展表），
        知识条目的知识域通过 knowledge_domain 保持统一索引。
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import InstrumentClass, ProductWrapper
from backend.app.models.types import MoneyAmount, enum_column

if TYPE_CHECKING:
    from backend.app.models.institution import Institution


class FinancialInstrument(Base, TimestampMixin):
    """金融工具：股票、债券、基金、期货、期权、远期、互换、信用衍生品、结构性产品。"""

    __tablename__ = "financial_instruments"
    __table_args__ = (
        UniqueConstraint("code", "exchange", name="uq_instrument_code_exchange"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    en_name: Mapped[str | None] = mapped_column(String(200))
    instrument_class: Mapped[InstrumentClass] = enum_column(
        InstrumentClass, name="instrument_class", default=InstrumentClass.OTHER, index=True
    )

    underlying: Mapped[str | None] = mapped_column(String(200))
    exchange: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str | None] = mapped_column(String(8))
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    settlement_days: Mapped[int | None] = mapped_column(Integer)
    contract_multiplier: Mapped[float | None] = mapped_column(Float)
    margin_ratio: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)

    as_of_date: Mapped[date | None] = mapped_column(Date)
    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    products: Mapped[list[FinancialProduct]] = relationship(back_populates="instrument")


class FinancialProduct(Base, TimestampMixin):
    """金融产品：面向投资者的封装形态。"""

    __tablename__ = "financial_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    product_wrapper: Mapped[ProductWrapper] = enum_column(
        ProductWrapper, name="product_wrapper", default=ProductWrapper.OTHER, index=True
    )

    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_instruments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    issuer_institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    underlying_summary: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    target_investor: Mapped[str | None] = mapped_column(Text)
    fee_structure: Mapped[str | None] = mapped_column(Text)
    min_investment: Mapped[float | None] = mapped_column(MoneyAmount)
    currency: Mapped[str | None] = mapped_column(String(8))
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    as_of_date: Mapped[date | None] = mapped_column(Date)

    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )

    instrument: Mapped[FinancialInstrument | None] = relationship(back_populates="products")
    issuer: Mapped[Institution | None] = relationship(back_populates="products")
