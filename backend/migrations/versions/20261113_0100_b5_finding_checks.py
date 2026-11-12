"""B5：Finding 语义核查表 ``finding_checks``。

**为什么新开一张表**：V5.1 要求每条结论都留下"模型判定 + 理由 + 支持片段 +
反对片段 + 缺失信息 + 模型与成本 + 人工复核状态"。前六项可以塞进
``research_reports.findings`` 的 JSON，但"人工复核状态"必须能被人逐条改，
JSON 里改一格既没法加审计也拦不住误覆盖；而且前端要按判定状态筛选列表。
所以模型判定与人工状态都落这张表，并且是两个**独立列**：

* ``verdict`` —— 模型说的（supported / partially_supported / contradicted /
  insufficient_evidence / unverifiable）；
* ``human_review_status`` —— 人说的（pending / confirmed / rejected /
  needs_more_evidence）。

任何代码路径都不许把 ``verdict=supported`` 自动写成
``human_review_status=confirmed``：模型核查 ≠ 人工核实。

本迁移只建表、不写行；``downgrade`` 直接删表（这张表里全是可重算的核查结果，
不需要像 topics 那样保留回填数据）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5_finding_checks"
down_revision = "b4_cost_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("research_report_id", sa.Integer(), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("claim_kind", sa.String(length=16), nullable=False, server_default="inference"),
        sa.Column("evidence_ids", sa.JSON(), nullable=True),
        sa.Column("source_ids", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("supporting", sa.JSON(), nullable=True),
        sa.Column("contradicting", sa.JSON(), nullable=True),
        sa.Column("missing", sa.JSON(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model_call_id", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cny", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "human_review_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("human_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("human_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_checks")),
        sa.ForeignKeyConstraint(
            ["research_report_id"],
            ["research_reports.id"],
            name=op.f("fk_finding_checks_research_report_id_research_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["model_calls.id"],
            name=op.f("fk_finding_checks_model_call_id_model_calls"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "finding_index >= 0", name=op.f("ck_finding_checks_index_non_negative")
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0",
            name=op.f("ck_finding_checks_tokens_non_negative"),
        ),
        sa.CheckConstraint("cost_cny >= 0", name=op.f("ck_finding_checks_cost_non_negative")),
    )
    op.create_index(
        op.f("ix_finding_checks_research_report_id"),
        "finding_checks",
        ["research_report_id"],
    )
    op.create_index(op.f("ix_finding_checks_verdict"), "finding_checks", ["verdict"])
    op.create_index(
        op.f("ix_finding_checks_human_review_status"),
        "finding_checks",
        ["human_review_status"],
    )
    op.create_index(
        op.f("ix_finding_checks_model_call_id"), "finding_checks", ["model_call_id"]
    )
    # TimestampMixin 的 created_at 带索引，ORM 侧认为它该存在；迁移里也建一份，
    # 否则 `alembic check` 会报漂移。
    op.create_index(
        op.f("ix_finding_checks_created_at"), "finding_checks", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_finding_checks_created_at"), table_name="finding_checks")
    op.drop_index(op.f("ix_finding_checks_model_call_id"), table_name="finding_checks")
    op.drop_index(
        op.f("ix_finding_checks_human_review_status"), table_name="finding_checks"
    )
    op.drop_index(op.f("ix_finding_checks_verdict"), table_name="finding_checks")
    op.drop_index(op.f("ix_finding_checks_research_report_id"), table_name="finding_checks")
    op.drop_table("finding_checks")
