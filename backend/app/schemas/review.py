"""审核流水 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.app.models.enums import (
    ReviewStage,
    ReviewerType,
    ReviewTargetType,
    ReviewVerdict,
)
from backend.app.schemas.common import ORMBase


class ReviewCreate(BaseModel):
    """记一次审核。

    `verdict=needs_revision` 时 `comment` 必填（服务层也校验一次，接口层先拦能给出
    字段级错误，不必等落库）。
    """

    target_type: ReviewTargetType
    target_id: int = Field(ge=1)
    verdict: ReviewVerdict
    reviewer_name: str = Field(min_length=1, max_length=64)
    reviewer_type: ReviewerType = ReviewerType.HUMAN
    stage: ReviewStage = ReviewStage.PROFESSIONAL
    comment: str | None = Field(default=None, max_length=4000)
    findings: list[dict[str, Any]] | None = None
    checklist: dict[str, Any] | None = None


class ReviewOut(ORMBase):
    id: int
    target_type: ReviewTargetType
    target_id: int
    target_version: int | None = None
    reviewer_type: ReviewerType
    reviewer_name: str
    stage: ReviewStage
    verdict: ReviewVerdict
    findings: list[dict[str, Any]] | None = None
    checklist: dict[str, Any] | None = None
    comment: str | None = None
    reviewed_at: datetime
    created_at: datetime
    updated_at: datetime
