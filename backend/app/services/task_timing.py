"""时间都花在哪了：按事件流算一条任务的耗时构成。

数据只有一份可信来源：`task_events`（只追加，带时间戳）。子任务表本身没有 started/finished
字段——所以这里**只按事件算**，事件缺了就如实说缺了，不用 `updated_at` 猜。

一条任务的墙钟时间分三块：

- **干活**（`work_seconds`）：每格 `subtask_started` → 它自己的 `subtask_succeeded/failed`。
- **等人**（`wait_seconds`）：`paused` → 下一次 `resumed`；任务还停着就记到"现在"，标 `ongoing`。
- **模型调用**（`model_seconds`）：`model_calls.duration_ms` 之和。这块**和上面两块会重叠**
  （模型调用发生在"干活"里），所以单独列、不参与加减——把重叠的时间加进去才是真的骗人。

三条口径写在函数里：`work + wait ≈ total`（差值是没配上对的零散事件，回执里给 `unpaired`）；
同一格重试多次就累加并记次数；一格还没跑完就按"到现在"算并标 `running`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.execution import ResearchSubtask, ResearchTask, TaskEvent

STARTED = "subtask_started"
FINISHED = ("subtask_succeeded", "subtask_failed")
PAUSED = "paused"
RESUMED = "resumed"

# 还"开着"的任务状态：这些状态下总时长要算到"现在"，不能只看最后一条事件。
_OPEN_STATUSES = frozenset({"created", "planning", "running", "paused"})


class TimingError(RuntimeError):
    """这条任务的时间算不出来（比如任务不存在）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class SubtaskTiming:
    subtask_id: int
    sequence: int
    task_type: str
    runs: int
    seconds: float
    running: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "sequence": self.sequence,
            "task_type": self.task_type,
            "runs": self.runs,
            "seconds": round(self.seconds, 1),
            "running": self.running,
            "note": self.note,
        }


@dataclass(frozen=True)
class WaitWindow:
    started_at: datetime
    ended_at: datetime | None
    seconds: float
    ongoing: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "seconds": round(self.seconds, 1),
            "ongoing": self.ongoing,
        }


@dataclass(frozen=True)
class TaskTiming:
    task_id: int
    title: str
    status: str
    first_at: datetime | None = None
    last_at: datetime | None = None
    total_seconds: float = 0.0
    work_seconds: float = 0.0
    wait_seconds: float = 0.0
    model_seconds: float = 0.0
    unpaired_seconds: float = 0.0
    subtasks: tuple[SubtaskTiming, ...] = field(default_factory=tuple)
    waits: tuple[WaitWindow, ...] = field(default_factory=tuple)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "first_at": self.first_at.isoformat() if self.first_at else None,
            "last_at": self.last_at.isoformat() if self.last_at else None,
            "total_seconds": round(self.total_seconds, 1),
            "work_seconds": round(self.work_seconds, 1),
            "wait_seconds": round(self.wait_seconds, 1),
            "model_seconds": round(self.model_seconds, 1),
            "unpaired_seconds": round(self.unpaired_seconds, 1),
            "subtasks": [item.as_dict() for item in self.subtasks],
            "waits": [item.as_dict() for item in self.waits],
            "note": self.note,
        }


def _aware(value: datetime | None) -> datetime | None:
    """库里是 naive UTC（SQLite 不存时区），比较前统一补上 UTC。"""

    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _event_type(row: TaskEvent) -> str:
    return str(getattr(row.event_type, "value", row.event_type) or "")


def timing_of(session: Session, task_id: int, *, now: datetime | None = None) -> TaskTiming:
    task = session.get(ResearchTask, task_id)
    if task is None:
        raise TimingError("unknown_task", f"任务 #{task_id} 不存在")

    moment = _aware(now) or datetime.now(UTC)
    events = list(
        session.execute(
            select(TaskEvent)
            .where(TaskEvent.research_task_id == task_id)
            .order_by(TaskEvent.event_seq.asc(), TaskEvent.id.asc())
        ).scalars()
    )
    subtasks = {
        row.id: row
        for row in session.execute(
            select(ResearchSubtask).where(ResearchSubtask.research_task_id == task_id)
        ).scalars()
    }

    if not events:
        return TaskTiming(
            task_id=task.id,
            title=task.title,
            status=str(getattr(task.status, "value", task.status) or ""),
            note="这条任务还没有任何事件记录：算不出耗时（不是 0 秒）",
        )

    first_at = _aware(events[0].created_at)
    last_at = _aware(events[-1].created_at)
    # 任务还开着（含停在闸门上）时，"总时长"应该算到**现在**：
    # 只看最后一条事件的话，一个停了 20 分钟等人放行的任务会显示"总共 0.3 秒"，
    # 却同时显示"等人 20 分钟"——两个数字当场自相矛盾。
    status_value = str(getattr(task.status, "value", task.status) or "")
    end_at = moment if status_value in _OPEN_STATUSES else (last_at or moment)
    total = max(0.0, (end_at - (first_at or moment)).total_seconds())

    # 每格：started → 它自己的一次 finished（重试就再来一对）
    started: dict[int, datetime] = {}
    per_subtask: dict[int, list[float]] = {}
    for row in events:
        kind = _event_type(row)
        when = _aware(row.created_at)
        if when is None or row.subtask_id is None:
            continue
        if kind == STARTED:
            started[row.subtask_id] = when
        elif kind in FINISHED:
            begin = started.pop(row.subtask_id, None)
            if begin is None:
                # 没配上 started 的 finished：算不出时长，也不编一个数出来
                continue
            per_subtask.setdefault(row.subtask_id, []).append(max(0.0, (when - begin).total_seconds()))
    # 还在跑的：started 没配上 finished，按"到现在"算，并标出来
    running_rows: list[SubtaskTiming] = []
    for subtask_id, begin in list(started.items()):
        row = subtasks.get(subtask_id)
        seconds = max(0.0, (moment - begin).total_seconds())
        running_rows.append(
            SubtaskTiming(
                subtask_id=subtask_id,
                sequence=int(getattr(row, "sequence", 0) or 0),
                task_type=str(getattr(row, "task_type", "") or ""),
                runs=1,
                seconds=seconds,
                running=True,
                note="这一格开始了但还没有结束事件（可能正在跑，也可能上次进程被杀了）",
            )
        )
        started.pop(subtask_id, None)

    timings = [
        SubtaskTiming(
            subtask_id=subtask_id,
            sequence=int(getattr(subtasks.get(subtask_id), "sequence", 0) or 0),
            task_type=str(getattr(subtasks.get(subtask_id), "task_type", "") or ""),
            runs=len(values),
            seconds=sum(values),
        )
        for subtask_id, values in per_subtask.items()
    ]
    timings.sort(key=lambda item: (item.sequence, item.subtask_id))
    work = sum(item.seconds for item in timings)

    # 等人：paused → 下一次 resumed；还停着就记到"现在"
    waits: list[WaitWindow] = []
    paused_at: datetime | None = None
    for row in events:
        kind = _event_type(row)
        when = _aware(row.created_at)
        if kind == PAUSED and when is not None:
            paused_at = when
        elif kind == RESUMED and paused_at is not None and when is not None:
            waits.append(WaitWindow(started_at=paused_at, ended_at=when, seconds=max(0.0, (when - paused_at).total_seconds())))
            paused_at = None
    if paused_at is not None:
        waits.append(
            WaitWindow(started_at=paused_at, ended_at=None, seconds=max(0.0, (moment - paused_at).total_seconds()), ongoing=True)
        )
    wait = sum(item.seconds for item in waits)

    model_seconds = _model_seconds(session, task_id)

    gap = max(0.0, total - work - wait)
    note = ""
    if running_rows:
        note = f"有 {len(running_rows)} 格只有开始没有结束，它们的耗时记到「现在」，可能偏大"
    elif gap > 1.0:
        note = f"总时长比「干活 + 等人」多 {gap:.0f} 秒：任务创建到第一格开始之间的空档没算进任何一块"

    return TaskTiming(
        task_id=task.id,
        title=task.title,
        status=str(getattr(task.status, "value", task.status) or ""),
        first_at=first_at,
        last_at=last_at,
        total_seconds=total,
        work_seconds=work,
        wait_seconds=wait,
        model_seconds=model_seconds,
        unpaired_seconds=gap,
        subtasks=tuple(timings + running_rows),
        waits=tuple(waits),
        note=note,
    )


def _model_seconds(session: Session, task_id: int) -> float:
    """模型调用耗时（来自 `model_calls.duration_ms`）。和墙钟重叠，单独列。"""

    from backend.app.models.metering import ModelCall

    total_ms = session.execute(
        select(func.coalesce(func.sum(ModelCall.duration_ms), 0)).where(ModelCall.research_task_id == task_id)
    ).scalar()
    return float(total_ms or 0) / 1000.0
