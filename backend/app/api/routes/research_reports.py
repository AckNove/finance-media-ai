"""研究报告路由。"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy import select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import ConflictError, DomainValidationError, NotFoundError
from backend.app.models.evidence import Evidence
from backend.app.models.research import FindingCheck
from backend.app.models.source import Source
from backend.app.research.draft_gate import (
    DraftGateError,
    assert_draft_ready,
    confirmed_findings,
    readiness_of,
)
from backend.app.research.persistence import HUMAN_REVIEW_STATUSES, set_human_review
from backend.app.schemas.common import Page
from backend.app.schemas.draft_gate import DraftReadinessOut
from backend.app.schemas.finding_check import (
    FindingCheckOut,
    FindingCheckReviewIn,
    FindingCheckSummaryOut,
)
from backend.app.schemas.research import ResearchReportCreate, ResearchReportOut
from backend.app.schemas.video import VideoJobIn, VideoJobOut
from backend.app.services.research_service import ResearchReportService
from backend.app.video.handoff import VideoHandoffError, create_video_job

logger = logging.getLogger(__name__)

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
    "/{report_id}/video-job",    response_model=VideoJobOut,
    status_code=status.HTTP_201_CREATED,
    summary="从报告开一个视频任务目录",
    description=(
        "把报告正文、证据行与来源清单搬进 `content/jobs/<job_id>/research/`，"
        "只要证据里有两个以上来源，就用**纯规则**排一份分镜草稿"
        "（`storyboard/storyboard.csv` + `OUTLINE.md`，口播稿是原文摘引、逐镜标来源）；"
        "`script/` 仍然留空——正式脚本必须由模型或人写。资料不够或没有可用模型时返回 "
        "`blocked=true` 与稳定原因码（`insufficient_sources` / `no_data_points` / "
        "`model_not_configured`），绝不生成模板脚本冒充。"
    ),
)
def create_video_job_for_report(
    db: DbSession, payload: VideoJobIn | None = None, report_id: int = Path(ge=1)
) -> VideoJobOut:
    report = ResearchReportService(db).get(report_id)
    try:
        gate = assert_draft_ready(db, report)
    except DraftGateError as error:
        raise ConflictError(str(error), details=dict(error.readiness)) from error
    confirmed = confirmed_findings(db, report)
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
        scaffold = create_video_job(
            report,
            evidence=evidence,
            sources=sources,
            job_id=(payload.job_id if payload else None),
            confirmed=confirmed,
        )
    except VideoHandoffError as error:
        if error.reason == "job_exists":
            raise ConflictError(
                "同名视频任务目录已存在，未覆盖", details={"reason": error.reason}
            ) from error
        if error.reason == "draft_not_confirmed":
            raise ConflictError(
                str(error), details={"reason": error.reason, **gate.as_dict()}
            ) from error
        raise NotFoundError(str(error)) from error
    return VideoJobOut(**{**scaffold.as_dict(), "path": str(scaffold.path)})


@router.get(
    "/{report_id}/draft-readiness",
    summary="这份报告能不能成稿（只有人工确认过的结论才允许）",
    response_model=DraftReadinessOut,
)
def read_draft_readiness(db: DbSession, report_id: int = Path(ge=1)) -> DraftReadinessOut:
    report = ResearchReportService(db).get(report_id)
    return DraftReadinessOut(**readiness_of(db, report).as_dict())


# ---------------------------------------------------------------- 语义核查


def _checks_of(db: DbSession, report_id: int) -> list[FindingCheck]:
    return list(
        db.scalars(
            select(FindingCheck)
            .where(FindingCheck.research_report_id == report_id)
            .order_by(FindingCheck.finding_index)
        )
    )


@router.get(
    "/{report_id}/checks",
    response_model=Page[FindingCheckOut],
    summary="结论核查明细（模型判定 + 人工复核，两条状态线）",
    description=(
        "每条结论一条记录：模型给了什么判定、理由、逐字支持/反对片段、缺什么信息，"
        "以及人工复核到了哪一步。模型判定**不代表**已核实——`verdict` 与 "
        "`human_review_status` 是两回事，只有后者为 `confirmed` 才算人确认过。"
    ),
)
def list_report_checks(
    db: DbSession,
    page: Pagination,
    report_id: int = Path(ge=1),
    verdict: str | None = None,
    human_review_status: str | None = None,
) -> Page[FindingCheckOut]:
    ResearchReportService(db).get(report_id)  # 报告不存在直接 404
    statement = select(FindingCheck).where(FindingCheck.research_report_id == report_id)
    if verdict:
        statement = statement.where(FindingCheck.verdict == verdict)
    if human_review_status:
        statement = statement.where(FindingCheck.human_review_status == human_review_status)
    rows = list(db.scalars(statement.order_by(FindingCheck.finding_index)))
    total = len(rows)
    window = rows[page.offset : page.offset + page.limit]
    return Page[FindingCheckOut](
        items=[FindingCheckOut.model_validate(item) for item in window],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{report_id}/checks/summary",
    response_model=FindingCheckSummaryOut,
    summary="核查覆盖情况（按判定与人工状态分组）",
)
def summarise_report_checks(db: DbSession, report_id: int = Path(ge=1)) -> FindingCheckSummaryOut:
    ResearchReportService(db).get(report_id)
    rows = _checks_of(db, report_id)
    by_verdict: dict[str, int] = {}
    by_human: dict[str, int] = {}
    cost = Decimal("0")
    calls = 0
    for row in rows:
        by_verdict[row.verdict] = by_verdict.get(row.verdict, 0) + 1
        by_human[row.human_review_status] = by_human.get(row.human_review_status, 0) + 1
        cost += row.cost_cny or Decimal("0")
        if row.model_call_id:
            calls += 1
    return FindingCheckSummaryOut(
        research_report_id=report_id,
        total=len(rows),
        by_verdict=by_verdict,
        by_human_review=by_human,
        model_cost_cny=cost,
        model_calls=calls,
        note=(
            "模型判定与人工复核是两条独立状态线；这里只有计数，判断依据看明细接口"
            if rows
            else "这份报告还没有核查记录（未跑语义核查或核查失败）"
        ),
    )


@router.post(
    "/{report_id}/checks/{check_id}/review",
    response_model=FindingCheckOut,
    summary="人工复核一条核查（只能人写）",
    description=(
        "把某条结论的人工复核状态改成 confirmed/rejected/needs_more_evidence，并留下依据。"
        "这条路径**不会**因为模型说了 supported 就自动 confirmed——必须有人真的点了。"
    ),
)
def review_report_check(
    db: DbSession,
    payload: FindingCheckReviewIn,
    report_id: int = Path(ge=1),
    check_id: int = Path(ge=1),
) -> FindingCheckOut:
    ResearchReportService(db).get(report_id)
    check = db.get(FindingCheck, check_id)
    if check is None or check.research_report_id != report_id:
        raise NotFoundError(
            f"核查记录 {check_id} 不存在于报告 {report_id}",
            details={"reason": "unknown_check"},
        )
    if payload.status == "confirmed" and not payload.note.strip():
        raise DomainValidationError(
            "确认一条结论必须写明依据（对照了哪份原文、哪个口径）",
            details={"reason": "note_required"},
        )
    try:
        updated = set_human_review(
            db, check_id=check_id, status_value=payload.status, note=payload.note
        )
    except ValueError as error:
        raise DomainValidationError(
            str(error), details={"reason": "bad_status", "allowed": sorted(HUMAN_REVIEW_STATUSES)}
        ) from error
    db.commit()

    # 撤回确认（confirmed → 其他）会让已经用这条结论建起来的视频任务失效：
    # 片子和产物都不删，但必须在任务目录里留下"不能再当成稿用"的标记。
    if payload.status != "confirmed":
        from backend.app.video.handoff import invalidate_jobs

        invalidated = invalidate_jobs(
            [check_id],
            reason=f"人工把核查记录 {check_id} 的确认撤回到 {payload.status}",
        )
        if invalidated:
            logger.warning(
                "核查记录 %s 的确认被撤回，%d 个视频任务已标记失效：%s",
                check_id,
                len(invalidated),
                "、".join(item["job_id"] for item in invalidated),
            )
    return FindingCheckOut.model_validate(updated)
