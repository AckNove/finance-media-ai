"""选题路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Response, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.models.taxonomy import TopicCollection, TopicTag
from backend.app.schemas.common import Page
from backend.app.schemas.topic import (
    TopicCollectionCreate,
    TopicCollectionOut,
    TopicColumnOut,
    TopicCreate,
    TopicOut,
    TopicTagCreate,
    TopicTagOut,
    TopicUpdate,
)
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


def _collection_out(link: TopicCollection) -> TopicCollectionOut:
    column = link.topic_column
    return TopicCollectionOut(
        topic_id=link.topic_id,
        column_id=link.column_id,
        is_primary=link.is_primary,
        weight=link.weight,
        column_code=column.code if column is not None else "",
        column_name=column.name if column is not None else "",
    )


@router.get(
    "/{topic_id}/collections",
    response_model=list[TopicCollectionOut],
    summary="选题所在的合集",
)
def list_topic_collections(db: DbSession, topic_id: int = Path(ge=1)) -> list[TopicCollectionOut]:
    return [_collection_out(link) for link in TopicService(db).list_collections(topic_id)]


@router.post(
    "/{topic_id}/collections",
    response_model=TopicCollectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="把选题挂进一个合集",
)
def attach_topic_collection(
    payload: TopicCollectionCreate, db: DbSession, topic_id: int = Path(ge=1)
) -> TopicCollectionOut:
    link = TopicService(db).attach_collection(
        topic_id,
        column_id=payload.column_id,
        is_primary=payload.is_primary,
        weight=payload.weight,
    )
    return _collection_out(link)


@router.delete(
    "/{topic_id}/collections/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="把选题从合集里摘下来",
)
def detach_topic_collection(
    db: DbSession, topic_id: int = Path(ge=1), column_id: int = Path(ge=1)
) -> Response:
    TopicService(db).detach_collection(topic_id, column_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _tag_out(link: TopicTag) -> TopicTagOut:
    tag = link.tag
    return TopicTagOut(
        topic_id=link.topic_id,
        tag_id=link.tag_id,
        weight=link.weight,
        namespace=tag.namespace,
        name=tag.name,
        slug=tag.slug,
        usage_count=tag.usage_count,
    )


@router.get(
    "/{topic_id}/tags",
    response_model=list[TopicTagOut],
    summary="选题上的标签",
)
def list_topic_tags(db: DbSession, topic_id: int = Path(ge=1)) -> list[TopicTagOut]:
    return [_tag_out(link) for link in TopicService(db).list_tags(topic_id)]


@router.post(
    "/{topic_id}/tags",
    response_model=TopicTagOut,
    status_code=status.HTTP_201_CREATED,
    summary="给选题打标签（标签不存在则按名字新建）",
)
def attach_topic_tag(
    payload: TopicTagCreate, db: DbSession, topic_id: int = Path(ge=1)
) -> TopicTagOut:
    link = TopicService(db).attach_tag(
        topic_id,
        name=payload.name,
        namespace=payload.namespace,
        weight=payload.weight,
        create_missing=payload.create_missing,
    )
    return _tag_out(link)


@router.delete(
    "/{topic_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="摘掉选题上的标签",
)
def detach_topic_tag(
    db: DbSession, topic_id: int = Path(ge=1), tag_id: int = Path(ge=1)
) -> Response:
    TopicService(db).detach_tag(topic_id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
