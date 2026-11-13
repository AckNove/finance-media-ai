"""运营层路由：内容日历、平台数据回填、汇总。

所有端点都是 HUMAN_ONLY：排期与"发了没发"是人做的决定，agent 不许代劳。
系统也不会调用任何平台 API——这里只记录人填的事实。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import DomainValidationError
from backend.app.schemas.common import Page
from backend.app.schemas.operations import (
    CalendarAnalyticsOut,
    CalendarCreate,
    CalendarOut,
    CalendarUpdate,
    MetricIn,
    MetricOut,
    MetricsImportIn,
)
from backend.app.services.operations_service import OperationsService

router = APIRouter(
    prefix="/calendar",
    tags=["operations"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@router.post("", response_model=CalendarOut, status_code=status.HTTP_201_CREATED, summary="新建排期")
def create_entry(payload: CalendarCreate, db: DbSession) -> CalendarOut:
    return CalendarOut.model_validate(OperationsService(db).create(payload))


@router.get("", response_model=Page[CalendarOut], summary="排期列表")
def list_entries(
    db: DbSession,
    page: Pagination,
    platform: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    publish_state: str | None = None,
) -> Page[CalendarOut]:
    items, total = OperationsService(db).list(
        platform=platform,
        status=status_filter,
        publish_state=publish_state,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[CalendarOut](
        items=[CalendarOut.model_validate(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/analytics", response_model=list[CalendarAnalyticsOut], summary="按平台汇总")
def analytics(db: DbSession) -> list[CalendarAnalyticsOut]:
    return [CalendarAnalyticsOut(**row) for row in OperationsService(db).analytics()]


@router.get("/{entry_id}", response_model=CalendarOut, summary="排期详情")
def get_entry(db: DbSession, entry_id: int = Path(ge=1)) -> CalendarOut:
    return CalendarOut.model_validate(OperationsService(db).get(entry_id))


@router.patch("/{entry_id}", response_model=CalendarOut, summary="改排期")
def update_entry(
    payload: CalendarUpdate, db: DbSession, entry_id: int = Path(ge=1)
) -> CalendarOut:
    """改状态/发布时间。标记为已发布必须带 `published_url`，否则 422。"""
    return CalendarOut.model_validate(OperationsService(db).update(entry_id, payload))


@router.post(
    "/{entry_id}/metrics",
    response_model=MetricOut,
    status_code=status.HTTP_201_CREATED,
    summary="回填一次平台数据",
)
def add_metric(payload: MetricIn, db: DbSession, entry_id: int = Path(ge=1)) -> MetricOut:
    return MetricOut.model_validate(OperationsService(db).add_metric(entry_id, payload))


@router.get("/{entry_id}/metrics", response_model=list[MetricOut], summary="一次排期的所有快照")
def list_metrics(db: DbSession, entry_id: int = Path(ge=1)) -> list[MetricOut]:
    return [MetricOut.model_validate(item) for item in OperationsService(db).list_metrics(entry_id)]


@router.post(
    "/{entry_id}/metrics/import",
    summary="批量导入平台 CSV（撞时间戳的跳过，坏行带行号报错）",
)
def import_metrics(
    payload: MetricsImportIn, db: DbSession, entry_id: int = Path(ge=1)
) -> dict:
    from backend.app.services.metrics_import import CsvImportError

    try:
        return OperationsService(db).import_metrics(entry_id, payload.csv_text)
    except CsvImportError as error:
        raise DomainValidationError(
            f"这份 CSV 用不了：{error.message}",
            details={"reason": error.reason},
        ) from error
