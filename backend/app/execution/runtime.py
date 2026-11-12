"""任务运行时：把 DAG 放进数据库，用租约与令牌保证"谁在跑、跑到哪、能不能接着跑"。

设计取舍（为什么不用 Celery/Redis）：

* 这是一台个人工作站上的单机系统，多一个 broker 就多一套要运维、要备份、
  要跟着 Windows 重启一起恢复的东西；SQLite 里加 ``claimed_by`` + ``lease_until``
  已经能表达"被谁抢走、租约到期没"。
* **崩溃恢复是可验证的**：进程被杀后租约自然过期，:meth:`TaskRuntime.reap_expired_leases`
  把子任务放回 ready，重跑时不重复计费（成本只在成功完成时累加）。
* ``attempt_token`` 每次抢占轮换：旧 worker 拿着上一轮的令牌写不进结果，
  避免"超时但仍在跑"的进程覆盖别人的成果。

诚实边界：

* 这里的原子性依赖 SQLite 的写事务；多进程并发写会有 ``database is locked``，
  连接层已设 ``busy_timeout=5000``，但仍建议 ``max_concurrency=1``（与
  ``config/settings.yaml`` 的 ``execution.max_concurrency`` 一致）。
* 运行时**不调用模型、不抓网页**——它只负责状态机；真正的执行在
  :mod:`backend.app.execution.executor`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database.session import get_session_factory
from backend.app.execution.task_types import TaskType, coerce as coerce_task_type
from backend.app.models.enums import (
    ResearchTaskStatus,
    SubtaskStatus,
    TaskEventType,
)
from backend.app.models.execution import (
    ResearchSubtask,
    ResearchSubtaskDependency,
    ResearchTask,
    TaskEvent,
)

DEFAULT_LEASE_SECONDS = 300
DEFAULT_HEARTBEAT_SECONDS = 100
_TERMINAL_SUBTASK = {
    SubtaskStatus.SUCCEEDED,
    SubtaskStatus.FAILED,
    SubtaskStatus.SKIPPED,
    SubtaskStatus.CANCELLED,
    SubtaskStatus.BLOCKED,
}


class TaskRuntimeError(RuntimeError):
    """运行时拒绝操作。``reason`` 是稳定错误码，便于测试与前端映射。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 不保存时区：读回来是 naive，这里统一按 UTC 解释。

    缺少这一步会踩 ``can't compare offset-naive and offset-aware datetimes``——
    租约判断与崩溃恢复都依赖这个比较，所以集中处理一次。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _status_key(key: Any) -> str:
    """把枚举状态统计的 key 变成稳定字符串（``ResearchTaskStatus.RUNNING`` → ``running``）。"""
    return str(getattr(key, "value", key))


def _as_payload(output: Any) -> Any:
    """把执行结果落成可读的 JSON：字典原样存，其余标量包一层 ``value``。

    为什么不再统一包 ``{"value": ...}``：下游阶段（研究综述、事实核查、视频脚本）
    要直接读上一阶段的 ``documents``/``results`` 等字段，外面多一层 key 会让每个
    消费者都要知道这个约定，很容易写成 ``payload["value"]["results"]`` 这种隐性耦合。
    """
    if output is None:
        return None
    if isinstance(output, Mapping):
        return dict(output)
    if isinstance(output, (list, tuple)):
        return list(output)
    return {"value": output}


@dataclass(frozen=True, slots=True)
class SubtaskSnapshot:
    id: int
    sequence: int
    question: str
    task_type: str
    assignee: str | None
    status: str
    attempts: int
    max_attempts: int
    failure_reason: str | None = None
    output: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "question": self.question,
            "task_type": self.task_type,
            "assignee": self.assignee,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    id: int
    title: str
    objective: str
    status: str
    topic_id: int | None
    calls_spent: int
    tokens_spent: int
    cost_spent_cny: Decimal
    claimed_by: str | None
    lease_until: datetime | None
    failure_reason: str | None
    subtasks: tuple[SubtaskSnapshot, ...] = ()

    @property
    def finished(self) -> bool:
        return self.status in {
            ResearchTaskStatus.COMPLETED.value,
            ResearchTaskStatus.FAILED.value,
            ResearchTaskStatus.CANCELLED.value,
        }

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for subtask in self.subtasks:
            counts[subtask.status] = counts.get(subtask.status, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "topic_id": self.topic_id,
            "calls_spent": self.calls_spent,
            "tokens_spent": self.tokens_spent,
            "cost_spent_cny": str(self.cost_spent_cny),
            "claimed_by": self.claimed_by,
            "lease_until": self.lease_until.isoformat() if self.lease_until else None,
            "failure_reason": self.failure_reason,
            "counts": self.counts(),
            "subtasks": [subtask.as_dict() for subtask in self.subtasks],
        }


@dataclass(frozen=True, slots=True)
class Claim:
    """一次成功抢占：worker 拿它去执行，并用 ``attempt_token`` 回写结果。"""

    task_id: int
    subtask_id: int
    sequence: int
    task_type: str
    assignee: str | None
    question: str
    attempt: int
    max_attempts: int
    attempt_token: str
    lease_until: datetime
    input_payload: Any = None
    budget_max_calls: int | None = None
    budget_max_cost_cny: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "sequence": self.sequence,
            "task_type": self.task_type,
            "assignee": self.assignee,
            "question": self.question,
            "attempt": self.attempt,
            "attempt_token": self.attempt_token,
            "lease_until": self.lease_until.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RuntimeStats:
    tasks: dict[str, int] = field(default_factory=dict)
    subtasks: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"tasks": dict(self.tasks), "subtasks": dict(self.subtasks)}


class TaskRuntime:
    """基于数据库的任务状态机。所有方法都是短事务，不持有 ORM 对象给外部。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._clock = clock
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex)

    # ------------------------------------------------------------------ 会话

    def _session(self) -> Session:
        return self._session_factory()

    # ------------------------------------------------------------------ 读

    def task(self, task_id: int) -> TaskSnapshot:
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            return self._snapshot(task, session)

    def list_tasks(self, *, status: str | None = None, limit: int = 20) -> list[TaskSnapshot]:
        with self._session() as session:
            statement = select(ResearchTask).order_by(ResearchTask.id.desc()).limit(limit)
            if status:
                statement = statement.where(ResearchTask.status == ResearchTaskStatus(status))
            return [self._snapshot(task, session) for task in session.scalars(statement)]

    def events(self, task_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(
                select(TaskEvent)
                .where(TaskEvent.research_task_id == task_id)
                .order_by(TaskEvent.event_seq)
                .limit(limit)
            )
            return [
                {
                    "seq": row.event_seq,
                    "type": row.event_type.value,
                    "subtask_id": row.subtask_id,
                    "payload": row.payload,
                    "cost_delta": str(row.cost_delta) if row.cost_delta is not None else None,
                    "actor": row.actor,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def stats(self) -> RuntimeStats:
        with self._session() as session:
            tasks = dict(
                session.execute(
                    select(ResearchTask.status, func.count()).group_by(ResearchTask.status)
                ).all()
            )
            subtasks = dict(
                session.execute(
                    select(ResearchSubtask.status, func.count()).group_by(ResearchSubtask.status)
                ).all()
            )
        return RuntimeStats(
            tasks={_status_key(key): int(value) for key, value in tasks.items()},
            subtasks={_status_key(key): int(value) for key, value in subtasks.items()},
        )

    # ------------------------------------------------------------------ 建任务

    def create_task(
        self,
        *,
        title: str,
        objective: str = "",
        topic_id: int | None = None,
        scope: str | None = None,
        out_of_scope: str | None = None,
        planner: str | None = None,
        requested_by: str = "human",
        budget_max_calls: int | None = None,
        budget_max_cost_cny: Decimal | float | None = None,
    ) -> TaskSnapshot:
        if not title.strip():
            raise TaskRuntimeError("empty_title", "任务标题不能为空")
        with self._session() as session:
            task = ResearchTask(
                title=title.strip(),
                objective=(objective or title).strip(),
                topic_id=topic_id,
                scope=scope,
                out_of_scope=out_of_scope,
                planner=planner,
                requested_by=requested_by,
                status=ResearchTaskStatus.CREATED,
                budget_max_calls=budget_max_calls,
                budget_max_cost=(
                    Decimal(str(budget_max_cost_cny)) if budget_max_cost_cny is not None else None
                ),
                version=1,
            )
            session.add(task)
            session.flush()
            self._event(session, task, TaskEventType.CREATED, actor=requested_by,
                        payload={"title": task.title, "objective": task.objective})
            session.commit()
            return self._snapshot(task, session)

    def add_subtask(
        self,
        task_id: int,
        *,
        sequence: int,
        question: str,
        task_type: str | TaskType,
        assignee: str | None = None,
        input_payload: dict[str, Any] | None = None,
        depends_on: Iterable[int] = (),
        max_attempts: int = 2,
    ) -> SubtaskSnapshot:
        # 只接受注册表里的 task_type：写库前就拦，而不是留给运行期才发现
        resolved_type = coerce_task_type(
            task_type if isinstance(task_type, str) else task_type.value
        ).value
        if not question.strip():
            raise TaskRuntimeError("empty_question", "子任务问题不能为空")
        if max_attempts < 1:
            raise TaskRuntimeError("bad_max_attempts", "max_attempts 至少为 1")
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            if task.status in {ResearchTaskStatus.COMPLETED, ResearchTaskStatus.CANCELLED}:
                raise TaskRuntimeError("task_closed", "任务已结束，不能再加子任务")
            exists = session.scalar(
                select(ResearchSubtask.id).where(
                    ResearchSubtask.research_task_id == task_id,
                    ResearchSubtask.sequence == sequence,
                )
            )
            if exists:
                raise TaskRuntimeError("duplicate_sequence", f"序号 {sequence} 已被占用")
            dependency_ids = list(depends_on)
            for dependency in dependency_ids:
                if session.get(ResearchSubtask, dependency) is None:
                    raise TaskRuntimeError("unknown_dependency", f"依赖的子任务 {dependency} 不存在")
            subtask = ResearchSubtask(
                research_task_id=task_id,
                sequence=sequence,
                question=question.strip(),
                task_type=resolved_type,
                assignee=assignee,
                status=SubtaskStatus.PENDING,
                attempts=0,
                max_attempts=max_attempts,
                input_payload=input_payload,
            )
            session.add(subtask)
            session.flush()
            for dependency in dependency_ids:
                session.add(
                    ResearchSubtaskDependency(
                        subtask_id=subtask.id,
                        depends_on_subtask_id=dependency,
                        created_at=self._clock(),
                    )
                )
            session.flush()
            self._refresh_readiness(session, task)
            session.commit()
            return self._subtask_snapshot(subtask)

    def set_plan(
        self,
        task_id: int,
        plan: dict[str, Any],
        *,
        planner: str | None = None,
        actor: str = "orchestrator",
    ) -> TaskSnapshot:
        """把编排结果（阶段、人工闸门、来源约束）落到任务的 ``plan`` 列。

        计划是**可审计的数据**而不是内存里的临时对象：任务重启后还能看出
        "这一跑本来打算做什么、哪些阶段被设计成人工闸门"。
        """
        if not isinstance(plan, dict):
            raise TaskRuntimeError("bad_plan", "plan 必须是字典")
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            if task.status in {ResearchTaskStatus.COMPLETED, ResearchTaskStatus.CANCELLED}:
                raise TaskRuntimeError("task_closed", "任务已结束，不能再改计划")
            task.plan = plan
            if planner:
                task.planner = planner
            self._event(
                session,
                task,
                TaskEventType.PLAN_CREATED,
                actor=actor,
                payload={
                    "pipeline": plan.get("pipeline"),
                    "task_stages": len(plan.get("stages", [])),
                    "human_gates": [gate.get("id") for gate in plan.get("human_gates", [])],
                },
            )
            session.commit()
            return self._snapshot(task, session)

    def start_task(self, task_id: int) -> TaskSnapshot:
        """created/planning → running，并把无依赖的子任务置为 ready。"""
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            if task.status is ResearchTaskStatus.RUNNING:
                return self._snapshot(task, session)
            if task.status in {ResearchTaskStatus.COMPLETED, ResearchTaskStatus.CANCELLED}:
                raise TaskRuntimeError("task_closed", "任务已结束，不能重新启动")
            task.status = ResearchTaskStatus.RUNNING
            task.failure_reason = None
            session.flush()
            self._refresh_readiness(session, task)
            self._event(session, task, TaskEventType.RESUMED, actor="runtime", payload={"phase": "start"})
            session.commit()
            return self._snapshot(task, session)

    # ------------------------------------------------------------------ 抢占与回写

    def claim_next(self, *, worker: str, task_id: int | None = None) -> Claim | None:
        """抢占下一个可执行的子任务；没有就返回 None（不是错误）。"""
        if not worker.strip():
            raise TaskRuntimeError("empty_worker", "worker 名字不能为空")
        with self._session() as session:
            statement = select(ResearchTask).where(ResearchTask.status == ResearchTaskStatus.RUNNING)
            if task_id is not None:
                statement = statement.where(ResearchTask.id == task_id)
            task = session.scalars(statement.order_by(ResearchTask.id)).first()
            if task is None:
                return None
            # 同一时刻只允许一个 worker 持有任务租约：别人还在跑就等
            now = self._clock()
            held_until = _as_utc(task.lease_until)
            if task.claimed_by and held_until and held_until > now and task.claimed_by != worker:
                return None
            subtask = session.scalars(
                select(ResearchSubtask)
                .where(
                    ResearchSubtask.research_task_id == task.id,
                    ResearchSubtask.status == SubtaskStatus.READY,
                )
                .order_by(ResearchSubtask.sequence)
            ).first()
            if subtask is None:
                self._maybe_finish(session, task)
                session.commit()
                return None

            token = self._token_factory()
            lease_until = now + timedelta(seconds=self.lease_seconds)
            subtask.status = SubtaskStatus.RUNNING
            subtask.attempts += 1
            task.claimed_by = worker
            task.lease_until = lease_until
            task.lease_heartbeat_at = now
            task.attempt_token = token
            self._event(
                session,
                task,
                TaskEventType.CLAIMED,
                subtask=subtask,
                actor=worker,
                payload={"attempt": subtask.attempts, "task_type": subtask.task_type},
            )
            self._event(
                session,
                task,
                TaskEventType.SUBTASK_STARTED,
                subtask=subtask,
                actor=worker,
                payload={"sequence": subtask.sequence},
            )
            session.commit()
            return Claim(
                task_id=task.id,
                subtask_id=subtask.id,
                sequence=subtask.sequence,
                task_type=subtask.task_type,
                assignee=subtask.assignee,
                question=subtask.question,
                attempt=subtask.attempts,
                max_attempts=subtask.max_attempts,
                attempt_token=token,
                lease_until=lease_until,
                input_payload=subtask.input_payload,
                budget_max_calls=task.budget_max_calls,
                budget_max_cost_cny=task.budget_max_cost,
            )

    def heartbeat(self, claim: Claim, *, worker: str | None = None) -> Claim:
        """续租。令牌不符直接拒绝——防止过期 worker 继续推进。"""
        with self._session() as session:
            task = self._require_token(session, claim)
            now = self._clock()
            lease_until = now + timedelta(seconds=self.lease_seconds)
            task.lease_until = lease_until
            task.lease_heartbeat_at = now
            session.commit()
            return Claim(
                task_id=claim.task_id,
                subtask_id=claim.subtask_id,
                sequence=claim.sequence,
                task_type=claim.task_type,
                assignee=claim.assignee,
                question=claim.question,
                attempt=claim.attempt,
                max_attempts=claim.max_attempts,
                attempt_token=claim.attempt_token,
                lease_until=lease_until,
                input_payload=claim.input_payload,
                budget_max_calls=claim.budget_max_calls,
                budget_max_cost_cny=claim.budget_max_cost_cny,
            )

    def complete(
        self,
        claim: Claim,
        *,
        output: Any = None,
        calls: int = 0,
        tokens: int = 0,
        cost_cny: Decimal | float = 0,
        agent_run_id: int | None = None,
    ) -> TaskSnapshot:
        """标记子任务成功，累加成本，唤醒后继。"""
        with self._session() as session:
            task = self._require_token(session, claim)
            subtask = session.get(ResearchSubtask, claim.subtask_id)
            if subtask is None:
                raise TaskRuntimeError("unknown_subtask", f"子任务 {claim.subtask_id} 不存在")
            subtask.status = SubtaskStatus.SUCCEEDED
            subtask.output_payload = _as_payload(output)
            subtask.failure_reason = None
            if agent_run_id is not None:
                subtask.agent_run_id = agent_run_id
            cost = Decimal(str(cost_cny))
            task.calls_spent += max(0, calls)
            task.tokens_spent += max(0, tokens)
            task.cost_spent = (task.cost_spent or Decimal("0")) + cost
            self._event(
                session,
                task,
                TaskEventType.SUBTASK_SUCCEEDED,
                subtask=subtask,
                actor=task.claimed_by,
                payload={"sequence": subtask.sequence, "calls": calls, "tokens": tokens},
                cost_delta=cost,
            )
            self._clear_lease(task)
            session.flush()
            self._refresh_readiness(session, task)
            self._maybe_finish(session, task)
            session.commit()
            return self._snapshot(task, session)

    def fail(
        self,
        claim: Claim,
        *,
        reason: str,
        retryable: bool = True,
        cost_cny: Decimal | float = 0,
    ) -> TaskSnapshot:
        """标记失败。可重试且还有次数就放回 ready，否则终结。"""
        with self._session() as session:
            task = self._require_token(session, claim)
            subtask = session.get(ResearchSubtask, claim.subtask_id)
            if subtask is None:
                raise TaskRuntimeError("unknown_subtask", f"子任务 {claim.subtask_id} 不存在")
            cost = Decimal(str(cost_cny))
            if cost:
                task.cost_spent = (task.cost_spent or Decimal("0")) + cost
            subtask.failure_reason = reason
            can_retry = retryable and subtask.attempts < subtask.max_attempts
            if can_retry:
                subtask.status = SubtaskStatus.READY
                self._event(
                    session,
                    task,
                    TaskEventType.RETRIED,
                    subtask=subtask,
                    actor=task.claimed_by,
                    payload={"reason": reason, "attempt": subtask.attempts},
                    cost_delta=cost or None,
                )
            else:
                subtask.status = SubtaskStatus.FAILED
                self._event(
                    session,
                    task,
                    TaskEventType.SUBTASK_FAILED,
                    subtask=subtask,
                    actor=task.claimed_by,
                    payload={"reason": reason, "attempt": subtask.attempts},
                    cost_delta=cost or None,
                )
            self._clear_lease(task)
            session.flush()
            self._refresh_readiness(session, task)
            self._maybe_finish(session, task)
            session.commit()
            return self._snapshot(task, session)

    def release(self, claim: Claim, *, reason: str = "released") -> TaskSnapshot:
        """主动放回队列（不消耗重试次数，用于优雅停机）。"""
        with self._session() as session:
            task = self._require_token(session, claim)
            subtask = session.get(ResearchSubtask, claim.subtask_id)
            if subtask is None:
                raise TaskRuntimeError("unknown_subtask", f"子任务 {claim.subtask_id} 不存在")
            subtask.status = SubtaskStatus.READY
            subtask.attempts = max(0, subtask.attempts - 1)
            self._clear_lease(task)
            self._event(
                session,
                task,
                TaskEventType.RETRIED,
                subtask=subtask,
                actor=task.claimed_by,
                payload={"reason": reason, "released": True},
            )
            session.commit()
            return self._snapshot(task, session)

    # ------------------------------------------------------------------ 人工介入

    def request_human_review(self, claim: Claim, *, reason: str) -> TaskSnapshot:
        """把子任务挂起等人看（blocked），并把任务置为 paused，避免继续花钱。"""
        with self._session() as session:
            task = self._require_token(session, claim)
            subtask = session.get(ResearchSubtask, claim.subtask_id)
            if subtask is None:
                raise TaskRuntimeError("unknown_subtask", f"子任务 {claim.subtask_id} 不存在")
            subtask.status = SubtaskStatus.BLOCKED
            subtask.failure_reason = f"human_review: {reason}"
            task.status = ResearchTaskStatus.PAUSED
            self._clear_lease(task)
            self._event(
                session,
                task,
                TaskEventType.PAUSED,
                subtask=subtask,
                actor=task.claimed_by,
                payload={"reason": reason, "human_review": True},
            )
            session.commit()
            return self._snapshot(task, session)

    def pause(self, task_id: int, *, reason: str = "") -> TaskSnapshot:
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            if task.status in {ResearchTaskStatus.COMPLETED, ResearchTaskStatus.CANCELLED}:
                raise TaskRuntimeError("task_closed", "任务已结束")
            task.status = ResearchTaskStatus.PAUSED
            for subtask in session.scalars(
                select(ResearchSubtask).where(
                    ResearchSubtask.research_task_id == task_id,
                    ResearchSubtask.status == SubtaskStatus.RUNNING,
                )
            ):
                subtask.status = SubtaskStatus.READY
                subtask.attempts = max(0, subtask.attempts - 1)
            self._clear_lease(task)
            self._event(session, task, TaskEventType.PAUSED, actor="human", payload={"reason": reason})
            session.commit()
            return self._snapshot(task, session)

    def resume(self, task_id: int) -> TaskSnapshot:
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            if task.status is not ResearchTaskStatus.PAUSED:
                raise TaskRuntimeError("not_paused", "只有暂停中的任务可以恢复")
            task.status = ResearchTaskStatus.RUNNING
            # 人工审核过的子任务退回 ready，重新排队
            for subtask in session.scalars(
                select(ResearchSubtask).where(
                    ResearchSubtask.research_task_id == task_id,
                    ResearchSubtask.status == SubtaskStatus.BLOCKED,
                )
            ):
                subtask.status = SubtaskStatus.READY
                subtask.failure_reason = None
            self._event(session, task, TaskEventType.RESUMED, actor="human", payload={})
            session.flush()
            self._refresh_readiness(session, task)
            session.commit()
            return self._snapshot(task, session)

    def cancel(self, task_id: int, *, reason: str = "") -> TaskSnapshot:
        with self._session() as session:
            task = session.get(ResearchTask, task_id)
            if task is None:
                raise TaskRuntimeError("unknown_task", f"任务 {task_id} 不存在")
            task.status = ResearchTaskStatus.CANCELLED
            task.failure_reason = reason or None
            for subtask in session.scalars(
                select(ResearchSubtask).where(
                    ResearchSubtask.research_task_id == task_id,
                    ResearchSubtask.status.in_(
                        [SubtaskStatus.PENDING, SubtaskStatus.READY, SubtaskStatus.RUNNING]
                    ),
                )
            ):
                subtask.status = SubtaskStatus.CANCELLED
            self._clear_lease(task)
            self._event(session, task, TaskEventType.CANCELLED, actor="human", payload={"reason": reason})
            session.commit()
            return self._snapshot(task, session)

    # ------------------------------------------------------------------ 崩溃恢复

    def reap_expired_leases(self, *, worker: str = "reaper") -> list[int]:
        """租约过期的运行中子任务放回 ready（超过重试次数则判失败）。

        这是"Windows 重启后能接着跑"的关键一步：进程被杀不会留下永久卡死的 running。
        """
        now = self._clock()
        recovered: list[int] = []
        with self._session() as session:
            # SQL 层比较跨库不可靠（SQLite 存的是字符串），这里取出来在 Python 里判
            running = session.scalars(
                select(ResearchTask).where(ResearchTask.status == ResearchTaskStatus.RUNNING)
            ).all()
            stale = [
                task
                for task in running
                if _as_utc(task.lease_until) is not None and _as_utc(task.lease_until) < now
            ]
            for task in stale:
                for subtask in session.scalars(
                    select(ResearchSubtask).where(
                        ResearchSubtask.research_task_id == task.id,
                        ResearchSubtask.status == SubtaskStatus.RUNNING,
                    )
                ):
                    recovered.append(subtask.id)
                    if subtask.attempts >= subtask.max_attempts:
                        subtask.status = SubtaskStatus.FAILED
                        subtask.failure_reason = "lease_expired"
                        self._event(
                            session,
                            task,
                            TaskEventType.SUBTASK_FAILED,
                            subtask=subtask,
                            actor=worker,
                            payload={"reason": "lease_expired", "attempt": subtask.attempts},
                        )
                    else:
                        subtask.status = SubtaskStatus.READY
                        self._event(
                            session,
                            task,
                            TaskEventType.RETRIED,
                            subtask=subtask,
                            actor=worker,
                            payload={"reason": "lease_expired", "attempt": subtask.attempts},
                        )
                self._clear_lease(task)
                self._refresh_readiness(session, task)
                self._maybe_finish(session, task)
            session.commit()
        return recovered

    # ------------------------------------------------------------------ 内部

    def _require_token(self, session: Session, claim: Claim) -> ResearchTask:
        task = session.get(ResearchTask, claim.task_id)
        if task is None:
            raise TaskRuntimeError("unknown_task", f"任务 {claim.task_id} 不存在")
        if task.attempt_token != claim.attempt_token:
            raise TaskRuntimeError(
                "stale_attempt",
                "抢占令牌已失效（租约过期或被其他 worker 接手），拒绝写入",
            )
        return task

    def _clear_lease(self, task: ResearchTask) -> None:
        task.claimed_by = None
        task.lease_until = None
        task.lease_heartbeat_at = None
        task.attempt_token = None

    def _event(
        self,
        session: Session,
        task: ResearchTask,
        event_type: TaskEventType,
        *,
        subtask: ResearchSubtask | None = None,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        cost_delta: Decimal | None = None,
    ) -> TaskEvent:
        next_seq = (
            session.scalar(
                select(func.max(TaskEvent.event_seq)).where(TaskEvent.research_task_id == task.id)
            )
            or 0
        ) + 1
        event = TaskEvent(
            research_task_id=task.id,
            subtask_id=subtask.id if subtask is not None else None,
            event_seq=next_seq,
            event_type=event_type,
            payload=payload,
            cost_delta=cost_delta,
            actor=actor,
            created_at=self._clock(),
        )
        session.add(event)
        # 立刻 flush：同一事务里可能连续追加多条事件，不 flush 的话
        # 下一次 max(event_seq) 查不到这条，会撞 uq_task_event_seq。
        session.flush()
        return event

    def _refresh_readiness(self, session: Session, task: ResearchTask) -> None:
        """把"依赖全部成功"的 pending 子任务升级为 ready，并让失败的上游阻塞下游。"""
        subtasks = session.scalars(
            select(ResearchSubtask).where(ResearchSubtask.research_task_id == task.id)
        ).all()
        by_id = {subtask.id: subtask for subtask in subtasks}
        edges = session.scalars(
            select(ResearchSubtaskDependency).where(
                ResearchSubtaskDependency.subtask_id.in_(list(by_id) or [-1])
            )
        ).all()
        dependencies: dict[int, list[int]] = {}
        for edge in edges:
            dependencies.setdefault(edge.subtask_id, []).append(edge.depends_on_subtask_id)

        for subtask in subtasks:
            if subtask.status is not SubtaskStatus.PENDING:
                continue
            upstream = [by_id.get(dep) for dep in dependencies.get(subtask.id, [])]
            if any(item is None for item in upstream):
                continue
            if any(item.status in {SubtaskStatus.FAILED, SubtaskStatus.CANCELLED} for item in upstream):
                subtask.status = SubtaskStatus.BLOCKED
                subtask.failure_reason = "upstream_failed"
                continue
            if all(item.status is SubtaskStatus.SUCCEEDED for item in upstream):
                subtask.status = SubtaskStatus.READY
                self._event(
                    session,
                    task,
                    TaskEventType.QUEUED,
                    subtask=subtask,
                    actor="runtime",
                    payload={"sequence": subtask.sequence},
                )

    def _maybe_finish(self, session: Session, task: ResearchTask) -> None:
        subtasks = session.scalars(
            select(ResearchSubtask).where(ResearchSubtask.research_task_id == task.id)
        ).all()
        if not subtasks:
            return
        if any(subtask.status not in _TERMINAL_SUBTASK for subtask in subtasks):
            return
        if task.status in {ResearchTaskStatus.COMPLETED, ResearchTaskStatus.FAILED, ResearchTaskStatus.CANCELLED}:
            return
        succeeded = [subtask for subtask in subtasks if subtask.status is SubtaskStatus.SUCCEEDED]
        if len(succeeded) == len(subtasks):
            task.status = ResearchTaskStatus.COMPLETED
            task.failure_reason = None
            self._event(session, task, TaskEventType.COMPLETED, actor="runtime", payload={"subtasks": len(subtasks)})
        else:
            task.status = ResearchTaskStatus.FAILED
            failed = [s for s in subtasks if s.status in {SubtaskStatus.FAILED, SubtaskStatus.BLOCKED}]
            task.failure_reason = "; ".join(
                sorted({f"{s.task_type}:{s.failure_reason or 'failed'}" for s in failed})
            )[:500]
            self._event(
                session,
                task,
                TaskEventType.FAILED,
                actor="runtime",
                payload={"failed": len(failed), "total": len(subtasks)},
            )

    def _subtask_snapshot(self, subtask: ResearchSubtask) -> SubtaskSnapshot:
        return SubtaskSnapshot(
            id=subtask.id,
            sequence=subtask.sequence,
            question=subtask.question,
            task_type=subtask.task_type,
            assignee=subtask.assignee,
            status=subtask.status.value,
            attempts=subtask.attempts,
            max_attempts=subtask.max_attempts,
            failure_reason=subtask.failure_reason,
            output=subtask.output_payload,
        )

    def _snapshot(self, task: ResearchTask, session: Session) -> TaskSnapshot:
        subtasks: Sequence[ResearchSubtask] = session.scalars(
            select(ResearchSubtask)
            .where(ResearchSubtask.research_task_id == task.id)
            .order_by(ResearchSubtask.sequence)
        ).all()
        return TaskSnapshot(
            id=task.id,
            title=task.title,
            objective=task.objective,
            status=task.status.value,
            topic_id=task.topic_id,
            calls_spent=task.calls_spent,
            tokens_spent=task.tokens_spent,
            cost_spent_cny=task.cost_spent or Decimal("0"),
            claimed_by=task.claimed_by,
            lease_until=task.lease_until,
            failure_reason=task.failure_reason,
            subtasks=tuple(self._subtask_snapshot(subtask) for subtask in subtasks),
        )


__all__ = [
    "Claim",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "RuntimeStats",
    "SubtaskSnapshot",
    "TaskRuntime",
    "TaskRuntimeError",
    "TaskSnapshot",
]
