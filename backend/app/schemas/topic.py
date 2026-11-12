"""选题相关 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from backend.app.models.enums import AudienceLevel, ContentStatus, KnowledgeDomain, ResearchStatus
from backend.app.schemas.common import ORMBase


class TopicColumnOut(ORMBase):
    id: int
    code: str
    name: str
    description: str | None = None
    display_order: int


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    column_id: int | None = None
    knowledge_domain: KnowledgeDomain = KnowledgeDomain.GENERAL
    audience_level: AudienceLevel = AudienceLevel.BEGINNER
    priority: int = Field(default=3, ge=1, le=5)
    football_analogy_required: bool = False
    estimated_duration: int | None = Field(
        default=None, ge=0, description="预计视频时长（秒）"
    )
    slug: str | None = Field(default=None, max_length=120, description="留空则自动生成")

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title 不能为空白字符串")
        return value.strip()


class TopicUpdate(BaseModel):
    """部分更新：只允许改动流程状态与优先级，不允许改 id/slug。"""

    description: str | None = Field(default=None, max_length=5000)
    priority: int | None = Field(default=None, ge=1, le=5)
    research_status: ResearchStatus | None = None
    content_status: ContentStatus | None = None
    football_analogy_required: bool | None = None
    estimated_duration: int | None = Field(default=None, ge=0)


class TopicOut(ORMBase):
    id: int
    slug: str
    title: str
    description: str | None = None
    column_id: int | None = None
    knowledge_domain: KnowledgeDomain
    audience_level: AudienceLevel
    priority: int
    football_analogy_required: bool
    research_status: ResearchStatus
    content_status: ContentStatus
    estimated_duration: int | None = None
    created_at: datetime
    updated_at: datetime


class TopicCollectionCreate(BaseModel):
    """把一个选题挂进某个合集。一个选题最多一个主合集。"""

    column_id: int = Field(ge=1)
    is_primary: bool = False
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class TopicCollectionOut(ORMBase):
    topic_id: int
    column_id: int
    is_primary: bool
    weight: float
    column_code: str = ""
    column_name: str = ""
