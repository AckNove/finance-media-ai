"""测试夹具：独立临时 SQLite 库 + TestClient。

设计要点：

1. **不碰开发库**：`data/finance_media.db` 不被测试修改，每个测试会话用一个临时文件库。
2. **建表方式**：本夹具用 `Base.metadata.create_all` 快速建表；迁移脚本的正确性
   由 `tests/test_migrations.py` 单独用真实 Alembic 命令验证（两条路径都验证）。
3. **环境变量顺序**：`DATABASE_URL` 必须在第一次读取配置前设置，因为
   `get_settings()` 有 lru_cache，这里调用 `clear_settings_cache()` 兜底。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 在导入 backend.app.* 之前固定测试环境
os.environ["APP_ENV"] = "test"
os.environ.pop("DATABASE_ECHO", None)


@pytest.fixture(scope="session", autouse=True)
def test_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """整个测试会话共享一个临时数据库文件。"""
    from backend.app.core.config import clear_settings_cache
    from backend.app.database.base import Base
    from backend.app.database.session import dispose_engine, get_engine

    import backend.app.models  # noqa: F401  确保所有模型已注册到 Base.metadata

    db_path = tmp_path_factory.mktemp("finance_media_test_db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    clear_settings_cache()
    dispose_engine()

    engine = get_engine()
    Base.metadata.create_all(engine)
    yield db_path
    dispose_engine()


@pytest.fixture()
def client() -> Iterator[Any]:
    """FastAPI 测试客户端（真实 lifespan：日志、目录初始化、引擎释放）。"""
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session() -> Iterator[Any]:
    """直接操作数据库的会话（服务层测试用）。"""
    from backend.app.database.session import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
