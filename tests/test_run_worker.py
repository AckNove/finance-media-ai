"""常驻 worker 的入口脚本：能停、不空转、不在只读开发库上跑。

这里测的是 ``backend/scripts/run_worker.py`` 这层「外壳」的行为——
轮询、空闲退出、迭代上限、库保护。真正的推进逻辑在 test_task_runtime /
test_execution_pipeline 里测过，不重复。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine

import backend.app.models  # noqa: F401  注册全部模型
from backend.app.database.base import Base
from backend.app.core.config import clear_settings_cache
from backend.scripts.run_worker import main


@pytest.fixture()
def database(tmp_path, monkeypatch):
    """一个空的临时库，并把 DATABASE_URL 指向它。"""
    path = tmp_path / "worker.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    clear_settings_cache()
    yield path
    clear_settings_cache()
    engine.dispose()


def _events(capsys) -> list[dict]:
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(line) for line in out if line.startswith("{")]


def test_idle_worker_stops_instead_of_spinning_forever(database, monkeypatch, capsys) -> None:
    # 没有任务时不该一直空转：把空闲判定压到 0 分钟，第一次空闲就退出
    monkeypatch.setattr("backend.scripts.run_worker.time.sleep", lambda _: None)
    code = main(["--poll-seconds", "0", "--max-idle-minutes", "0"])
    assert code == 0
    events = _events(capsys)
    assert events[0]["event"] == "start"
    assert events[-1]["event"] == "stop"
    assert events[-1]["reason"] == "idle"


def test_max_iterations_caps_the_loop(database, monkeypatch, capsys) -> None:
    monkeypatch.setattr("backend.scripts.run_worker.time.sleep", lambda _: None)
    code = main(["--poll-seconds", "0", "--max-iterations", "2", "--forever"])
    assert code == 0
    events = _events(capsys)
    assert events[-1] == {
        "event": "stop",
        "reason": "max_iterations",
        "iterations": 2,
        "totals": {"completed": 0, "blocked": 0, "failed": 0, "retried": 0, "reaped": 0},
    }


def test_worker_refuses_the_readonly_development_database(monkeypatch, capsys) -> None:
    """data/finance_media.db 是只读开发库，worker 必须直接拒绝而不是偷偷写它。"""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///C:/repo/data/finance_media.db")
    clear_settings_cache()
    try:
        code = main(["--max-iterations", "1"])
    finally:
        clear_settings_cache()
    assert code == 2
    payload = _events(capsys)[0]
    assert payload["error"] == "protected_database"
    assert "finance_media.db" in payload["message"]
