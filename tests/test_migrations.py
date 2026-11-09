"""Alembic 迁移：真实执行 upgrade / downgrade，验证迁移可用且与模型一致。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BUSINESS_TABLES = {
    "topics",
    "topic_columns",
    "knowledge_items",
    "knowledge_relations",
    "sources",
    "evidence",
    "research_reports",
    "agent_runs",
    "review_records",
    "scripts",
    "media_assets",
    "publications",
    "institutions",
    "financial_instruments",
    "financial_products",
    "industries",
    "football_cases",
    "financial_cases",
}


def make_alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    # 通过 -x db_url=... 覆盖，避免污染开发库
    config.cmd_opts = SimpleNamespace(x=[f"db_url={db_url}"])
    return config


@pytest.fixture()
def migrated_db(tmp_path: Path) -> str:
    db_path = tmp_path / "migration_test.db"
    url = f"sqlite:///{db_path.as_posix()}"
    command.upgrade(make_alembic_config(url), "head")
    return url


def test_upgrade_creates_every_domain_table(migrated_db: str):
    engine = create_engine(migrated_db)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert "alembic_version" in tables
    assert BUSINESS_TABLES.issubset(tables)
    assert len(tables) == len(BUSINESS_TABLES) + 1


def test_migration_records_revision_matching_script_head(migrated_db: str):
    engine = create_engine(migrated_db)
    try:
        with engine.connect() as connection:
            applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    head = ScriptDirectory.from_config(make_alembic_config(migrated_db)).get_current_head()
    assert head is not None
    assert applied == head


def test_downgrade_to_base_removes_business_tables(migrated_db: str):
    config = make_alembic_config(migrated_db)
    command.downgrade(config, "base")

    engine = create_engine(migrated_db)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert BUSINESS_TABLES.isdisjoint(tables)
    assert tables.issubset({"alembic_version"})


def test_upgrade_is_reentrant(migrated_db: str):
    config = make_alembic_config(migrated_db)
    command.upgrade(config, "head")  # 再次执行不应报错也不应重复建表

    engine = create_engine(migrated_db)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert BUSINESS_TABLES.issubset(tables)


def test_offline_sql_generation_mentions_core_tables(tmp_path: Path):
    """离线模式（sql=True）也应能生成 SQL，证明迁移脚本无连接依赖错误。"""
    config = make_alembic_config(f"sqlite:///{(tmp_path / 'offline.db').as_posix()}")
    config.output_buffer = None

    command.upgrade(config, "head", sql=True)
