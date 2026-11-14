"""运营层路由：内容日历、平台数据回填、汇总。

所有端点都是 HUMAN_ONLY：排期与"发了没发"是人做的决定，agent 不许代劳。
系统也不会调用任何平台 API——这里只记录人填的事实。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.schemas.common import Page
from backend.app.schemas.operations import (
    CalendarAnalyticsOut,
    CalendarConsistencyOut,
    CalendarCreate,
    CalendarExportIn,
    CalendarOut,
    CalendarReadinessOut,
    CalendarUpdate,
    MetricIn,
    MetricOut,
    MetricsImportIn,
)
from backend.app.services.calendar_readiness import job_readiness
from backend.app.services.operations_service import OperationsService

router = APIRouter(
    prefix="/calendar",
    tags=["operations"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


def _consistency_of(entry, metric_count: int) -> CalendarConsistencyOut:
    """一条排期自己的账对不对得上——只报矛盾，不替人改。

    ``status``（进度）与 ``publish_state``（发布）是两条独立状态线，谁也不会自动
    跟着谁走，所以很容易出现"数据都回填了却忘了标已发布"这种账。
    """
    warnings: list[str] = []
    if metric_count and entry.publish_state != "published":
        warnings.append(
            f"有 {metric_count} 份平台数据快照，但这条还没标已发布："
            "平台汇总只算已发布的排期，这些数字不会计入"
        )
    if entry.publish_state == "published" and entry.status in ("planned", "scripted"):
        warnings.append(
            f"已标已发布，但进度还停在「{entry.status}」：两个字段不会自己同步，"
            "建议把进度也标到「已渲染」或「已导出」，免得回头对不上账"
        )
    return CalendarConsistencyOut(metric_count=metric_count, warnings=warnings)


def _to_out(entry, metric_count: int = 0) -> CalendarOut:
    """把排期行连"那片子在磁盘上的真实状态"一起返回。

    状态读的是 ``content/jobs/<job_id>/`` 里的文件，不是数据库字段——
    数据库只知道"打算发哪条"，不知道"片子渲染好了没有、结论还作不作数"。
    """
    payload = CalendarOut.model_validate(entry)
    readiness = CalendarReadinessOut(**job_readiness(entry.video_job_id))
    return payload.model_copy(
        update={
            "readiness": readiness,
            "consistency": _consistency_of(entry, metric_count),
        }
    )


@router.post("", response_model=CalendarOut, status_code=status.HTTP_201_CREATED, summary="新建排期")
def create_entry(payload: CalendarCreate, db: DbSession) -> CalendarOut:
    return _to_out(OperationsService(db).create(payload))


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
    counts = OperationsService(db).metric_counts([int(item.id) for item in items])
    return Page[CalendarOut](
        items=[_to_out(item, counts.get(int(item.id), 0)) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/analytics", response_model=list[CalendarAnalyticsOut], summary="按平台汇总")
def analytics(db: DbSession) -> list[CalendarAnalyticsOut]:
    return [CalendarAnalyticsOut(**row) for row in OperationsService(db).analytics()]


@router.get("/{entry_id}", response_model=CalendarOut, summary="排期详情")
def get_entry(db: DbSession, entry_id: int = Path(ge=1)) -> CalendarOut:
    service = OperationsService(db)
    entry = service.get(entry_id)
    counts = service.metric_counts([int(entry.id)])
    return _to_out(entry, counts.get(int(entry.id), 0))


@router.patch("/{entry_id}", response_model=CalendarOut, summary="改排期")
def update_entry(
    payload: CalendarUpdate, db: DbSession, entry_id: int = Path(ge=1)
) -> CalendarOut:
    """改状态/发布时间。标记为已发布必须带 `published_url`，否则 422。"""
    service = OperationsService(db)
    entry = service.update(entry_id, payload)
    counts = service.metric_counts([int(entry.id)])
    return _to_out(entry, counts.get(int(entry.id), 0))


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


@router.post(
    "/{entry_id}/export",
    summary="导出成文件并留记录（导出不是发布，发布仍要人工填链接）",
)
def export_entry(
    db: DbSession, payload: CalendarExportIn | None = None, entry_id: int = Path(ge=1)
) -> dict:
    from backend.app.services.export_service import ExportError, export_entry as run_export

    try:
        return run_export(db, entry_id, platforms=(payload.platforms if payload else None))
    except ExportError as error:
        if error.reason == "unknown_entry":
            raise NotFoundError(f"没有这条排期：{entry_id}", details={"reason": error.reason}) from error
        raise DomainValidationError(error.message, details={"reason": error.reason}) from error

