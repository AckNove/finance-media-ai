"""资料来源与事实证据路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.schemas.common import Page
from backend.app.schemas.source import (
    EvidenceCreate,
    EvidenceDetailOut,
    EvidenceOut,
    SourceCreate,
    SourceOut,
)
from backend.app.services.source_service import EvidenceService, SourceService

# 资料来源与证据都只允许人工操作者写入：Agent 侧必须走执行层与 db_write 工具
sources_router = APIRouter(
    prefix="/sources",
    tags=["sources"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)
evidence_router = APIRouter(
    prefix="/evidence",
    tags=["evidence"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@sources_router.post(
    "",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="登记资料来源",
)
def create_source(payload: SourceCreate, db: DbSession) -> SourceOut:
    """登记一份原始资料。传入 raw_excerpt 时服务端会计算 content_hash 作为去重指纹。"""
    return SourceOut.model_validate(SourceService(db).create(payload))


@sources_router.get("", response_model=Page[SourceOut], summary="资料来源列表")
def list_sources(
    db: DbSession,
    page: Pagination,
    source_type: str | None = None,
    credibility_level: str | None = None,
) -> Page[SourceOut]:
    items, total = SourceService(db).list(
        source_type=source_type,
        credibility_level=credibility_level,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[SourceOut](
        items=[SourceOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@evidence_router.post(
    "",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
    summary="登记事实证据",
)
def create_evidence(payload: EvidenceCreate, db: DbSession) -> EvidenceOut:
    """证据必须挂在真实存在的 source 上，并且指向一个知识条目或研究报告。"""
    return EvidenceOut.model_validate(EvidenceService(db).create(payload))


@evidence_router.get(
    "",
    response_model=Page[EvidenceDetailOut],
    summary="按主张目标查询证据（附来源摘要）",
)
def list_evidence(
    db: DbSession,
    page: Pagination,
    knowledge_item_id: int | None = Query(default=None, ge=1),
    research_report_id: int | None = Query(default=None, ge=1),
) -> Page[EvidenceDetailOut]:
    service = EvidenceService(db)
    items, total = service.list_for_target(
        knowledge_item_id=knowledge_item_id,
        research_report_id=research_report_id,
        limit=page.limit,
        offset=page.offset,
    )
    details: list[EvidenceDetailOut] = []
    source_service = SourceService(db)
    for item in items:
        source = source_service.get(item.source_id)
        detail = EvidenceDetailOut.model_validate(item)
        detail.source_title = source.title
        detail.source_publisher = source.publisher
        detail.source_url = source.url
        details.append(detail)
    return Page[EvidenceDetailOut](
        items=details, total=total, limit=page.limit, offset=page.offset
    )
