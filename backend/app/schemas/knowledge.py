"""知识条目与知识关联 Schema。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from backend.app.models.enums import KnowledgeDomain, RelationType, VerificationStatus
from backend.app.schemas.common import ORMBase


class KnowledgeItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    knowledge_domain: KnowledgeDomain = KnowledgeDomain.GENERAL
    definition: str | None = None
    mechanism: str | None = None
    participants: str | None = None
    profit_model: str | None = None
    pricing_logic: str | None = None
    risk_structure: str | None = None
    football_analogy: str | None = None
    analogy_limitations: str | None = None

    # 数据口径：记录"这句话在什么范围、什么时点成立"
    jurisdiction: str = Field(default="global", max_length=64)
    as_of_date: date | None = None
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=32)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    slug: str | None = Field(default=None, max_length=120, description="留空则自动生成")

    @model_validator(mode="after")
    def analogy_pair_required(self) -> KnowledgeItemCreate:
        """足球类比必须同时给出边界说明：只有类比没有边界属于误导性内容。"""
        has_analogy = bool(self.football_analogy and self.football_analogy.strip())
        has_limits = bool(self.analogy_limitations and self.analogy_limitations.strip())
        if has_analogy != has_limits:
            raise ValueError("football_analogy 与 analogy_limitations 必须成对提供")
        return self

    @model_validator(mode="after")
    def title_not_blank(self) -> KnowledgeItemCreate:
        if not self.title.strip():
            raise ValueError("title 不能为空白字符串")
        return self


class KnowledgeItemUpdate(BaseModel):
    definition: str | None = None
    mechanism: str | None = None
    participants: str | None = None
    profit_model: str | None = None
    pricing_logic: str | None = None
    risk_structure: str | None = None
    football_analogy: str | None = None
    analogy_limitations: str | None = None
    jurisdiction: str | None = Field(default=None, max_length=64)
    as_of_date: date | None = None
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=32)
    verification_status: VerificationStatus | None = None


class KnowledgeItemOut(ORMBase):
    id: int
    slug: str
    title: str
    knowledge_domain: KnowledgeDomain
    definition: str | None = None
    mechanism: str | None = None
    participants: str | None = None
    profit_model: str | None = None
    pricing_logic: str | None = None
    risk_structure: str | None = None
    football_analogy: str | None = None
    analogy_limitations: str | None = None
    jurisdiction: str
    as_of_date: date | None = None
    currency: str | None = None
    unit: str | None = None
    verification_status: VerificationStatus
    last_verified_at: datetime | None = None
    version: int
    is_current: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeMatch(BaseModel):
    """检索命中的知识条目：附带命中了哪些字段，便于人工复核检索质量。"""

    item: KnowledgeItemOut
    matched_fields: list[str]
    score: float


class KnowledgeSearchResult(BaseModel):
    query: str
    items: list[KnowledgeMatch]
    total: int
    limit: int
    offset: int


class KnowledgeRelationCreate(BaseModel):
    source_item_id: int
    target_item_id: int
    relation_type: RelationType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def no_self_relation(self) -> KnowledgeRelationCreate:
        if self.source_item_id == self.target_item_id:
            raise ValueError("知识关联不允许自环（source_item_id 不能等于 target_item_id）")
        return self


class KnowledgeRelationOut(ORMBase):
    id: int
    source_item_id: int
    target_item_id: int
    relation_type: RelationType
    confidence: float
    description: str | None = None
    created_at: datetime
