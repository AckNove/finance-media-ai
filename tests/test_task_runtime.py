"""任务运行时：状态机、租约、重试、崩溃恢复、人工闸门。

每个用例用一个**独立的内存/临时库**，不共享 conftest 的会话级库——
运行时关心的是"状态与并发"，互不干扰才好断言。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.models  # noqa: F401  注册全部模型
from backend.app.database.base import Base
from backend.app.execution.runtime import Claim, TaskRuntime, TaskRuntimeError


class FakeClock:
    """可控时钟：让租约过期可被断言，而不是真的等 5 分钟。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory):
    """整组用例共用一个临时库：建 28 张表要一两秒，每个用例重建太慢。"""
    path = tmp_path_factory.mktemp("runtime_db") / "runtime.db"
    created = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(created)
    yield created
    created.dispose()


@pytest.fixture()
def runtime(engine, clock: FakeClock) -> TaskRuntime:
    # 每个用例前清空业务表，保证互不影响。
    # 用 defer_foreign_keys 而不是 sorted_tables：agent_runs 与 research_subtasks
    # 互指外键，拓扑排序排不出来（会刷 SAWarning），但"整库删空"本身没有环问题。
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        for table in Base.metadata.tables.values():
            connection.exec_driver_sql(f'DELETE FROM "{table.name}"')
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    counter = {"n": 0}

    def token() -> str:
        counter["n"] += 1
        return f"token-{counter['n']:03d}"

    return TaskRuntime(factory, clock=clock, token_factory=token)


def _two_step_task(runtime: TaskRuntime) -> tuple[int, int, int]:
    """建一个 task + 两个有依赖的子任务，返回 (task_id, 第一个, 第二个)。"""
    task = runtime.create_task(title="商业银行怎么赚钱", objective="拆解净息差与手续费收入")
    first = runtime.add_subtask(
        task.id, sequence=1, question="净息差的构成是什么", task_type="research.collect"
    )
    second = runtime.add_subtask(
        task.id,
        sequence=2,
        question="净息差怎么影响估值",
        task_type="research.analyze",
        depends_on=[first.id],
    )
    return task.id, first.id, second.id


# ---------------------------------------------------------------- 建任务与就绪


def test_new_task_starts_created_and_first_subtask_is_ready(runtime: TaskRuntime) -> None:
    task_id, first_id, second_id = _two_step_task(runtime)

    snapshot = runtime.task(task_id)

    assert snapshot.status == "created"
    statuses = {subtask.id: subtask.status for subtask in snapshot.subtasks}
    assert statuses[first_id] == "ready"
    assert statuses[second_id] == "pending"


def test_subtask_rejects_unknown_task_type(runtime: TaskRuntime) -> None:
    from backend.app.execution.task_types import UnknownTaskTypeError

    task = runtime.create_task(title="t")

    with pytest.raises(UnknownTaskTypeError) as excinfo:
        runtime.add_subtask(task.id, sequence=1, question="q", task_type="not.a.real.type")

    assert excinfo.value.details["reason"] == "unknown_task_type"


def test_subtask_rejects_duplicate_sequence_and_unknown_dependency(runtime: TaskRuntime) -> None:
    task = runtime.create_task(title="t")
    runtime.add_subtask(task.id, sequence=1, question="q", task_type="research.collect")

    with pytest.raises(TaskRuntimeError) as duplicate:
        runtime.add_subtask(task.id, sequence=1, question="q2", task_type="research.collect")
    assert duplicate.value.reason == "duplicate_sequence"

    with pytest.raises(TaskRuntimeError) as unknown:
        runtime.add_subtask(
            task.id, sequence=2, question="q2", task_type="research.analyze", depends_on=[999]
        )
    assert unknown.value.reason == "unknown_dependency"


def test_empty_question_is_rejected(runtime: TaskRuntime) -> None:
    task = runtime.create_task(title="t")

    with pytest.raises(TaskRuntimeError) as excinfo:
        runtime.add_subtask(task.id, sequence=1, question="   ", task_type="research.collect")

    assert excinfo.value.reason == "empty_question"


# ---------------------------------------------------------------- 抢占


def test_claim_picks_the_lowest_sequence_and_marks_running(runtime: TaskRuntime) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)

    claim = runtime.claim_next(worker="worker-a")

    assert claim is not None
    assert claim.subtask_id == first_id
    assert claim.attempt == 1
    assert claim.attempt_token == "token-001"
    snapshot = runtime.task(task_id)
    assert snapshot.status == "running"
    assert snapshot.claimed_by == "worker-a"
    assert {s.id: s.status for s in snapshot.subtasks}[first_id] == "running"


def test_claim_returns_none_when_nothing_is_ready(runtime: TaskRuntime) -> None:
    task = runtime.create_task(title="empty")

    assert runtime.claim_next(worker="worker-a") is None


def test_task_that_was_not_started_cannot_be_claimed(runtime: TaskRuntime) -> None:
    _two_step_task(runtime)

    assert runtime.claim_next(worker="worker-a") is None


def test_another_worker_cannot_claim_while_the_lease_is_alive(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    runtime.claim_next(worker="worker-a")

    assert runtime.claim_next(worker="worker-b") is None


# ---------------------------------------------------------------- 完成与推进


def test_completing_a_subtask_unlocks_its_dependent(runtime: TaskRuntime) -> None:
    task_id, first_id, second_id = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None

    snapshot = runtime.complete(claim, output={"findings": 3}, calls=2, tokens=900, cost_cny="0.0042")

    statuses = {subtask.id: subtask.status for subtask in snapshot.subtasks}
    assert statuses[first_id] == "succeeded"
    assert statuses[second_id] == "ready"
    assert snapshot.calls_spent == 2
    assert snapshot.tokens_spent == 900
    assert snapshot.cost_spent_cny == Decimal("0.0042")


def test_finishing_every_subtask_completes_the_task(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)

    first = runtime.claim_next(worker="worker-a")
    assert first is not None
    runtime.complete(first, output={"ok": 1})
    second = runtime.claim_next(worker="worker-a")
    assert second is not None
    snapshot = runtime.complete(second, output={"ok": 2})

    assert snapshot.status == "completed"
    assert snapshot.claimed_by is None
    assert snapshot.lease_until is None


def test_stale_attempt_token_is_rejected(runtime: TaskRuntime, clock: FakeClock) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None
    clock.advance(runtime.lease_seconds + 5)
    runtime.reap_expired_leases()
    reclaimed = runtime.claim_next(worker="worker-b")
    assert reclaimed is not None and reclaimed.attempt_token != claim.attempt_token

    with pytest.raises(TaskRuntimeError) as excinfo:
        runtime.complete(claim, output={})

    assert excinfo.value.reason == "stale_attempt"


# ---------------------------------------------------------------- 失败与重试


def test_failure_retries_until_max_attempts_then_fails_and_blocks_downstream(
    runtime: TaskRuntime,
) -> None:
    task_id, first_id, second_id = _two_step_task(runtime)
    runtime.start_task(task_id)

    first_attempt = runtime.claim_next(worker="worker-a")
    assert first_attempt is not None
    snapshot = runtime.fail(first_attempt, reason="timeout")
    assert {s.id: s.status for s in snapshot.subtasks}[first_id] == "ready"

    second_attempt = runtime.claim_next(worker="worker-a")
    assert second_attempt is not None and second_attempt.attempt == 2
    snapshot = runtime.fail(second_attempt, reason="timeout")

    statuses = {subtask.id: subtask.status for subtask in snapshot.subtasks}
    assert statuses[first_id] == "failed"
    assert statuses[second_id] == "blocked"
    assert snapshot.status == "failed"
    assert "research.collect:timeout" in (snapshot.failure_reason or "")


def test_non_retryable_failure_ends_the_subtask_immediately(runtime: TaskRuntime) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None

    snapshot = runtime.fail(claim, reason="bad_arguments", retryable=False)

    assert {s.id: s.status for s in snapshot.subtasks}[first_id] == "failed"


def test_release_puts_the_subtask_back_without_spending_an_attempt(runtime: TaskRuntime) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None

    snapshot = runtime.release(claim, reason="shutdown")

    subtask = {s.id: s for s in snapshot.subtasks}[first_id]
    assert subtask.status == "ready"
    assert subtask.attempts == 0


# ---------------------------------------------------------------- 暂停/恢复/取消


def test_pause_puts_running_work_back_to_ready(runtime: TaskRuntime) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    runtime.claim_next(worker="worker-a")

    snapshot = runtime.pause(task_id, reason="人工确认方向")

    assert snapshot.status == "paused"
    assert {s.id: s.status for s in snapshot.subtasks}[first_id] == "ready"


def test_resume_requires_the_paused_state(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)

    with pytest.raises(TaskRuntimeError) as excinfo:
        runtime.resume(task_id)

    assert excinfo.value.reason == "not_paused"

    runtime.pause(task_id)
    assert runtime.resume(task_id).status == "running"


def test_human_review_pauses_the_task_and_resume_requeues(runtime: TaskRuntime) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None

    snapshot = runtime.request_human_review(claim, reason="脚本需要人看")
    assert snapshot.status == "paused"
    blocked = {s.id: s for s in snapshot.subtasks}[first_id]
    assert blocked.status == "blocked"
    assert "human_review" in (blocked.failure_reason or "")

    resumed = runtime.resume(task_id)
    assert {s.id: s for s in resumed.subtasks}[first_id].status == "ready"
    assert resumed.status == "running"


def test_cancel_closes_open_subtasks(runtime: TaskRuntime) -> None:
    task_id, first_id, second_id = _two_step_task(runtime)
    runtime.start_task(task_id)

    snapshot = runtime.cancel(task_id, reason="选题放弃")

    assert snapshot.status == "cancelled"
    assert {s.id: s.status for s in snapshot.subtasks} == {
        first_id: "cancelled",
        second_id: "cancelled",
    }


def test_closed_task_rejects_new_subtasks(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.cancel(task_id)

    with pytest.raises(TaskRuntimeError) as excinfo:
        runtime.add_subtask(task_id, sequence=3, question="q", task_type="research.collect")

    assert excinfo.value.reason == "task_closed"


# ---------------------------------------------------------------- 崩溃恢复


def test_expired_lease_is_reaped_back_to_ready(runtime: TaskRuntime, clock: FakeClock) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None

    clock.advance(runtime.lease_seconds + 1)
    recovered = runtime.reap_expired_leases()

    assert recovered == [first_id]
    statuses = {s.id: s.status for s in runtime.task(task_id).subtasks}
    assert statuses[first_id] == "ready"


def test_lease_expiry_beyond_max_attempts_fails_instead_of_looping(
    runtime: TaskRuntime, clock: FakeClock
) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    for _ in range(2):
        claim = runtime.claim_next(worker="worker-a")
        assert claim is not None
        clock.advance(runtime.lease_seconds + 1)
        runtime.reap_expired_leases()

    statuses = {s.id: s.status for s in runtime.task(task_id).subtasks}
    assert statuses[first_id] == "failed"


def test_heartbeat_extends_the_lease(runtime: TaskRuntime, clock: FakeClock) -> None:
    task_id, first_id, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None
    clock.advance(runtime.lease_seconds - 10)

    refreshed = runtime.heartbeat(claim)

    assert refreshed.lease_until > claim.lease_until
    clock.advance(20)
    assert runtime.reap_expired_leases() == []
    assert {s.id: s.status for s in runtime.task(task_id).subtasks}[first_id] == "running"


# ---------------------------------------------------------------- 事件与统计


def test_events_are_append_only_and_ordered(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim = runtime.claim_next(worker="worker-a")
    assert claim is not None
    runtime.complete(claim, output={})

    events = runtime.events(task_id)
    sequences = [event["seq"] for event in events]
    types = [event["type"] for event in events]

    assert sequences == sorted(sequences) == list(range(1, len(events) + 1))
    assert types[0] == "created"
    assert "claimed" in types
    assert "subtask_started" in types
    assert "subtask_succeeded" in types
    assert "queued" in types


def test_stats_group_by_status(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)

    stats = runtime.stats()

    assert stats.tasks.get("running") == 1
    assert stats.subtasks.get("ready") == 1
    assert stats.subtasks.get("pending") == 1


def test_claim_serialises_to_a_plain_dict(runtime: TaskRuntime) -> None:
    task_id, _, _ = _two_step_task(runtime)
    runtime.start_task(task_id)
    claim: Claim | None = runtime.claim_next(worker="worker-a")

    assert claim is not None
    payload = claim.as_dict()
    assert payload["task_id"] == task_id
    assert payload["task_type"] == "research.collect"
    assert payload["attempt_token"].startswith("token-")
