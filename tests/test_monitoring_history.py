"""运行历史曲线：把「每天花了多少、跑了几条、失败几次」画出来。

只画真实记录；某天没有记录就画 0，不补示例数据。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.models.enums import ResearchTaskStatus
from backend.app.models.execution import ResearchTask
from backend.app.models.metering import ModelCall

NOW = datetime.now(UTC)


def _day(offset: int) -> str:
    return (NOW - timedelta(days=offset)).astimezone(UTC).strftime("%Y-%m-%d")


def _task(session: Session, *, offset: int, status: ResearchTaskStatus) -> ResearchTask:
    stamp = (NOW - timedelta(days=offset)).replace(tzinfo=None)
    task = ResearchTask(
        title=f"历史用例 {status.value} {offset}",
        objective="验证按天统计",
        status=status,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(task)
    session.commit()
    return task


def _call(session: Session, *, offset: int, tokens: int, cost: str) -> ModelCall:
    stamp = (NOW - timedelta(days=offset)).replace(tzinfo=None)
    row = ModelCall(
        provider="deepseek",
        model="deepseek-flash",
        purpose="history-test",
        prompt_tokens=tokens // 2,
        completion_tokens=tokens - tokens // 2,
        total_tokens=tokens,
        cost_estimate=Decimal(cost),
        status="ok",
        created_at=stamp,
    )
    session.add(row)
    session.commit()
    return row


def test_history_covers_every_day_in_the_window(client: TestClient) -> None:
    response = client.get("/api/system/history?days=7")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["days"] == 7
    assert len(data["items"]) == 7
    # 正序，且最后一天是今天（UTC）
    days = [item["day"] for item in data["items"]]
    assert days == sorted(days)
    assert days[-1] == _day(0)
    # 结构字段齐全，缺的天补 0 而不是缺项
    for item in data["items"]:
        for key in (
            "model_calls",
            "tokens",
            "cost_cny",
            "tasks_created",
            "tasks_completed",
            "tasks_failed",
            "subtasks_failed",
            "gates",
        ):
            assert key in item


def test_history_sums_real_records_per_day(client: TestClient, db_session: Session) -> None:
    _call(db_session, offset=2, tokens=1000, cost="0.010000")
    _call(db_session, offset=2, tokens=500, cost="0.005000")
    _call(db_session, offset=0, tokens=200, cost="0.002000")
    _task(db_session, offset=1, status=ResearchTaskStatus.COMPLETED)
    _task(db_session, offset=1, status=ResearchTaskStatus.FAILED)

    data = client.get("/api/system/history?days=4").json()
    by_day = {item["day"]: item for item in data["items"]}

    two = by_day[_day(2)]
    assert two["model_calls"] == 2
    assert two["tokens"] == 1500
    assert Decimal(two["cost_cny"]) == Decimal("0.015000")

    one = by_day[_day(1)]
    assert one["tasks_completed"] == 1
    assert one["tasks_failed"] == 1
    assert one["model_calls"] == 0

    assert Decimal(data["total_cost_cny"]) >= Decimal("0.017000")
    assert data["total_model_calls"] >= 3
    assert data["total_tasks_completed"] >= 1
    assert data["total_tasks_failed"] >= 1


def test_history_note_is_honest_about_failures(client: TestClient, db_session: Session) -> None:
    _task(db_session, offset=0, status=ResearchTaskStatus.FAILED)
    data = client.get("/api/system/history?days=3").json()
    assert "失败" in data["note"]
    assert "不代表内容质量" in data["note"] or "重跑" in data["note"]


def test_history_outside_the_window_is_ignored(client: TestClient, db_session: Session) -> None:
    _call(db_session, offset=40, tokens=100, cost="0.001000")
    data = client.get("/api/system/history?days=7").json()
    # 40 天前的账不进 7 天窗口
    assert all(item["model_calls"] == 0 or item["tokens"] != 100 for item in data["items"])


def test_history_rejects_absurd_windows(client: TestClient) -> None:
    assert client.get("/api/system/history?days=0").status_code == 422
    assert client.get("/api/system/history?days=400").status_code == 422
