"""视频脚本路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.content import PLATFORMS, adapt_payload
from backend.app.core.caller import CallerKind
from backend.app.models.enums import ScriptStatus
from backend.app.schemas.common import Page
from backend.app.schemas.script import (
    AdaptedVariantOut,
    ScriptAdaptIn,
    ScriptAdaptOut,
    ScriptCreate,
    ScriptOut,
    ScriptStatusUpdate,
)
from backend.app.services.script_service import ScriptService

router = APIRouter(
    prefix="/scripts",
    tags=["scripts"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@router.post(
    "",
    response_model=ScriptOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建脚本（版本号按选题+模板自增）",
)
def create_script(payload: ScriptCreate, db: DbSession) -> ScriptOut:
    script = ScriptService(db).create(
        topic_id=payload.topic_id,
        title=payload.title,
        body=payload.body,
        research_report_id=payload.research_report_id,
        template=payload.template,
        hook=payload.hook,
        segments=payload.segments,
        disclaimer=payload.disclaimer,
        estimated_duration=payload.estimated_duration,
    )
    return ScriptOut.model_validate(script)


@router.get("", response_model=Page[ScriptOut], summary="脚本列表")
def list_scripts(
    db: DbSession,
    page: Pagination,
    topic_id: int | None = None,
    status: ScriptStatus | None = None,
) -> Page[ScriptOut]:
    items, total = ScriptService(db).list(
        topic_id=topic_id, status=status, limit=page.limit, offset=page.offset
    )
    return Page[ScriptOut](
        items=[ScriptOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{script_id}", response_model=ScriptOut, summary="脚本详情")
def get_script(db: DbSession, script_id: int = Path(ge=1)) -> ScriptOut:
    return ScriptOut.model_validate(ScriptService(db).get(script_id))


@router.patch("/{script_id}/status", response_model=ScriptOut, summary="迁移脚本状态")
def update_script_status(
    payload: ScriptStatusUpdate, db: DbSession, script_id: int = Path(ge=1)
) -> ScriptOut:
    script = ScriptService(db).set_status(script_id, payload.status, actor=payload.actor)
    return ScriptOut.model_validate(script)

@router.post(
    "/{script_id}/adapt",
    response_model=ScriptAdaptOut,
    summary="一稿三版（抖音 / 小红书 / 公众号）",
)
def adapt_script(
    db: DbSession,
    payload: ScriptAdaptIn | None = None,
    script_id: int = Path(ge=1),
) -> ScriptAdaptOut:
    """把同一份脚本改成三个平台的成稿：只调长度与排版，**不改事实、不编标签**。

    被截断、凑不够标签、出现导流词等情况都会写进每个平台的 ``warnings``。
    """
    script = ScriptService(db).get(script_id)
    body = payload or ScriptAdaptIn()
    platforms = tuple(body.platforms) if body.platforms else PLATFORMS
    envelope = adapt_payload(
        title=str(script.title or ""),
        body=str(script.body or ""),
        tags=tuple(body.tags or ()),
        platforms=platforms,
    )
    return ScriptAdaptOut(
        script_id=int(script.id),
        platforms=envelope["platforms"],
        variants=[AdaptedVariantOut.model_validate(item) for item in envelope["variants"]],
        note=envelope["note"],
    )
