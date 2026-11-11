"""Alembic 迁移环境。

- 数据库 URL 来自应用配置（DATABASE_URL 环境变量 / .env），不写死在 alembic.ini；
- target_metadata 指向 backend.app.models 的 Base.metadata，因此新增模型必须
  在 backend/app/models/__init__.py 中导入，否则 autogenerate 会漏表；
- SQLite 不支持大多数 ALTER TABLE，使用 batch 模式（render_as_batch=True）。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.core.config import get_settings

# 导入全部模型以填充 Base.metadata
from backend.app.database.base import Base
import backend.app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers 默认是 True：在同进程里跑迁移（测试、脚本）
    # 会把 backend.app.* 的 logger 全部禁用，之后应用日志就哑了。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    """返回迁移使用的数据库 URL（可通过 -x db_url=... 覆盖，主要用于测试）。"""
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    return get_settings().resolved_database_url()


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
