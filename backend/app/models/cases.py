"""案例域模型：足球商业案例 + 金融案例。

设计选择：足球案例与金融案例分表。
理由：
- 足球案例的专属维度是赛季（season）、俱乐部/联赛主体、转会与转播权结构；
- 金融案例的专属维度是司法辖区、事件周期（period_start/period_end）、资金流向复盘；
- 两者共享的是"案例叙事 + 知识条目挂载 + 报告挂载"，通过相同的关联列保持一致性。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import FinancialCaseType, FootballCaseType
from backend.app.models.types import MoneyAmount, enum_column


class FootballCase(Base, TimestampMixin):
    """足球商业案例：转会、俱乐部财务、股权收购、转播权、赞助、财务公平竞赛。"""

    __tablename__ = "football_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    case_type: Mapped[FootballCaseType] = enum_column(
        FootballCaseType, name="case_type", default=FootballCaseType.OTHER, index=True
    )

    club_institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    league_institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    season: Mapped[str | None] = mapped_column(String(16))

    amount_value: Mapped[float | None] = mapped_column(MoneyAmount)
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32))
    as_of_date: Mapped[date | None] = mapped_column(Date)

    summary: Mapped[str | None] = mapped_column(Text)
    financial_mechanism: Mapped[str | None] = mapped_column(Text)
    analogy_scope: Mapped[str | None] = mapped_column(Text)
    analogy_limitations: Mapped[str | None] = mapped_column(Text)

    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )


class FinancialCase(Base, TimestampMixin):
    """金融案例：风险事件、资本运作、商业模式、市场事件、欺诈案例。"""

    __tablename__ = "financial_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    case_type: Mapped[FinancialCaseType] = enum_column(
        FinancialCaseType, name="case_type", default=FinancialCaseType.OTHER, index=True
    )

    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)

    amount_value: Mapped[float | None] = mapped_column(MoneyAmount)
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32))

    summary: Mapped[str | None] = mapped_column(Text)
    fund_flow_summary: Mapped[str | None] = mapped_column(Text)
    risk_notes: Mapped[str | None] = mapped_column(Text)

    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
