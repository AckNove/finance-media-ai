"""知识条目与知识关联路由。"""

from __future__ import annotations

from fastapi import APIRouter, Path, Query, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination
from backend.app.schemas.common import Page
from backend.app.schemas.knowledge import (
    KnowledgeItemCreate,
    KnowledgeItemOut,
    KnowledgeItemUpdate,
    KnowledgeRelationCreate,
    KnowledgeRelationOut,
    KnowledgeSearchResult,
)
from backend.app.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"], responses=COMMON_RESPONSES)


@router.post(
    "",
    response_model=KnowledgeItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建知识条目",
)
def create_knowledge_item(payload: KnowledgeItemCreate, db: DbSession) -> KnowledgeItemOut:
    item = KnowledgeService(db).create(payload)
    return KnowledgeItemOut.model_validate(item)


@router.get("", response_model=Page[KnowledgeItemOut], summary="知识条目列表")
def list_knowledge_items(
    db: DbSession,
    page: Pagination,
    knowledge_domain: str | None = None,
    verification_status: str | None = None,
) -> Page[KnowledgeItemOut]:
    items, total = KnowledgeService(db).list(
        knowledge_domain=knowledge_domain,
        verification_status=verification_status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[KnowledgeItemOut](
        items=[KnowledgeItemOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/search",
    response_model=KnowledgeSearchResult,
    summary="知识检索（关键词多字段匹配）",
)
def search_knowledge(
    db: DbSession,
    q: str = Query(min_length=1, max_length=200, description="检索关键词"),
    knowledge_domain: str | None = None,
    verification_status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> KnowledgeSearchResult:
    """按关键词匹配标题/定义/机制/风险结构等字段，并返回命中字段与权重分数。

    第一阶段为 SQL LIKE 匹配，不是语义检索；不夸大检索能力。
    """
    return KnowledgeService(db).search(
        q,
        knowledge_domain=knowledge_domain,
        verification_status=verification_status,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/relations",
    response_model=KnowledgeRelationOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建知识关联",
)
def create_knowledge_relation(
    payload: KnowledgeRelationCreate, db: DbSession
) -> KnowledgeRelationOut:
    relation = KnowledgeService(db).create_relation(payload)
    return KnowledgeRelationOut.model_validate(relation)


@router.get(
    "/{item_id}/relations",
    response_model=list[KnowledgeRelationOut],
    summary="知识条目的关联列表",
)
def list_knowledge_relations(db: DbSession, item_id: int = Path(ge=1)) -> list[KnowledgeRelationOut]:
    relations = KnowledgeService(db).list_relations(item_id)
    return [KnowledgeRelationOut.model_validate(relation) for relation in relations]


@router.get("/{item_id}", response_model=KnowledgeItemOut, summary="知识条目详情")
def get_knowledge_item(db: DbSession, item_id: int = Path(ge=1)) -> KnowledgeItemOut:
    return KnowledgeItemOut.model_validate(KnowledgeService(db).get(item_id))


@router.patch("/{item_id}", response_model=KnowledgeItemOut, summary="更新知识条目")
def update_knowledge_item(
    payload: KnowledgeItemUpdate, db: DbSession, item_id: int = Path(ge=1)
) -> KnowledgeItemOut:
    return KnowledgeItemOut.model_validate(KnowledgeService(db).update(item_id, payload))
