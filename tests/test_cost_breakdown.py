"""成本页的「钱花在哪一步 / 哪条研究上」。

两个口径值得钉住：
1. ``purpose`` 为空时不许猜成某个阶段，必须如实写「未标用途」；
2. 没有绑研究任务的调用（脚本探针、单次冒烟）要单独成一条——否则各任务之和
   小于总额，读者会以为漏账。

**测试库是 session 级共享的**（`tests/conftest.py` 的 `test_database`），所以这里
一律用「前后差值」断言，不假设库里只有本用例造的数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.models.execution import ResearchTask
from backend.app.models.metering import ModelCall


def _task(session: Session, *, task_id: int, title: str) -> ResearchTask:
    stamp = datetime(2026, 5, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None)
    task = ResearchTask(
        id=task_id,
        title=title,
        objective=title,
        status="completed",
        plan={},
        planner="pipeline:research_only",
        budget_max_calls=10,
        calls_spent=0,
        tokens_spent=0,
        cost_spent=Decimal("0"),
        version=1,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(task)
    session.commit()
    return task


def _call(
    session: Session,
    *,
    purpose: str,
    cost: str,
    tokens: int = 100,
    task_id: int | None = None,
) -> ModelCall:
    stamp = datetime(2026, 5, 20, 10, 0, tzinfo=UTC).replace(tzinfo=None)
    row = ModelCall(
        research_task_id=task_id,
        provider="deepseek",
        model="deepseek-flash",
        purpose=purpose,
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


def _costs(client: TestClient) -> dict[str, object]:
    response = client.get("/api/system/costs")
    assert response.status_code == 200, response.text
    return response.json()


def _index(items: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["key"]): item for item in items}


def test_costs_break_down_by_purpose(client: TestClient, db_session: Session) -> None:
    before = _index(_costs(client)["by_purpose"])
    _call(db_session, purpose="task-analyze", cost="0.040000", tokens=2000)
    _call(db_session, purpose="task-analyze", cost="0.010000", tokens=500)
    _call(db_session, purpose="task-synthesize", cost="0.020000", tokens=1000)

    after = _index(_costs(client)["by_purpose"])
    analyze = after["task-analyze"]
    base_calls = int(before.get("task-analyze", {}).get("calls", 0))
    base_cost = Decimal(str(before.get("task-analyze", {}).get("cost_cny", "0")))
    assert int(analyze["calls"]) - base_calls == 2
    assert Decimal(str(analyze["cost_cny"])) - base_cost == Decimal("0.050000")
    assert after["task-synthesize"]["calls"] == 1
    # 花得多的排前面（页面从上往下看就是"哪一步最贵"）
    costs = [Decimal(str(item["cost_cny"])) for item in _costs(client)["by_purpose"]]
    assert costs == sorted(costs, reverse=True)


def test_calls_without_a_purpose_are_not_guessed(client: TestClient, db_session: Session) -> None:
    _call(db_session, purpose="", cost="0.001000")
    keys = [str(item["key"]) for item in _costs(client)["by_purpose"]]
    assert "未标用途" in keys
    # 空串绝不能被当成某个真实阶段
    assert "" not in keys


def test_costs_break_down_by_task_and_list_unbound_calls(
    client: TestClient, db_session: Session
) -> None:
    before = _index(_costs(client)["by_task"])
    unbound_before = next(
        (int(item["calls"]) for key, item in before.items() if key.startswith("未绑定任务")), 0
    )

    _task(db_session, task_id=901, title="储能行业那条研究")
    _call(db_session, purpose="research.analyze", cost="0.040000", task_id=901)
    _call(db_session, purpose="research.synthesize", cost="0.020000", task_id=901)
    _call(db_session, purpose="content.script", cost="0.010000")
    _call(db_session, purpose="", cost="0.001000")

    items = _costs(client)["by_task"]
    by_key = _index(items)
    task_bucket = by_key["#901 储能行业那条研究"]
    assert task_bucket["calls"] == 2
    assert Decimal(str(task_bucket["cost_cny"])) == Decimal("0.060000")
    unbound = next(item for item in items if str(item["key"]).startswith("未绑定任务"))
    assert int(unbound["calls"]) - unbound_before == 2
    # 有任务的排前面（按花费降序）
    assert str(items[0]["key"]).startswith("#901") or Decimal(
        str(items[0]["cost_cny"])
    ) >= Decimal("0.060000")


def test_task_buckets_add_up_to_the_model_total(client: TestClient, db_session: Session) -> None:
    _task(db_session, task_id=902, title="光伏产业链")
    _call(db_session, purpose="research.analyze", cost="0.030000", task_id=902)
    _call(db_session, purpose="research.analyze", cost="0.005000")

    data = _costs(client)
    total = Decimal(str(data["model_cost_cny"]))
    summed = sum(Decimal(str(item["cost_cny"])) for item in data["by_task"])
    assert summed == total


def test_task_buckets_are_capped(client: TestClient, db_session: Session) -> None:
    for index in range(12):
        _task(db_session, task_id=950 + index, title=f"第{index}条")
        _call(
            db_session,
            purpose="research.analyze",
            cost=f"0.0{10 + index}0000",
            task_id=950 + index,
        )

    items = _costs(client)["by_task"]
    assert len(items) == 10
    keys = [str(item["key"]) for item in items]
    # 截断的是"花得最少"的那些：最贵的那条必须在
    assert any("#961" in key for key in keys)


@pytest.mark.parametrize("key", ["by_purpose", "by_task", "by_model", "by_day", "by_tool"])
def test_every_grouping_uses_the_same_bucket_shape(
    client: TestClient, db_session: Session, key: str
) -> None:
    _call(db_session, purpose="shape-check", cost="0.010000")
    payload = _costs(client)
    assert isinstance(payload[key], list)
    for item in payload[key]:
        assert set(item) == {"key", "calls", "tokens", "cost_cny"}
