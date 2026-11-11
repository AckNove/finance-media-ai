"""外键环 ``agent_runs ↔ research_subtasks`` 的语义与影响验证。

背景：``agent_runs.subtask_id`` → ``research_subtasks.id`` 与
``research_subtasks.agent_run_id`` → ``agent_runs.id`` 互指，SQLAlchemy 在**排序建表**时
会报 SAWarning（``backend/migrations/env.py:77``，来源 ``sqlalchemy/sql/ddl.py``）。

本文件不"屏蔽"这条警告，而是逐条验证它到底影响什么：

1. 两条外键在真实库里**都存在**（不是被静默丢弃）；
2. 双向删除都是 ``ON DELETE SET NULL``，不会互相阻塞；
3. 删除后 ``PRAGMA foreign_key_check`` 依然干净、``integrity_check`` 依然 ok；
4. 关系映射（``ResearchSubtask.agent_run`` 明确 ``foreign_keys``）与 ORM 写入、查询正常。

对应交付报告风险项 R13：警告来自 DDL 排序器，不影响运行期行为。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from backend.app.models import AgentRun, Base, ResearchSubtask, ResearchTask
from backend.app.models.execution import SubtaskStatus


def _make_task_graph(session) -> tuple[ResearchTask, ResearchSubtask, AgentRun]:
    marker = uuid.uuid4().hex[:12]
    task = ResearchTask(
        title=f"外键环测试任务 {marker}", objective="验证双向 SET NULL", requested_by="test"
    )
    session.add(task)
    session.flush()

    subtask = ResearchSubtask(
        research_task_id=task.id,
        sequence=1,
        question="这条子任务会不会被外键环卡住？",
        task_type="research.collect",
        status=SubtaskStatus.PENDING,
    )
    session.add(subtask)
    session.flush()

    run = AgentRun(
        # agent_runs.task_id 有唯一约束，必须每次唯一
        task_id=f"task-fkcycle-{marker}",
        agent_name="financial_researcher",
        task_type="research.collect",
        research_task_id=task.id,
        subtask_id=subtask.id,
    )
    session.add(run)
    session.flush()

    subtask.agent_run_id = run.id
    session.flush()
    return task, subtask, run


def _cleanup(session, task_id: int) -> None:
    """清掉本用例造的行，避免污染整个测试会话共享的库。"""
    session.rollback()
    session.execute(text("DELETE FROM agent_runs WHERE research_task_id = :id"), {"id": task_id})
    session.execute(
        text("DELETE FROM research_subtasks WHERE research_task_id = :id"), {"id": task_id}
    )
    session.execute(text("DELETE FROM research_tasks WHERE id = :id"), {"id": task_id})
    session.commit()


def test_both_directions_of_the_cycle_have_foreign_keys():
    """环是**真实存在**的：两边都有 FK，不是只写了一边。"""
    agent_runs = Base.metadata.tables["agent_runs"]
    subtasks = Base.metadata.tables["research_subtasks"]

    run_fk_targets = {fk.column.table.name for fk in agent_runs.foreign_keys}
    subtask_fk_targets = {fk.column.table.name for fk in subtasks.foreign_keys}

    assert "research_subtasks" in run_fk_targets
    assert "agent_runs" in subtask_fk_targets


def test_deleting_subtask_nulls_agent_run_reference(db_session):
    task, subtask, run = _make_task_graph(db_session)
    task_id, subtask_id, run_id = task.id, subtask.id, run.id
    db_session.commit()
    try:
        db_session.execute(
            text("DELETE FROM research_subtasks WHERE id = :id"), {"id": subtask_id}
        )
        db_session.commit()

        row = db_session.execute(
            text("SELECT subtask_id, research_task_id FROM agent_runs WHERE id = :id"),
            {"id": run_id},
        ).one()
        assert row is not None, "删子任务不应连带删掉执行记录"
        assert row[0] is None, "agent_runs.subtask_id 应为 ON DELETE SET NULL"
        assert row[1] == task_id
    finally:
        _cleanup(db_session, task_id)


def test_deleting_agent_run_nulls_subtask_reference(db_session):
    task, subtask, run = _make_task_graph(db_session)
    task_id, subtask_id, run_id = task.id, subtask.id, run.id
    db_session.commit()
    try:
        db_session.execute(text("DELETE FROM agent_runs WHERE id = :id"), {"id": run_id})
        db_session.commit()

        row = db_session.execute(
            text("SELECT agent_run_id FROM research_subtasks WHERE id = :id"), {"id": subtask_id}
        ).one()
        assert row is not None, "删执行记录不应连带删掉子任务"
        assert row[0] is None, "research_subtasks.agent_run_id 应为 SET NULL"
    finally:
        _cleanup(db_session, task_id)


def test_foreign_key_check_and_integrity_clean_after_cycle_deletes(db_session):
    task, subtask, run = _make_task_graph(db_session)
    task_id, subtask_id, run_id = task.id, subtask.id, run.id
    db_session.commit()
    try:
        db_session.execute(
            text("DELETE FROM research_subtasks WHERE id = :id"), {"id": subtask_id}
        )
        db_session.execute(text("DELETE FROM agent_runs WHERE id = :id"), {"id": run_id})
        db_session.commit()

        assert db_session.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert db_session.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    finally:
        _cleanup(db_session, task_id)


def test_relationship_mapping_is_unambiguous(db_session):
    """``ResearchSubtask.agent_run`` 必须显式 ``foreign_keys``，否则两条路径会歧义。"""
    task, subtask, run = _make_task_graph(db_session)
    task_id, subtask_id, run_id = task.id, subtask.id, run.id
    db_session.commit()
    try:
        loaded = db_session.scalar(
            select(ResearchSubtask).where(ResearchSubtask.id == subtask_id)
        )
        assert loaded is not None
        assert loaded.agent_run is not None
        assert loaded.agent_run.id == run_id
        assert loaded.agent_run.subtask_id == subtask_id
    finally:
        _cleanup(db_session, task_id)
