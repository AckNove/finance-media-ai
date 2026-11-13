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

# **测试绝不调用真实模型**：开发者本机 .env 里可能有真密钥，这里既禁止 .env 注入，
# 又把可能已导出的 key 抹掉。要用密钥的用例自己给 ModelGateway 传 environ={...}。
os.environ["FM_SKIP_DOTENV"] = "1"
MODEL_KEY_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
)
for _key in MODEL_KEY_ENV_VARS:
    os.environ.pop(_key, None)


@pytest.fixture(autouse=True)
def no_real_model_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例都保证进程里没有真实密钥（防止一次手滑就产生费用）。"""
    for name in MODEL_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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


def confirm_findings(session: Any, report: Any, *, count: int = 1) -> list[int]:
    """把一份报告的前 ``count`` 条结论标成"人工已确认"，返回核查行 id。

    成稿闸门要求结论必须有 ``human_review_status='confirmed'`` 的核查行才能进稿；
    凡是"从报告生脚本/视频"的用例都得先过这一步，否则拿到的是 409。
    """
    from backend.app.models.research import FindingCheck

    findings = list(getattr(report, "findings", None) or [])
    created: list[int] = []
    for index in range(min(count, len(findings))):
        item = findings[index]
        text = ""
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("claim") or item.get("statement") or "")
        row = FindingCheck(
            research_report_id=int(report.id),
            finding_index=index,
            claim=text,
            verdict="supported",
            reason="原文逐字可查，人工已核对",
            evidence_ids=[],
            source_ids=["S01"],
            human_review_status="confirmed",
            human_note="用例里模拟人工确认",
        )
        session.add(row)
        session.flush()
        created.append(int(row.id))
    session.commit()
    return created
