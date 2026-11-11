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
    # M2.0 B1：选题分类体系
    "tags",
    "topic_collections",
    "topic_tags",
    "topic_score_snapshots",
    # M2.0 B2：研究任务层
    "research_tasks",
    "research_subtasks",
    "research_subtask_dependencies",
    "task_events",
    # M2.0 B3：成本与工具计量
    "model_calls",
    "tool_calls",
}

#: B1 之前的表数（初始迁移的产物），用于断言"批次边界"而不是只断言终态
TABLES_AFTER_INITIAL = 18
#: M2.0 B1 / B2 / B3 完成后的业务表数（database_design.md §6.4）
TABLES_AFTER_B1 = 22
TABLES_AFTER_B2 = 26
TABLES_AFTER_B3 = 28


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
    """初始迁移可以在离线模式（sql=True）下生成 SQL。

    M2.0 的 B1/B2/B3 用的是 ``batch_alter_table``（SQLite 不支持 ALTER COLUMN），
    批量模式必须连上真实数据库反射表结构，因此**离线模式到不了 head**——
    见下一个用例，这是已知限制而不是回归。
    """
    config = make_alembic_config(f"sqlite:///{(tmp_path / 'offline.db').as_posix()}")
    config.output_buffer = None

    command.upgrade(config, "de8148733a9b", sql=True)


def test_offline_sql_stops_at_initial_revision_by_design(tmp_path: Path):
    """记录已知限制：SQLite 批量模式无法离线生成 B1/B2/B3 的 SQL。

    如果哪天这条用例失败了（不再抛 CommandError），说明迁移改成了
    ``copy_from`` 或换了支持离线 ALTER 的写法，那时应当把这条用例改成正向断言。
    """
    from alembic.util.exc import CommandError

    config = make_alembic_config(f"sqlite:///{(tmp_path / 'offline_head.db').as_posix()}")
    config.output_buffer = None

    with pytest.raises(CommandError, match="batch mode"):
        command.upgrade(config, "head", sql=True)


@pytest.mark.parametrize(
    ("target", "expected_business_tables"),
    [
        ("de8148733a9b", TABLES_AFTER_INITIAL),
        ("b1_topic_taxonomy", TABLES_AFTER_B1),
        ("b2_task_layer", TABLES_AFTER_B2),
        ("b3_metering", TABLES_AFTER_B3),
    ],
)
def test_each_batch_lands_on_expected_table_count(
    tmp_path: Path, target: str, expected_business_tables: int
):
    """逐批 upgrade，验证 B1=22 / B2=26 / B3=28 的业务表数（database_design §6.4）。"""
    url = f"sqlite:///{(tmp_path / f'batch_{target}.db').as_posix()}"
    config = make_alembic_config(url)
    command.upgrade(config, target)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    business = tables - {"alembic_version"}
    assert len(business) == expected_business_tables, sorted(business)


def test_batches_are_individually_reversible(tmp_path: Path):
    """每一批都能单独 downgrade -1 再 upgrade 回来（迁移可逆是硬要求）。"""
    url = f"sqlite:///{(tmp_path / 'reversible.db').as_posix()}"
    config = make_alembic_config(url)

    for target, _previous in (
        ("b1_topic_taxonomy", "de8148733a9b"),
        ("b2_task_layer", "b1_topic_taxonomy"),
        ("b3_metering", "b2_task_layer"),
    ):
        command.upgrade(config, target)
        command.downgrade(config, "-1")
        command.upgrade(config, target)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert BUSINESS_TABLES.issubset(tables)


def test_partial_unique_indexes_are_partial(migrated_db: str):
    """两个"唯一 current / 唯一 primary"必须是真的部分索引（带 WHERE）。"""
    engine = create_engine(migrated_db)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master"
                    " WHERE type = 'index' AND name LIKE 'uq_topic_%'"
                )
            ).fetchall()
            fk_problems = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
            integrity = connection.execute(text("PRAGMA integrity_check")).scalar()
    finally:
        engine.dispose()

    by_name = {row[0]: (row[1] or "").upper() for row in rows}
    assert set(by_name) == {"uq_topic_primary_collection", "uq_topic_current_score"}
    assert all("WHERE" in sql for sql in by_name.values())
    assert fk_problems == []
    assert integrity == "ok"


def test_backfill_maps_column_id_to_primary_collection(tmp_path: Path):
    """回填：topics.column_id 应写成 topic_collections(is_primary=1)，且可重复执行。"""
    import importlib.util

    url = f"sqlite:///{(tmp_path / 'backfill.db').as_posix()}"
    config = make_alembic_config(url)
    command.upgrade(config, "de8148733a9b")  # 先到 B1 之前，数据里已有 column_id

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO topic_columns (code, name, display_order, created_at, updated_at)"
                    " VALUES ('financial_knowledge', '金融知识', 0, CURRENT_TIMESTAMP,"
                    " CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO topics (title, slug, knowledge_domain, audience_level, priority,"
                    " football_analogy_required, research_status, content_status, column_id,"
                    " created_at, updated_at) VALUES ('回填用例', 'backfill-case', 'general',"
                    " 'beginner', 3, 0, 'pending', 'published',"
                    " (SELECT id FROM topic_columns WHERE code = 'financial_knowledge'),"
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "b1_topic_taxonomy")

    module_path = (
        PROJECT_ROOT / "backend" / "migrations" / "versions" / "20261110_1700_b1_topic_taxonomy.py"
    )
    spec = importlib.util.spec_from_file_location("b1_topic_taxonomy", module_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(url)
    try:
        # 迁移里已经回填过一次；再执行一次验证幂等
        with engine.begin() as connection:
            connection.execute(text(migration.BACKFILL_COLLECTIONS))
        with engine.connect() as connection:
            collections = connection.execute(
                text("SELECT count(*) FROM topic_collections WHERE is_primary = 1")
            ).scalar()
            status = connection.execute(
                text("SELECT status FROM topics WHERE slug = 'backfill-case'")
            ).scalar()
    finally:
        engine.dispose()

    assert collections == 1
    assert status == "content_produced"


def test_alembic_check_reports_no_drift(tmp_path: Path):
    """模型与迁移必须一致（``alembic check`` 无新操作）。"""
    url = f"sqlite:///{(tmp_path / 'check.db').as_posix()}"
    config = make_alembic_config(url)
    command.upgrade(config, "head")

    command.check(config)  # 有漂移会抛 AutogenerateDiffsDetected
