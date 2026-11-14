"""选题相关 Schema。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.enums import (
    AudienceLevel,
    ContentStatus,
    KnowledgeDomain,
    ResearchStatus,
    TagNamespace,
)
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


class TopicTagCreate(BaseModel):
    """给选题打一个标签。标签不存在时按名字新建（`create_missing=False` 则报 422）。"""

    name: str = Field(min_length=1, max_length=64)
    namespace: TagNamespace = TagNamespace.OTHER
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    create_missing: bool = True


class TopicTagOut(ORMBase):
    """标签挂在选题上的样子：带上标签自身的命名空间与名称，省得前端再查一次。"""

    topic_id: int
    tag_id: int
    weight: float
    namespace: TagNamespace
    name: str
    slug: str
    usage_count: int = 0


class TopicSuggestionOut(BaseModel):
    """一条选题建议（来自已入库来源的规则推荐）。

    ``reasons`` 是给人看的分数构成——**采纳与否由人决定**，
    系统不会自动把建议变成研究任务。
    """

    title: str
    score: int
    reasons: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)
    tier: str
    tier_label: str
    freshness: str
    freshness_label: str
    age_days: int | None = None
    corroborated: bool = False


class TopicSuggestionsOut(BaseModel):
    """一次选题建议的结果：扫描了多少来源、按什么窗口、以及建议列表。"""

    generated_at: str
    window_days: int
    min_score: int
    scanned: int
    items: list[TopicSuggestionOut] = Field(default_factory=list)
    note: str = ""


class TopicFromSuggestionIn(BaseModel):
    """采纳一条建议：客户端只交来源 id 与「当时页面上显示的分数/理由」。

    来源的标题、发布方、链接一律由服务端按 id 回库读——页面传来的文本不可信
    （它可能是旧的，也可能被改过）。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    source_ids: list[int] = Field(default_factory=list, max_length=40)
    column_id: int | None = Field(default=None, ge=1)
    score: int | None = Field(default=None, ge=0, le=1000)
    reasons: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=500)


class TopicIntakeSourceOut(BaseModel):
    """采纳时带进选题的一条来源（服务端回库读到的真实内容）。"""

    index: int
    id: int
    title: str
    publisher: str = ""
    url: str = ""
    published_at: str = ""


class TopicFromSuggestionOut(BaseModel):
    topic: TopicOut
    sources: list[TopicIntakeSourceOut] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    description: str = ""
    note: str = ""


class TopicResearchTaskIn(BaseModel):
    """从选题建研究任务：默认只做研究（不逼着写脚本），标题与目标可覆盖。"""

    model_config = ConfigDict(extra="forbid")

    pipeline: str = Field(default="research_only", max_length=64)
    title: str | None = Field(default=None, max_length=200)
    objective: str = Field(default="", max_length=2000)
    queries: list[str] = Field(default_factory=list, max_length=20)
    budget_max_calls: int | None = Field(default=None, ge=1)
    budget_max_cost_cny: Decimal | None = Field(default=None, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    start: bool = True


class TopicResearchTaskOut(BaseModel):
    task_id: int
    title: str
    objective: str
    pipeline: str
    queries: list[str] = Field(default_factory=list)
    source_ids: list[int] = Field(default_factory=list)
    subtask_count: int = 0
    note: str = ""
