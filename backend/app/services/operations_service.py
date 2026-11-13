"""运营服务：内容日历与平台数据回填。

三条纪律写在这里：

1. **不自动发布**。``publish_state`` 改成 ``published`` 必须有 ``published_url``
   与 ``published_at``，缺一个就 422 —— 不允许空口标记"发了"。
2. **数字只能来自人给的回填**。这里不做任何推测：没有快照就是没有数据，
   分析接口会如实返回 0 并给出 ``note``。
3. **同一条排期同一时刻只有一份快照**（由唯一约束兜底），重复回填同时间戳
   就会撞约束，不会悄悄覆盖历史。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import ConflictError, DomainValidationError, NotFoundError
from backend.app.models.operations import ContentCalendar, PlatformMetric
from backend.app.models.research import ResearchReport
from backend.app.models.script import Script
from backend.app.models.topic import Topic
from backend.app.schemas.operations import (
    CalendarCreate,
    CalendarUpdate,
    MetricIn,
)
from backend.app.services.base import BaseService


def _naive_utc(value: datetime) -> datetime:
    """统一成库里存的那种 naive UTC（带时区的先换算再剥掉 tzinfo）。"""

    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class OperationsService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    # ---- 内容日历 ----------------------------------------------------
    def create(self, payload: CalendarCreate) -> ContentCalendar:
        self._check_refs(payload.topic_id, payload.script_id, payload.research_report_id)
        entry = ContentCalendar(
            title=payload.title,
            platform=payload.platform,
            status=payload.status,
            scheduled_for=payload.scheduled_for,
            topic_id=payload.topic_id,
            script_id=payload.script_id,
            research_report_id=payload.research_report_id,
            video_job_id=payload.video_job_id,
            export_path=payload.export_path,
            notes=payload.notes,
        )
        self.db.add(entry)
        return self._commit(entry, "这条排期写不进去：可能撞了唯一约束")

    def get(self, entry_id: int) -> ContentCalendar:
        entry = self.db.get(ContentCalendar, entry_id)
        if entry is None:
            raise NotFoundError(f"排期 {entry_id} 不存在", details={"resource": "content_calendar"})
        return entry

    def list(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
        publish_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ContentCalendar], int]:
        stmt = select(ContentCalendar)
        count_stmt = select(func.count()).select_from(ContentCalendar)
        for column, value in (
            (ContentCalendar.platform, platform),
            (ContentCalendar.status, status),
            (ContentCalendar.publish_state, publish_state),
        ):
            if value:
                stmt = stmt.where(column == value)
                count_stmt = count_stmt.where(column == value)
        stmt = stmt.order_by(
            ContentCalendar.scheduled_for.is_(None), ContentCalendar.scheduled_for, ContentCalendar.id
        )
        stmt = stmt.limit(limit).offset(offset)
        items = list(self.db.execute(stmt).scalars())
        total = int(self.db.execute(count_stmt).scalar_one())
        return items, total

    def update(self, entry_id: int, payload: CalendarUpdate) -> ContentCalendar:
        entry = self.get(entry_id)
        data = payload.model_dump(exclude_unset=True)

        if "video_job_id" in data and data["video_job_id"]:
            job_id = str(data["video_job_id"]).strip()
            if any(ch in job_id for ch in ("/", "\\", "..")):
                raise DomainValidationError(
                    "video_job_id 只能是目录名，不能含 / \\ 或 ..",
                    details={"field": "video_job_id"},
                )
            data["video_job_id"] = job_id

        publish_state = data.get("publish_state", entry.publish_state)
        url = data.get("published_url", entry.published_url)
        if publish_state == "published":
            if not url:
                raise DomainValidationError(
                    "标记为已发布必须给 published_url：没发就是没发",
                    details={"field": "published_url"},
                )
            data.setdefault("published_at", datetime.now(UTC))
        if "publish_state" in data and data["publish_state"] != "published":
            # 撤回发布时保留 url（留痕），但把发布时间清空，避免"未发布却有发布时间"。
            data["published_at"] = None

        for key, value in data.items():
            setattr(entry, key, value)
        return self._commit(entry, "这条排期改不进去：可能撞了唯一约束")

    # ---- 平台数据 ----------------------------------------------------
    def add_metric(self, entry_id: int, payload: MetricIn) -> PlatformMetric:
        entry = self.get(entry_id)
        exists = self.db.execute(
            select(PlatformMetric.id).where(
                PlatformMetric.calendar_id == entry.id,
                PlatformMetric.captured_at == payload.captured_at,
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise ConflictError(
                "这条排期在这个时刻已经有一份快照了：换个时间戳，或先删掉旧的（历史不覆盖）",
                details={"field": "captured_at", "existing_id": int(exists)},
            )
        metric = PlatformMetric(
            calendar_id=entry.id,
            platform=entry.platform,
            captured_at=payload.captured_at,
            views=payload.views,
            likes=payload.likes,
            comments=payload.comments,
            shares=payload.shares,
            followers_delta=payload.followers_delta,
            source=payload.source,
            raw=payload.raw,
        )
        self.db.add(metric)
        return self._commit(metric, "这份快照写不进去：可能撞了唯一约束")

    def list_metrics(self, entry_id: int) -> list[PlatformMetric]:
        self.get(entry_id)
        stmt = (
            select(PlatformMetric)
            .where(PlatformMetric.calendar_id == entry_id)
            .order_by(PlatformMetric.captured_at)
        )
        return list(self.db.execute(stmt).scalars())

    def import_metrics(self, entry_id: int, csv_text: str) -> dict:
        """批量导入平台导出的 CSV：能写的写，撞时间戳的跳过，坏行带行号报出来。"""

        from backend.app.services.metrics_import import parse_metrics_csv

        entry = self.get(entry_id)
        parsed = parse_metrics_csv(csv_text, platform=str(entry.platform))
        # 库里 DateTime 是 naive 的，解析出来是带时区的 UTC——比较前要统一，
        # 否则同一个时刻永远"不相等"，重复导入会撞唯一约束而不是被跳过。
        existing = {
            _naive_utc(row.captured_at)
            for row in self.db.execute(
                select(PlatformMetric).where(PlatformMetric.calendar_id == entry.id)
            ).scalars()
        }
        written = []
        for item in parsed.imported:
            stamp = _naive_utc(item.captured_at)
            if stamp in existing:
                parsed.skipped.append(
                    {
                        "line": item.line,
                        "reason": "这个时间点已经有快照了（历史不覆盖）",
                        "captured_at": item.captured_at.isoformat(),
                    }
                )
                continue
            self.db.add(
                PlatformMetric(
                    calendar_id=entry.id,
                    platform=entry.platform,
                    captured_at=stamp,
                    views=item.views,
                    likes=item.likes,
                    comments=item.comments,
                    shares=item.shares,
                    followers_delta=item.followers_delta,
                    source="csv",
                )
            )
            existing.add(stamp)
            written.append(item)
        parsed.imported = written
        self.db.commit()
        return parsed.as_dict()

    def analytics(self) -> list[dict]:
        """按平台汇总：每条取**最新**快照的绝对值，不给"累计"这种假数。"""
        rows: list[dict] = []
        platforms = list(self.db.execute(select(ContentCalendar.platform).distinct()).scalars())
        for platform in sorted({str(p) for p in platforms}):
            entries = int(
                self.db.execute(
                    select(func.count())
                    .select_from(ContentCalendar)
                    .where(ContentCalendar.platform == platform)
                ).scalar_one()
            )
            published = int(
                self.db.execute(
                    select(func.count())
                    .select_from(ContentCalendar)
                    .where(
                        ContentCalendar.platform == platform,
                        ContentCalendar.publish_state == "published",
                    )
                ).scalar_one()
            )
            snapshots = int(
                self.db.execute(
                    select(func.count())
                    .select_from(PlatformMetric)
                    .where(PlatformMetric.platform == platform)
                ).scalar_one()
            )
            latest = self._latest_snapshot(platform)
            rows.append(
                {
                    "platform": platform,
                    "entries": entries,
                    "snapshots": snapshots,
                    "latest_views": latest.views if latest else 0,
                    "latest_likes": latest.likes if latest else 0,
                    "latest_comments": latest.comments if latest else 0,
                    "latest_shares": latest.shares if latest else 0,
                    "published": published,
                    "note": (
                        "还没有任何数据回填：这里显示 0 是因为确实没数据，不是没人看"
                        if latest is None
                        else f"取最新一次回填（{latest.captured_at:%Y-%m-%d %H:%M}）的绝对值"
                    ),
                }
            )
        return rows

    def _latest_snapshot(self, platform: str) -> PlatformMetric | None:
        stmt = (
            select(PlatformMetric)
            .where(PlatformMetric.platform == platform)
            .order_by(PlatformMetric.captured_at.desc(), PlatformMetric.id.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # ---- 内部 --------------------------------------------------------
    def _check_refs(
        self,
        topic_id: int | None,
        script_id: int | None,
        research_report_id: int | None,
    ) -> None:
        for model, value, field in (
            (Topic, topic_id, "topic_id"),
            (Script, script_id, "script_id"),
            (ResearchReport, research_report_id, "research_report_id"),
        ):
            if value is None:
                continue
            if self.db.get(model, value) is None:
                raise DomainValidationError(
                    f"{field}={value} 不存在",
                    details={"field": field},
                )


__all__ = ["OperationsService"]
