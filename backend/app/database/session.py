"""数据库引擎与会话管理。

第一阶段：SQLite 本地文件库。
兼容性约定：只使用 SQLAlchemy 通用构造，不使用 SQLite 专有 SQL，
因此后续切换到 PostgreSQL 只需替换 DATABASE_URL 并安装 psycopg。

SQLite 连接参数（M2.0 T03，见 docs/research_hub/system_architecture.md §3.4.1）：

- ``foreign_keys=ON``：SQLite 默认不强制外键，这里逐连接开启，与 PostgreSQL 行为一致；
- ``busy_timeout=5000``：写锁冲突时等待 5 秒而不是立刻抛 database is locked；
- ``synchronous=NORMAL``：WAL 下的常规取舍（崩溃时不丢已提交事务，可能丢最后一次 checkpoint）。

**这三个参数都是"连接级"的，不会改动库文件本身。**

``journal_mode=WAL`` 刻意**不在这里**：它会持久化到数据库文件头，一旦在某个库上执行过，
该库以后每次打开都是 WAL（并生成 ``-wal`` / ``-shm`` 附属文件）。把这种"改文件"的动作放进
``connect`` 事件，等于"随便连一下就把你的库改了"。所以 WAL 改由
:mod:`backend.app.database.wal` 的 :func:`~backend.app.database.wal.enable_wal`
**显式调用**，可 dry-run、可审计、默认拒绝对真实开发库执行。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

# M2.0 T03：SQLite 连接级运行参数（唯一一处定义，测试与状态接口都读这里）
SQLITE_BUSY_TIMEOUT_MS: int = 5000
SQLITE_SYNCHRONOUS: str = "NORMAL"

#: 逐连接执行的 PRAGMA：**只含连接级参数，不含 journal_mode**
_SQLITE_PRAGMA_STATEMENTS: tuple[str, ...] = (
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}",
    f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}",
)


def _sqlite_apply_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """在每个新连接上应用 SQLite 连接级参数（不改变库文件的 journal_mode）。"""
    cursor = dbapi_connection.cursor()
    try:
        for statement in _SQLITE_PRAGMA_STATEMENTS:
            cursor.execute(statement)
    finally:
        cursor.close()


def build_engine(settings: Settings | None = None) -> Engine:
    """按配置创建引擎（不做全局缓存）。"""
    settings = settings or get_settings()
    url = settings.resolved_database_url()
    kwargs: dict[str, Any] = {
        "echo": settings.database_echo,
        "future": True,
        "pool_pre_ping": True,
    }
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {
            "check_same_thread": False,
            # 与 PRAGMA busy_timeout 同值：驱动层与 SQLite 层都等待，避免写锁争抢直接报错
            "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
        }
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_apply_pragmas)
    logger.debug("database engine created: %s", url)
    return engine


def read_sqlite_pragmas(connection: Any) -> dict[str, str]:
    """读取当前连接的 SQLite 运行参数（供 /api/system/status 与测试断言）。

    ``journal_mode`` 是按库文件读出来的**事实**，本函数只读不写：
    它反映"这个库现在是不是 WAL"，绝不负责把库切成 WAL。
    """
    values: dict[str, str] = {}
    for name in ("journal_mode", "busy_timeout", "synchronous", "foreign_keys"):
        values[name] = str(connection.execute(text(f"PRAGMA {name}")).scalar())
    return values


def get_engine() -> Engine:
    """进程内单例引擎。"""
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话。事务边界由服务层负责提交/回滚。"""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/CLI 使用：自动提交或回滚。"""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """释放连接池（测试与进程退出时使用）。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
