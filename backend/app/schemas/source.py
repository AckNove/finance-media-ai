"""资料来源与事实证据 Schema。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from backend.app.models.enums import (
    CredibilityLevel,
    LicenseStatus,
    SourceType,
    StatementNature,
    SupportType,
    VerificationStatus,
)
from backend.app.schemas.common import ORMBase


class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    publisher: str | None = Field(default=None, max_length=200)
    source_type: SourceType
    published_at: date | None = None
    accessed_at: datetime | None = Field(default=None, description="留空则取当前时间")
    jurisdiction: str | None = Field(default=None, max_length=64)
    credibility_level: CredibilityLevel = CredibilityLevel.MEDIUM
    license_status: LicenseStatus = LicenseStatus.UNCLEAR
    # 允许调用方自带原文哈希；不传则由服务端对正文计算
    content_hash: str | None = Field(default=None, max_length=64)
    raw_excerpt: str | None = Field(default=None, description="用于计算指纹的原文片段")
    notes: str | None = None


class SourceOut(ORMBase):
    id: int
    title: str
    url: str | None = None
    publisher: str | None = None
    source_type: SourceType
    #: 库里是 DateTime；真实抓取拿到的"发布时间"常常带时分（``2024-11-22T20:46``），
    #: 用 ``date`` 会让有时间的来源整页 500，所以这里照实返回日期时间。
    published_at: datetime | None = None
    accessed_at: datetime
    jurisdiction: str | None = None
    credibility_level: CredibilityLevel
    license_status: LicenseStatus
    content_hash: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class EvidenceCreate(BaseModel):
    source_id: int
    claim: str = Field(min_length=1, description="该证据支持/反驳的事实主张")
    original_excerpt: str | None = Field(default=None, description="原文引用片段")
    page_number: str | None = Field(default=None, max_length=64)
    quote_locator: str | None = Field(default=None, max_length=200)
    support_type: SupportType = SupportType.SUPPORTS
    claim_nature: StatementNature = StatementNature.FACT
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    # 证据必须挂在一个"主张目标"上：知识条目或研究报告
    knowledge_item_id: int | None = None
    research_report_id: int | None = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> EvidenceCreate:
        targets = [t for t in (self.knowledge_item_id, self.research_report_id) if t is not None]
        if len(targets) != 1:
            raise ValueError(
                "evidence 必须且只能关联一个主张目标：knowledge_item_id 或 research_report_id"
            )
        return self


class EvidenceOut(ORMBase):
    id: int
    source_id: int
    claim: str
    original_excerpt: str | None = None
    page_number: str | None = None
    quote_locator: str | None = None
    support_type: SupportType
    claim_nature: StatementNature
    verification_status: VerificationStatus
    verified_at: datetime | None = None
    knowledge_item_id: int | None = None
    research_report_id: int | None = None
    created_at: datetime
    updated_at: datetime


class EvidenceDetailOut(EvidenceOut):
    """证据详情：附带来源摘要，避免前端再发一次请求。"""

    source_title: str | None = None
    source_publisher: str | None = None
    source_url: str | None = None
