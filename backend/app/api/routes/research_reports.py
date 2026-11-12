"""研究报告路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy import select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import ConflictError, NotFoundError
from backend.app.models.evidence import Evidence
from backend.app.models.source import Source
from backend.app.schemas.common import Page
from backend.app.schemas.research import ResearchReportCreate, ResearchReportOut
from backend.app.schemas.video import VideoJobOut
from backend.app.services.research_service import ResearchReportService
from backend.app.video.handoff import VideoHandoffError, create_video_job

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


@router.post(
    "/{report_id}/video-job",
    response_model=VideoJobOut,
    status_code=status.HTTP_201_CREATED,
    summary="从报告开一个视频任务目录",
    description=(
        "把报告正文、证据行与来源清单搬进 `content/jobs/<job_id>/research/`，"
        "脚本与分镜目录留空——它们必须基于这些材料来写。没有可用模型时返回 "
        "`blocked=true` 与稳定原因码，绝不生成模板脚本冒充。"
    ),
)
def create_video_job_for_report(
    db: DbSession, report_id: int = Path(ge=1)
) -> VideoJobOut:
    report = ResearchReportService(db).get(report_id)
    evidence = list(
        db.scalars(select(Evidence).where(Evidence.research_report_id == report.id))
    )
    source_ids = {item.source_id for item in evidence}
    sources = (
        list(db.scalars(select(Source).where(Source.id.in_(sorted(source_ids)))))
        if source_ids
        else []
    )
    try:
        scaffold = create_video_job(report, evidence=evidence, sources=sources)
    except VideoHandoffError as error:
        if error.reason == "job_exists":
            raise ConflictError(
                "同名视频任务目录已存在，未覆盖", details={"reason": error.reason}
            ) from error
        raise NotFoundError(str(error)) from error
    return VideoJobOut(**{**scaffold.as_dict(), "path": str(scaffold.path)})
