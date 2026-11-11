"""选题路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.schemas.common import Page
from backend.app.schemas.topic import TopicColumnOut, TopicCreate, TopicOut, TopicUpdate
from backend.app.services.topic_service import TopicService

router = APIRouter(
    prefix="/topics",
    tags=["topics"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@router.post(
    "",
    response_model=TopicOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建选题",
)
def create_topic(payload: TopicCreate, db: DbSession) -> TopicOut:
    topic = TopicService(db).create(payload)
    return TopicOut.model_validate(topic)


@router.get("", response_model=Page[TopicOut], summary="选题列表")
def list_topics(
    db: DbSession,
    page: Pagination,
    knowledge_domain: str | None = None,
    research_status: str | None = None,
    content_status: str | None = None,
) -> Page[TopicOut]:
    items, total = TopicService(db).list(
        knowledge_domain=knowledge_domain,
        research_status=research_status,
        content_status=content_status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[TopicOut](
        items=[TopicOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/columns", response_model=list[TopicColumnOut], summary="栏目列表")
def list_columns(db: DbSession) -> list[TopicColumnOut]:
    return [TopicColumnOut.model_validate(column) for column in TopicService(db).list_columns()]


@router.get("/{topic_id}", response_model=TopicOut, summary="选题详情")
def get_topic(db: DbSession, topic_id: int = Path(ge=1)) -> TopicOut:
    return TopicOut.model_validate(TopicService(db).get(topic_id))


@router.patch("/{topic_id}", response_model=TopicOut, summary="更新选题流程状态")
def update_topic(payload: TopicUpdate, db: DbSession, topic_id: int = Path(ge=1)) -> TopicOut:
    return TopicOut.model_validate(TopicService(db).update(topic_id, payload))
