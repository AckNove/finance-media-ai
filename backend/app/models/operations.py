"""运营层（V6.0 K 段）：内容日历、导出与发布记录、账号数据回填。

这里只做"记录事实"，不做"自动发布"：系统不替人去平台上按发布键。
每个发布记录都要求人工填 URL 与时间——因为没发就是没发。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import CalendarStatus, Platform, PublishState


class ContentCalendar(Base, TimestampMixin):
    """一条排期：选题/脚本/报告 → 某个平台的一个档期。"""

    __tablename__ = "content_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    script_id: Mapped[int | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[Platform] = mapped_column(
        String(40), default=Platform.DOUYIN.value, nullable=False, index=True
    )
    status: Mapped[CalendarStatus] = mapped_column(
        String(40), default=CalendarStatus.PLANNED.value, nullable=False, index=True
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    video_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    export_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    publish_state: Mapped[PublishState] = mapped_column(
        String(40), default=PublishState.NOT_PUBLISHED.value, nullable=False, index=True
    )
    published_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    metrics: Mapped[list["PlatformMetric"]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("length(title) >= 2", name="title_len"),
        CheckConstraint(
            "publish_state != 'published' OR published_url IS NOT NULL",
            name="published_needs_url",
        ),
        Index("ix_content_calendar_platform_status", "platform", "status"),
    )


class PlatformMetric(Base, TimestampMixin):
    """一次数据回填（手工填或从导出文件导入），一条记录一次快照。"""

    __tablename__ = "platform_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("content_calendar.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    followers_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    calendar: Mapped[ContentCalendar] = relationship(back_populates="metrics")

    __table_args__ = (
        CheckConstraint("views >= 0 AND likes >= 0", name="metrics_non_negative"),
        CheckConstraint("comments >= 0 AND shares >= 0", name="metrics_engagement"),
        UniqueConstraint("calendar_id", "captured_at", name="uq_platform_metric_snapshot"),
    )


__all__ = ["ContentCalendar", "PlatformMetric"]
