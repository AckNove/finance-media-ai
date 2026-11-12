"""审核流水服务。

审核记录用 (target_type, target_id) 弱多态挂在三种对象上（知识条目、研究报告、
脚本）。数据库层没法用外键保证 target 存在，所以**存在性校验必须在这里做**——
否则库里会留下指向空气的审核记录。

审核结论会回写到目标对象上，但只做最小映射，不发明状态：
- 研究报告：`pass` → `VERIFIED`；`needs_revision` → `DISPUTED`；`reject` → `REFUTED`。
  （`REFUTED` 是"被推翻"，用在报告上表示"这份报告的结论不可用"，语义比自定义状态更稳。）
- 知识条目 / 脚本：只记审核流水，不改它们的业务状态——脚本状态机由内容生产流程驱动，
  审核通过不等于可以发布（发布仍需人工单独操作）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.core.utils import utcnow
from backend.app.models.enums import (
    ReviewStage,
    ReviewerType,
    ReviewTargetType,
    ReviewVerdict,
    VerificationStatus,
)
from backend.app.models.knowledge import KnowledgeItem
from backend.app.models.research import ResearchReport
from backend.app.models.review import ReviewRecord
from backend.app.models.script import Script

#: 审核结论 → 研究报告的核查状态。脚本与知识条目不在其中（见模块 docstring）。
REPORT_VERDICT_STATUS = {
    ReviewVerdict.PASS: VerificationStatus.VERIFIED,
    ReviewVerdict.NEEDS_REVISION: VerificationStatus.DISPUTED,
    ReviewVerdict.REJECT: VerificationStatus.REFUTED,
}

_TARGET_MODELS = {
    ReviewTargetType.KNOWLEDGE_ITEM: KnowledgeItem,
    ReviewTargetType.RESEARCH_REPORT: ResearchReport,
    ReviewTargetType.SCRIPT: Script,
}


class ReviewService:
    """写在审核记录上，并把结论回写到目标对象。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- 查询 -------------------------------------------------------------
    def list(
        self,
        *,
        target_type: ReviewTargetType | None = None,
        target_id: int | None = None,
        verdict: ReviewVerdict | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReviewRecord], int]:
        stmt = select(ReviewRecord)
        if target_type is not None:
            stmt = stmt.where(ReviewRecord.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(ReviewRecord.target_id == target_id)
        if verdict is not None:
            stmt = stmt.where(ReviewRecord.verdict == verdict)
        rows = list(
            self.db.scalars(
                stmt.order_by(ReviewRecord.reviewed_at.desc(), ReviewRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        total = len(
            list(
                self.db.scalars(
                    stmt.with_only_columns(ReviewRecord.id).order_by(None)
                )
            )
        )
        return rows, total

    def get(self, review_id: int) -> ReviewRecord:
        record = self.db.get(ReviewRecord, review_id)
        if record is None:
            raise NotFoundError(f"审核记录 id={review_id} 不存在", details={"review_id": review_id})
        return record

    # --- 写入 -------------------------------------------------------------
    def create(
        self,
        *,
        target_type: ReviewTargetType,
        target_id: int,
        verdict: ReviewVerdict,
        reviewer_name: str,
        reviewer_type: ReviewerType = ReviewerType.HUMAN,
        stage: ReviewStage = ReviewStage.PROFESSIONAL,
        comment: str | None = None,
        findings: list[dict[str, Any]] | None = None,
        checklist: dict[str, Any] | None = None,
    ) -> ReviewRecord:
        """记一次审核。

        存在的对象才允许审核；`reviewer_type=AGENT` 也要求填名字，
        否则事后查"这条结论是谁下的"会查不到。
        """
        model = _TARGET_MODELS[target_type]
        target = self.db.get(model, target_id)
        if target is None:
            raise NotFoundError(
                f"{target_type.value} id={target_id} 不存在",
                details={"target_type": target_type.value, "target_id": target_id},
            )
        name = (reviewer_name or "").strip()
        if not name:
            raise DomainValidationError(
                "reviewer_name 不能为空", details={"field": "reviewer_name"}
            )
        if verdict is ReviewVerdict.NEEDS_REVISION and not (comment or "").strip():
            raise DomainValidationError(
                "结论为 needs_revision 时必须写清要改什么（comment 不能为空）",
                details={"field": "comment"},
            )

        record = ReviewRecord(
            target_type=target_type,
            target_id=target_id,
            target_version=getattr(target, "version", None),
            reviewer_type=reviewer_type,
            reviewer_name=name[:64],
            stage=stage,
            verdict=verdict,
            findings=findings,
            checklist=checklist,
            comment=comment,
            reviewed_at=utcnow(),
        )
        self.db.add(record)

        if target_type is ReviewTargetType.RESEARCH_REPORT:
            target.verification_status = REPORT_VERDICT_STATUS[verdict]
            self.db.add(target)

        self.db.commit()
        self.db.refresh(record)
        return record
