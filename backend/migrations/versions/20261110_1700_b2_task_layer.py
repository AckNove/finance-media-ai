"""M2.0 B2：研究任务层（research_tasks / research_subtasks / 依赖 / task_events）。

Revision ID: b2_task_layer
Revises: b1_topic_taxonomy
Create Date: 2026-11-10

设计依据：``docs/research_hub/database_design.md`` §4.5–§4.8、§5（agent_runs / research_reports 加列）。

本批次只建"任务骨架"，不建成本计量表（那是 B3）：
1. 建 4 张新表：research_tasks、research_subtasks、research_subtask_dependencies、task_events；
2. ``agent_runs`` 加 ``research_task_id`` / ``subtask_id`` / ``cost_estimate``；
3. ``research_reports`` 加 ``research_task_id`` / ``outline`` / ``tier_stats``。

刻意不做的事：
- ``research_subtasks.task_type`` **不加枚举 CHECK**，只做格式校验
  （``GLOB '*.*'``）——加任务类型不应该需要一次迁移，权威清单在
  ``config/agent_routing.yaml`` 与 ``backend/app/execution/task_types.py``。
- 依赖表不做环检测（SQLite 无法表达），环检测属于服务层职责。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2_task_layer"
down_revision: str | None = "b1_topic_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("out_of_scope", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "planning",
                "running",
                "paused",
                "completed",
                "failed",
                "cancelled",
                name="research_task_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("planner", sa.String(length=64), nullable=True),
        sa.Column("budget_max_calls", sa.Integer(), nullable=True),
        sa.Column("budget_max_tokens", sa.Integer(), nullable=True),
        sa.Column("budget_max_cost", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("calls_spent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_spent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_spent", sa.Numeric(precision=20, scale=2), server_default="0", nullable=False),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "budget_max_calls IS NULL OR budget_max_calls >= 0",
            name=op.f("ck_research_tasks_max_calls_non_negative"),
        ),
        sa.CheckConstraint(
            "budget_max_cost IS NULL OR budget_max_cost >= 0",
            name=op.f("ck_research_tasks_max_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "calls_spent >= 0 AND tokens_spent >= 0",
            name=op.f("ck_research_tasks_spent_non_negative"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_research_tasks_version_positive")),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_research_tasks_topic_id_topics"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_tasks")),
    )
    with op.batch_alter_table("research_tasks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_research_tasks_claimed_by"), ["claimed_by"], unique=False)
        batch_op.create_index(batch_op.f("ix_research_tasks_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_research_tasks_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_research_tasks_title"), ["title"], unique=False)
        batch_op.create_index(batch_op.f("ix_research_tasks_topic_id"), ["topic_id"], unique=False)

    op.create_table(
        "research_subtasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("research_task_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("assignee", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "ready",
                "running",
                "succeeded",
                "failed",
                "blocked",
                "skipped",
                "cancelled",
                name="subtask_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(task_type) >= 3 AND length(task_type) <= 64 AND task_type GLOB '*.*'",
            name=op.f("ck_research_subtasks_task_type_format"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_research_subtasks_attempts_non_negative")
        ),
        sa.CheckConstraint(
            "max_attempts >= 1", name=op.f("ck_research_subtasks_max_attempts_positive")
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_research_subtasks_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["research_tasks.id"],
            name=op.f("fk_research_subtasks_research_task_id_research_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_subtasks")),
        sa.UniqueConstraint("research_task_id", "sequence", name="uq_subtask_sequence"),
    )
    with op.batch_alter_table("research_subtasks", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_research_subtasks_agent_run_id"), ["agent_run_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_research_subtasks_assignee"), ["assignee"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_research_subtasks_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_research_subtasks_research_task_id"),
            ["research_task_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_research_subtasks_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_research_subtasks_task_status",
            ["research_task_id", "status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_research_subtasks_task_type"), ["task_type"], unique=False
        )

    op.create_table(
        "research_subtask_dependencies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subtask_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_subtask_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subtask_id <> depends_on_subtask_id",
            name=op.f("ck_research_subtask_dependencies_no_self_dependency"),
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_subtask_id"],
            ["research_subtasks.id"],
            name=op.f("fk_research_subtask_dependencies_depends_on_subtask_id_research_subtasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subtask_id"],
            ["research_subtasks.id"],
            name=op.f("fk_research_subtask_dependencies_subtask_id_research_subtasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_subtask_dependencies")),
        sa.UniqueConstraint("subtask_id", "depends_on_subtask_id", name="uq_subtask_dependency"),
    )
    with op.batch_alter_table("research_subtask_dependencies", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_research_subtask_dependencies_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_research_subtask_dependencies_depends_on_subtask_id"),
            ["depends_on_subtask_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_research_subtask_dependencies_subtask_id"),
            ["subtask_id"],
            unique=False,
        )

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("research_task_id", sa.Integer(), nullable=False),
        sa.Column("subtask_id", sa.Integer(), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "queued",
                "claimed",
                "plan_created",
                "subtask_started",
                "subtask_succeeded",
                "subtask_failed",
                "retried",
                "paused",
                "resumed",
                "direction_changed",
                "budget_exceeded",
                "completed",
                "failed",
                "cancelled",
                name="task_event_type",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("cost_delta", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["research_tasks.id"],
            name=op.f("fk_task_events_research_task_id_research_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subtask_id"],
            ["research_subtasks.id"],
            name=op.f("fk_task_events_subtask_id_research_subtasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
        sa.UniqueConstraint("research_task_id", "event_seq", name="uq_task_event_seq"),
    )
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_task_events_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_task_events_event_type"), ["event_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_task_events_research_task_id"), ["research_task_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_task_events_subtask_id"), ["subtask_id"], unique=False)

    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("research_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("subtask_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("cost_estimate", sa.Numeric(precision=20, scale=2), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_agent_runs_research_task_id"), ["research_task_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_agent_runs_subtask_id"), ["subtask_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_agent_runs_subtask_id_research_subtasks"),
            "research_subtasks",
            ["subtask_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_agent_runs_research_task_id_research_tasks"),
            "research_tasks",
            ["research_task_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("research_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("research_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("outline", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("tier_stats", sa.JSON(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_research_reports_research_task_id"), ["research_task_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_research_reports_research_task_id_research_tasks"),
            "research_tasks",
            ["research_task_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("research_reports", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_research_reports_research_task_id_research_tasks"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_research_reports_research_task_id"))
        batch_op.drop_column("tier_stats")
        batch_op.drop_column("outline")
        batch_op.drop_column("research_task_id")

    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_agent_runs_research_task_id_research_tasks"), type_="foreignkey"
        )
        batch_op.drop_constraint(
            batch_op.f("fk_agent_runs_subtask_id_research_subtasks"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_agent_runs_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_agent_runs_research_task_id"))
        batch_op.drop_column("cost_estimate")
        batch_op.drop_column("subtask_id")
        batch_op.drop_column("research_task_id")

    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_events_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_task_events_research_task_id"))
        batch_op.drop_index(batch_op.f("ix_task_events_event_type"))
        batch_op.drop_index(batch_op.f("ix_task_events_created_at"))
    op.drop_table("task_events")

    with op.batch_alter_table("research_subtask_dependencies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_subtask_dependencies_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_research_subtask_dependencies_depends_on_subtask_id"))
        batch_op.drop_index(batch_op.f("ix_research_subtask_dependencies_created_at"))
    op.drop_table("research_subtask_dependencies")

    with op.batch_alter_table("research_subtasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_subtasks_task_type"))
        batch_op.drop_index("ix_research_subtasks_task_status")
        batch_op.drop_index(batch_op.f("ix_research_subtasks_status"))
        batch_op.drop_index(batch_op.f("ix_research_subtasks_research_task_id"))
        batch_op.drop_index(batch_op.f("ix_research_subtasks_created_at"))
        batch_op.drop_index(batch_op.f("ix_research_subtasks_assignee"))
        batch_op.drop_index(batch_op.f("ix_research_subtasks_agent_run_id"))
    op.drop_table("research_subtasks")

    with op.batch_alter_table("research_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_tasks_topic_id"))
        batch_op.drop_index(batch_op.f("ix_research_tasks_title"))
        batch_op.drop_index(batch_op.f("ix_research_tasks_status"))
        batch_op.drop_index(batch_op.f("ix_research_tasks_created_at"))
        batch_op.drop_index(batch_op.f("ix_research_tasks_claimed_by"))
    op.drop_table("research_tasks")
