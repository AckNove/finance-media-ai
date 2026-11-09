"""研究报告服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.models.research import ResearchReport
from backend.app.models.topic import Topic
from backend.app.schemas.research import ResearchReportCreate
from backend.app.services.base import BaseService


class ResearchReportService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create(self, payload: ResearchReportCreate) -> ResearchReport:
        if payload.topic_id is not None and self.db.get(Topic, payload.topic_id) is None:
            raise DomainValidationError(
                f"topic_id={payload.topic_id} 不存在", details={"field": "topic_id"}
            )

        data = payload.model_dump(exclude={"findings"})
        # JSON 列只接受纯 JSON 类型：枚举转字符串后存储
        data["findings"] = [finding.model_dump(mode="json") for finding in payload.findings]
        report = ResearchReport(**data)
        self.db.add(report)
        return self._commit(report, "研究报告写入冲突")

    def get(self, report_id: int) -> ResearchReport:
        report = self.db.get(ResearchReport, report_id)
        if report is None:
            raise NotFoundError(
                f"研究报告 id={report_id} 不存在", details={"research_report_id": report_id}
            )
        return report

    def list(
        self,
        *,
        topic_id: int | None = None,
        verification_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ResearchReport], int]:
        stmt = select(ResearchReport)
        if topic_id is not None:
            stmt = stmt.where(ResearchReport.topic_id == topic_id)
        if verification_status:
            stmt = stmt.where(ResearchReport.verification_status == verification_status)
        return self._paginate(
            stmt,
            limit=limit,
            offset=offset,
            order_by=(ResearchReport.created_at.desc(), ResearchReport.id.desc()),
        )
