"""研究报告域模型。"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import VerificationStatus
from backend.app.models.types import enum_column

if TYPE_CHECKING:
    from backend.app.models.evidence import Evidence
    from backend.app.models.script import Script
    from backend.app.models.topic import Topic


class ResearchReport(Base, TimestampMixin):
    """研究报告：研究的正式产出，是脚本写作的唯一事实底座。

    findings 使用 JSON 数组，每个元素形如：
        {"statement": "...", "nature": "fact|inference|opinion", "evidence_ids": [1, 2]}

    limitations 必填（由服务层校验），用于沉淀"这个研究没解决什么"，
    避免把有限研究包装成完整结论。
    """

    __tablename__ = "research_reports"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "reading_time_minutes IS NULL OR reading_time_minutes > 0",
            name="reading_time_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    limitations: Mapped[str] = mapped_column(Text, nullable=False)

    verification_status: Mapped[VerificationStatus] = enum_column(
        VerificationStatus,
        name="verification_status",
        default=VerificationStatus.UNVERIFIED,
        index=True,
    )

    # --- 数据口径 ---
    jurisdiction: Mapped[str] = mapped_column(
        String(64), nullable=False, default="global", server_default="global"
    )
    as_of_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32))

    # --- 溯源与版本 ---
    author_agent: Mapped[str | None] = mapped_column(String(64))
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reading_time_minutes: Mapped[int | None] = mapped_column(Integer)

    # --- M2.0 B2 追加：把报告挂到研究任务上，并保留大纲与档位统计 ---
    research_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: 报告大纲（结构化章节，供内容生产复用）
    outline: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: FindingTier 五档各自的条目数，便于一眼看出这份报告有多少"证据不足"
    tier_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    topic: Mapped[Topic | None] = relationship(back_populates="research_reports")
    evidence_items: Mapped[list[Evidence]] = relationship(
        back_populates="research_report", cascade="all, delete-orphan"
    )
    scripts: Mapped[list[Script]] = relationship(back_populates="research_report")
