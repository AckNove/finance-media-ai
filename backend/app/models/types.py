"""ORM 数据类型辅助工具。"""

from __future__ import annotations

from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Numeric
from sqlalchemy.orm import MappedColumn, mapped_column

# 金额统一用 Numeric(20, 2)：SQLite 与 PostgreSQL 均兼容，
# 避免 float 舍入误差；币种与单位单独建列记录。
MoneyAmount = Numeric(20, 2)


def enum_column(
    enum_cls: type[PyEnum],
    *,
    name: str,
    default: PyEnum | None = None,
    nullable: bool = False,
    index: bool = False,
    length: int = 40,
    server_default: PyEnum | None = None,
) -> Any:
    """构造可移植的枚举列（返回可直接赋值给 Mapped 属性的 mapped_column）。

    - native_enum=False：使用 VARCHAR + CHECK 约束，SQLite 上真正生效；
    - values_callable：数据库里存枚举 value（小写字符串），而不是成员名；
    - 不创建 PostgreSQL 原生 ENUM 类型，避免后续迁移中的 ALTER TYPE 复杂度；
    - server_default：仅当该列需要给**已存在的行**提供默认值时才设置
      （SQLite 的 ADD COLUMN ... NOT NULL 必须有默认值，否则批量重建会失败）。
    """
    return mapped_column(
        SAEnum(
            enum_cls,
            name=name,
            native_enum=False,
            length=length,
            values_callable=lambda members: [member.value for member in members],
            validate_strings=True,
            create_constraint=True,
        ),
        default=default,
        nullable=nullable,
        index=index,
        server_default=server_default.value if server_default is not None else None,
    )


# 便于类型标注使用
__all__ = ["MoneyAmount", "enum_column", "MappedColumn"]
