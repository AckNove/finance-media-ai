"""升级演练的测试：证明"带数据整库升上来"这件事真的能被验出来。

这些用例刻意都跑**真迁移**（不 mock），因为要验的就是迁移本身。为了不让整套变慢，
只有一条用例跑完整的 roundtrip，其余都关掉它或用很小的库。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.app.core.config import PROJECT_ROOT
from backend.app.services import upgrade_drill as drill
from backend.app.services.upgrade_drill import (
    BASE_REVISION,
    EXPECTED_BUSINESS_TABLES,
    DrillError,
    is_protected,
    run_drill,
)
from backend.scripts import upgrade_drill as cli


def make_alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = SimpleNamespace(x=[f"db_url={db_url}"])
    return config


def _base_database(path: Path) -> str:
    """造一个"初版 schema 那个年代"的库：只升到 de8148733a9b。"""

    url = f"sqlite:///{path.as_posix()}"
    command.upgrade(make_alembic_config(url), BASE_REVISION)
    return url


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _business_tables(path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def _step_names(report) -> list[str]:
    return [step.name for step in report.steps]


def _step_detail(report, name: str) -> str:
    for step in report.steps:
        if step.name == name:
            return step.detail
    raise AssertionError(f"没有这一步：{name}（实际有 {_step_names(report)}）")


def test_drill_from_the_initial_revision_passes(tmp_path: Path):
    """空库 → 塞旧数据 → 升到 head：数据一个字不差，回填真的发生了。"""

    target = tmp_path / "drill.db"
    report = run_drill(target, seed=True, roundtrip=False)

    assert report.ok, report.summary() + "\n" + "\n".join(
        f"{step.name}: {step.detail}" for step in report.steps
    )
    assert "backfill" in _step_names(report)
    assert "origin_type 默认 manual" in _step_detail(report, "backfill")
    assert len(_business_tables(target)) == EXPECTED_BUSINESS_TABLES

    with sqlite3.connect(target) as connection:
        origin, status = connection.execute(
            "SELECT origin_type, status FROM topics WHERE id = 1"
        ).fetchone()
        summary = connection.execute(
            "SELECT summary FROM research_reports WHERE id = 1"
        ).fetchone()[0]
    assert origin == "manual"
    assert status == "content_produced"
    assert summary == "这句话必须在升级之后还能一个字不差地读出来。"


def test_drill_roundtrip_rewinds_and_reapplies_the_backfill(tmp_path: Path):
    """降回初版再升上来，回填要重做一遍（这是"迁移可逆"的真检验）。"""

    target = tmp_path / "roundtrip.db"
    report = run_drill(target, seed=True, roundtrip=True)

    assert report.ok, report.summary()
    assert "roundtrip" in _step_names(report)
    assert "回填重做成功" in _step_detail(report, "roundtrip")


def test_drill_refuses_to_overwrite_an_existing_target(tmp_path: Path):
    target = tmp_path / "occupied.db"
    target.write_bytes(b"not empty")

    with pytest.raises(DrillError) as caught:
        run_drill(target, seed=False, roundtrip=False)
    assert caught.value.reason == "target_exists"


def test_drill_copies_the_source_database_without_touching_it(tmp_path: Path):
    """源库只读：演练前后源库 sha256 必须一样，副本才被改动。"""

    source = tmp_path / "source.db"
    _base_database(source)
    before = _sha256(source)
    source_size = source.stat().st_size

    target = tmp_path / "copy.db"
    report = run_drill(target, source_db=source, seed=False, roundtrip=False)

    assert report.ok, report.summary()
    assert "copy_source" in _step_names(report)
    assert report.source_db == source.as_posix()
    assert _sha256(source) == before
    assert source.stat().st_size == source_size
    # 副本被升到了 head，源库还停在起点版本
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] != (
            BASE_REVISION
        )
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            BASE_REVISION
        )


def test_seed_rows_satisfy_the_real_check_constraints(tmp_path: Path):
    """演练数据自己不能违反 CHECK 约束——那次 `ck_topics_knowledge_domain` 失败就是这么来的。"""

    target = tmp_path / "seed.db"
    command.upgrade(make_alembic_config(f"sqlite:///{target.as_posix()}"), BASE_REVISION)
    report = drill.DrillReport(database=target.as_posix())
    seeded = drill._seed(target, report)

    assert set(seeded) == {
        "topic_columns",
        "topics",
        "sources",
        "research_reports",
        "evidence",
        "scripts",
        "agent_runs",
    }
    assert all(count == 1 for count in seeded.values())


def test_backfill_check_catches_an_unmapped_status(tmp_path: Path):
    """反向用例：把 status 改回未映射的值，回填检查必须报出来（这才是它存在的意义）。"""

    target = tmp_path / "broken.db"
    report = run_drill(target, seed=True, roundtrip=False)
    assert report.ok

    with sqlite3.connect(target) as connection:
        # origin_type 现在是 NOT NULL（B7 收紧的），所以这里换一个能弄坏的地方：
        # 把 B1 从 content_status 映射出来的主状态改回去
        connection.execute("UPDATE topics SET status = 'candidate' WHERE id = 1")
        connection.commit()

    ok, detail = drill._check_backfill(target)
    assert ok is False
    assert "content_produced" in detail


def test_protected_development_database_is_recognised():
    assert is_protected(Path("data") / "finance_media.db") is True
    assert is_protected(Path("C:/Users/someone/finance-media-ai/data/finance_media.db")) is True
    assert is_protected(Path("data") / "finance_media.work.db") is False


def test_cli_returns_two_when_the_source_is_missing(tmp_path: Path):
    missing = tmp_path / "nope.db"

    assert cli.main(["--source-db", str(missing)]) == 2


def test_cli_runs_a_real_drill_and_keeps_the_copy_when_asked(tmp_path: Path):
    source = tmp_path / "source.db"
    _base_database(source)
    work = tmp_path / "work"

    code = cli.main(
        [
            "--source-db",
            str(source),
            "--work-dir",
            str(work),
            "--no-seed",
            "--no-roundtrip",
        ]
    )

    assert code == 0
    kept = work / cli.DRILL_NAME
    assert kept.is_file()
    assert len(_business_tables(kept)) == EXPECTED_BUSINESS_TABLES
