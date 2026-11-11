"""WAL 初始化整改的回归测试（M2.0 专项整改）。

这些用例要证明的只有一件事：**改库文件的动作不会自己发生**。

- 普通连接不切 WAL；
- 应用启动不切 WAL；
- 查状态不切 WAL；
- 只有显式调用 :func:`backend.app.database.wal.enable_wal` 才切，且必须指明目标；
- 真实开发库 ``data/finance_media.db`` 保持 ``journal_mode=delete``，不产生 ``-wal`` / ``-shm``。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.database import session as session_module
from backend.app.database.session import build_engine, dispose_engine, read_sqlite_pragmas
from backend.app.database.wal import (
    DEFAULT_JOURNAL_MODE,
    WAL_JOURNAL_MODE,
    WalInitializationError,
    enable_wal,
    is_protected_database_url,
    resolve_sqlite_target,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_DB = PROJECT_ROOT / "data" / "finance_media.db"


def _file_journal_mode(path: Path) -> str:
    """用标准库直连读取库文件的 journal_mode（只读查询，不会切换模式）。"""
    connection = sqlite3.connect(str(path))
    try:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        connection.close()


def _sidecars(path: Path) -> list[str]:
    return [suffix for suffix in ("-wal", "-shm") if Path(f"{path}{suffix}").exists()]


def _make_database(path: Path) -> Path:
    """造一个最小 SQLite 库（默认 journal_mode=delete）。"""
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, note TEXT)")
        connection.execute("INSERT INTO demo (note) VALUES ('hello')")
        connection.commit()
    finally:
        connection.close()
    return path


def _url_for(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


# --------------------------------------------------------------------------
# 一、结构保证：连接事件里不许出现 journal_mode
# --------------------------------------------------------------------------


def test_connect_pragmas_never_include_journal_mode():
    statements = session_module._SQLITE_PRAGMA_STATEMENTS
    joined = " ".join(statements).lower()
    assert "journal_mode" not in joined, (
        "连接事件里的 PRAGMA 只允许连接级参数；journal_mode 会持久化到库文件，"
        "必须走 backend.app.database.wal.enable_wal"
    )
    # 三条连接级参数必须都在
    assert "foreign_keys=on" in joined
    assert "busy_timeout" in joined
    assert "synchronous=normal" in joined


# --------------------------------------------------------------------------
# 二、普通连接 / 启动 / 状态查询都不改库文件
# --------------------------------------------------------------------------


def test_plain_connection_keeps_default_journal_mode(test_database: Path):
    engine = build_engine()
    try:
        with engine.connect() as connection:
            pragmas = read_sqlite_pragmas(connection)
    finally:
        engine.dispose()

    # 连接级参数生效
    assert pragmas["foreign_keys"] == "1"
    assert pragmas["busy_timeout"] == "5000"
    # 库文件本身的模式没有被改
    assert pragmas["journal_mode"].lower() == DEFAULT_JOURNAL_MODE
    assert _file_journal_mode(test_database) == DEFAULT_JOURNAL_MODE
    assert _sidecars(test_database) == []


def test_application_startup_does_not_switch_journal_mode(client, test_database: Path):
    # 真实 lifespan：启动自检会连数据库、读表数
    response = client.get("/health")
    assert response.status_code == 200
    dispose_engine()

    assert _file_journal_mode(test_database) == DEFAULT_JOURNAL_MODE
    assert _sidecars(test_database) == []


def test_status_endpoint_does_not_switch_journal_mode(client, test_database: Path):
    body = client.get("/api/system/status").json()
    assert body["database"]["journal_mode"].lower() == DEFAULT_JOURNAL_MODE
    dispose_engine()

    assert _file_journal_mode(test_database) == DEFAULT_JOURNAL_MODE
    assert _sidecars(test_database) == []


# --------------------------------------------------------------------------
# 三、显式初始化：能切、幂等、dry-run 不动手
# --------------------------------------------------------------------------


def test_enable_wal_switches_explicit_target(tmp_path: Path):
    target = _make_database(tmp_path / "explicit.db")
    assert _file_journal_mode(target) == DEFAULT_JOURNAL_MODE

    result = enable_wal(_url_for(target))

    assert result.before == DEFAULT_JOURNAL_MODE
    assert result.after == WAL_JOURNAL_MODE.lower()
    assert result.changed is True
    assert result.already_enabled is False
    assert result.dry_run is False
    assert result.protected is False
    assert Path(result.target_path or "") == target.resolve()
    assert _file_journal_mode(target) == WAL_JOURNAL_MODE.lower()
    # WAL 模式下会生成 -wal 附属文件；关闭连接后可能被 checkpoint 掉，这里只断言模式
    assert "wal" in result.describe().lower()


def test_enable_wal_is_idempotent(tmp_path: Path):
    target = _make_database(tmp_path / "repeat.db")
    first = enable_wal(_url_for(target))
    second = enable_wal(_url_for(target))

    assert first.changed is True
    assert second.changed is False
    assert second.already_enabled is True
    assert second.before == second.after == WAL_JOURNAL_MODE.lower()


def test_enable_wal_dry_run_does_not_touch_database(tmp_path: Path):
    target = _make_database(tmp_path / "dry.db")

    result = enable_wal(_url_for(target), dry_run=True)

    assert result.dry_run is True
    assert result.before is None and result.after is None
    assert result.changed is False
    assert _file_journal_mode(target) == DEFAULT_JOURNAL_MODE
    assert _sidecars(target) == []


# --------------------------------------------------------------------------
# 四、安全阈值：拒绝不明确的目标
# --------------------------------------------------------------------------


def test_enable_wal_refuses_development_database():
    url = _url_for(DEVELOPMENT_DB)
    assert is_protected_database_url(url) is True

    with pytest.raises(WalInitializationError) as excinfo:
        enable_wal(url)

    assert excinfo.value.reason == "protected_database"


def test_enable_wal_allows_development_database_only_when_explicit():
    """放行标记本身也必须可用于 dry-run（真正的切换在 M2.0 阶段禁止）。"""
    result = enable_wal(_url_for(DEVELOPMENT_DB), allow_development_db=True, dry_run=True)
    assert result.protected is True
    assert result.dry_run is True
    assert result.before is None and result.after is None


def test_enable_wal_rejects_missing_database(tmp_path: Path):
    missing = tmp_path / "nope.db"
    with pytest.raises(WalInitializationError) as excinfo:
        enable_wal(_url_for(missing))
    assert excinfo.value.reason == "missing_database"
    assert not missing.exists(), "初始化不得顺手新建数据库文件"


def test_enable_wal_rejects_memory_and_non_sqlite():
    with pytest.raises(WalInitializationError) as memory_error:
        enable_wal("sqlite://")
    assert memory_error.value.reason == "in_memory"

    with pytest.raises(WalInitializationError) as dialect_error:
        enable_wal("postgresql://user@localhost/db")
    assert dialect_error.value.reason == "not_sqlite"


def test_resolve_sqlite_target_returns_absolute_path(tmp_path: Path):
    target = tmp_path / "abs.db"
    assert resolve_sqlite_target(_url_for(target)) == target.resolve()


# --------------------------------------------------------------------------
# 五、真实开发库保持原状
# --------------------------------------------------------------------------


@pytest.mark.skipif(not DEVELOPMENT_DB.exists(), reason="本机没有真实开发库")
def test_development_database_remains_untouched():
    assert _file_journal_mode(DEVELOPMENT_DB) == DEFAULT_JOURNAL_MODE
    assert _sidecars(DEVELOPMENT_DB) == []

    connection = sqlite3.connect(str(DEVELOPMENT_DB))
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()

    assert len(tables) == 19
    assert revision == "de8148733a9b"
    # B1/B2/B3 的新表一张都不该出现在真实开发库里
    assert "research_tasks" not in tables
    assert "model_calls" not in tables
