"""研究报告 Schema。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from backend.app.models.enums import StatementNature, VerificationStatus
from backend.app.schemas.common import ORMBase


class Finding(BaseModel):
    """研究结论单条：必须声明它是事实、推断还是观点。"""

    statement: str = Field(min_length=1)
    nature: StatementNature = StatementNature.FACT
    evidence_ids: list[int] = Field(default_factory=list)


class ResearchReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    topic_id: int | None = None
    summary: str = Field(min_length=1)
    findings: list[Finding] = Field(default_factory=list)
    limitations: str = Field(
        min_length=1, description="必填：本研究的边界与未解决问题，不允许留空"
    )
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    jurisdiction: str = Field(default="global", max_length=64)
    as_of_date: date | None = None
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=32)
    author_agent: str | None = Field(default=None, max_length=64)
    reading_time_minutes: int | None = Field(default=None, gt=0)

    @field_validator("limitations")
    @classmethod
    def limitations_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("limitations 不能为空白：必须显式写下研究边界")
        return value.strip()


class ResearchReportOut(ORMBase):
    id: int
    title: str
    topic_id: int | None = None
    research_task_id: int | None = None
    summary: str
    findings: list[dict]
    limitations: str
    verification_status: VerificationStatus
    jurisdiction: str
    as_of_date: date | None = None
    currency: str | None = None
    unit: str | None = None
    author_agent: str | None = None
    agent_run_id: int | None = None
    version: int
    reading_time_minutes: int | None = None
    created_at: datetime
    updated_at: datetime
