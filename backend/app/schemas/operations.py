"""运营层（内容日历 / 平台数据）接口模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CALENDAR_STATUSES = (
    "planned",
    "scripted",
    "rendered",
    "exported",
    "archived",
    "cancelled",
)
PUBLISH_STATES = ("not_published", "published", "removed")
PLATFORMS = (
    "douyin",
    "xiaohongshu",
    "wechat",
    "bilibili",
    "video_account",
    "youtube",
    "other",
)


class CalendarCreate(BaseModel):
    """新建排期。``title`` 至少要两个字，否则列表里全是没法辨认的短标题。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    platform: Literal[
        "douyin", "xiaohongshu", "wechat", "bilibili", "video_account", "youtube", "other"
    ] = "douyin"
    status: Literal[
        "planned", "scripted", "rendered", "exported", "archived", "cancelled"
    ] = "planned"
    scheduled_for: datetime | None = None
    topic_id: int | None = None
    script_id: int | None = None
    research_report_id: int | None = None
    video_job_id: str | None = Field(default=None, max_length=80)
    export_path: str | None = Field(default=None, max_length=400)
    notes: str = ""

    @field_validator("video_job_id")
    @classmethod
    def _check_job_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        # 目录名，不允许穿越与分隔符（与 job_paths 的白名单口径一致）
        if any(ch in cleaned for ch in ("/", "\\", "..")):
            raise ValueError("video_job_id 只能是目录名，不能含 / \\ 或 ..")
        return cleaned


class CalendarUpdate(BaseModel):
    """改排期。只给要改的字段；``published`` 必须有 ``published_url``。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=2, max_length=200)
    platform: Literal[
        "douyin", "xiaohongshu", "wechat", "bilibili", "video_account", "youtube", "other"
    ] | None = None
    status: Literal[
        "planned", "scripted", "rendered", "exported", "archived", "cancelled"
    ] | None = None
    scheduled_for: datetime | None = None
    video_job_id: str | None = Field(default=None, max_length=80)
    export_path: str | None = Field(default=None, max_length=400)
    publish_state: Literal["not_published", "published", "removed"] | None = None
    published_url: str | None = Field(default=None, max_length=600)
    published_at: datetime | None = None
    notes: str | None = None


class MetricIn(BaseModel):
    """一次数据回填。``captured_at`` 必填——不写时间就没法算增长。"""

    model_config = ConfigDict(extra="forbid")

    captured_at: datetime
    views: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    followers_delta: int = 0
    source: str = Field(default="manual", max_length=40)
    raw: dict | None = None


class MetricsImportIn(BaseModel):
    """把平台后台导出的 CSV 直接粘进来批量回填。

    ``csv_text`` 上限 200,000 字符（约几千行），超过就该拆分而不是硬塞。
    """

    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(min_length=1, max_length=200_000)


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calendar_id: int
    platform: str
    captured_at: datetime
    views: int
    likes: int
    comments: int
    shares: int
    followers_delta: int
    source: str
    raw: dict | None = None
    created_at: datetime


class CalendarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    platform: str
    status: str
    scheduled_for: datetime | None
    topic_id: int | None
    script_id: int | None
    research_report_id: int | None
    video_job_id: str | None
    export_path: str | None
    publish_state: str
    published_url: str | None
    published_at: datetime | None
    notes: str
    created_at: datetime
    updated_at: datetime


class CalendarAnalyticsOut(BaseModel):
    """按平台汇总。数字全部来自 ``platform_metrics`` 的真实回填。"""

    platform: str
    entries: int
    snapshots: int
    latest_views: int
    latest_likes: int
    latest_comments: int
    latest_shares: int
    published: int
    note: str


class CalendarExportIn(BaseModel):
    """导出时可选指定平台；不填就按一稿三版的三个平台全导。"""

    model_config = ConfigDict(extra="forbid")

    platforms: list[Literal["douyin", "xiaohongshu", "wechat"]] | None = None


__all__ = [
    "CALENDAR_STATUSES",
    "PLATFORMS",
    "PUBLISH_STATES",
    "CalendarAnalyticsOut",
    "CalendarCreate",
    "CalendarExportIn",
    "CalendarOut",
    "CalendarUpdate",
    "MetricIn",
    "MetricOut",
]
