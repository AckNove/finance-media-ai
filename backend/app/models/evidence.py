"""证据域模型：把"来源"映射到"它支持或反驳的具体主张"。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import StatementNature, SupportType, VerificationStatus
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.knowledge import KnowledgeItem
    from backend.app.models.research import ResearchReport
    from backend.app.models.source import Source


class Evidence(Base, TimestampMixin):
    """一条证据 = 一个受引用的原文片段 + 它支持/反驳的主张。

    设计要求（第一阶段）：
    - 必须来自某个 source（source_id 非空）；
    - 必须挂到某个知识条目或研究报告上（否则该证据无处可用，视为脏数据）；
    - claim_nature 显式区分事实/推断/观点，禁止把观点伪装成事实。
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "knowledge_item_id IS NOT NULL OR research_report_id IS NOT NULL",
            name="has_claim_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # 被支持/反驳的主张原文
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    original_excerpt: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[str | None] = mapped_column(String(64))
    quote_locator: Mapped[str | None] = mapped_column(String(200))

    support_type: Mapped[SupportType] = enum_column(
        SupportType, name="support_type", default=SupportType.SUPPORTS, index=True
    )
    claim_nature: Mapped[StatementNature] = enum_column(
        StatementNature, name="claim_nature", default=StatementNature.FACT, index=True
    )
    verification_status: Mapped[VerificationStatus] = enum_column(
        VerificationStatus,
        name="verification_status",
        default=VerificationStatus.UNVERIFIED,
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 证据挂载点（至少一个非空）
    knowledge_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    research_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=True, index=True
    )

    source: Mapped[Source] = relationship(back_populates="evidence_items")
    knowledge_item: Mapped[KnowledgeItem | None] = relationship()
    research_report: Mapped[ResearchReport | None] = relationship(back_populates="evidence_items")
