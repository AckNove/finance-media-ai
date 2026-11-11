"""M2.0 B3：成本与工具计量（model_calls / tool_calls + research_tasks 预留列）。

Revision ID: b3_metering
Revises: b2_task_layer
Create Date: 2026-11-10

设计依据：``docs/research_hub/database_design.md`` §4.15、§4.16、§5（research_tasks 加列）。
前置要求：**不过 B3，2.1 不得开工**（没有 model_calls 就没有成本真相）。

本批次做两件事：
1. 建 ``model_calls``（每次模型调用的唯一记账点）与 ``tool_calls``（每次工具调用的留痕点）；
2. 给 ``research_tasks`` 加四列：``calls_reserved`` / ``cost_reserved`` /
   ``lease_heartbeat_at`` / ``attempt_token``——预留额度是防止多个 worker
   同时花钱的机制，必须与计量表同批上线。

注意：本迁移只建表，**不写入任何行**。M2.0 全程禁止真实模型调用，
因此 ``model_calls`` 在交付时必须为 0 行。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3_metering"
down_revision: str | None = "b2_task_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("research_task_id", sa.Integer(), nullable=True),
        sa.Column("subtask_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_preview", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_estimate", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("pricing_unknown", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("cache_hit", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "error",
                "timeout",
                "budget_blocked",
                name="call_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cost_estimate IS NULL OR cost_estimate >= 0",
            name=op.f("ck_model_calls_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_model_calls_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0",
            name=op.f("ck_model_calls_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["research_tasks.id"],
            name=op.f("fk_model_calls_research_task_id_research_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subtask_id"],
            ["research_subtasks.id"],
            name=op.f("fk_model_calls_subtask_id_research_subtasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
    )
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_model_calls_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_model_calls_model"), ["model"], unique=False)
        batch_op.create_index(batch_op.f("ix_model_calls_provider"), ["provider"], unique=False)
        batch_op.create_index(batch_op.f("ix_model_calls_purpose"), ["purpose"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_model_calls_request_hash"), ["request_hash"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_model_calls_research_task_id"), ["research_task_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_model_calls_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_model_calls_subtask_id"), ["subtask_id"], unique=False)

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("research_task_id", sa.Integer(), nullable=True),
        sa.Column("subtask_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("caller_kind", sa.String(length=16), nullable=True),
        sa.Column("caller_name", sa.String(length=64), nullable=True),
        sa.Column("payload_summary", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "denied",
                "error",
                "timeout",
                name="tool_call_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("billable", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("cost_estimate", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cost_estimate IS NULL OR cost_estimate >= 0",
            name=op.f("ck_tool_calls_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_tool_calls_duration_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["research_tasks.id"],
            name=op.f("fk_tool_calls_research_task_id_research_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subtask_id"],
            ["research_subtasks.id"],
            name=op.f("fk_tool_calls_subtask_id_research_subtasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tool_calls_caller_kind"), ["caller_kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_tool_calls_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_tool_calls_research_task_id"), ["research_task_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_tool_calls_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_tool_calls_subtask_id"), ["subtask_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tool_calls_tool_name"), ["tool_name"], unique=False)

    # 预留额度 + 租约心跳 + 抢占令牌：没有这四列，多个 worker 会重复花钱
    with op.batch_alter_table("research_tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("calls_reserved", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "cost_reserved", sa.Numeric(precision=20, scale=2), server_default="0", nullable=False
            )
        )
        batch_op.add_column(sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("attempt_token", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_research_tasks_attempt_token"), ["attempt_token"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("research_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_tasks_attempt_token"))
        batch_op.drop_column("attempt_token")
        batch_op.drop_column("lease_heartbeat_at")
        batch_op.drop_column("cost_reserved")
        batch_op.drop_column("calls_reserved")

    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tool_calls_tool_name"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_status"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_research_task_id"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_created_at"))
        batch_op.drop_index(batch_op.f("ix_tool_calls_caller_kind"))
    op.drop_table("tool_calls")

    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_calls_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_model_calls_status"))
        batch_op.drop_index(batch_op.f("ix_model_calls_research_task_id"))
        batch_op.drop_index(batch_op.f("ix_model_calls_request_hash"))
        batch_op.drop_index(batch_op.f("ix_model_calls_purpose"))
        batch_op.drop_index(batch_op.f("ix_model_calls_provider"))
        batch_op.drop_index(batch_op.f("ix_model_calls_model"))
        batch_op.drop_index(batch_op.f("ix_model_calls_created_at"))
    op.drop_table("model_calls")
