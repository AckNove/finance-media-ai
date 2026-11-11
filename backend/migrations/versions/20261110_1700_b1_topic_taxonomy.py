"""M2.0 B1：选题分类体系（tags / topic_collections / topic_tags / topic_score_snapshots）。

Revision ID: b1_topic_taxonomy
Revises: de8148733a9b
Create Date: 2026-11-10

设计依据：``docs/research_hub/database_design.md`` §4.1–§4.4、§5（topics / topic_columns 加列）。

本批次做四件事：
1. 建 4 张新表（标签、选题↔合集、选题↔标签、评分快照）；
2. ``topic_columns`` 加 ``is_active`` / ``is_system`` / ``taxonomy_version``；
3. ``topics`` 加 7 列（主状态机 + 来源 + 去重 + 合并 + 评分），并加自引用外键；
4. **幂等回填**：``topics.column_id`` → ``topic_collections(is_primary=1)``，
   以及 ``content_status`` → ``status`` 的一次性映射。

回填刻意写成 ``WHERE NOT EXISTS`` / 带状态条件的 UPDATE，重复执行不会产生副作用
（开发库当前除 ``alembic_version`` 外为空，回填实际是 0 行，但脚本必须可执行）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1_topic_taxonomy"
down_revision: str | None = "de8148733a9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 幂等回填：column_id → topic_collections（每行只插一次）
BACKFILL_COLLECTIONS = """
INSERT INTO topic_collections (topic_id, column_id, is_primary, weight, created_at, updated_at)
SELECT t.id, t.column_id, 1, 1.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM topics AS t
WHERE t.column_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM topic_collections AS tc
      WHERE tc.topic_id = t.id AND tc.column_id = t.column_id
  )
"""

# 幂等回填：只有已经发布/归档的选题才需要把 content_status 抬成主状态机状态
BACKFILL_STATUS_PUBLISHED = """
UPDATE topics
SET status = 'content_produced'
WHERE content_status = 'published' AND status = 'candidate'
"""

BACKFILL_STATUS_ARCHIVED = """
UPDATE topics
SET status = 'archived'
WHERE content_status = 'archived' AND status <> 'archived'
"""


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "namespace",
            sa.Enum(
                "instrument",
                "institution_type",
                "industry",
                "geography",
                "period",
                "mechanism",
                "other",
                name="tag_namespace",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_tags_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("namespace", "name", name="uq_tag_namespace_name"),
    )
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tags_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_tags_name"), ["name"], unique=False)
        batch_op.create_index(batch_op.f("ix_tags_namespace"), ["namespace"], unique=False)
        batch_op.create_index(batch_op.f("ix_tags_slug"), ["slug"], unique=True)

    op.create_table(
        "topic_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("column_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["topic_columns.id"],
            name=op.f("fk_topic_collections_column_id_topic_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_collections_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_collections")),
        sa.UniqueConstraint("topic_id", "column_id", name="uq_topic_collection"),
    )
    with op.batch_alter_table("topic_collections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_topic_collections_column_id"), ["column_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_collections_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_collections_topic_id"), ["topic_id"], unique=False
        )
        # 部分唯一索引：一个选题最多一个主合集（SQLite 部分索引必须手写）
        batch_op.create_index(
            "uq_topic_primary_collection",
            ["topic_id"],
            unique=True,
            sqlite_where=sa.text("is_primary = 1"),
        )

    op.create_table(
        "topic_score_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("research_value", sa.Float(), nullable=False),
        sa.Column("timeliness", sa.Float(), nullable=False),
        sa.Column("reach_potential", sa.Float(), nullable=False),
        sa.Column("commercial_value", sa.Float(), nullable=False),
        sa.Column("source_availability", sa.Float(), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "scored_by",
            sa.Enum(
                "rule",
                "model",
                "human",
                name="scored_by",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("model_call_id", sa.Integer(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1 AND research_value >= 0"
            " AND research_value <= 1 AND timeliness >= 0 AND timeliness <= 1"
            " AND reach_potential >= 0 AND reach_potential <= 1 AND commercial_value >= 0"
            " AND commercial_value <= 1 AND source_availability >= 0 AND source_availability <= 1",
            name=op.f("ck_topic_score_snapshots_dimensions_range"),
        ),
        sa.CheckConstraint(
            "score_total >= 0 AND score_total <= 1",
            name=op.f("ck_topic_score_snapshots_score_total_range"),
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_score_snapshots_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_score_snapshots")),
    )
    with op.batch_alter_table("topic_score_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_topic_score_snapshots_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_score_snapshots_is_current"), ["is_current"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_score_snapshots_score_total"), ["score_total"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_score_snapshots_scored_by"), ["scored_by"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_score_snapshots_topic_id"), ["topic_id"], unique=False
        )
        batch_op.create_index(
            "uq_topic_current_score",
            ["topic_id"],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
        )

    op.create_table(
        "topic_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name=op.f("ck_topic_tags_weight_range")),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_topic_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_tags_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_tags")),
        sa.UniqueConstraint("topic_id", "tag_id", name="uq_topic_tag"),
    )
    with op.batch_alter_table("topic_tags", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_topic_tags_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_topic_tags_tag_id"), ["tag_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_topic_tags_topic_id"), ["topic_id"], unique=False)

    with op.batch_alter_table("topic_columns", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("is_system", sa.Boolean(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("taxonomy_version", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_topic_columns_is_active"), ["is_active"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_topic_columns_is_system"), ["is_system"], unique=False
        )

    with op.batch_alter_table("topics", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.Enum(
                    "candidate",
                    "scored",
                    "awaiting_review",
                    "approved",
                    "in_research",
                    "researched",
                    "content_produced",
                    "rejected",
                    "parked",
                    "merged",
                    "archived",
                    name="topic_status",
                    native_enum=False,
                    create_constraint=True,
                    length=40,
                ),
                # 已有行必须有值：SQLite 批量重建时 NOT NULL 无默认值会失败
                server_default="candidate",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin_type",
                sa.Enum(
                    "manual",
                    "rss",
                    "news",
                    "knowledge_gap",
                    "ai_suggested",
                    name="topic_origin",
                    native_enum=False,
                    create_constraint=True,
                    length=40,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("discovered_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("dedupe_key", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("merged_into_topic_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("score_total", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("score_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_topics_dedupe_key"), ["dedupe_key"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_topics_merged_into_topic_id"), ["merged_into_topic_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_topics_origin_type"), ["origin_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_topics_score_total"), ["score_total"], unique=False)
        batch_op.create_index(batch_op.f("ix_topics_status"), ["status"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_topics_merged_into_topic_id_topics"),
            "topics",
            ["merged_into_topic_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # --- 幂等回填（数据迁移，不是 seed） ---
    op.execute(BACKFILL_COLLECTIONS)
    op.execute(BACKFILL_STATUS_PUBLISHED)
    op.execute(BACKFILL_STATUS_ARCHIVED)


def downgrade() -> None:
    with op.batch_alter_table("topics", schema=None) as batch_op:
        # SQLite 的 batch 重建会带走反射到的 CHECK 约束；这两个 CHECK 依赖的列
        # 马上要被删掉，必须先显式删除，否则重建 SQL 会引用不存在的列。
        batch_op.drop_constraint(batch_op.f("ck_topics_topic_status"), type_="check")
        batch_op.drop_constraint(batch_op.f("ck_topics_topic_origin"), type_="check")
        batch_op.drop_constraint(
            batch_op.f("fk_topics_merged_into_topic_id_topics"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_topics_status"))
        batch_op.drop_index(batch_op.f("ix_topics_score_total"))
        batch_op.drop_index(batch_op.f("ix_topics_origin_type"))
        batch_op.drop_index(batch_op.f("ix_topics_merged_into_topic_id"))
        batch_op.drop_index(batch_op.f("ix_topics_dedupe_key"))
        batch_op.drop_column("score_updated_at")
        batch_op.drop_column("score_total")
        batch_op.drop_column("merged_into_topic_id")
        batch_op.drop_column("dedupe_key")
        batch_op.drop_column("discovered_by")
        batch_op.drop_column("origin_type")
        batch_op.drop_column("status")

    with op.batch_alter_table("topic_columns", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_topic_columns_is_system"))
        batch_op.drop_index(batch_op.f("ix_topic_columns_is_active"))
        batch_op.drop_column("taxonomy_version")
        batch_op.drop_column("is_system")
        batch_op.drop_column("is_active")

    with op.batch_alter_table("topic_tags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_topic_tags_topic_id"))
        batch_op.drop_index(batch_op.f("ix_topic_tags_tag_id"))
        batch_op.drop_index(batch_op.f("ix_topic_tags_created_at"))
    op.drop_table("topic_tags")

    with op.batch_alter_table("topic_score_snapshots", schema=None) as batch_op:
        batch_op.drop_index("uq_topic_current_score", sqlite_where=sa.text("is_current = 1"))
        batch_op.drop_index(batch_op.f("ix_topic_score_snapshots_topic_id"))
        batch_op.drop_index(batch_op.f("ix_topic_score_snapshots_scored_by"))
        batch_op.drop_index(batch_op.f("ix_topic_score_snapshots_score_total"))
        batch_op.drop_index(batch_op.f("ix_topic_score_snapshots_is_current"))
        batch_op.drop_index(batch_op.f("ix_topic_score_snapshots_created_at"))
    op.drop_table("topic_score_snapshots")

    with op.batch_alter_table("topic_collections", schema=None) as batch_op:
        batch_op.drop_index("uq_topic_primary_collection", sqlite_where=sa.text("is_primary = 1"))
        batch_op.drop_index(batch_op.f("ix_topic_collections_topic_id"))
        batch_op.drop_index(batch_op.f("ix_topic_collections_created_at"))
        batch_op.drop_index(batch_op.f("ix_topic_collections_column_id"))
    op.drop_table("topic_collections")

    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tags_slug"))
        batch_op.drop_index(batch_op.f("ix_tags_namespace"))
        batch_op.drop_index(batch_op.f("ix_tags_name"))
        batch_op.drop_index(batch_op.f("ix_tags_created_at"))
    op.drop_table("tags")
