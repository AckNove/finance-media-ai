"""审核流水路由。

审核是"人给结论"，所以整组端点只接受人类身份；agent 不能自己审自己。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.models.enums import ReviewTargetType, ReviewVerdict
from backend.app.schemas.common import Page
from backend.app.schemas.review import ReviewCreate, ReviewOut
from backend.app.services.review_service import ReviewService

router = APIRouter(
    prefix="/reviews",
    tags=["reviews"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@router.post(
    "",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="记一次审核（结论回写到目标对象）",
)
def create_review(payload: ReviewCreate, db: DbSession) -> ReviewOut:
    record = ReviewService(db).create(
        target_type=payload.target_type,
        target_id=payload.target_id,
        verdict=payload.verdict,
        reviewer_name=payload.reviewer_name,
        reviewer_type=payload.reviewer_type,
        stage=payload.stage,
        comment=payload.comment,
        findings=payload.findings,
        checklist=payload.checklist,
    )
    return ReviewOut.model_validate(record)


@router.get("", response_model=Page[ReviewOut], summary="审核流水")
def list_reviews(
    db: DbSession,
    page: Pagination,
    target_type: ReviewTargetType | None = None,
    target_id: int | None = None,
    verdict: ReviewVerdict | None = None,
) -> Page[ReviewOut]:
    items, total = ReviewService(db).list(
        target_type=target_type,
        target_id=target_id,
        verdict=verdict,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[ReviewOut](
        items=[ReviewOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{review_id}", response_model=ReviewOut, summary="审核记录详情")
def get_review(db: DbSession, review_id: int = Path(ge=1)) -> ReviewOut:
    return ReviewOut.model_validate(ReviewService(db).get(review_id))
