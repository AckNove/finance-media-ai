"""研究报告路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.schemas.common import Page
from backend.app.schemas.research import ResearchReportCreate, ResearchReportOut
from backend.app.services.research_service import ResearchReportService

router = APIRouter(
    prefix="/research-reports",
    tags=["research"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@router.post(
    "",
    response_model=ResearchReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究报告",
)
def create_research_report(payload: ResearchReportCreate, db: DbSession) -> ResearchReportOut:
    report = ResearchReportService(db).create(payload)
    return ResearchReportOut.model_validate(report)


@router.get("", response_model=Page[ResearchReportOut], summary="研究报告列表")
def list_research_reports(
    db: DbSession,
    page: Pagination,
    topic_id: int | None = None,
    verification_status: str | None = None,
) -> Page[ResearchReportOut]:
    items, total = ResearchReportService(db).list(
        topic_id=topic_id,
        verification_status=verification_status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[ResearchReportOut](
        items=[ResearchReportOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{report_id}", response_model=ResearchReportOut, summary="研究报告详情")
def get_research_report(db: DbSession, report_id: int = Path(ge=1)) -> ResearchReportOut:
    return ResearchReportOut.model_validate(ResearchReportService(db).get(report_id))
