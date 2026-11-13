"""成稿闸门：只有人工确认过的结论才允许进入成稿。

为什么要有这一层
----------------
语义核查（`backend/app/research/semantic.py`）给出的是**模型判断**，它只会把
`human_review_status` 置为 `pending`。用户定的口径是：**结论必须 `confirmed`
才能进入成稿**（脚本、分镜、旁白、成片）。所以"能不能成稿"不由模型说了算，
而由人工复核状态说了算。

这个模块只做三件事：
1. 把一个报告的核查状态汇总成一份 `DraftReadiness`（前端和接口都用它）；
2. 给出"已确认的结论"（只有这些能进稿）；
3. 不满足条件时抛出带原因的 `DraftGateError` —— 调用方不许绕过。

刻意不做的事：没有任何"忽略闸门"的开关。需要成稿就先去 `#/checks` 点确认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "CONFIRMED",
    "HUMAN_REVIEW_STATUSES",
    "DraftGateError",
    "DraftReadiness",
    "readiness_of",
    "confirmed_findings",
    "assert_draft_ready",
]

CONFIRMED = "confirmed"
HUMAN_REVIEW_STATUSES = ("pending", CONFIRMED, "rejected", "needs_more_evidence")

NOTE_BLOCKED = (
    "报告里还没有被人工确认的结论：语义核查的判定只是模型意见，"
    "必须有人在「语义核查」页逐条确认后才允许进入成稿。"
)
NOTE_NO_CHECKS = (
    "这份报告还没有语义核查记录（没跑到 verify.claims，或当时没有可用模型）："
    "没有核查就不能成稿。"
)
NOTE_READY = "有 {confirmed} 条结论已人工确认，可以进入成稿；未确认的结论不会进稿。"


class DraftGateError(RuntimeError):
    """成稿条件不满足。`reason` 是稳定原因码，`readiness` 是当时的完整状态。"""

    def __init__(self, reason: str, message: str, *, readiness: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.readiness = dict(readiness or {})


@dataclass(frozen=True)
class DraftReadiness:
    """一个报告的成稿就绪状态（全部来自库里的真实记录）。"""

    report_id: int
    total_checks: int
    confirmed: int
    by_human_review: dict[str, int] = field(default_factory=dict)
    by_verdict: dict[str, int] = field(default_factory=dict)
    allowed: bool = False
    reason: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "total_checks": self.total_checks,
            "confirmed": self.confirmed,
            "by_human_review": dict(self.by_human_review),
            "by_verdict": dict(self.by_verdict),
            "allowed": self.allowed,
            "reason": self.reason,
            "note": self.note,
            "rule": "只有 human_review_status=confirmed 的结论可以进入成稿",
        }


def _rows_for_report(session: Any, report_id: int) -> Sequence[Any]:
    from sqlalchemy import select

    from backend.app.models.research import FindingCheck

    return list(
        session.execute(
            select(FindingCheck)
            .where(FindingCheck.research_report_id == int(report_id))
            .order_by(FindingCheck.finding_index.asc(), FindingCheck.id.asc())
        ).scalars()
    )


def _status_of(row: Any) -> str:
    value = str(getattr(row, "human_review_status", "") or "pending").strip().lower()
    return value if value in HUMAN_REVIEW_STATUSES else "pending"


def readiness_of(session: Any, report: Any) -> DraftReadiness:
    """汇总核查状态。不猜、不推算：一切以 `finding_checks` 里的行为准。"""
    report_id = int(getattr(report, "id", 0) or 0)
    rows = _rows_for_report(session, report_id) if report_id else []
    if not rows:
        return DraftReadiness(
            report_id=report_id,
            total_checks=0,
            confirmed=0,
            allowed=False,
            reason="no_checks",
            note=NOTE_NO_CHECKS,
        )
    by_human: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    for row in rows:
        status = _status_of(row)
        by_human[status] = by_human.get(status, 0) + 1
        verdict = str(getattr(row, "verdict", "") or "").strip().lower()
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    confirmed = by_human.get(CONFIRMED, 0)
    allowed = confirmed > 0
    return DraftReadiness(
        report_id=report_id,
        total_checks=len(rows),
        confirmed=confirmed,
        by_human_review=by_human,
        by_verdict=by_verdict,
        allowed=allowed,
        reason="" if allowed else "not_confirmed",
        note=NOTE_READY.format(confirmed=confirmed) if allowed else NOTE_BLOCKED,
    )


def _finding_text(item: Any) -> str:
    """一条结论的正文。

    库里两种写法都存在：执行器写入的 `{text, kind, source_ids, numbers}`，
    以及人工经接口创建的 `{statement, nature, evidence_ids}`。三个键都认。
    """
    if isinstance(item, Mapping):
        return str(
            item.get("claim") or item.get("text") or item.get("statement") or ""
        ).strip()
    return str(item or "").strip()


def confirmed_findings(session: Any, report: Any) -> list[dict[str, Any]]:
    """返回**已人工确认**的结论（附带核查信息），顺序与报告里一致。

    报告里的一条结论对应一条 `finding_checks`（按 `finding_index` 对齐）。
    没对上核查行的结论一律不算确认——宁可少写，也不许把待复核的东西当定稿。
    """
    rows = _rows_for_report(session, int(getattr(report, "id", 0) or 0))
    if not rows:
        return []
    confirmed_by_index = {
        int(getattr(row, "finding_index", -1)): row
        for row in rows
        if _status_of(row) == CONFIRMED
    }
    if not confirmed_by_index:
        return []
    findings = list(getattr(report, "findings", None) or [])
    picked: list[dict[str, Any]] = []
    for index, item in enumerate(findings):
        row = confirmed_by_index.get(index)
        if row is None:
            continue
        text = _finding_text(item)
        if not text:
            continue
        evidence_ids: Iterable[Any] = getattr(row, "evidence_ids", None) or ()
        picked.append(
            {
                "finding_index": index,
                "text": text,
                "kind": (
                    str(item.get("kind", "")).strip() if isinstance(item, Mapping) else ""
                )
                or "inference",
                "source_ids": list(getattr(row, "source_ids", None) or ()),
                "evidence_ids": [int(value) for value in evidence_ids if isinstance(value, int)],
                "verdict": str(getattr(row, "verdict", "") or ""),
                "reason": str(getattr(row, "reason", "") or ""),
                "check_id": int(getattr(row, "id", 0) or 0),
                "human_note": str(getattr(row, "human_note", "") or ""),
            }
        )
    return picked


def assert_draft_ready(session: Any, report: Any) -> DraftReadiness:
    """不满足成稿条件就抛 `DraftGateError`（调用方不许吞掉）。"""
    state = readiness_of(session, report)
    if not state.allowed:
        raise DraftGateError(
            state.reason or "not_confirmed",
            state.note or NOTE_BLOCKED,
            readiness=state.as_dict(),
        )
    return state
