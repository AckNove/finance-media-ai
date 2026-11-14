"""流水线目录与"只研究不写脚本"这条路。

老问题：产线在界面上是写死的 `research_to_script`，想只要一份研究报告的人没有入口——
真跑过才知道，那样会一路走到 `content.script`，在"结论还没确认"上停下，白占一道人工闸门。
这里钉住三件事：目录能列出来、`research_only` 真的到核查就收、写错产线时报错能照做。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_pipelines_config
from backend.app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_pipeline_catalogue_lists_every_configured_pipeline(client: TestClient) -> None:
    response = client.get("/api/pipelines")
    assert response.status_code == 200
    body = response.json()
    configured = set(get_pipelines_config().pipelines)
    assert {item["name"] for item in body["items"]} == configured
    assert body["default"] == "research_to_script"


def test_research_only_stops_at_the_fact_check(client: TestClient) -> None:
    body = client.get("/api/pipelines").json()
    item = next(entry for entry in body["items"] if entry["name"] == "research_only")
    assert item["ends_with"] == "fact_check"
    assert item["stage_count"] == 9
    # 选题立项是 gate，事实核查是 task 上挂 human_gate——两种"等人"都要报出来
    assert item["gates"] == ["topic_approval", "fact_check"]
    task_types = [stage["task_type"] for stage in item["stages"] if stage["kind"] == "task"]
    assert "content.script" not in task_types
    assert item["stages"][-1]["human_gate"] is True


def test_research_to_script_still_runs_to_the_script_gate(client: TestClient) -> None:
    body = client.get("/api/pipelines").json()
    item = next(entry for entry in body["items"] if entry["name"] == "research_to_script")
    assert item["ends_with"] == "script_approval"
    assert "content.script" in [stage["task_type"] for stage in item["stages"]]


def test_a_task_can_be_created_on_the_research_only_pipeline(client: TestClient) -> None:
    response = client.post(
        "/api/research-tasks",
        json={
            "title": "只研究不写脚本",
            "objective": "商业银行净息差为什么一路往下",
            "pipeline": "research_only",
            "start": False,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["planner"] == "pipeline:research_only"
    task_types = [item["task_type"] for item in body["subtasks"]]
    assert task_types[-1] == "verify.claims"
    assert "content.script" not in task_types
    # 9 个阶段里有 1 个是 gate（人工立项），gate 不落地成子任务，所以是 8 格
    assert len(task_types) == 8


def test_unknown_pipeline_says_what_is_available(client: TestClient) -> None:
    response = client.post(
        "/api/research-tasks",
        json={"title": "写错产线", "objective": "随便", "pipeline": "research_everything", "start": False},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["details"]["reason"] == "unknown_pipeline"
    assert "research_only" in error["details"]["available"]
    # 报错信息里就得能直接照做，而不是让人去翻 YAML
    assert "research_only" in error["message"]
