"""B6：运营层两张表 ``content_calendar`` / ``platform_metrics``（V6.0 K 段）。

**为什么要这两张表**：V6.0 要求把"内容日历 / 选题排期 / 内容状态 /
视频审核状态 / 导出记录 / 发布记录 / 多平台适配 / 账号数据导入 /
播放量与互动分析"落成真数据。此前这些只在人的脑子里和聊天记录里。

两条设计纪律写进表结构：

1. **系统不自动发布**。``publish_state`` 只能由人改，且改成 ``published``
   必须有 ``published_url``（CHECK 约束强制）——"发了"必须有痕迹，不允许
   空口标记。
2. **数据是一串快照，不是一列累计值**。``platform_metrics`` 每条记录是
   某一时刻的一次回填（``captured_at`` 唯一），这样才能算增长曲线；
   直接在日历行上覆盖数字会丢掉历史，也没法算"这条内容三天后还在涨吗"。

本迁移只建表、不写行；``downgrade`` 逆序删表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6_v6_operations"
down_revision = "b5_finding_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_calendar",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("script_id", sa.Integer(), nullable=True),
        sa.Column("research_report_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False, server_default="douyin"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="planned"),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("video_job_id", sa.String(length=80), nullable=True),
        sa.Column("export_path", sa.String(length=400), nullable=True),
        sa.Column(
            "publish_state", sa.String(length=40), nullable=False, server_default="not_published"
        ),
        sa.Column("published_url", sa.String(length=600), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_calendar")),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_content_calendar_topic_id_topics"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name=op.f("fk_content_calendar_script_id_scripts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_report_id"],
            ["research_reports.id"],
            name=op.f("fk_content_calendar_research_report_id_research_reports"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(title) >= 2", name=op.f("ck_content_calendar_title_len")),
        sa.CheckConstraint(
            "publish_state != 'published' OR published_url IS NOT NULL",
            name=op.f("ck_content_calendar_published_needs_url"),
        ),
    )
    op.create_index(op.f("ix_content_calendar_topic_id"), "content_calendar", ["topic_id"])
    op.create_index(op.f("ix_content_calendar_script_id"), "content_calendar", ["script_id"])
    op.create_index(
        op.f("ix_content_calendar_research_report_id"), "content_calendar", ["research_report_id"]
    )
    op.create_index(op.f("ix_content_calendar_platform"), "content_calendar", ["platform"])
    op.create_index(op.f("ix_content_calendar_status"), "content_calendar", ["status"])
    op.create_index(
        op.f("ix_content_calendar_scheduled_for"), "content_calendar", ["scheduled_for"]
    )
    op.create_index(op.f("ix_content_calendar_video_job_id"), "content_calendar", ["video_job_id"])
    op.create_index(
        op.f("ix_content_calendar_publish_state"), "content_calendar", ["publish_state"]
    )
    op.create_index(
        "ix_content_calendar_platform_status", "content_calendar", ["platform", "status"]
    )
    # TimestampMixin 的 created_at 带索引，迁移里也建一份，否则 `alembic check` 会报漂移。
    op.create_index(op.f("ix_content_calendar_created_at"), "content_calendar", ["created_at"])

    op.create_table(
        "platform_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("followers_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_metrics")),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["content_calendar.id"],
            name=op.f("fk_platform_metrics_calendar_id_content_calendar"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "views >= 0 AND likes >= 0", name=op.f("ck_platform_metrics_metrics_non_negative")
        ),
        sa.CheckConstraint(
            "comments >= 0 AND shares >= 0", name=op.f("ck_platform_metrics_metrics_engagement")
        ),
        sa.UniqueConstraint("calendar_id", "captured_at", name="uq_platform_metric_snapshot"),
    )
    op.create_index(
        op.f("ix_platform_metrics_calendar_id"), "platform_metrics", ["calendar_id"]
    )
    op.create_index(op.f("ix_platform_metrics_platform"), "platform_metrics", ["platform"])
    op.create_index(
        op.f("ix_platform_metrics_captured_at"), "platform_metrics", ["captured_at"]
    )
    op.create_index(op.f("ix_platform_metrics_created_at"), "platform_metrics", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_platform_metrics_created_at"), table_name="platform_metrics")
    op.drop_index(op.f("ix_platform_metrics_captured_at"), table_name="platform_metrics")
    op.drop_index(op.f("ix_platform_metrics_platform"), table_name="platform_metrics")
    op.drop_index(op.f("ix_platform_metrics_calendar_id"), table_name="platform_metrics")
    op.drop_table("platform_metrics")

    op.drop_index(op.f("ix_content_calendar_created_at"), table_name="content_calendar")
    op.drop_index("ix_content_calendar_platform_status", table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_publish_state"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_video_job_id"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_scheduled_for"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_status"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_platform"), table_name="content_calendar")
    op.drop_index(
        op.f("ix_content_calendar_research_report_id"), table_name="content_calendar"
    )
    op.drop_index(op.f("ix_content_calendar_script_id"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_topic_id"), table_name="content_calendar")
    op.drop_table("content_calendar")
