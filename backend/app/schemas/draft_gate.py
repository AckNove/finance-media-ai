"""成稿闸门的接口模型（`GET /api/research-reports/{id}/draft-readiness`）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DraftReadinessOut(BaseModel):
    """一份报告能不能成稿：只有人工确认过的结论才允许。"""

    model_config = ConfigDict(extra="ignore")

    report_id: int = Field(..., description="研究报告 ID")
    total_checks: int = Field(..., description="这份报告有几条语义核查记录")
    confirmed: int = Field(..., description="其中被人工确认了几条")
    by_human_review: dict[str, int] = Field(
        default_factory=dict, description="按人工复核状态计数（pending/confirmed/...）"
    )
    by_verdict: dict[str, int] = Field(
        default_factory=dict, description="按模型判定计数（供参考，不决定能否成稿）"
    )
    allowed: bool = Field(..., description="现在能不能建视频任务/进成稿")
    reason: str = Field("", description="不能成稿时的稳定原因码（no_checks / not_confirmed）")
    note: str = Field("", description="给人看的一句话解释")
    rule: str = Field("", description="这条闸门的口径原文")


__all__ = ["DraftReadinessOut"]
