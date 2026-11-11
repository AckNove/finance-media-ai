"""计量域模型（M2.0 迁移 B3：model_calls / tool_calls）。

设计依据：``docs/research_hub/database_design.md`` §4.15–§4.16。

M2.0 只建表与约束，**不写入任何行**：真实模型调用与工具派发都还没实现，
写空行等于伪造审计记录。表结构先就位，是为了让 2.1 的 ``reserve_call()`` 有地方记账。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base
from backend.app.models.enums import CallStatus, ToolCallStatus
from backend.app.models.types import MoneyAmount, enum_column

if TYPE_CHECKING:
    from backend.app.models.execution import ResearchSubtask, ResearchTask


class ModelCall(Base):
    """一次模型调用的记账行（成本审计的唯一真相）。"""

    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint("prompt_tokens >= 0 AND completion_tokens >= 0", name="tokens_non_negative"),
        CheckConstraint("cost_estimate IS NULL OR cost_estimate >= 0", name="cost_non_negative"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subtask_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    #: 调用用途（plan / extract / synthesize ...），便于按用途拆成本
    purpose: Mapped[str | None] = mapped_column(String(64), index=True)

    request_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    #: 提示词前 2KB 预览；不存全文，避免把整篇资料写进审计库
    prompt_preview: Mapped[str | None] = mapped_column(Text)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cost_estimate: Mapped[Decimal | None] = mapped_column(MoneyAmount)
    #: 价目未知时置 1：该行成本不可信，只能按上界估算（Q8=B 默认拒绝新调用）
    pricing_unknown: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    status: Mapped[CallStatus] = enum_column(
        CallStatus, name="call_status", default=CallStatus.OK, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    research_task: Mapped[ResearchTask | None] = relationship()
    subtask: Mapped[ResearchSubtask | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ModelCall {self.provider}/{self.model} status={self.status.value}>"


class ToolCall(Base):
    """一次工具派发的记账行。M2.0 不写入（没有真实工具实现）。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint("cost_estimate IS NULL OR cost_estimate >= 0", name="cost_non_negative"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subtask_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_subtasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 调用者类型（human/agent/dev）与名字，用于审计"谁在用什么"
    caller_kind: Mapped[str | None] = mapped_column(String(16), index=True)
    caller_name: Mapped[str | None] = mapped_column(String(64))
    #: 参数摘要（脱敏、截断），不是完整参数
    payload_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[ToolCallStatus] = enum_column(
        ToolCallStatus, name="tool_call_status", default=ToolCallStatus.OK, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    billable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    cost_estimate: Mapped[Decimal | None] = mapped_column(MoneyAmount)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    research_task: Mapped[ResearchTask | None] = relationship()
    subtask: Mapped[ResearchSubtask | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ToolCall {self.tool_name} status={self.status.value}>"


__all__ = ["ModelCall", "ToolCall"]
