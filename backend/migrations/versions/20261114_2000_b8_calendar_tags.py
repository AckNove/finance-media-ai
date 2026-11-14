"""B8：``content_calendar.tags`` —— 给话题标签一个正经字段

为什么要有这一版
-----------------
导出时三个平台的成稿都要带话题标签（抖音建议 3~5 个、小红书 5~10 个），而标签
此前是**从排期的 ``notes``（备注）里按空格切出来的**：

.. code-block:: text

    tags=(entry.notes or "").split()

中文备注通常没有空格，于是「研究储能行业，重点看电芯价格」整句变成一个标签，
写一整段笔记就变成十几个莫名其妙的标签——而人完全看不出这件事，导出稿却已经
带着这些标签准备发出去了。**备注不是标签**，两者语义不同，混用还会互相挤占。

本版只做一件事：加一列 ``tags TEXT NOT NULL DEFAULT ''``。
不做从 ``notes`` 的历史回填——那等于把"整段备注"固化成标签，是把坑挖深一点，
不是填坑。已有排期的标签留空，导出清单会如实提示"标签 0 个，平台建议 N~M 个"。

修订：``b8_calendar_tags``；父版本：``b7_topic_origin``。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b8_calendar_tags"
down_revision = "b7_topic_origin"
branch_labels = None
depends_on = None

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


def upgrade() -> None:
    with op.batch_alter_table("content_calendar") as batch_op:
        batch_op.add_column(
            sa.Column("tags", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    with op.batch_alter_table("content_calendar") as batch_op:
        batch_op.drop_column("tags")
