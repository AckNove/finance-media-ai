"""模型驱动的研究规划：把"要研究什么"拆成可执行、可追溯的检索计划。

和 ``executor._plan()`` 里那条 ``planner="keywords"`` 的规则拆句的分工：

* 规则拆句只按标点与长度切，不做语义扩展，作用是**降级兜底**——没有模型时链路也能跑；
* 本模块让模型给出核心问题、子任务、检索意图、同义词、来源类型、优先级、依赖与
  完成条件，产出直接驱动下游 ``research.collect``（每个子任务的 keywords 会真的被拿去搜）。

三条纪律：

1. **不许伪造规划**。模型没跑通就如实降级，``planner`` 写 ``keywords`` 并带
   ``degraded_reason``；绝不把规则产出标成模型产出。
2. **限制规模**。子任务数、关键词数都有上限——动态规划最容易出的事故是
   "越研究越发散"，把预算烧在重复检索上。
3. **去重**。同一批关键词重复出现只保留一次，避免同一句话搜两遍。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: 一次规划最多产出多少个子任务
MAX_SUBTASKS = 6
#: 每个子任务最多几个关键词（含同义词）
MAX_KEYWORDS = 6
#: 最多几条核心问题
MAX_CORE_QUESTIONS = 6

_PRIORITIES = ("high", "medium", "low")
_WHITESPACE = re.compile(r"\s+")


class PlanningError(RuntimeError):
    """规划失败。``reason`` 是稳定原因码，供上层写进降级说明。

    ``usage`` 记已经**真的花掉**的那次调用（prompt/completion/cost）。失败也是花了钱
    的：把用量带出来，上层才能把成本记进任务账，否则任务花费会小于模型账单。
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
class PlannedSubtask:
    """一个子研究任务：问题 + 检索意图 + 关键词 + 来源类型 + 完成条件。"""

    id: str
    question: str
    search_intent: str = ""
    keywords: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    priority: str = "medium"
    depends_on: tuple[str, ...] = ()
    done_when: str = ""

    def all_queries(self) -> tuple[str, ...]:
        """真正拿去搜的词：关键词 + 同义词（去重保序）。"""
        return tuple(dict.fromkeys([*self.keywords, *self.synonyms]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "search_intent": self.search_intent,
            "keywords": list(self.keywords),
            "synonyms": list(self.synonyms),
            "source_types": list(self.source_types),
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "done_when": self.done_when,
        }


@dataclass(frozen=True)
class ResearchPlan:
    """一次模型规划的完整产出。"""

    objective: str
    core_questions: tuple[str, ...] = ()
    subtasks: tuple[PlannedSubtask, ...] = ()
    risks: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    planner: str = "model"
    provider: str = ""
    model: str = ""
    model_call_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def queries(self) -> list[str]:
        """展平成检索词列表，供 ``research.collect`` 直接用（保持子任务顺序）。"""
        out: list[str] = []
        for subtask in self.subtasks:
            for query in subtask.all_queries():
                if query not in out:
                    out.append(query)
        return out

    def high_priority(self) -> tuple[PlannedSubtask, ...]:
        return tuple(item for item in self.subtasks if item.priority == "high")

    @property
    def model_used(self) -> bool:
        """这份计划是不是真由模型产出（``planner="keywords"`` 时恒为 False）。"""
        return self.planner == "model"

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "core_questions": list(self.core_questions),
            "subtasks": [item.as_dict() for item in self.subtasks],
            "risks": list(self.risks),
            "gaps": list(self.gaps),
            "planner": self.planner,
            "model_used": self.model_used,
            "provider": self.provider,
            "model": self.model,
            "model_call_id": self.model_call_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(self.cost_cny, 6),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------- 提示词


def build_messages(
    objective: str,
    context: Mapping[str, Any] | None = None,
    *,
    max_subtasks: int | None = None,
) -> list[dict[str, str]]:
    """把研究目标（和已有资料概况）拼成规划请求。

    ``max_subtasks`` 用来在重试时**收紧要求**：上一轮输出被截断，说明要它写的东西
    太多，与其把 token 上限一味调大，不如让它少写几条、把每条写完整。
    """
    system = (
        "你是财经内容团队的研究规划员。用户会给你一个研究目标，你要把它拆成可执行的检索计划。\n"
        "只输出 JSON，不要解释。字段：\n"
        "{\n"
        '  "objective": "一句话复述你要回答的问题",\n'
        '  "core_questions": ["3~6 个必须回答的核心问题"],\n'
        '  "subtasks": [\n'
        "    {\n"
        f'      "id": "s1",\n'
        '      "question": "这个子任务要回答的具体问题",\n'
        '      "search_intent": "为什么要搜它、期望找到什么",\n'
        '      "keywords": ["2~4 个用于搜索引擎的中文关键词"],\n'
        '      "synonyms": ["1~2 个同义或替代表述"],\n'
        '      "source_types": ["official_statistics|regulator_filing|exchange_disclosure|'
        'industry_report|financial_news|company_report|academic_paper|interview"],\n'
        '      "priority": "high|medium|low",\n'
        '      "depends_on": ["s1"],\n'
        '      "done_when": "什么样的证据算够用"\n'
        "    }\n"
        "  ],\n"
        '  "risks": ["这项研究可能踩的坑"],\n'
        '  "gaps": ["现在还缺的信息"]\n'
        "}\n"
        "规则：\n"
        f"1. 子任务最多 {max_subtasks or MAX_SUBTASKS} 个；关键词必须是能直接丢进搜索引擎的中文短语，"
        "不要写成句子。\n"
        "2. 不要重复检索：不同子任务的关键词不要雷同。\n"
        "3. 优先安排能拿到**权威口径**（统计局、监管机构、交易所披露、行业协会年报）的子任务。\n"
        "4. 不要编造数字、不要预设结论；你只负责规划要查什么，不负责给答案。"
    )
    payload: dict[str, Any] = {"objective": objective}
    if context:
        payload["context"] = dict(context)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "研究目标与已有情况（JSON）：\n"
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
            raise PlanningError("bad_output", "模型输出里找不到 JSON 对象") from None
        try:
            loaded = json.loads(body[start : end + 1])
        except json.JSONDecodeError as error:
            raise PlanningError("bad_output", f"模型输出不是合法 JSON：{error}") from None
    if not isinstance(loaded, dict):
        raise PlanningError("bad_output", "模型输出的 JSON 顶层不是对象")
    return loaded


def _clean_list(values: Any, limit: int, *, min_len: int = 1) -> tuple[str, ...]:
    out: list[str] = []
    if not isinstance(values, (list, tuple)):
        return ()
    for item in values:
        text = _WHITESPACE.sub(" ", str(item or "")).strip()
        if len(text) < min_len or text in out:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def parse_plan(
    text: str, *, objective: str, max_subtasks: int = MAX_SUBTASKS
) -> ResearchPlan:
    """解析并校验模型的规划输出。

    校验不通过的部分**丢弃**而不是"猜一个"：关键词为空、问题为空的子任务直接扔掉；
    子任务过多按优先级截断。宁可规划少一点，也不要拿着一堆空壳子任务去跑搜索。
    """
    payload = _first_object(text)
    problems: list[str] = []

    core_questions = _clean_list(payload.get("core_questions"), MAX_CORE_QUESTIONS, min_len=4)

    raw_subtasks = payload.get("subtasks")
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        raise PlanningError("no_subtasks", "模型没有给出任何子任务")

    subtasks: list[PlannedSubtask] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for position, raw in enumerate(raw_subtasks):
        if not isinstance(raw, Mapping):
            continue
        question = _WHITESPACE.sub(" ", str(raw.get("question", "") or "")).strip()
        if len(question) < 6:
            problems.append(f"drop_subtask:{position}:没有可执行的问题")
            continue
        keywords = _clean_list(raw.get("keywords"), MAX_KEYWORDS, min_len=2)
        if not keywords:
            problems.append(f"drop_subtask:{position}:没有关键词，无法检索")
            continue
        synonyms = tuple(item for item in _clean_list(raw.get("synonyms"), 3, min_len=2) if item not in keywords)
        # 跨子任务去重：同一批词重复搜等于白烧钱
        fresh = tuple(item for item in keywords if item not in seen_queries)
        if not fresh:
            problems.append(f"drop_subtask:{position}:关键词与前面的子任务重复")
            continue
        seen_queries.update(fresh)

        raw_id = str(raw.get("id", "") or f"s{position + 1}").strip() or f"s{position + 1}"
        identifier = raw_id if raw_id not in seen_ids else f"{raw_id}-{position + 1}"
        seen_ids.add(identifier)

        priority = str(raw.get("priority", "") or "medium").strip().lower()
        if priority not in _PRIORITIES:
            priority = "medium"

        subtasks.append(
            PlannedSubtask(
                id=identifier,
                question=question,
                search_intent=str(raw.get("search_intent", "") or "").strip(),
                keywords=fresh,
                synonyms=synonyms,
                source_types=_clean_list(raw.get("source_types"), 4, min_len=3),
                priority=priority,
                depends_on=_clean_list(raw.get("depends_on"), 4, min_len=1),
                done_when=str(raw.get("done_when", "") or "").strip(),
            )
        )

    if not subtasks:
        raise PlanningError("no_usable_subtask", "模型的子任务全部不可用（缺问题或缺关键词）")

    order = {name: index for index, name in enumerate(_PRIORITIES)}
    subtasks.sort(key=lambda item: order.get(item.priority, 1))
    if len(subtasks) > max_subtasks:
        problems.append(f"truncated_subtasks:{len(subtasks)}->{max_subtasks}")
        subtasks = subtasks[:max_subtasks]

    return ResearchPlan(
        objective=str(payload.get("objective", "") or objective).strip() or objective,
        core_questions=core_questions,
        subtasks=tuple(subtasks),
        risks=_clean_list(payload.get("risks"), 6, min_len=4),
        gaps=_clean_list(payload.get("gaps"), 6, min_len=4),
        notes=tuple(problems),
    )


# ---------------------------------------------------------------------- 调用


def plan_research(
    objective: str,
    *,
    context: Mapping[str, Any] | None = None,
    gateway: Any = None,
    caller: Any = None,
    budget: Any = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 3200,
    purpose: str = "research.plan",
    max_subtasks: int = MAX_SUBTASKS,
) -> ResearchPlan:
    """让模型做一次研究规划。没有可用模型时抛 ``PlanningError('no_model')``。"""
    from backend.app.capabilities.model import ModelRequest, get_model_gateway
    from backend.app.capabilities.model.providers import provider_status
    from backend.app.core.caller import CallerContext

    if not (objective or "").strip():
        raise PlanningError("empty_objective", "研究目标为空，没法规划")

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
        raise PlanningError("no_model", "没有可用的模型 provider（缺密钥或未启用）")

    target_provider = provider or ready[0]
    target_model = model
    if target_model is None:
        from backend.app.core.config import get_models_config

        config = getattr(engine, "models_config", None) or get_models_config()
        candidates = [
            item for item in config.models if item.provider == target_provider and item.enabled
        ]
        if not candidates:
            raise PlanningError("no_model", f"provider {target_provider} 没有启用的模型")
        target_model = candidates[0].model

    # 规划被截断时**不能**直接降级成规则拆词（那等于白花钱、还把模型能力丢掉）：
    # 先要它少写几条子任务、把 cap 抬一档再试，逐级收紧到装得下为止。
    attempts = (
        (max_subtasks, max_tokens),
        (max(3, max_subtasks - 3), max(2400, int(max_tokens * 1.25))),
        (3, max(1600, int(max_tokens * 0.75))),
    )

    last_error: PlanningError | None = None
    last_usage: dict[str, Any] = {}
    # 逐次尝试的花费要**累加**：撞上限重试也是真花了钱，只记最后一次会让任务账
    # 少于模型账单。
    spent: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "calls": 0,
    }
    for attempt_index, (cap_subtasks, tokens_cap) in enumerate(attempts):
        request = ModelRequest(
            provider=target_provider,
            model=target_model,
            messages=build_messages(
                objective.strip(), context, max_subtasks=cap_subtasks
            ),
            purpose=purpose,
            temperature=0.3,
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
            raise PlanningError(
                reply.error_code or "model_error",
                reply.error_message or "模型调用失败",
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )

        usage = getattr(reply, "raw_usage", None) or {}
        finish_reason = str(usage.get("finish_reason") or "")
        completion_tokens = last_usage["completion_tokens"]
        if finish_reason == "length" or (tokens_cap and completion_tokens >= tokens_cap):
            last_error = PlanningError(
                "truncated",
                (
                    f"规划输出被截断（finish_reason={finish_reason or '未提供'}，"
                    f"completion_tokens={completion_tokens}，max_tokens={tokens_cap}，"
                    f"第 {attempt_index + 1}/{len(attempts)} 次尝试）"
                ),
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            )
            continue

        try:
            parsed = parse_plan(
                reply.text, objective=objective.strip(), max_subtasks=cap_subtasks
            )
        except PlanningError as error:
            # 解析失败也是花过钱的，把用量挂上去再抛，别让成本凭空消失
            raise PlanningError(
                error.reason,
                error.message,
                usage={**spent, "model_call_id": last_usage.get("model_call_id")},
            ) from error

        notes = list(parsed.notes)
        if attempt_index:
            notes.append(f"retried_with_fewer_subtasks:attempt{attempt_index + 1}")
        return ResearchPlan(
            objective=parsed.objective,
            core_questions=parsed.core_questions,
            subtasks=parsed.subtasks,
            risks=parsed.risks,
            gaps=parsed.gaps,
            planner="model",
            provider=request.provider,
            model=request.model,
            model_call_id=last_usage["model_call_id"],
            prompt_tokens=last_usage["prompt_tokens"],
            completion_tokens=completion_tokens,
            cost_cny=last_usage["cost_cny"],
            notes=tuple(notes),
        )

    assert last_error is not None  # 只有全部尝试都被截断才会走到这里
    raise last_error


def keyword_plan(objective: str, queries: Sequence[str]) -> ResearchPlan:
    """规则降级计划：只有检索词，如实标注来源。"""
    cleaned = tuple(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))
    return ResearchPlan(
        objective=objective.strip(),
        core_questions=(objective.strip(),) if objective.strip() else (),
        subtasks=(
            PlannedSubtask(
                id="s1",
                question=objective.strip() or "检索",
                search_intent="按标点与长度拆分检索词（未做语义扩展）",
                keywords=cleaned[:MAX_KEYWORDS],
            ),
        )
        if cleaned
        else (),
        planner="keywords",
        notes=("规则拆词，没有做语义扩展",),
    )


__all__ = [
    "MAX_CORE_QUESTIONS",
    "MAX_KEYWORDS",
    "MAX_SUBTASKS",
    "PlanningError",
    "PlannedSubtask",
    "ResearchPlan",
    "build_messages",
    "keyword_plan",
    "parse_plan",
    "plan_research",
]
