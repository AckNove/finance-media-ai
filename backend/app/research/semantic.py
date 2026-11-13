"""语义事实核查：判断"这条结论在多大程度上被证据支持"。

和 ``verify.py`` 的分工必须说清楚：

* ``verify.py`` 只做**逐字核对**——这句话有没有出现在该来源正文里。它能发现"抄错了"，
  但对"这句话说得对不对"一无所知。
* 本模块做**语义核查**——把结论和它引用的证据一起交给模型，判断证据到底支持到什么程度，
  并要求模型给出**支持片段 / 反对片段 / 缺失信息**，再把这些片段逐字拿回证据里核验。

三条硬规则（都是为了防止"模型说成立就成立"）：

1. **模型说 SUPPORTED 但没有一条能在证据里逐字找到的支持片段 → 降级为证据不足**。
   引不出原文的"支持"不算支持。
2. **模型说 CONTRADICTED 但没有一条逐字的反对片段 → 降级为无法判定**。
3. **模型核查结果与人工核查状态是两个独立字段**。这里永远只写模型结论，
   ``human_review_status`` 一律以 ``pending`` 起步，任何自动化流程都不许把它写成
   ``confirmed``——那是人的动作。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

#: 一次核查最多带多少条结论（防止提示词无限长）
MAX_FINDINGS = 12

#: 每条证据截取的字符数
EXCERPT_CHARS = 400

#: 最多带多少条证据进提示词
MAX_EVIDENCE = 30

_WHITESPACE = re.compile(r"\s+")


class Verdict(str, Enum):
    """模型对一条结论的判定。注意：这不是事实核查结论，是"证据支持度"。"""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNVERIFIABLE = "unverifiable"


#: 允许模型使用的判定值
VERDICTS: frozenset[str] = frozenset(item.value for item in Verdict)

#: 任务书列出的八类常见错误，模型只能在这些码里选
ISSUE_CODES: frozenset[str] = frozenset(
    {
        "number_unit_mismatch",  # 数值或单位对不上
        "time_range_mismatch",  # 时间范围不一致
        "yoy_vs_mom",  # 同比环比混淆
        "overgeneralized",  # 个别案例被推广成整体
        "causal_leap",  # 相关当因果
        "partial_support",  # 证据只支持一部分
        "source_conflict",  # 不同来源互相冲突
        "stale_evidence",  # 证据过期或不适用的口径
    }
)

#: 需要"逐字引证"才成立的判定
_NEEDS_SUPPORT = {Verdict.SUPPORTED.value, Verdict.PARTIALLY_SUPPORTED.value}
_NEEDS_CONTRADICTION = {Verdict.CONTRADICTED.value}


class SemanticError(RuntimeError):
    """核查失败。``reason`` 是稳定原因码，供上层如实写进任务状态。

    ``usage`` 带上已经真的花掉的那次调用：截断/失败也计费，必须能记进任务成本。
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.usage: dict[str, Any] = dict(usage or {})

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class EvidenceUnit:
    """证据库里的一个片段，是核查的最小依据。"""

    evidence_id: int | None
    source_id: str
    text: str

    def excerpt(self, limit: int = EXCERPT_CHARS) -> str:
        text = _WHITESPACE.sub(" ", (self.text or "").strip())
        return text[:limit]


@dataclass(frozen=True)
class Issue:
    code: str
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "note": self.note}


@dataclass(frozen=True)
class FindingCheck:
    """一条结论的核查结果（字段对齐任务书要求的 12 项）。"""

    index: int
    claim: str
    verdict: str
    reason: str
    claim_kind: str = "inference"
    evidence_ids: tuple[int, ...] = ()
    source_ids: tuple[str, ...] = ()
    supporting: tuple[str, ...] = ()
    contradicting: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()
    provider: str = ""
    model: str = ""
    model_call_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    human_review_status: str = "pending"
    checked_at: str = ""
    notes: tuple[str, ...] = ()

    @property
    def confirmed_by_human(self) -> bool:
        return self.human_review_status == "confirmed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "claim": self.claim,
            "claim_kind": self.claim_kind,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "source_ids": list(self.source_ids),
            "supporting": list(self.supporting),
            "contradicting": list(self.contradicting),
            "missing": list(self.missing),
            "issues": [item.as_dict() for item in self.issues],
            "provider": self.provider,
            "model": self.model,
            "model_call_id": self.model_call_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(self.cost_cny, 6),
            "human_review_status": self.human_review_status,
            "checked_at": self.checked_at,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SemanticReport:
    """一次核查（一批结论）的整体结果。"""

    checks: tuple[FindingCheck, ...]
    provider: str = ""
    model: str = ""
    model_call_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    checked_at: str = ""
    model_used: bool = True
    skipped_reason: str = ""
    counts: Mapping[str, int] | None = None
    notes: tuple[str, ...] = ()

    def verdict_counts(self) -> dict[str, int]:
        counter = {item.value: 0 for item in Verdict}
        for check in self.checks:
            counter[check.verdict] = counter.get(check.verdict, 0) + 1
        return counter

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_used": self.model_used,
            "skipped_reason": self.skipped_reason,
            "provider": self.provider,
            "model": self.model,
            "model_call_id": self.model_call_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(self.cost_cny, 6),
            "checked_at": self.checked_at,
            "counts": dict(self.verdict_counts()),
            "checks": [item.as_dict() for item in self.checks],
            "notes": list(self.notes),
            "note": (
                "以上是模型对证据支持度的判断，不等于事实核查结论。"
                "人工复核状态单独记录，模型结果不会自动变成 verified。"
            ),
        }

    def to_markdown(self, *, objective: str = "") -> str:
        lines = ["# 语义核查（模型判定，待人工复核）", ""]
        if objective:
            lines += [f"研究目标：{objective}", ""]
        if not self.model_used:
            lines += ["本次未做模型核查：", "", f"- {self.skipped_reason}", ""]
        else:
            lines += [
                f"模型：{self.provider}/{self.model}",
                f"tokens：{self.prompt_tokens} + {self.completion_tokens}",
                f"费用：¥{self.cost_cny:.6f}",
                f"时间：{self.checked_at}",
                "",
            ]
            for verdict, count in self.verdict_counts().items():
                lines.append(f"- {verdict}: {count}")
            lines.append("")
        for check in self.checks:
            lines.append(f"## {check.index + 1}. [{check.verdict}] {check.claim}")
            lines.append("")
            lines.append(f"- 理由：{check.reason or '（模型未给）'}")
            lines.append(f"- 来源：{', '.join(check.source_ids) or '（无）'}")
            lines.append(f"- 证据行：{', '.join(str(i) for i in check.evidence_ids) or '（无）'}")
            if check.supporting:
                lines.append("- 支持片段：")
                lines += [f"  - 「{item}」" for item in check.supporting]
            if check.contradicting:
                lines.append("- 反对片段：")
                lines += [f"  - 「{item}」" for item in check.contradicting]
            if check.missing:
                lines.append("- 缺失信息：")
                lines += [f"  - {item}" for item in check.missing]
            if check.issues:
                lines.append(
                    "- 疑点：" + "、".join(item.code for item in check.issues)
                )
            if check.notes:
                lines.append("- 核查说明：" + "；".join(check.notes))
            lines.append(f"- 人工复核：{check.human_review_status}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------- 输入


def _claim_of(item: Mapping[str, Any]) -> str:
    """结论正文：报告里存字段叫 `text`，核查入参按语义叫 `claim`，两个都认。"""
    return str(item.get("claim") or item.get("text") or "").strip()

def _normalise(text: str) -> str:
    return _WHITESPACE.sub("", text or "")


def build_messages(
    findings: Sequence[Mapping[str, Any]], evidence: Sequence[EvidenceUnit]
) -> list[dict[str, str]]:
    """把结论与证据拼成一次核查请求。

    刻意不做"每结论一次调用"：一条一条问会让成本线性膨胀，而且模型看不到
    跨来源冲突。整批一次问，同时要求它指出冲突。
    """
    system = (
        "你是事实核查员。用户会给你若干条研究结论，以及这些结论所引用来源的原文摘录。\n"
        "你的任务是逐条判断：**现有证据在多大程度上支持这条结论**。\n"
        "判定只能用这五个值之一：supported / partially_supported / contradicted / "
        "insufficient_evidence / unverifiable。\n"
        "规则：\n"
        "1. 只能使用给定证据，不得引入外部知识；证据里没有的数字、时点、主体一律不许补。\n"
        "2. supporting / contradicting 里必须放**证据原文的逐字片段**（可以截取一句，但不得改写、不得拼接）。"
        "引不出原文就不要写。\n"
        "3. 判断不了就写 insufficient_evidence 或 unverifiable，不要为了给结论而猜。\n"
        "4. 需要检查这八类错误，命中就写进 issues（code 只能取给定枚举）：\n"
        "   number_unit_mismatch（数值/单位对不上）、time_range_mismatch（时间范围不一致）、\n"
        "   yoy_vs_mom（同比与环比混淆）、overgeneralized（个别案例当成整体）、\n"
        "   causal_leap（相关当因果）、partial_support（证据只支持一部分）、\n"
        "   source_conflict（不同来源互相冲突）、stale_evidence（证据过期或口径不适用）。\n"
        "5. 只输出 JSON，不要解释。格式：\n"
        '{"checks":[{"index":0,"verdict":"partially_supported","reason":"一句话说明为什么",'
        '"supporting":["原文片段"],"contradicting":["原文片段"],"missing":["还缺什么"],'
        '"issues":[{"code":"overgeneralized","note":"说明"}]}]}'
    )
    payload = {
        "findings": [
            {
                "index": int(item.get("index", position)),
                "claim": _claim_of(item),
                "kind": str(item.get("kind", "")),
                "source_ids": list(item.get("source_ids") or []),
                "numbers": list(item.get("numbers") or []),
            }
            for position, item in enumerate(findings)
        ],
        "evidence": [
            {
                "evidence_id": unit.evidence_id,
                "source_id": unit.source_id,
                "text": unit.excerpt(),
            }
            for unit in evidence
        ],
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "待核查数据（JSON）：\n"
            + json.dumps(payload, ensure_ascii=False, indent=1),
        },
    ]


# ---------------------------------------------------------------------- 解析


def _strip_fence(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"```\s*$", "", body)
    return body.strip()


def _first_object(text: str) -> dict[str, Any]:
    body = _strip_fence(text)
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}")
        if start < 0 or end <= start:
            raise SemanticError("bad_output", "模型输出里找不到 JSON 对象") from None
        try:
            loaded = json.loads(body[start : end + 1])
        except json.JSONDecodeError as error:
            raise SemanticError("bad_output", f"模型输出不是合法 JSON：{error}") from None
    if not isinstance(loaded, dict):
        raise SemanticError("bad_output", "模型输出的 JSON 顶层不是对象")
    return loaded


def _quote_hits(
    quotes: Iterable[str], corpus: Sequence[EvidenceUnit], *, allowed_sources: Sequence[str]
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """把模型给的片段逐字拿回证据里找。

    返回（保留的片段, 片段所在的证据行 id）。找不到的片段直接丢掉——
    "引不出原文的支持"不算支持，这是本模块与 ``verify.py`` 一致的底线。
    """
    allowed = {item for item in allowed_sources if item}
    kept: list[str] = []
    hits: list[int] = []
    for quote in quotes:
        needle = _normalise(str(quote))
        if len(needle) < 6:  # 太短的片段没有证明力，也容易被凑出来
            continue
        for unit in corpus:
            if allowed and unit.source_id not in allowed:
                continue
            if needle and needle in _normalise(unit.text):
                kept.append(str(quote).strip())
                if isinstance(unit.evidence_id, int):
                    hits.append(unit.evidence_id)
                break
    return tuple(dict.fromkeys(kept)), tuple(dict.fromkeys(hits))


def parse_checks(
    text: str,
    *,
    findings: Sequence[Mapping[str, Any]],
    evidence: Sequence[EvidenceUnit],
) -> tuple[FindingCheck, ...]:
    """解析模型的核查输出，并执行三条降级规则。"""
    payload = _first_object(text)
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        raise SemanticError("bad_output", "模型输出里没有 checks 数组")

    by_index: dict[int, Mapping[str, Any]] = {}
    for item in raw_checks:
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(findings):
            by_index[index] = item

    results: list[FindingCheck] = []
    for position, finding in enumerate(findings):
        claim = _claim_of(finding)
        source_ids = tuple(
            dict.fromkeys(str(item) for item in (finding.get("source_ids") or []) if str(item))
        )
        claim_kind = str(finding.get("kind", "") or "inference")
        raw = by_index.get(position)
        if raw is None:
            results.append(
                FindingCheck(
                    index=position,
                    claim=claim,
                    claim_kind=claim_kind,
                    verdict=Verdict.UNVERIFIABLE.value,
                    reason="模型没有给出这一条的判定",
                    source_ids=source_ids,
                    notes=("missing_from_reply",),
                )
            )
            continue

        verdict = str(raw.get("verdict", "") or "").strip().lower()
        notes: list[str] = []
        if verdict not in VERDICTS:
            notes.append(f"unknown_verdict:{verdict or '空'}")
            verdict = Verdict.UNVERIFIABLE.value

        supporting, support_hits = _quote_hits(
            raw.get("supporting") or (), evidence, allowed_sources=source_ids
        )
        contradicting, contra_hits = _quote_hits(
            raw.get("contradicting") or (), evidence, allowed_sources=source_ids
        )
        raw_support = [str(item) for item in (raw.get("supporting") or []) if str(item).strip()]
        raw_contra = [
            str(item) for item in (raw.get("contradicting") or []) if str(item).strip()
        ]
        if len(supporting) < len(raw_support):
            notes.append("supporting_quote_not_found")
        if len(contradicting) < len(raw_contra):
            notes.append("contradicting_quote_not_found")

        reason = str(raw.get("reason", "") or "").strip()
        if verdict in _NEEDS_SUPPORT and not supporting:
            # 引不出原文的"支持"不算支持
            verdict = Verdict.INSUFFICIENT_EVIDENCE.value
            notes.append("downgraded:no_verifiable_supporting_quote")
            reason = reason or "模型声称被支持，但给不出能在证据里找到的原文片段"
        if verdict in _NEEDS_CONTRADICTION and not contradicting:
            verdict = Verdict.UNVERIFIABLE.value
            notes.append("downgraded:no_verifiable_contradicting_quote")
            reason = reason or "模型声称被推翻，但给不出能在证据里找到的原文片段"

        issues: list[Issue] = []
        for item in raw.get("issues") or []:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code", "") or "").strip().lower()
            if code not in ISSUE_CODES:
                notes.append(f"unknown_issue:{code or '空'}")
                continue
            issues.append(Issue(code=code, note=str(item.get("note", "") or "").strip()))

        missing = tuple(
            str(item).strip() for item in (raw.get("missing") or []) if str(item).strip()
        )
        results.append(
            FindingCheck(
                index=position,
                claim=claim,
                claim_kind=claim_kind,
                verdict=verdict,
                reason=reason,
                evidence_ids=tuple(dict.fromkeys(support_hits + contra_hits)),
                source_ids=source_ids,
                supporting=supporting,
                contradicting=contradicting,
                missing=missing,
                issues=tuple(issues),
                notes=tuple(notes),
            )
        )
    return tuple(results)


# ---------------------------------------------------------------------- 调用


def _select_evidence(
    findings: Sequence[Mapping[str, Any]],
    evidence: Sequence[EvidenceUnit],
    *,
    limit: int = MAX_EVIDENCE,
    per_source: int = 4,
) -> list[EvidenceUnit]:
    """挑进提示词的证据：先按结论自己挂的来源挑，剩下的再用其余证据补齐。

    为什么不能直接按 id 取前 N 条：库里一次研究能有三百多条证据，按顺序切前 30 条时，
    某条结论引用的 S16、S19 很可能根本没进候选，模型只能说"给定证据里没有 S16 原文"，
    于是一份本来有据的报告被大面积判成"证据不足"。**先保结论自己的来源**，
    剩下的名额才按顺序补，这样每一条结论都有机会被真正核对。
    """
    if len(evidence) <= limit:
        return list(evidence)

    by_source: dict[str, list[EvidenceUnit]] = {}
    for unit in evidence:
        by_source.setdefault(str(unit.source_id), []).append(unit)

    picked: list[EvidenceUnit] = []
    seen: set[int] = set()

    def take(unit: EvidenceUnit) -> bool:
        key = id(unit)
        if key in seen or len(picked) >= limit:
            return False
        seen.add(key)
        picked.append(unit)
        return True

    for item in findings:
        for label in item.get("source_ids") or []:
            for unit in by_source.get(str(label), [])[:per_source]:
                take(unit)

    for item in findings:
        for label in item.get("source_ids") or []:
            for unit in by_source.get(str(label), [])[per_source:]:
                take(unit)

    for unit in evidence:
        if len(picked) >= limit:
            break
        take(unit)
    return picked


def check_findings(
    findings: Sequence[Mapping[str, Any]],
    evidence: Sequence[EvidenceUnit],
    *,
    gateway: Any = None,
    caller: Any = None,
    budget: Any = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 3200,
    purpose: str = "research.verify",
    now: Any = None,
) -> SemanticReport:
    """对一批结论做一次语义核查。没有可用模型时抛 ``SemanticError('no_model')``。"""
    from backend.app.capabilities.model import ModelRequest, get_model_gateway
    from backend.app.capabilities.model.providers import provider_status
    from backend.app.core.caller import CallerContext

    checked_at = _timestamp(now)
    items = [item for item in findings if _claim_of(item)][:MAX_FINDINGS]
    evidence = _select_evidence(items, evidence, limit=MAX_EVIDENCE)
    if not items:
        return SemanticReport(checks=(), checked_at=checked_at, model_used=False, skipped_reason="没有可核查的结论")
    if not evidence:
        return SemanticReport(
            checks=tuple(
                FindingCheck(
                    index=position,
                    claim=_claim_of(item),
                    claim_kind=str(item.get("kind", "") or "inference"),
                    verdict=Verdict.INSUFFICIENT_EVIDENCE.value,
                    reason="证据库里没有可用的证据片段",
                    source_ids=tuple(str(s) for s in (item.get("source_ids") or [])),
                    notes=("no_evidence",),
                )
                for position, item in enumerate(items)
            ),
            checked_at=checked_at,
            model_used=False,
            skipped_reason="证据库为空",
        )

    engine = gateway or get_model_gateway()
    if hasattr(engine, "ready_providers"):
        ready = list(engine.ready_providers())
    else:  # 老对象/替身：退回全局探测
        status = provider_status()
        ready = [
            name
            for name, info in status.items()
            if isinstance(info, dict) and info.get("status") == "ready"
        ]
    if not ready:
        raise SemanticError("no_model", "没有可用的模型 provider（缺密钥或未启用）")

    target_provider = provider or ready[0]
    target_model = model
    if target_model is None:
        from backend.app.core.config import get_models_config

        config = getattr(engine, "models_config", None) or get_models_config()
        candidates = [
            item for item in config.models if item.provider == target_provider and item.enabled
        ]
        if not candidates:
            raise SemanticError("no_model", f"provider {target_provider} 没有启用的模型")
        target_model = candidates[0].model

    # 一条结论要输出 12 个字段，撞上限很常见。**不要**拿半截 JSON 硬解析，也先别直接
    # 失败：减少"要核查的结论条数 + 进提示词的证据条数"再试，逐级递减到能装下为止。
    # 证据条数必须从 MAX_EVIDENCE 起算——库里可能有几百条证据，全塞进提示词一次就是
    # 十几万 token，比核查本身值钱得多。
    base_evidence = min(len(evidence), MAX_EVIDENCE)
    attempts = (
        (len(items), base_evidence, max_tokens),
        (max(4, len(items) // 2), max(12, base_evidence // 2), max_tokens),
        (3, 12, max(1800, int(max_tokens * 0.75))),
    )
    reply = None
    used_items = items
    used_units: list[Any] = list(evidence)[:base_evidence]
    last_error: SemanticError | None = None
    last_usage: dict[str, Any] = {}
    # 多次尝试的花费要累加：重试也是真花了钱，只记最后一次会让任务账少于模型账单
    spent: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "calls": 0,
    }
    for finding_cap, evidence_cap, tokens_cap in attempts:
        used_items = list(items)[: max(1, finding_cap)]
        used_units = list(evidence)[: max(6, min(evidence_cap, MAX_EVIDENCE))]
        request = ModelRequest(
            provider=target_provider,
            model=target_model,
            messages=build_messages(used_items, used_units),
            purpose=purpose,
            temperature=0.0,
            max_tokens=tokens_cap,
            response_format="json_object",
            cacheable=False,
        )
        reply = engine.complete(caller or CallerContext.human(), request, budget=budget)
        record = getattr(reply, "record", None)
        last_usage = {
            "prompt_tokens": int(getattr(record, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(record, "completion_tokens", 0) or 0),
            "cost_cny": float(getattr(record, "cost_cny", 0.0) or 0.0),
            "model_call_id": getattr(record, "id", None),
        }
        spent["prompt_tokens"] += last_usage["prompt_tokens"]
        spent["completion_tokens"] += last_usage["completion_tokens"]
        spent["cost_cny"] += last_usage["cost_cny"]
        spent["calls"] += 1
        if not reply.ok:
            raise SemanticError(
                reply.error_code or "model_error",
                reply.error_message or "模型调用失败",
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )
        usage = getattr(reply, "raw_usage", None) or {}
        finish_reason = str(usage.get("finish_reason") or "")
        completion_tokens = last_usage["completion_tokens"]
        if finish_reason == "length" or (tokens_cap and completion_tokens >= tokens_cap):
            last_error = SemanticError(
                "truncated",
                (
                    f"核查输出被截断（finish_reason={finish_reason or '未提供'}，"
                    f"completion_tokens={completion_tokens}，max_tokens={tokens_cap}，"
                    f"结论 {len(used_items)} 条、证据 {len(used_units)} 条）："
                    "已减少条数重试仍不够"
                ),
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )
            continue
        break
    else:
        raise last_error or SemanticError("truncated", "核查输出被截断", usage={**spent, "model_call_id": last_usage.get("model_call_id")})
    if reply is None:  # pragma: no cover
        raise last_error or SemanticError("truncated", "核查输出被截断", usage={**spent, "model_call_id": last_usage.get("model_call_id")})
    items = used_items
    units = used_units

    record = getattr(reply, "record", None)
    completion_tokens = int(getattr(record, "completion_tokens", 0) or 0)

    checks = parse_checks(reply.text, findings=items, evidence=units)
    # 一次调用核查多条结论：把这次核查的 Token 与费用按条均摊到每行，
    # 这样 SUM(finding_checks.cost_cny) 等于真实花费，不会因为"一行记全款"而重复计数。
    # 注意用 spent 而不是最后一次 record：撞上限重试的第一次也是真花了钱，
    # 只记最后一次会让任务账少于模型账单（实测差过 0.0475 元）。
    shares = max(len(checks), 1)
    prompt_total = int(spent["prompt_tokens"])
    cost_total = float(spent["cost_cny"])
    retried = int(spent["calls"]) > 1
    stamped = tuple(
        FindingCheck(
            index=check.index,
            claim=check.claim,
            claim_kind=check.claim_kind,
            verdict=check.verdict,
            reason=check.reason,
            evidence_ids=check.evidence_ids,
            source_ids=check.source_ids,
            supporting=check.supporting,
            contradicting=check.contradicting,
            missing=check.missing,
            issues=check.issues,
            provider=request.provider,
            model=request.model,
            model_call_id=getattr(record, "id", None),
            prompt_tokens=prompt_total // shares,
            completion_tokens=int(spent["completion_tokens"]) // shares,
            cost_cny=round(cost_total / shares, 6),
            checked_at=checked_at,
            notes=tuple(check.notes) + (f"usage_share:1/{shares}",),
        )
        for check in checks
    )
    return SemanticReport(
        checks=stamped,
        provider=request.provider,
        model=request.model,
        model_call_id=getattr(record, "id", None),
        prompt_tokens=prompt_total,
        completion_tokens=int(spent["completion_tokens"]),
        cost_cny=round(cost_total, 6),
        checked_at=checked_at,
        notes=(f"retried:{spent['calls']} 次（撞上限后减少条数重试，费用按实际累加）",) if retried else (),
    )


def _timestamp(now: Any = None) -> str:
    from datetime import datetime, timezone

    moment = now or datetime.now(timezone.utc)
    if isinstance(moment, str):
        return moment
    try:
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    except AttributeError:  # 裸 datetime
        return str(moment)


__all__ = [
    "EXCERPT_CHARS",
    "ISSUE_CODES",
    "MAX_EVIDENCE",
    "MAX_FINDINGS",
    "VERDICTS",
    "EvidenceUnit",
    "FindingCheck",
    "Issue",
    "SemanticError",
    "SemanticReport",
    "Verdict",
    "build_messages",
    "check_findings",
    "parse_checks",
]
