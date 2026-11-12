"""语义核查的 API 模型。

两条独立的状态线，**不能互相覆盖**：

* ``verdict`` —— 模型对"这条结论站不站得住"的判断（supported / partially_supported /
  contradicted / insufficient_evidence / unverifiable）；
* ``human_review_status`` —— 人工复核（pending / confirmed / rejected / needs_more_evidence）。

模型再自信也只是 ``verdict``；把结论当"已核实"对外用，必须先有人写 ``confirmed``。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.common import ORMBase

VERDICT_VALUES = (
    "supported",
    "partially_supported",
    "contradicted",
    "insufficient_evidence",
    "unverifiable",
)

HUMAN_REVIEW_VALUES = ("pending", "confirmed", "rejected", "needs_more_evidence")

ISSUE_CODE_VALUES = (
    "number_unit_mismatch",
    "time_range_mismatch",
    "yoy_vs_mom",
    "overgeneralized",
    "causal_leap",
    "partial_support",
    "source_conflict",
    "stale_evidence",
)


class FindingCheckOut(ORMBase):
    """一条结论的核查记录（12 项字段全在这里，前端直接照单渲染）。"""

    id: int
    research_report_id: int
    finding_index: int
    claim: str
    claim_kind: str = "inference"
    verdict: str
    reason: str = ""
    evidence_ids: list[int] | None = None
    source_ids: list[str] | None = None
    supporting: str = ""
    contradicting: str = ""
    missing: str = ""
    issues: list[dict[str, Any]] | None = None
    provider: str = ""
    model: str = ""
    model_call_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: Decimal = Decimal("0")
    checked_at: datetime | None = None
    human_review_status: str = "pending"
    human_note: str | None = None
    human_reviewed_at: datetime | None = None
    notes: dict[str, Any] | None = None


class FindingCheckSummaryOut(BaseModel):
    """一屏看清这份报告的核查覆盖情况。"""

    model_config = ConfigDict(extra="forbid")

    research_report_id: int
    total: int = 0
    by_verdict: dict[str, int] = Field(default_factory=dict)
    by_human_review: dict[str, int] = Field(default_factory=dict)
    model_cost_cny: Decimal = Decimal("0")
    model_calls: int = 0
    note: str = ""


class FindingCheckReviewIn(BaseModel):
    """人工复核：只能人写，且必须说明理由。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "confirmed", "rejected", "needs_more_evidence"]
    note: str = Field(default="", max_length=2000, description="复核依据（写了什么、对照了哪份原文）")


__all__ = [
    "FindingCheckOut",
    "FindingCheckReviewIn",
    "FindingCheckSummaryOut",
    "HUMAN_REVIEW_VALUES",
    "ISSUE_CODE_VALUES",
    "VERDICT_VALUES",
]
