"""重新核查一份报告的结论：给"当年的核查漏了条"留一条补救路径。

为什么需要它
------------
语义核查曾经有个 bug：模型输出被截断重试时，把**这一轮没问到的结论**从结果里删掉，
于是报告里 6 条结论只留下 4 条核查行（已在 `semantic.py` 修掉）。修好之后新报告不会再漏，
但**已经漏掉的报告改不回来**——报告本身是对的，缺的是那几行核查记录。

所以这里提供一个显式的"重新核查"动作：拿报告现有的结论 + 已入库证据，重新跑一遍
语义核查，整批替换 `finding_checks`（`record_checks` 会按 `finding_index` 继承人工复核
状态，人已经确认过的不会被一次重跑打回 pending）。

刻意不做的事
------------
* 不联网、不重新检索：证据就是当初入库的那些，重跑只重判"这段证据支不支持这条结论"。
* 不替人做确认：重跑只写模型判定，`human_review_status` 仍是 pending（继承来的除外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

__all__ = ["RecheckError", "RecheckResult", "recheck_report"]


class RecheckError(RuntimeError):
    """重新核查没能完成。`reason` 是稳定原因码。"""

    def __init__(self, reason: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class RecheckResult:
    """一次重新核查的结果摘要（给人看，也给接口用）。"""

    report_id: int
    findings_total: int
    checks: int
    by_verdict: dict[str, int] = field(default_factory=dict)
    by_human_review: dict[str, int] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    retried: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "findings_total": self.findings_total,
            "checks": self.checks,
            "by_verdict": dict(self.by_verdict),
            "by_human_review": dict(self.by_human_review),
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(float(self.cost_cny), 6),
            "retried": self.retried,
            "note": self.note,
        }


def _finding_rows(raw_findings: Sequence[Any]) -> list[dict[str, Any]]:
    """把报告里的 findings 归一成核查要的形状。

    库里两种写法都存在：执行器写的 `{text, kind, source_ids, numbers}`，
    人工经接口建的 `{statement, nature, evidence_ids}`。三个键都认。
    """
    rows: list[dict[str, Any]] = []
    for item in raw_findings or []:
        if isinstance(item, Mapping):
            text = str(item.get("text") or item.get("claim") or item.get("statement") or "").strip()
            kind = str(item.get("kind") or item.get("nature") or "inference") or "inference"
            source_ids = [str(value) for value in (item.get("source_ids") or [])]
            numbers = [str(value) for value in (item.get("numbers") or [])]
        else:
            text, kind, source_ids, numbers = str(item or "").strip(), "inference", [], []
        if text:
            rows.append({"text": text, "kind": kind, "source_ids": source_ids, "numbers": numbers})
    return rows


def _evidence_units(session: Any, report: Any) -> list[Any]:
    """把入库证据变成核查用的 EvidenceUnit，并把数据库主键换回底稿里的来源编号。"""
    from sqlalchemy import select

    from backend.app.models.evidence import Evidence
    from backend.app.models.source import Source as SourceRow
    from backend.app.research.semantic import EvidenceUnit

    report_id = int(getattr(report, "id", 0) or 0)
    rows = session.execute(
        select(Evidence.id, Evidence.source_id, Evidence.claim, Evidence.original_excerpt)
        .where(Evidence.research_report_id == report_id)
        .order_by(Evidence.id)
    ).all()
    if not rows:
        return []

    # 来源编号（S01）是底稿里的对外编号，证据行只认 sources 主键，得先把两者对上。
    # 可靠的做法是以入库时写下的 `report.outline` 为准（它就是当时底稿的来源清单）。
    by_url: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for item in getattr(report, "outline", None) or []:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("source_id") or "")
        if not label:
            continue
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        if url:
            by_url.setdefault(url, label)
        if title:
            by_title.setdefault(title, label)

    label_of_source: dict[int, str] = {}
    source_ids = {row[1] for row in rows if row[1] is not None}
    if source_ids:
        for source in session.scalars(select(SourceRow).where(SourceRow.id.in_(source_ids))).all():
            label = by_url.get(str(source.url or "")) or by_title.get(str(source.title or ""))
            if label:
                label_of_source[source.id] = label

    units: list[Any] = []
    for evidence_id, source_id, text, excerpt in rows:
        body = (excerpt or text or "").strip()
        if not body:
            continue
        units.append(
            EvidenceUnit(
                evidence_id=int(evidence_id),
                source_id=label_of_source.get(source_id, f"db-{source_id}"),
                text=body,
            )
        )
    return units


def _check_findings(findings: Any, units: Any, **kwargs: Any) -> Any:
    """转一手调用语义核查，方便测试替换（真正的实现在 `semantic.check_findings`）。"""
    from backend.app.research.semantic import check_findings

    return check_findings(findings, units, **kwargs)


def recheck_report(
    session_factory: Callable[[], Any],
    report_id: int,
    *,
    gateway: Any = None,
    caller: Any = None,
    budget: Any = None,
    purpose: str = "research.verify",
) -> RecheckResult:
    """重新核查一份报告的全部结论，整批替换 `finding_checks`。

    * `gateway` 不传就用默认模型网关（价目未知会在这里被拒，不会偷偷按 0 元记账）；
    * `budget` 不传就是"这道闸不额外限次"，日上限/单任务上限仍由网关与运行时把关。
    """
    from backend.app.models.research import ResearchReport
    from backend.app.research.persistence import record_checks

    with session_factory() as session:
        report = session.get(ResearchReport, int(report_id))
        if report is None:
            raise RecheckError("unknown_report", f"研究报告 {report_id} 不存在")
        findings = _finding_rows(report.findings if isinstance(report.findings, list) else [])
        if not findings:
            raise RecheckError(
                "no_findings",
                "这份报告里没有可核查的结论（findings 为空），没什么可重跑的",
            )
        units = _evidence_units(session, report)
        if not units:
            raise RecheckError(
                "no_evidence",
                "这份报告没有入库证据，重新核查会得到一堆『证据不足』——先修证据链再重跑",
            )

        checked = _check_findings(
            findings,
            units,
            gateway=gateway,
            caller=caller,
            budget=budget,
            purpose=purpose,
        )
        stored = record_checks(session, report=report, checks=checked.checks)
        session.commit()

        by_verdict: dict[str, int] = {}
        by_human: dict[str, int] = {}
        for row in stored:
            key = str(getattr(row, "verdict", "") or "unverifiable")
            by_verdict[key] = by_verdict.get(key, 0) + 1
            human = str(getattr(row, "human_review_status", "") or "pending")
            by_human[human] = by_human.get(human, 0) + 1

    unreported = sum(1 for check in checked.checks if "missing_from_reply" in tuple(check.notes or ()))
    # `_report_notes` 会写一条 `retried:N 次`：N>1 说明中途被截断过、重试过
    retried = False
    for text in tuple(getattr(checked, "notes", ()) or ()):
        piece = str(text)
        if piece.startswith("retried:"):
            digits = piece[len("retried:") :].split(" ")[0]
            retried = digits.isdigit() and int(digits) > 1
    note = (
        f"按现有证据重判了 {len(checked.checks)} 条结论"
        f"（报告里共 {len(findings)} 条）"
    )
    if unreported:
        note += f"；其中 {unreported} 条模型这次没有给出判定，已如实标成『证据不足』"
    note += "。模型判定不等于人工确认，仍需有人在「语义核查」页逐条确认。"

    return RecheckResult(
        report_id=int(report_id),
        findings_total=len(findings),
        checks=len(stored),
        by_verdict=by_verdict,
        by_human_review=by_human,
        provider=str(getattr(checked, "provider", "") or ""),
        model=str(getattr(checked, "model", "") or ""),
        prompt_tokens=int(getattr(checked, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(checked, "completion_tokens", 0) or 0),
        cost_cny=float(getattr(checked, "cost_cny", 0.0) or 0.0),
        retried=retried,
        note=note,
    )
