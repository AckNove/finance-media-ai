"""选题路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy import select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import ConflictError, DomainValidationError
from backend.app.database.session import get_session_factory
from backend.app.execution.runtime import TaskRuntime
from backend.app.models.enums import TagNamespace
from backend.app.models.execution import ResearchSubtask
from backend.app.models.taxonomy import TopicCollection, TopicTag
from backend.app.research.topic_suggestions import suggest_from_db
from backend.app.schemas.common import Page
from backend.app.schemas.topic import (
    TopicCollectionCreate,
    TopicCollectionOut,
    TopicColumnOut,
    TopicCreate,
    TopicFromSuggestionIn,
    TopicFromSuggestionOut,
    TopicIntakeSourceOut,
    TopicOut,
    TopicResearchTaskIn,
    TopicResearchTaskOut,
    TopicSuggestionOut,
    TopicSuggestionsOut,
    TopicTagCreate,
    TopicTagOut,
    TopicUpdate,
)
from backend.app.services.topic_intake import IntakeError, intake_suggestion
from backend.app.services.topic_research import (
    TopicResearchError,
    plan_for_topic,
    source_ids_from_description,
    start_research,
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
    tag_id: int | None = None,
    tag: str | None = None,
    tag_namespace: TagNamespace | None = None,
) -> Page[TopicOut]:
    """按标签过滤：`tag_id` 精确定位，或 `tag`(=名字)+可选 `tag_namespace`。

    `tag` 在多个命名空间下同名时报 422 并列出候选，不做"猜一个"的处理。
    """
    service = TopicService(db)
    resolved_tag_id = tag_id
    if resolved_tag_id is None and tag:
        resolved_tag_id = service.find_tag(name=tag, namespace=tag_namespace).id
    items, total = service.list(
        knowledge_domain=knowledge_domain,
        research_status=research_status,
        content_status=content_status,
        tag_id=resolved_tag_id,
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


@router.get(
    "/suggestions",
    response_model=TopicSuggestionsOut,
    summary="选题建议（规则推荐，不联网）",
)
def list_topic_suggestions(
    db: DbSession,
    window_days: int = Query(default=30, ge=1, le=365, description="只看最近多少天抓到的来源"),
    limit: int = Query(default=10, ge=1, le=50),
    min_score: int = Query(default=40, ge=0, le=200),
) -> TopicSuggestionsOut:
    """从**已经入库**的来源里挑候选题目，给出分数与理由。

    这里不联网、不调模型：数据就是 ``sources`` 表里抓到的那些，
    分数构成写在 ``reasons`` 里，采纳与否由人决定。
    """
    payload = suggest_from_db(
        db, window_days=window_days, limit=limit, min_score=min_score
    )
    return TopicSuggestionsOut.model_validate(payload)


@router.post(
    "/from-suggestion",
    response_model=TopicFromSuggestionOut,
    status_code=status.HTTP_201_CREATED,
    summary="把选题建议采纳为选题（带上来源与合集）",
)
def create_topic_from_suggestion(
    payload: TopicFromSuggestionIn, db: DbSession
) -> TopicFromSuggestionOut:
    """采纳一条建议：**来源由服务端回库读**，重复标题直接拒绝。

    这条路径存在的意义是别让「建议」在采纳那一刻丢掉来路：来源清单会写进选题描述，
    研究任务从它起跑时就能回原文核对。已经在库里的高度相似选题不会被重复创建。
    """
    try:
        result = intake_suggestion(
            db,
            title=payload.title,
            source_ids=payload.source_ids,
            column_id=payload.column_id,
            score=payload.score,
            reasons=payload.reasons,
            adopt_note=payload.note,
        )
    except IntakeError as error:
        if error.reason == "duplicate_topic":
            raise ConflictError(error.message, details={"reason": error.reason}) from error
        raise DomainValidationError(error.message, details={"reason": error.reason}) from error
    return TopicFromSuggestionOut(
        topic=TopicOut.model_validate(result.topic),
        sources=[TopicIntakeSourceOut(**item) for item in result.sources],
        collections=list(result.collections),
        description=result.description,
        note=result.note,
    )


@router.post(
    "/{topic_id}/research-task",
    response_model=TopicResearchTaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="从选题建研究任务",
)
def create_research_task_for_topic(
    payload: TopicResearchTaskIn, db: DbSession, topic_id: int = Path(ge=1)
) -> TopicResearchTaskOut:
    """把选题的标题与起点来源带进研究任务，不必到「研究任务」页重敲一遍。

    默认产线是 ``research_only``（先拿报告，要不要写稿再决定）；同一条选题下
    已有没结束的任务时返回 409，并指向那条任务。
    """
    topic = TopicService(db).get(topic_id)
    source_ids = source_ids_from_description(topic.description)
    plan = plan_for_topic(
        topic,
        source_ids=source_ids,
        pipeline=payload.pipeline,
        title=payload.title,
        objective=payload.objective,
        queries=payload.queries,
    )
    runtime = TaskRuntime(get_session_factory())
    try:
        result = start_research(
            db,
            runtime,
            topic,
            plan=plan,
            budget_max_calls=payload.budget_max_calls,
            budget_max_cost_cny=payload.budget_max_cost_cny,
            max_attempts=payload.max_attempts,
            start=payload.start,
        )
    except TopicResearchError as error:
        if error.reason == "task_in_progress":
            raise ConflictError(error.message, details={"reason": error.reason, **error.details}) from error
        raise DomainValidationError(
            error.message, details={"reason": error.reason, **error.details}
        ) from error

    subtasks = db.execute(
        select(ResearchSubtask).where(ResearchSubtask.research_task_id == result.task.id)
    ).scalars().all()
    note = plan.note
    if source_ids and not plan.source_ids:
        note += "；没从选题描述里认出源编号"
    return TopicResearchTaskOut(
        task_id=result.task.id,
        title=plan.title,
        objective=plan.objective,
        pipeline=plan.pipeline,
        queries=list(plan.queries),
        source_ids=list(plan.source_ids),
        subtask_count=len(subtasks),
        note=note,
    )


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
