"""研究报告域模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import VerificationStatus
from backend.app.models.types import CostAmount, enum_column

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


class FindingCheck(Base, TimestampMixin):
    """一条 Finding 的语义核查结果（模型判定）。

    为什么单独开表而不是塞进 ``research_reports.findings`` 的 JSON：

    * 核查结果要能**被人改**（人工复核状态），JSON 里改一格没法做乐观锁与审计；
    * 前端要按 ``verdict`` / ``human_review_status`` 列表筛选，JSON 查不动；
    * 模型判定与人工判定必须是两个独立字段——``verdict`` 是模型说的，
      ``human_review_status`` 是人说的，**任何代码都不许用前者自动写后者**。
    """

    __tablename__ = "finding_checks"
    __table_args__ = (
        CheckConstraint("finding_index >= 0", name="ck_finding_checks_index_non_negative"),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0",
            name="ck_finding_checks_tokens_non_negative",
        ),
        CheckConstraint("cost_cny >= 0", name="ck_finding_checks_cost_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    research_report_id: Mapped[int] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 该结论在报告 findings 数组里的下标（0 起），用于对回原文
    finding_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- 1. 原始结论 ---
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    #: fact / inference / hypothesis（沿用 analysis 的分类）
    claim_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="inference")

    # --- 2/3. 关联证据行与来源 ---
    evidence_ids: Mapped[list[int] | None] = mapped_column(JSON)
    source_ids: Mapped[list[str] | None] = mapped_column(JSON)

    # --- 4/5. 判定与理由 ---
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- 6/7/8. 支持片段、反对片段、缺失信息 ---
    supporting: Mapped[list[str] | None] = mapped_column(JSON)
    contradicting: Mapped[list[str] | None] = mapped_column(JSON)
    missing: Mapped[list[str] | None] = mapped_column(JSON)
    #: 命中的八类疑点（number_unit_mismatch 等）
    issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    # --- 9/10/11. 模型、调用记录、时间 ---
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_calls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cny: Mapped[Any] = mapped_column(CostAmount, nullable=False, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- 12. 人工复核（与模型判定彻底分开）---
    human_review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    human_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    #: 解析过程中的降级说明（如 downgraded:no_verifiable_supporting_quote）
    notes: Mapped[list[str] | None] = mapped_column(JSON)

    report: Mapped[ResearchReport] = relationship()
