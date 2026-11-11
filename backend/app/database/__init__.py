"""数据库基础设施包。"""

from backend.app.database.base import Base, TimestampMixin
from backend.app.database.session import (
    build_engine,
    dispose_engine,
    get_db,
    get_engine,
    get_session_factory,
    session_scope,
)
from backend.app.database.wal import (
    DEFAULT_JOURNAL_MODE,
    WAL_JOURNAL_MODE,
    WalInitializationError,
    WalResult,
    enable_wal,
    is_protected_database_url,
    resolve_sqlite_target,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "DEFAULT_JOURNAL_MODE",
    "WAL_JOURNAL_MODE",
    "WalInitializationError",
    "WalResult",
    "build_engine",
    "dispose_engine",
    "enable_wal",
    "get_db",
    "get_engine",
    "get_session_factory",
    "is_protected_database_url",
    "resolve_sqlite_target",
    "session_scope",
]
