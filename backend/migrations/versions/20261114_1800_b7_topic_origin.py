"""B7：``topics.origin_type`` 补回填并收紧成 NOT NULL default manual

为什么要有这一版
----------------
``docs/research_hub/database_design.md`` 里 ``topics.origin_type`` 写的是
「Enum ``TopicOrigin`` NOT NULL default ``manual``」——用来区分人工发现的选题与
RSS / 新闻 / 知识缺口 / AI 建议发现的。B1 加这一列时写成了 ``nullable=True``
且没给默认值，于是：

* 从 V5 老库升上来的行，这一列是 ``NULL``；
* ORM 侧写的是 ``Mapped[TopicOrigin | None]``，看着"合法"，读代码的人也发现不了；
* ``alembic check`` 只比结构，也不会报——**只有带数据整库演练才会露出来**
  （见 ``backend/app/services/upgrade_drill.py`` 的 ``backfill`` 一步）。

本版做两件事：
1. ``UPDATE topics SET origin_type='manual' WHERE origin_type IS NULL``
   ——老行都是人在界面上建的，标 ``manual`` 是当时的事实，不是猜；
2. 把该列收紧成 ``NOT NULL`` 并给 ``server_default='manual'``，
   新建行即使用裸 SQL 插入也不会留空。

修订：``b7_topic_origin``；父版本：``b6_v6_operations``。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b7_topic_origin"
down_revision = "b6_v6_operations"
branch_labels = None
depends_on = None

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

_TOPIC_ORIGIN_VALUES = ("manual", "rss", "news", "knowledge_gap", "ai_suggested")

#: 老行的来源就是"人工建的"，这里是有依据的判定，不是随手挑一个值
BACKFILL_ORIGIN = (
    "UPDATE topics SET origin_type = 'manual' WHERE origin_type IS NULL OR origin_type = ''"
)


def _origin_column(*, nullable: bool) -> sa.Column:
    return sa.Column(
        "origin_type",
        sa.Enum(
            *_TOPIC_ORIGIN_VALUES,
            name="topic_origin",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=nullable,
        server_default=None if nullable else "manual",
    )


def upgrade() -> None:
    # 1) 先回填，再收紧约束：顺序反了会在有老行的库上直接失败
    op.execute(BACKFILL_ORIGIN)

    # 2) SQLite 不支持 ALTER COLUMN，batch 模式会重建表；重建会带上反射到的
    #    CHECK 约束，所以这里只改这一个列的 nullable / server_default。
    with op.batch_alter_table("topics") as batch_op:
        batch_op.alter_column(
            "origin_type",
            existing_type=sa.Enum(
                *_TOPIC_ORIGIN_VALUES,
                name="topic_origin",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            existing_nullable=True,
            existing_server_default=None,
            nullable=False,
            server_default="manual",
        )


def downgrade() -> None:
    with op.batch_alter_table("topics") as batch_op:
        batch_op.alter_column(
            "origin_type",
            existing_type=sa.Enum(
                *_TOPIC_ORIGIN_VALUES,
                name="topic_origin",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
            existing_nullable=False,
            existing_server_default=sa.text("'manual'"),
            nullable=True,
            server_default=None,
        )
