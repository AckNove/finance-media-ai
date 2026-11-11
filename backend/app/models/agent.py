"""Agent 执行记录模型。

映射关系（统一协议 -> 数据库列）：
    task_id        -> agent_runs.task_id
    agent_name     -> agent_runs.agent_name
    task_type      -> agent_runs.task_type
    input          -> agent_runs.input_payload
    context        -> agent_runs.context_payload
    sources        -> agent_runs.sources
    evidence       -> agent_runs.evidence
    output         -> agent_runs.output_payload
    status         -> agent_runs.status
    errors         -> agent_runs.error_payload / error_message

第一阶段不接通任何模型：状态只会停留在 pending，
除非人工/脚本显式更新，不存在"假装跑完"的路径。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.utils import utcnow
from backend.app.database.base import Base, TimestampMixin
from backend.app.models.enums import AgentStatus
from backend.app.models.types import MoneyAmount, enum_column


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    status: Mapped[AgentStatus] = enum_column(
        AgentStatus, name="status", default=AgentStatus.PENDING, index=True
    )

    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    context_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sources: Mapped[list[Any] | None] = mapped_column(JSON)
    evidence: Mapped[list[Any] | None] = mapped_column(JSON)
    error_payload: Mapped[list[Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )

    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    # --- M2.0 B2 追加：把 Agent 执行记录挂到任务层级，并记录成本估算 ---
    research_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subtask_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: 本次执行的预估成本（实际值在 model_calls 逐笔记录）
    cost_estimate: Mapped[Decimal | None] = mapped_column(MoneyAmount)
