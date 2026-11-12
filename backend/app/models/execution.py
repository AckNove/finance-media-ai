"""任务域模型（M2.0 迁移 B2：research_tasks / research_subtasks /
research_subtask_dependencies / task_events）。

设计依据：``docs/research_hub/database_design.md`` §4.5–§4.8。

要点：
- 队列放在数据库里（不用 Celery/Redis），靠 ``claimed_by`` + ``lease_until`` 抢占；
- ``research_subtasks.task_type`` 只做**格式校验**，不做枚举 CHECK（新增 task_type 不应触发迁移）；
- ``task_events`` 只追加，``event_seq`` 在同一任务内严格递增（唯一约束保证）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import ResearchTaskStatus, SubtaskStatus, TaskEventType
from backend.app.models.types import CostAmount, MoneyAmount, enum_column

if TYPE_CHECKING:
    from backend.app.models.agent import AgentRun
    from backend.app.models.topic import Topic


class ResearchTask(Base, TimestampMixin):
    """一次研究任务（一个选题的一次研究请求）。"""

    __tablename__ = "research_tasks"
    __table_args__ = (
        CheckConstraint("budget_max_calls IS NULL OR budget_max_calls >= 0", name="max_calls_non_negative"),
        CheckConstraint(
            "budget_max_cost IS NULL OR budget_max_cost >= 0", name="max_cost_non_negative"
        ),
        CheckConstraint("calls_spent >= 0 AND tokens_spent >= 0", name="spent_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    out_of_scope: Mapped[str | None] = mapped_column(Text)

    status: Mapped[ResearchTaskStatus] = enum_column(
        ResearchTaskStatus, name="research_task_status", default=ResearchTaskStatus.CREATED, index=True
    )
    #: 规划结果（子任务清单的原始快照），真正执行结构以 research_subtasks 为准
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    planner: Mapped[str | None] = mapped_column(String(64))

    budget_max_calls: Mapped[int | None] = mapped_column(Integer)
    budget_max_tokens: Mapped[int | None] = mapped_column(Integer)
    budget_max_cost: Mapped[Decimal | None] = mapped_column(CostAmount)
    calls_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_spent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cost_spent: Mapped[Decimal] = mapped_column(
        CostAmount, nullable=False, default=Decimal("0"), server_default="0"
    )

    #: 抢占者与租约（DB 队列）
    claimed_by: Mapped[str | None] = mapped_column(String(64), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # --- M2.0 B3 追加：预留额度与租约心跳（防止多个 worker 同时花钱） ---
    calls_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cost_reserved: Mapped[Decimal] = mapped_column(
        CostAmount, nullable=False, default=Decimal("0"), server_default="0"
    )
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 每次抢占轮换的令牌：旧 worker 拿着过期 token 写不进来
    attempt_token: Mapped[str | None] = mapped_column(String(64), index=True)

    requested_by: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    topic: Mapped[Topic | None] = relationship()
    subtasks: Mapped[list[ResearchSubtask]] = relationship(
        back_populates="research_task",
        cascade="save-update, merge",
        order_by="ResearchSubtask.sequence",
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="research_task", cascade="save-update, merge"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ResearchTask id={self.id} status={self.status.value}>"


class ResearchSubtask(Base, TimestampMixin):
    """研究任务下的一个子任务（可重试、可分配、可依赖）。"""

    __tablename__ = "research_subtasks"
    __table_args__ = (
        UniqueConstraint("research_task_id", "sequence", name="uq_subtask_sequence"),
        # 只校验形态 `domain.action`；注册表在 config/agent_routing.yaml，避免每次加类型都迁移
        CheckConstraint(
            "length(task_type) >= 3 AND length(task_type) <= 64 AND task_type GLOB '*.*'",
            name="task_type_format",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_research_subtasks_task_status", "research_task_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assignee: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[SubtaskStatus] = enum_column(
        SubtaskStatus, name="subtask_status", default=SubtaskStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")

    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    research_task: Mapped[ResearchTask] = relationship(back_populates="subtasks")
    # agent_runs 同时通过 research_task_id 与 subtask_id 指向研究任务，
    # 与本表的 agent_run_id 构成两条外键路径，必须显式指定只用 agent_run_id。
    agent_run: Mapped[AgentRun | None] = relationship(foreign_keys=[agent_run_id])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ResearchSubtask {self.id} seq={self.sequence} {self.task_type}>"


class ResearchSubtaskDependency(Base):
    """子任务依赖边（DAG；环检测在服务层，SQLite 不做递归约束）。"""

    __tablename__ = "research_subtask_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "subtask_id", "depends_on_subtask_id", name="uq_subtask_dependency"
        ),
        CheckConstraint(
            "subtask_id <> depends_on_subtask_id", name="no_self_dependency"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subtask_id: Mapped[int] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_subtask_id: Mapped[int] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Dependency {self.subtask_id} <- {self.depends_on_subtask_id}>"


class TaskEvent(Base):
    """任务事件流（只追加，用于审计与崩溃恢复）。"""

    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("research_task_id", "event_seq", name="uq_task_event_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_task_id: Mapped[int] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subtask_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[TaskEventType] = enum_column(
        TaskEventType, name="task_event_type", index=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: 该事件带来的成本增量（正数=花钱），便于按事件流重算成本
    cost_delta: Mapped[Decimal | None] = mapped_column(CostAmount)
    actor: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    research_task: Mapped[ResearchTask] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskEvent task={self.research_task_id} #{self.event_seq} {self.event_type.value}>"


__all__ = [
    "ResearchSubtask",
    "ResearchSubtaskDependency",
    "ResearchTask",
    "TaskEvent",
]
