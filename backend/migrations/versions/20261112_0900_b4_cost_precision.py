"""B4：成本列精度从 2 位小数提到 6 位。

**为什么必须改**：一次模型调用通常只花 0.0001–0.02 元。``NUMERIC(20, 2)`` 会把
0.0002 元舍成 ``0.00``，后果是

- ``model_calls.cost_estimate`` 读回来永远是 0 → "当日已花"永远为 0；
- 于是 ``daily_cap_cny`` 闸门形同不存在；
- 成本统计页（V5.0 第 19 项）拿不到任何真实数字。

业务金额（合同金额、投资额、最低投资额）继续用 ``NUMERIC(20, 2)``，只有
**成本核算列**改成 ``NUMERIC(20, 6)``。

涉及列（7 个）：

- ``model_calls.cost_estimate``、``tool_calls.cost_estimate``、
  ``agent_runs.cost_estimate``、``task_events.cost_delta``（可空）；
- ``research_tasks.budget_max_cost``（可空）、``research_tasks.cost_spent`` 与
  ``research_tasks.cost_reserved``（非空，默认 0）。

SQLite 的 NUMERIC 亲和性本身不截断精度，但 SQLAlchemy 的 ``Numeric(scale=2)``
结果处理器会把读出的 Decimal 量化到两位——所以模型层与迁移层必须同时改，
只改一边会被 ``alembic check`` 判成漂移（本迁移就是为了消掉这个漂移）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4_cost_precision"
down_revision = "b3_metering"
branch_labels = None
depends_on = None

NEW_TYPE = sa.Numeric(20, 6)
OLD_TYPE = sa.Numeric(20, 2)

#: (表, 列, 是否可空, 服务端默认值)
TARGETS: tuple[tuple[str, str, bool, str | None], ...] = (
    ("model_calls", "cost_estimate", True, None),
    ("tool_calls", "cost_estimate", True, None),
    ("agent_runs", "cost_estimate", True, None),
    ("task_events", "cost_delta", True, None),
    ("research_tasks", "budget_max_cost", True, None),
    ("research_tasks", "cost_spent", False, "0"),
    ("research_tasks", "cost_reserved", False, "0"),
)


def _alter(target_type: sa.types.TypeEngine, old_type: sa.types.TypeEngine) -> None:
    # 按表分组，一次 batch 里改完该表的所有列，减少 SQLite 重建次数
    by_table: dict[str, list[tuple[str, bool, str | None]]] = {}
    for table, column, nullable, default in TARGETS:
        by_table.setdefault(table, []).append((column, nullable, default))
    for table, columns in by_table.items():
        with op.batch_alter_table(table) as batch_op:
            for column, nullable, default in columns:
                batch_op.alter_column(
                    column,
                    existing_type=old_type,
                    type_=target_type,
                    existing_nullable=nullable,
                    existing_server_default=default,
                    server_default=default,
                )


def upgrade() -> None:
    _alter(NEW_TYPE, OLD_TYPE)


def downgrade() -> None:
    _alter(OLD_TYPE, NEW_TYPE)
