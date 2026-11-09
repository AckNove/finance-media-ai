"""数据库引擎与会话管理。

第一阶段：SQLite 本地文件库。
兼容性约定：只使用 SQLAlchemy 通用构造，不使用 SQLite 专有 SQL，
因此后续切换到 PostgreSQL 只需替换 DATABASE_URL 并安装 psycopg。

注意：SQLite 默认不强制外键约束，这里在每个连接上执行 PRAGMA foreign_keys=ON，
使外键在开发/测试环境同样生效（与 PostgreSQL 行为一致）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _sqlite_enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
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
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_enable_foreign_keys)
    logger.debug("database engine created: %s", url)
    return engine


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
