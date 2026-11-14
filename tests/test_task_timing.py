"""任务耗时看板：事件流里没有 started_at/finished_at，只能从事件推。

三条口径要守住：
1. 「干活 + 等人」加起来要能对上总时长；对不上的差值单独列出来，不许悄悄吃掉；
2. 模型调用耗时单独列——它发生在「干活」里面，加进加减法会重复计算；
3. 只有开始没有结束的那格按「到现在」算，并明确标出来（可能正在跑，也可能进程被杀了）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.models.enums import ResearchTaskStatus, SubtaskStatus
from backend.app.models.execution import ResearchSubtask, ResearchTask, TaskEvent
from backend.app.models.metering import ModelCall
from backend.app.services.task_timing import TimingError, timing_of

BASE = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)


def _task(session, *, status=ResearchTaskStatus.RUNNING, title: str = "耗时测试") -> ResearchTask:
    task = ResearchTask(title=title, objective="研究点什么", status=status, requested_by="human")
    session.add(task)
    session.commit()
    return task


def _subtask(session, task: ResearchTask, *, sequence: int, task_type: str = "research.collect") -> ResearchSubtask:
    row = ResearchSubtask(
        research_task_id=task.id,
        sequence=sequence,
        question=f"第 {sequence} 格",
        task_type=task_type,
        status=SubtaskStatus.SUCCEEDED,
        max_attempts=2,
    )
    session.add(row)
    session.commit()
    return row


def _event(
    session,
    task: ResearchTask,
    *,
    seq: int,
    kind: str,
    at: datetime,
    subtask_id: int | None = None,
) -> TaskEvent:
    row = TaskEvent(
        research_task_id=task.id,
        subtask_id=subtask_id,
        event_seq=seq,
        event_type=kind,
        actor="worker-1",
        created_at=at.replace(tzinfo=None),
    )
    session.add(row)
    session.commit()
    return row


def test_a_task_without_events_says_it_cannot_be_measured(db_session) -> None:
    task = _task(db_session)
    timing = timing_of(db_session, task.id)

    assert timing.total_seconds == 0
    assert "算不出耗时" in timing.note
    assert timing.subtasks == ()


def test_normal_pairing_adds_up_to_the_work_time(db_session) -> None:
    task = _task(db_session)
    first = _subtask(db_session, task, sequence=1)
    second = _subtask(db_session, task, sequence=2, task_type="research.extract")

    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=first.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=30), subtask_id=first.id)
    _event(db_session, task, seq=3, kind="subtask_started", at=BASE + timedelta(seconds=30), subtask_id=second.id)
    _event(db_session, task, seq=4, kind="subtask_succeeded", at=BASE + timedelta(seconds=90), subtask_id=second.id)

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=90))

    assert [row.sequence for row in timing.subtasks] == [1, 2]
    assert timing.work_seconds == pytest.approx(90.0, abs=0.5)
    assert timing.total_seconds == pytest.approx(90.0, abs=0.5)
    assert timing.wait_seconds == 0
    assert timing.unpaired_seconds < 1
    assert all(row.runs == 1 for row in timing.subtasks)


def test_a_retried_subtask_accumulates_its_attempts(db_session) -> None:
    task = _task(db_session)
    row = _subtask(db_session, task, sequence=1)

    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_failed", at=BASE + timedelta(seconds=20), subtask_id=row.id)
    _event(db_session, task, seq=3, kind="subtask_started", at=BASE + timedelta(seconds=20), subtask_id=row.id)
    _event(db_session, task, seq=4, kind="subtask_succeeded", at=BASE + timedelta(seconds=50), subtask_id=row.id)

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=50))

    assert len(timing.subtasks) == 1
    assert timing.subtasks[0].runs == 2
    assert timing.subtasks[0].seconds == pytest.approx(50.0, abs=0.5)


def test_a_subtask_that_never_finished_is_marked_running(db_session) -> None:
    """只有开始没有结束：按「到现在」算，并明确说这可能是进程被杀了。"""

    task = _task(db_session)
    row = _subtask(db_session, task, sequence=1)
    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=120))

    assert len(timing.subtasks) == 1
    assert timing.subtasks[0].running is True
    assert timing.subtasks[0].seconds == pytest.approx(120.0, abs=0.5)
    assert "只有开始没有结束" in timing.note


def test_waiting_at_a_human_gate_is_counted_separately(db_session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.PAUSED)
    row = _subtask(db_session, task, sequence=1)

    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=30), subtask_id=row.id)
    _event(db_session, task, seq=3, kind="paused", at=BASE + timedelta(seconds=30))
    _event(db_session, task, seq=4, kind="resumed", at=BASE + timedelta(seconds=210))

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=210))

    assert timing.work_seconds == pytest.approx(30.0, abs=0.5)
    assert timing.wait_seconds == pytest.approx(180.0, abs=0.5)
    assert len(timing.waits) == 1
    assert timing.waits[0].ongoing is False
    assert timing.waits[0].seconds == pytest.approx(180.0, abs=0.5)


def test_still_waiting_counts_up_to_now(db_session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.PAUSED)
    row = _subtask(db_session, task, sequence=1)

    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=10), subtask_id=row.id)
    _event(db_session, task, seq=3, kind="paused", at=BASE + timedelta(seconds=10))

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=310))

    assert timing.waits[0].ongoing is True
    assert timing.waits[0].ended_at is None
    assert timing.wait_seconds == pytest.approx(300.0, abs=0.5)


def test_the_gap_between_creation_and_first_event_is_reported_not_hidden(db_session) -> None:
    """总时长比「干活 + 等人」多出来的部分要写出来，不许悄悄并进任何一块。"""

    task = _task(db_session)
    row = _subtask(db_session, task, sequence=1)

    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=30), subtask_id=row.id)
    # 收尾事件在 300 秒之后：中间 270 秒既没干活也没等人
    _event(db_session, task, seq=3, kind="completed", at=BASE + timedelta(seconds=300))

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=300))

    assert timing.unpaired_seconds == pytest.approx(270.0, abs=1.0)
    assert "空档没算进任何一块" in timing.note


def test_model_time_is_listed_but_not_added_into_the_arithmetic(db_session) -> None:
    task = _task(db_session)
    row = _subtask(db_session, task, sequence=1)
    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=40), subtask_id=row.id)
    db_session.add(
        ModelCall(
            research_task_id=task.id,
            subtask_id=row.id,
            provider="deepseek",
            model="deepseek-flash",
            purpose="research.analyze",
            duration_ms=12_500,
            created_at=BASE.replace(tzinfo=None),
        )
    )
    db_session.commit()

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=40))

    assert timing.model_seconds == pytest.approx(12.5, abs=0.1)
    # 关键：模型那 12.5 秒不能再加进「干活」，否则和总时长对不上
    assert timing.work_seconds == pytest.approx(40.0, abs=0.5)
    assert timing.work_seconds + timing.wait_seconds <= timing.total_seconds + 0.5


def test_total_includes_the_time_you_are_still_waiting(db_session) -> None:
    """真机上撞到的自相矛盾：等人 22 秒，总时长却显示 0.3 秒。

    原因是最初把"总时长"算成最后一条事件减第一条——任务停在闸门上时再没有新事件，
    总时长就冻住了，而"等人"那块是按"到现在"算的，两个数字当场打架。
    """

    task = _task(db_session, status=ResearchTaskStatus.PAUSED)
    row = _subtask(db_session, task, sequence=1)
    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=10), subtask_id=row.id)
    _event(db_session, task, seq=3, kind="paused", at=BASE + timedelta(seconds=10))

    timing = timing_of(db_session, task.id, now=BASE + timedelta(seconds=310))

    assert timing.total_seconds == pytest.approx(310.0, abs=1.0)
    assert timing.wait_seconds == pytest.approx(300.0, abs=1.0)
    # 关键不变式：等待时间不可能超过总时长
    assert timing.wait_seconds <= timing.total_seconds + 0.5
    assert timing.work_seconds + timing.wait_seconds <= timing.total_seconds + 0.5


def test_a_finished_task_stops_counting_at_its_last_event(db_session) -> None:
    """任务已经结束：总时长按最后一条事件算，不能一路涨到"现在"。"""

    task = _task(db_session, status=ResearchTaskStatus.COMPLETED)
    row = _subtask(db_session, task, sequence=1)
    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=60), subtask_id=row.id)
    _event(db_session, task, seq=3, kind="completed", at=BASE + timedelta(seconds=90))

    timing = timing_of(db_session, task.id, now=BASE + timedelta(days=3))

    assert timing.total_seconds == pytest.approx(90.0, abs=1.0)


def test_unknown_task_is_rejected(db_session) -> None:
    with pytest.raises(TimingError) as error:
        timing_of(db_session, 987654)
    assert error.value.reason == "unknown_task"


def test_the_api_returns_the_timing_and_404_for_unknown(client: TestClient, db_session) -> None:
    task = _task(db_session, title="接口耗时")
    row = _subtask(db_session, task, sequence=1)
    _event(db_session, task, seq=1, kind="subtask_started", at=BASE, subtask_id=row.id)
    _event(db_session, task, seq=2, kind="subtask_succeeded", at=BASE + timedelta(seconds=30), subtask_id=row.id)

    response = client.get(f"/api/research-tasks/{task.id}/timing")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task.id
    assert body["work_seconds"] == pytest.approx(30.0, abs=1.0)
    assert body["subtasks"][0]["task_type"] == "research.collect"
    assert set(body) >= {
        "task_id", "title", "status", "total_seconds", "work_seconds",
        "wait_seconds", "model_seconds", "unpaired_seconds", "subtasks", "waits", "note",
    }

    missing = client.get("/api/research-tasks/987654/timing")
    assert missing.status_code == 404
    assert missing.json()["error"]["details"]["reason"] == "unknown_task"
