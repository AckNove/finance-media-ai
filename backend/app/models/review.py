"""审核记录模型：知识条目、研究报告、脚本的统一审核流水。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.utils import utcnow
from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import ReviewStage, ReviewTargetType, ReviewVerdict, ReviewerType
from backend.app.models.types import enum_column


class ReviewRecord(Base, TimestampMixin):
    """一次审核 = 一个明确的对象 + 明确的结论 + 明确的审核人类型。

    采用 (target_type, target_id) 弱多态关联，而不是给三张表各建一套审核表：
    第一阶段审核流程完全一致，弱多态能避免三倍重复结构；
    代价是数据库层无法用外键约束保证 target 存在，
    因此存在性校验放在服务层（backend/app/services/review_service.py）。
    """

    __tablename__ = "review_records"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[ReviewTargetType] = enum_column(
        ReviewTargetType, name="target_type", index=True
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_version: Mapped[int | None] = mapped_column(Integer)

    reviewer_type: Mapped[ReviewerType] = enum_column(
        ReviewerType, name="reviewer_type", default=ReviewerType.HUMAN
    )
    reviewer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[ReviewStage] = enum_column(
        ReviewStage, name="stage", default=ReviewStage.PROFESSIONAL, index=True
    )
    verdict: Mapped[ReviewVerdict] = enum_column(
        ReviewVerdict, name="verdict", default=ReviewVerdict.PASS, index=True
    )

    findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
