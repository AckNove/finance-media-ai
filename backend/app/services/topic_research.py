"""从选题直接建研究任务：把标题、起点来源、栏目一起带过去。

为什么要有这一层：选题采纳完只落在 ``topics`` 表里，人还得切到「研究任务」页把标题、
研究目标重新敲一遍。抄错一个字，"这条任务研究的是哪个选题"就断了——而 ``research_tasks.topic_id``
本来就是这个用途。

三条纪律：

1. **同一条选题不许并行开两条没结束的任务**。查重按"没结束"（created/planning/running/paused）算，
   已经失败或完成的不算——那种情况下再开一条是正常的。
2. **目标文案是模板，不是结论**：自动生成的 objective 只写"要回答什么"，不预设任何答案；
   人可以在请求里覆盖它。
3. **不改选题状态**。建了任务就把选题标成"研究中"听起来顺，但任务失败/停下时没人把它改回来，
   最后会留下一堆"永远在研究中"的假状态。宁可不动，并在回执里写明。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.enums import ResearchTaskStatus
from backend.app.models.execution import ResearchTask
from backend.app.models.topic import Topic

logger = logging.getLogger(__name__)

DEFAULT_PIPELINE = "research_only"
#: 还没结束的任务（有就算"这条选题正在被研究"）
ACTIVE_STATUSES: tuple[ResearchTaskStatus, ...] = (
    ResearchTaskStatus.CREATED,
    ResearchTaskStatus.PLANNING,
    ResearchTaskStatus.RUNNING,
    ResearchTaskStatus.PAUSED,
)
MAX_QUERIES = 20
# 采纳建议时写进描述的行形如 ``- S12《标题》 发布方 日期 链接``
_SOURCE_LINE = re.compile(r"^-\s*S(\d+)《", re.MULTILINE)


class TopicResearchError(RuntimeError):
    """带稳定原因码：路由层据此决定 409 还是 422。"""

    def __init__(self, reason: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class TopicResearchPlan:
    """建任务之前就能给人看的"要建什么"。"""

    topic_id: int
    title: str
    objective: str
    pipeline: str
    queries: tuple[str, ...]
    source_ids: tuple[int, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "objective": self.objective,
            "pipeline": self.pipeline,
            "queries": list(self.queries),
            "source_ids": list(self.source_ids),
            "note": self.note,
        }


def source_ids_from_description(text: str | None) -> tuple[int, ...]:
    """从选题描述里认出采纳时写下的来源编号。

    只认我们自己的格式（``- S12《…》``）；描述被人手改过就认不出来——
    那也不算错，回执里会写"没从描述里认出来源编号"。
    """

    if not text:
        return ()
    seen: list[int] = []
    for match in _SOURCE_LINE.finditer(text):
        value = int(match.group(1))
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def active_task_of(session: Session, topic_id: int) -> ResearchTask | None:
    """这条选题下还没结束的任务（有就说明不该再开一条）。"""

    return session.scalars(
        select(ResearchTask)
        .where(
            ResearchTask.topic_id == topic_id,
            ResearchTask.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ResearchTask.id.desc())
    ).first()


def _objective_of(topic: Topic, source_ids: Sequence[int]) -> str:
    """自动目标：只写"要回答什么"，不预设答案。"""

    base = (
        f"回答「{topic.title}」：它是什么、为什么会这样、谁受影响、"
        "有哪些带出处的数字可以证实。"
    )
    if source_ids:
        base += f" 起点是 {len(source_ids)} 条已入库来源（清单在选题描述里，可直接回原文核对）。"
    return base


def plan_for_topic(
    topic: Topic,
    *,
    source_ids: Iterable[int] = (),
    pipeline: str = DEFAULT_PIPELINE,
    title: str | None = None,
    objective: str = "",
    queries: Iterable[str] = (),
) -> TopicResearchPlan:
    """算一条"要建的任务长什么样"，不落库。"""

    clean_title = (title or "").strip() or (topic.title or "").strip()
    if not clean_title:
        raise TopicResearchError("empty_title", "选题没有标题，也没给任务标题：先把选题标题补上")
    ids = tuple(int(value) for value in source_ids if int(value) > 0)
    clean_objective = objective.strip() or _objective_of(topic, ids)
    clean_queries = tuple(dict.fromkeys(text.strip() for text in queries if str(text).strip()))
    if not clean_queries:
        # 没有显式检索词时只放标题一条：规则规划会基于它扩词，不拿模板句子去搜
        clean_queries = (clean_title,)
    if len(clean_queries) > MAX_QUERIES:
        clean_queries = clean_queries[:MAX_QUERIES]

    note = (
        f"任务挂在选题 #{topic.id} 上；选题状态不会自动改"
        "（任务失败或停下时没人把它改回来，宁可不写假状态）"
    )
    return TopicResearchPlan(
        topic_id=topic.id,
        title=clean_title,
        objective=clean_objective,
        pipeline=pipeline or DEFAULT_PIPELINE,
        queries=clean_queries,
        source_ids=ids,
        note=note,
    )


def start_research(
    session: Session,
    runtime: Any,
    topic: Topic,
    *,
    plan: TopicResearchPlan,
    budget_max_calls: int | None = None,
    budget_max_cost_cny: Any = None,
    max_attempts: int = 2,
    start: bool = True,
) -> Any:
    """真建任务：先查重，再交给 Orchestrator 按产线拆解。"""

    existing = active_task_of(session, topic.id)
    if existing is not None:
        raise TopicResearchError(
            "task_in_progress",
            f"这条选题已经有一个没结束的任务 #{existing.id}"
            f"（状态 {getattr(existing.status, 'value', existing.status)}）："
            "接着推它就行，别开第二条——两条并行只会把同一批抓取各做一遍，钱也各花一遍",
            details={"task_id": existing.id},
        )

    from backend.app.execution.orchestrator import Orchestrator

    orchestrator = Orchestrator(runtime)
    try:
        result = orchestrator.plan_task(
            title=plan.title,
            objective=plan.objective,
            pipeline=plan.pipeline,
            topic_id=topic.id,
            queries=list(plan.queries),
            files=[],
            skip=[],
            skip_reason="",
            budget_max_calls=budget_max_calls,
            budget_max_cost_cny=budget_max_cost_cny,
            max_attempts=max_attempts,
            start=start,
            requested_by="human",
        )
    except Exception as error:  # noqa: BLE001 - 原因码原样往上抛
        reason = getattr(error, "reason", "")
        if reason in {"unknown_pipeline", "unknown_topic"}:
            raise TopicResearchError(
                reason, getattr(error, "message", str(error))
            ) from error
        raise
    return result
