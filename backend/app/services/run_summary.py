"""把一次推进回执翻成人话：这一轮做完了哪几格、停在什么地方、下一步做什么。

为什么单独一个模块：
- ``RunReportOut`` 里有 ``completed=[2,3]`` 这类**子任务号**，界面上只显示"N 格"，
  人点完「放行并继续跑」看不出到底推进了哪些阶段、是不是又卡在另一道门上；
- 同一段话要在三处说：任务页、``python -m backend.scripts.research_task run``、
  以及将来的定时任务报告。措辞写三遍必然走形，所以只写一遍。

判定只用回执与库里的子任务行，**不猜**：
- 做完了哪些格子＝回执的 ``completed`` 列表，逐个查 ``sequence``/``task_type``；
- 停在哪＝任务状态 + 回执的 ``stopped_reason`` + 闸门（``task_gate.gate_of``）；
- 下一步＝按停下的原因给动作，不承诺"再点一次一定成功"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.app.services.task_gate import GateInfo

#: 回执的 ``stopped_reason`` 里出现这些值，说明是"没活可干"而不是"到点了"。
_IDLE_REASONS = frozenset({"no_ready_subtask"})


@dataclass(frozen=True)
class RunSummary:
    """一次推进的人话总结。"""

    headline: str
    lines: list[str] = field(default_factory=list)
    stopped_at: str = ""
    next_step: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "lines": list(self.lines),
            "stopped_at": self.stopped_at,
            "next_step": self.next_step,
        }


def _label_of(item: Any) -> str:
    """``第 N 格 阶段名（状态）``——状态要带上，免得"做完了"和"卡住了"看起来一样。"""
    sequence = getattr(item, "sequence", None)
    task_type = getattr(item, "task_type", "") or ""
    status = getattr(item, "status", "")
    status_value = getattr(status, "value", status) or ""
    head = f"第 {sequence} 格" if sequence is not None else "某一格"
    ref = f"{head} {task_type}".strip()
    return f"{ref}（{status_value}）" if status_value else ref


def _ref_of(subtask_id: int, item: Any) -> str:
    """能翻出子任务行就用序号+阶段名，翻不出就退回编号——不能假装知道。"""
    if item is None:
        return f"子任务 #{subtask_id}"
    return _label_of(item)


def _reason_of(item: Any) -> str:
    reason = getattr(item, "failure_reason", "") or ""
    return reason


def summarise_run(
    report: Any,
    *,
    subtasks: Sequence[Any],
    task_status: str,
    gate: GateInfo | None = None,
) -> RunSummary:
    """把 ``WorkerReport`` 翻成三句话：做了多少、停在哪、下一步。

    ``subtasks`` 是任务下的**全部**子任务行（用于把 id 翻成序号与阶段名）。
    """
    by_id = {getattr(item, "id", None): item for item in subtasks}
    completed = list(getattr(report, "completed", []) or [])
    blocked = list(getattr(report, "blocked", []) or [])
    retried = list(getattr(report, "retried", []) or [])
    failed = list(getattr(report, "failed", []) or [])
    iterations = int(getattr(report, "iterations", 0) or 0)
    stopped_reason = str(getattr(report, "stopped_reason", "") or "")

    lines: list[str] = []
    for subtask_id in completed:
        lines.append(f"做完了 {_ref_of(subtask_id, by_id.get(subtask_id))}")
    for subtask_id in blocked:
        item = by_id.get(subtask_id)
        reason = _reason_of(item) if item is not None else ""
        detail = f"：{reason}" if reason else ""
        lines.append(f"卡住了 {_ref_of(subtask_id, item)}{detail}")
    for subtask_id in failed:
        item = by_id.get(subtask_id)
        reason = _reason_of(item) if item is not None else ""
        detail = f"：{reason}" if reason else ""
        lines.append(f"失败了 {_ref_of(subtask_id, item)}{detail}")
    for subtask_id in retried:
        lines.append(f"重试了 {_ref_of(subtask_id, by_id.get(subtask_id))}")

    # 停在哪 —— 先看任务状态，再看回执原因，最后才是迭代上限。
    if task_status == "paused" and gate is not None:
        stopped_at = "gate"
        if iterations:
            headline = (
                f"推进 {iterations} 格：又停在人工闸门「{gate.label}」，"
                "这一步的产物在下面，看完再决定放不放"
            )
        else:
            headline = (
                f"任务还停在人工闸门「{gate.label}」：这一轮没有可推进的格子"
                "（先放行，或者换个任务）"
            )
        next_step = gate.next_step
    elif task_status == "completed":
        stopped_at = "completed"
        headline = f"推进 {iterations} 格：任务跑完了"
        next_step = "去「研究报告」看成品；确认结论之后再决定要不要做视频。"
    elif task_status == "failed":
        stopped_at = "failed"
        headline = f"推进 {iterations} 格：任务失败停下了"
        next_step = "看下面失败那一格的 `failure_reason`，修完再点恢复；别直接重跑整个任务。"
    elif task_status == "paused":
        stopped_at = "paused"
        headline = f"推进 {iterations} 格：任务处于暂停状态"
        next_step = "看事件流最后一条的 reason；确认之后点「恢复」。"
    elif stopped_reason == "max_iterations":
        stopped_at = "max_iterations"
        headline = f"推进 {iterations} 格：到本轮上限了，还没做完（不是出错）"
        next_step = "再点一次「推进执行」，或者让常驻 worker 接着跑。"
    elif stopped_reason in _IDLE_REASONS or getattr(report, "idle", False):
        stopped_at = "idle"
        headline = f"推进 {iterations} 格：现在没有能推进的格子了"
        next_step = "看阶段表里谁还是 pending：如果有，说明它的上游没完成；如果全都完成，任务该结束了。"
    else:
        stopped_at = stopped_reason or "unknown"
        headline = f"推进 {iterations} 格"
        next_step = "看下面阶段表确认每一格的状态。"

    return RunSummary(
        headline=headline, lines=lines, stopped_at=stopped_at, next_step=next_step
    )


__all__ = ["RunSummary", "summarise_run"]
