"""`GET /api/system/daily` 的回归测试（全离线，不联网、不调模型）。

接口与 CLI 共用 `backend.app.services.daily_service` 的判断逻辑，这里只验三件事：
形状对不对、越界参数会不会被挡、**对着没迁移的老库会不会崩**（这是最容易踩的坑：
SQLite 会顺手建空库，然后每张表都报 `no such table`，如果不用 `_tolerant` 包起来就整条接口 500）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import get_settings
from backend.app.database.session import build_engine
from backend.app.services.daily_service import collect_daily

REQUIRED_KEYS = {
    "generated_at",
    "database_url",
    "protected_database",
    "window_days",
    "tasks",
    "subtasks",
    "checks",
    "jobs",
    "cost",
    "backup",
    "disk",
    "sections_unavailable",
    "attention",
    "ok",
}


def test_daily_endpoint_returns_the_same_shape_as_the_cli(client) -> None:
    response = client.get("/api/system/daily")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert REQUIRED_KEYS <= set(payload)
    assert payload["ok"] == (payload["attention"] == [])
    assert isinstance(payload["attention"], list)
    # 花费两格：今天 / 本窗口（默认 1 天时两者相同，但语义不同）
    assert set(payload["cost"]) == {"today", "window"}
    for cell in payload["cost"].values():
        assert set(cell) == {"calls", "tokens", "cost_cny"}
    assert set(payload["tasks"]) == {"by_status", "paused", "failed_in_window"}
    assert set(payload["checks"]) == {"by_status", "pending", "pending_reports"}


def test_daily_endpoint_accepts_a_window_and_rejects_out_of_range(client) -> None:
    assert client.get("/api/system/daily?days=30").status_code == 200
    for bad in (0, 91):
        response = client.get(f"/api/system/daily?days={bad}")
        assert response.status_code == 422, response.text


def test_daily_service_marks_unavailable_sections_on_an_old_database(tmp_path: Path) -> None:
    """空库（一张表都没有）不能让体检抛异常，只能如实说"这几块查不了"。"""

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = build_engine(get_settings().model_copy(update={"database_url": url}))
    try:
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with factory() as session:
            report = collect_daily(
                session,
                jobs_root=tmp_path / "jobs",
                backups=tmp_path / "backups",
                database_url=url,
            )
    finally:
        engine.dispose()

    assert REQUIRED_KEYS <= set(report)
    assert report["sections_unavailable"], "空库必须明确标出哪几块查不了"
    # 真实形状：键是给人看的中文标签，值是数据库给出的原因
    assert any("no such table" in str(reason) for reason in report["sections_unavailable"].values())
    assert any("查不了" in item for item in report["attention"])
    # 查不了的库不许被说成"今天干净"
    assert report["ok"] is False


@pytest.mark.parametrize("days", [1, 7, 30])
def test_daily_window_is_carried_into_the_report(client, days: int) -> None:
    payload = client.get(f"/api/system/daily?days={days}").json()
    assert payload["window_days"] == days
