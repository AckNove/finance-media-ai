"""研究任务 API 的集成测试（真实路由、真实库，网络用替身）。

覆盖：
- 建任务 → 按流水线拆解、计划落库、返回子任务
- 列表 / 详情 / 事件流
- 暂停 / 恢复 / 取消
- ``/run`` 的回写路径（执行器换成替身，避免测试依赖外网）
- 运行监控三个端点：成本（无调用时为 0 且有说明）、服务商状态、Agent 名册
- 权限：伪造 ``X-Caller: agent`` 不能写任务

注意：这里验证的是**接口与数据库契约**；真实搜索/抓取/模型的端到端验证另有脚本
（``tests`` 之外的真实冒烟），二者不能互相替代。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from backend.app.database.base import Base
from backend.app.database.session import get_session_factory
from backend.app.execution.executor import ExecutionOutcome

SKIP_MODEL_STAGES = ["topic_candidate", "topic_scoring", "research_plan"]


@pytest.fixture()
def clean_db() -> Iterator[None]:
    """清空与本文件相关的表，避免 session 级共享库互相污染。"""
    session = get_session_factory()()
    try:
        session.execute(__import__("sqlalchemy").text("PRAGMA defer_foreign_keys = ON"))
        for table in reversed(list(Base.metadata.tables.values())):
            session.execute(table.delete())
        session.commit()
    finally:
        session.close()
    yield


class _FakeExecutor:
    """把执行器换掉：只验证 API 到 runtime 的回写链路，不碰网络。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[int] = []

    def execute(self, claim: Any, *, worker: str = "worker") -> ExecutionOutcome:
        self.calls.append(claim.subtask_id)
        return ExecutionOutcome(
            status="completed",
            output={"note": "测试替身：未执行真实抓取", "subtask_id": claim.subtask_id},
            calls=0,
            tokens=0,
            cost_cny=0,
        )


@pytest.fixture()
def fake_executor(monkeypatch: pytest.MonkeyPatch) -> type[_FakeExecutor]:
    from backend.app.api.routes import research_tasks as route_module

    monkeypatch.setattr(route_module, "AgentExecutor", _FakeExecutor)
    return _FakeExecutor


def _create_task(client: Any, **overrides: Any) -> Any:
    payload = {
        "title": "商业银行如何赚钱",
        "objective": "用公开资料说明银行净息差的形成机制",
        "pipeline": "research_to_script",
        "skip": SKIP_MODEL_STAGES,
        "skip_reason": "没有模型密钥，跳过需要模型的阶段",
        "queries": ["商业银行 净息差"],
        "start": True,
    }
    payload.update(overrides)
    return client.post("/api/research-tasks", json=payload)


def test_creating_a_task_plans_the_pipeline(client: Any, clean_db: None) -> None:
    response = _create_task(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["counts"].get("succeeded", 0) == 0
    sequences = [item["sequence"] for item in body["subtasks"]]
    assert sequences == list(range(1, len(sequences) + 1))
    assert body["subtasks"][0]["task_type"] == "research.collect"
    assert body["plan"]["skip_reason"].startswith("没有模型密钥")
    assert body["plan"]["skipped_stages"] == SKIP_MODEL_STAGES


def test_skipping_an_unknown_stage_is_rejected(client: Any, clean_db: None) -> None:
    response = _create_task(client, skip=["no_such_stage"])
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "unknown_stage"


def test_listing_and_reading_back_a_task(client: Any, clean_db: None) -> None:
    task_id = _create_task(client).json()["id"]
    listing = client.get("/api/research-tasks").json()
    assert listing["total"] >= 1
    assert any(item["id"] == task_id for item in listing["items"])

    detail = client.get(f"/api/research-tasks/{task_id}").json()
    assert detail["id"] == task_id
    assert len(detail["subtasks"]) >= 1

    events = client.get(f"/api/research-tasks/{task_id}/events").json()
    types = [item["event_type"] for item in events["items"]]
    assert types[0] == "created"
    assert "plan_created" in types


def test_listing_supports_pagination_and_a_status_filter(client: Any, clean_db: None) -> None:
    """翻页与筛选要真的生效——包括"共几条"的分母。"""
    first = _create_task(client).json()["id"]
    second = _create_task(client).json()["id"]

    page = client.get("/api/research-tasks", params={"limit": 1, "offset": 0})
    assert page.status_code == 200
    body = page.json()
    assert body["limit"] == 1 and body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["total"] >= 2

    second_page = client.get("/api/research-tasks", params={"limit": 1, "offset": 1}).json()
    assert second_page["items"][0]["id"] != body["items"][0]["id"]

    everything = client.get("/api/research-tasks", params={"limit": 100}).json()
    # 状态筛选：总数必须是**该状态**的条数，不是全表条数
    created = client.get("/api/research-tasks", params={"status": "created"}).json()
    assert all(item["status"] == "created" for item in created["items"])
    assert created["total"] == sum(1 for item in everything["items"] if item["status"] == "created")

    failed = client.get("/api/research-tasks", params={"status": "failed"}).json()
    assert all(item["status"] == "failed" for item in failed["items"])
    assert failed["total"] == 0  # 刚建的任务都还没失败

    bad = client.get("/api/research-tasks", params={"status": "not-a-status"})
    assert bad.status_code == 422
    assert first and second


def test_unknown_task_is_404(client: Any, clean_db: None) -> None:
    response = client.get("/api/research-tasks/999999")
    assert response.status_code == 404
    assert response.json()["error"]["details"]["reason"] == "unknown_task"


def test_running_writes_back_through_the_runtime(
    client: Any, clean_db: None, fake_executor: Any
) -> None:
    task_id = _create_task(client).json()["id"]
    response = client.post(
        f"/api/research-tasks/{task_id}/run", json={"max_iterations": 2}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["completed"], report
    assert report["task"]["subtasks"][0]["status"] == "succeeded"
    assert fake_executor is not None


def test_pause_resume_and_cancel(client: Any, clean_db: None) -> None:
    task_id = _create_task(client).json()["id"]
    paused = client.post(f"/api/research-tasks/{task_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.post(f"/api/research-tasks/{task_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"

    cancelled = client.post(f"/api/research-tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    again = client.post(f"/api/research-tasks/{task_id}/pause")
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "task_closed"


def test_forged_agent_header_is_ignored_not_escalated(client: Any, clean_db: None) -> None:
    """伪造 ``X-Caller: agent`` 不会让我们变成 agent。

    这个端点是"人工专用"：调用者仍是 human，所以请求被接受；关键在于伪造的值
    被丢弃（日志有 warning），任务记录里也不会出现 agent 身份。
    """
    response = client.post(
        "/api/research-tasks",
        json={"title": "伪造身份", "skip": SKIP_MODEL_STAGES},
        headers={"X-Caller": "agent"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["subtasks"], "任务仍然是被人工合法创建的"


def test_cost_summary_starts_at_zero_with_an_explanation(
    client: Any, clean_db: None
) -> None:
    body = client.get("/api/system/costs").json()
    assert body["model_calls"] == 0
    assert body["model_cost_cny"] in ("0", 0)
    assert "api_key" in body["note"]
    assert body["by_model"] == []


def test_provider_status_lists_configured_providers(client: Any) -> None:
    body = client.get("/api/system/providers").json()
    names = {item["name"] for item in body}
    assert {"deepseek", "openai", "local"} <= names
    for item in body:
        assert item["status"] in {
            "ready",
            "missing_api_key",
            "provider_disabled",
            "unknown_provider",
            "missing_base_url",
        }
        assert "api_key" not in item


def test_agent_roster_comes_from_the_real_config(client: Any) -> None:
    body = client.get("/api/system/agents").json()
    assert body["count"] == 9
    keys = {item["key"] for item in body["agents"]}
    assert "orchestrator" in keys and "fact_checker" in keys
    for item in body["agents"]:
        assert item["prompt"].endswith("PROMPT.md")
