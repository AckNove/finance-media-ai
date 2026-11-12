"""用真实模型把"资料汇编"变成"分析"。

边界（写在这里，免得以后自己骗自己）：

- 模型**只能看到**已经抓下来、已经落库的证据（来源正文摘录 + 候选数据点）。
  提示词里明确禁止引入外部事实，并且每条结论必须挂 `source_ids`；
  挂不上来源的结论一律丢弃，不写进报告。
- 这里不做"要不要相信模型"的判断：模型给的是**推断**，业务字段 `kind` 必须
  标 `fact` / `inference` / `hypothesis`，报告里按这三档呈现，事实核查另走
  `backend/app/research/verify.py` 的逐字核对。
- 调用成本与 Token 由 `ModelGateway` 记账，本模块只负责把请求发对、把结果解析干净。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: 分析阶段最多拿多少个数据点进提示词：太多会把上下文塞满，也太贵。
MAX_POINTS = 40
#: 每条来源正文摘录的最大长度（字符）。
MAX_EXCERPT_CHARS = 700

_PROMPT_SYSTEM = (
    "你是一名严谨的财经研究分析师。你只能使用用户提供的【证据】作答，"
    "禁止引入证据之外的事实、数字或公司名。"
    "每条结论必须挂上来证据编号（如 S01）。"
    "如果证据不足以支撑某个判断，就把它写进 data_gaps，不要编。"
    "严格输出 JSON，不要输出任何解释性文字或代码块围栏。"
)

_PROMPT_TEMPLATE = """研究目标：{objective}

【证据】（按来源编号）
{sources}

【候选数据点】（来自上述来源原文，可直接引用）
{points}

请输出 JSON，结构如下：
{{
  "summary": "不超过200字的核心结论，结论必须是证据能支撑的",
  "findings": [
    {{"claim": "一句话结论", "kind": "fact|inference|hypothesis",
      "source_ids": ["S01"], "numbers": ["1.52%"]}}
  ],
  "data_gaps": ["还缺什么证据才能回答研究目标"]
}}

要求：
- findings 3~6 条，每条 claim 不超过 60 字；summary 不超过 200 字；
- 每条 source_ids 至少一个且在证据里存在；
- numbers 只写证据里出现过的数字；
- 不要复述原文、不要把原文整句搬进 claim，要给出因果或结构上的判断；
- **整个 JSON 控制在 1200 字以内**（写太长会被截断，反而丢掉全部结论）。
"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_KINDS = ("fact", "inference", "hypothesis")


class AnalysisError(ValueError):
    """分析阶段失败。``reason`` 稳定，供上层判断。

    ``usage`` 带上已经真的花掉的那次调用（prompt/completion/cost）：截断或调用失败
    也计费，上层要把成本记进任务账，否则任务花费会小于模型账单。
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
        self.usage: dict[str, Any] = dict(usage or {})


@dataclass(frozen=True)
class AnalysisFinding:
    claim: str
    kind: str
    source_ids: tuple[str, ...]
    numbers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "kind": self.kind,
            "source_ids": list(self.source_ids),
            "numbers": list(self.numbers),
        }


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    findings: tuple[AnalysisFinding, ...]
    data_gaps: tuple[str, ...]
    raw_text: str
    provider: str
    model: str
    model_call_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    dropped: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "findings": [item.as_dict() for item in self.findings],
            "data_gaps": list(self.data_gaps),
            "provider": self.provider,
            "model": self.model,
            "model_call_id": self.model_call_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": self.cost_cny,
            "dropped": list(self.dropped),
            "generated_by": "model",
        }

    def to_markdown(self, *, objective: str = "") -> str:
        lines = ["# 分析小结（模型生成，仅依据下列证据）", ""]
        if objective:
            lines += [f"研究目标：{objective}", ""]
        lines += [self.summary or "（模型没有给出小结）", "", "## 结论与推断", ""]
        label = {"fact": "事实", "inference": "推断", "hypothesis": "假设"}
        for item in self.findings:
            cites = "、".join(item.source_ids) or "无来源"
            numbers = "、".join(item.numbers)
            tail = f"（数字：{numbers}）" if numbers else ""
            lines.append(f"- [{label.get(item.kind, item.kind)}] {item.claim} —— {cites}{tail}")
        if self.data_gaps:
            lines += ["", "## 证据缺口", ""]
            lines += [f"- {gap}" for gap in self.data_gaps]
        if self.dropped:
            lines += ["", "## 被丢弃的结论（挂不上来源）", ""]
            lines += [f"- {item}" for item in self.dropped]
        lines += [
            "",
            "---",
            "",
            f"模型：`{self.provider}/{self.model}`；"
            f"tokens {self.prompt_tokens}+{self.completion_tokens}；"
            f"成本 ¥{self.cost_cny:.6f}；模型调用 id {self.model_call_id}",
            "本段是模型基于已抓取证据生成的**分析**，不是事实核查结论；"
            "引用前仍需回原文核对口径与时点。",
        ]
        return "\n".join(lines) + "\n"


def _excerpt_for(ref: Any, excerpts: dict[str, str]) -> str:
    """取这条来源的正文摘录：优先用底稿自带的，其次读抓取快照。"""
    source_id = str(getattr(ref, "source_id", "") or "")
    text = str(excerpts.get(source_id, "") or "").strip()
    if text:
        return text[:MAX_EXCERPT_CHARS]
    snapshot = str(getattr(ref, "snapshot_path", "") or "")
    if not snapshot:
        return ""
    try:
        from pathlib import Path

        path = Path(snapshot)
        if not path.is_file():
            return ""
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return " ".join(raw.split())[:MAX_EXCERPT_CHARS]


def _source_block(digest: Any) -> str:
    lines: list[str] = []
    excerpts = (getattr(digest, "meta", {}) or {}).get("excerpts", {}) or {}
    for ref in getattr(digest, "sources", ()) or ():
        source_id = str(getattr(ref, "source_id", "") or "")
        title = str(getattr(ref, "title", "") or "")
        site = str(getattr(ref, "site_name", "") or "")
        published = str(getattr(ref, "published_at", "") or "")
        excerpt = _excerpt_for(ref, excerpts)
        head = f"[{source_id}] {title}（{site} {published}）".strip()
        lines.append(head)
        if excerpt:
            lines.append(f"  摘录：{excerpt}")
    return "\n".join(lines) if lines else "（没有可用证据）"


def _point_block(digest: Any, max_points: int | None = None) -> str:
    lines: list[str] = []
    limit = MAX_POINTS if max_points is None else max(1, int(max_points))
    for point in list(getattr(digest, "data_points", ()) or ())[:limit]:
        text = str(getattr(point, "text", "") or "").strip()
        source_id = str(getattr(point, "source_id", "") or "")
        numbers = ", ".join(str(n) for n in (getattr(point, "numbers", ()) or ()))
        suffix = f"（数字：{numbers}）" if numbers else ""
        lines.append(f"- [{source_id}] {text}{suffix}")
    return "\n".join(lines) if lines else "（没有候选数据点）"


def build_messages(digest: Any, *, max_points: int | None = None) -> list[dict[str, str]]:
    """把底稿变成一次模型请求的 messages（纯函数，便于测试）。"""
    objective = str(getattr(digest, "objective", "") or getattr(digest, "title", "") or "")
    user = _PROMPT_TEMPLATE.format(
        objective=objective or "（未指定）",
        sources=_source_block(digest),
        points=_point_block(digest, max_points),
    )
    return [
        {"role": "system", "content": _PROMPT_SYSTEM},
        {"role": "user", "content": user},
    ]


def _known_source_ids(digest: Any) -> set[str]:
    return {
        str(getattr(ref, "source_id", "") or "")
        for ref in (getattr(digest, "sources", ()) or ())
    }


def parse_analysis(text: str, *, known_sources: Sequence[str] = ()) -> tuple[
    str, tuple[AnalysisFinding, ...], tuple[str, ...], tuple[str, ...]
]:
    """解析模型输出，返回 (summary, findings, data_gaps, dropped)。

    挂不上已知来源的结论会被丢进 ``dropped``——**宁可少写一条，也不写没出处的判断**。
    """
    cleaned = _CODE_FENCE.sub("", str(text or "")).strip()
    match = _JSON_BLOCK.search(cleaned)
    if match is None:
        raise AnalysisError("bad_output", "模型输出里没有 JSON 对象")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise AnalysisError("bad_output", f"模型输出的 JSON 解析失败：{error}") from error
    if not isinstance(payload, dict):
        raise AnalysisError("bad_output", "模型输出的 JSON 不是对象")

    allowed = {str(item) for item in known_sources if item}
    summary = str(payload.get("summary", "") or "").strip()
    findings: list[AnalysisFinding] = []
    dropped: list[str] = []
    for raw in payload.get("findings", []) or []:
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim", "") or "").strip()
        if not claim:
            continue
        source_ids = tuple(
            dict.fromkeys(str(item) for item in (raw.get("source_ids") or []) if str(item))
        )
        missing = [item for item in source_ids if allowed and item not in allowed]
        if not source_ids or missing:
            dropped.append(claim)
            continue
        kind = str(raw.get("kind", "") or "").strip().lower()
        if kind not in _KINDS:
            kind = "inference"
        numbers = tuple(
            dict.fromkeys(str(item) for item in (raw.get("numbers") or []) if str(item))
        )
        findings.append(
            AnalysisFinding(claim=claim, kind=kind, source_ids=source_ids, numbers=numbers)
        )
    gaps = tuple(
        str(item).strip() for item in (payload.get("data_gaps") or []) if str(item).strip()
    )
    if not summary and not findings:
        raise AnalysisError("empty_analysis", "模型既没有给小结也没有给结论")
    return summary, tuple(findings), gaps, tuple(dropped)


def analyze(
    digest: Any,
    *,
    gateway: Any = None,
    caller: Any = None,
    budget: Any = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 5000,
    purpose: str = "research.analyze",
) -> AnalysisResult:
    """调用真实模型做一次分析。没有可用模型时抛 ``AnalysisError('no_model')``。"""
    from backend.app.capabilities.model import ModelRequest, get_model_gateway
    from backend.app.capabilities.model.providers import provider_status
    from backend.app.core.caller import CallerContext

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
        raise AnalysisError("no_model", "没有可用的模型 provider（缺密钥或未启用）")

    target_provider = provider or ready[0]
    target_model = model
    if target_model is None:
        from backend.app.core.config import get_models_config

        config = getattr(engine, "models_config", None) or get_models_config()
        candidates = [
            item for item in config.models if item.provider == target_provider and item.enabled
        ]
        if not candidates:
            raise AnalysisError("no_model", f"provider {target_provider} 没有启用的模型")
        target_model = candidates[0].model

    # 回答被截断时**不要**直接失败，也不要拿半截 JSON 当结果：先减少进提示词的数据点
    # 再试一次（证据越多、模型越想逐条列，越容易撞上限）。两次都撞上限才算真失败。
    attempts = (
        (MAX_POINTS, max_tokens),
        (MAX_POINTS // 2, max(2000, int(max_tokens * 0.8))),
        (MAX_POINTS // 4, max(1500, int(max_tokens * 0.6))),
    )
    last_error: AnalysisError | None = None
    last_usage: dict[str, Any] = {}
    # 多次尝试的花费要累加：重试也是真花了钱，只记最后一次会让任务账少于模型账单
    spent: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "calls": 0,
    }
    reply = None
    used_max_tokens = max_tokens
    for points_cap, tokens_cap in attempts:
        request = ModelRequest(
            provider=target_provider,
            model=target_model,
            messages=build_messages(digest, max_points=points_cap),
            purpose=purpose,
            temperature=0.2,
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
            raise AnalysisError(
                reply.error_code or "model_error",
                reply.error_message or "模型调用失败",
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )
        usage = getattr(reply, "raw_usage", None) or {}
        finish_reason = str(usage.get("finish_reason") or "")
        completion_tokens = last_usage["completion_tokens"]
        used_max_tokens = tokens_cap
        if finish_reason == "length" or (tokens_cap and completion_tokens >= tokens_cap):
            # 撞到 max_tokens：正文被截断，JSON 必然不完整。**不要**拿半截结果去解析，
            # 否则要么报一个含糊的 bad_output，要么更糟——悄悄得出残缺结论。
            last_error = AnalysisError(
                "truncated",
                (
                    f"模型输出被截断（finish_reason={finish_reason or '未提供'}，"
                    f"completion_tokens={completion_tokens}，max_tokens={tokens_cap}，"
                    f"数据点上限={points_cap}）：已减少证据重试仍不够，请提高 max_tokens"
                ),
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )
            continue
        break
    else:
        raise last_error or AnalysisError("truncated", "模型输出被截断", usage={**spent, "model_call_id": last_usage.get("model_call_id")})
    if reply is None:  # pragma: no cover - attempts 非空时不可达
        raise last_error or AnalysisError("truncated", "模型输出被截断", usage={**spent, "model_call_id": last_usage.get("model_call_id")})

    record = getattr(reply, "record", None)
    known = sorted(_known_source_ids(digest))
    summary, findings, gaps, dropped = parse_analysis(reply.text, known_sources=known)
    return AnalysisResult(
        summary=summary,
        findings=findings,
        data_gaps=gaps,
        raw_text=reply.text,
        provider=request.provider,
        model=request.model,
        model_call_id=getattr(record, "id", None),
        prompt_tokens=int(getattr(record, "prompt_tokens", 0) or 0),
        completion_tokens=completion_tokens,
        cost_cny=float(getattr(record, "cost_cny", 0.0) or 0.0),
        dropped=dropped,
    )


__all__ = [
    "AnalysisError",
    "AnalysisFinding",
    "AnalysisResult",
    "MAX_EXCERPT_CHARS",
    "MAX_POINTS",
    "analyze",
    "build_messages",
    "parse_analysis",
]
