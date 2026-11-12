"""视频脚本 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.app.models.enums import ScriptStatus, VideoTemplate
from backend.app.schemas.common import ORMBase


class ScriptCreate(BaseModel):
    topic_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    research_report_id: int | None = Field(default=None, ge=1)
    template: VideoTemplate = VideoTemplate.FINANCIAL_MECHANISM
    hook: str | None = None
    segments: list[dict[str, Any]] | None = None
    disclaimer: str | None = None
    estimated_duration: int | None = Field(default=None, ge=0)

    @field_validator("body")
    @classmethod
    def body_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body 不能为空白字符串")
        return value


class ScriptStatusUpdate(BaseModel):
    """只允许改状态；正文改动应当新开一版（版本号自增，历史可追溯）。"""

    status: ScriptStatus
    actor: str = Field(default="human", max_length=64)


class ScriptOut(ORMBase):
    id: int
    topic_id: int
    research_report_id: int | None = None
    title: str
    template: VideoTemplate
    version: int
    status: ScriptStatus
    hook: str | None = None
    body: str
    segments: list[dict[str, Any]] | None = None
    disclaimer: str | None = None
    word_count: int | None = None
    estimated_duration: int | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime
    updated_at: datetime
