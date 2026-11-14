"""从选题直接建研究任务。

老问题：选题采纳完只落在 `topics` 表里，人还得切到「研究任务」页把标题和研究目标重敲一遍；
抄错一个字，"这条任务研究的是哪个选题"就断了（`research_tasks.topic_id` 本来就是干这个的）。

这里钉住四件事：描述里的来源编号认得出、默认只做研究、同一条选题不许并行开两条、失败/完成的不拦。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models.enums import ResearchTaskStatus
from backend.app.services import topic_research
from backend.app.services.topic_research import (
    TopicResearchError,
    TopicResearchPlan,
    active_task_of,
    plan_for_topic,
    source_ids_from_description,
)
from backend.app.services.topic_service import TopicService
from backend.app.schemas.topic import TopicCreate


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_topic(session, *, title: str, description: str = ""):
    return TopicService(session).create(
        TopicCreate(title=title, description=description, slug=_unique("topic"))
    )


DESCRIPTION = """由「选题建议」采纳而来：这些是当时支撑它的已入库来源，研究时可直接回原文核对。

采纳时页面显示的建议分数：62 分（规则打分，不是事实判断）。

- S12《光伏组件价格跌破现金成本线》 中国光伏行业协会 · 2026-10-01 https://example.com/a
- S7《2025 年组件产能统计》 工信部 · 2026-09-20 https://example.com/b
"""


def test_source_ids_are_read_from_the_description() -> None:
    assert source_ids_from_description(DESCRIPTION) == (12, 7)


def test_hand_edited_descriptions_just_yield_nothing() -> None:
    assert source_ids_from_description("来源：S12 和 S7") == ()
    assert source_ids_from_description("") == ()
    assert source_ids_from_description(None) == ()


def test_plan_defaults_to_research_only_and_only_searches_the_title(db_session) -> None:
    topic = _make_topic(db_session, title="光伏组件为什么跌破现金成本")
    plan = plan_for_topic(topic, source_ids=(12, 7))

    assert plan.pipeline == "research_only"
    assert plan.title == topic.title
    # 没有显式检索词时只放标题一条：规则规划会基于它扩词，不拿模板句子去搜
    assert plan.queries == (topic.title,)
    assert plan.source_ids == (12, 7)
    assert "2 条已入库来源" in plan.objective
    assert "选题状态不会自动改" in plan.note


def test_plan_honours_overrides_and_caps_the_queries(db_session) -> None:
    topic = _make_topic(db_session, title="储能行业走到哪一步了")
    queries = [f"储能 {index}" for index in range(30)]
    plan = plan_for_topic(
        topic,
        pipeline="research_to_script",
        title="改写后的标题",
        objective="只回答装机增速",
        queries=queries,
    )

    assert plan.pipeline == "research_to_script"
    assert plan.title == "改写后的标题"
    assert plan.objective == "只回答装机增速"
    assert len(plan.queries) == topic_research.MAX_QUERIES


def test_a_topic_without_a_title_is_refused(db_session) -> None:
    topic = _make_topic(db_session, title="临时标题")
    topic.title = "   "
    with pytest.raises(TopicResearchError) as error:
        plan_for_topic(topic)
    assert error.value.reason == "empty_title"


def test_active_task_lookup_ignores_finished_ones(db_session) -> None:
    from backend.app.execution.runtime import TaskRuntime
    from backend.app.database.session import get_session_factory
    from backend.app.execution.orchestrator import Orchestrator

    topic = _make_topic(db_session, title=_unique("选题"))
    assert active_task_of(db_session, topic.id) is None

    runtime = TaskRuntime(get_session_factory())
    plan = plan_for_topic(topic, source_ids=())
    first = topic_research.start_research(
        db_session, runtime, topic, plan=plan, start=False
    )
    assert active_task_of(db_session, topic.id).id == first.task.id

    # 撞车：同一条选题再开一条要拦下来，并指出是哪条
    with pytest.raises(TopicResearchError) as error:
        topic_research.start_research(db_session, runtime, topic, plan=plan, start=False)
    assert error.value.reason == "task_in_progress"
    assert error.value.details["task_id"] == first.task.id

    # 那条任务失败之后就不该再拦（快照是冻结的，改库里的行）
    from backend.app.models.execution import ResearchTask

    row = db_session.get(ResearchTask, first.task.id)
    row.status = ResearchTaskStatus.FAILED
    db_session.commit()
    assert active_task_of(db_session, topic.id) is None


def test_api_creates_the_task_and_points_back_at_it(db_session, client: TestClient) -> None:
    topic = _make_topic(db_session, title=_unique("光伏组件"), description=DESCRIPTION)

    response = client.post(
        f"/api/topics/{topic.id}/research-task", json={"start": False}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["pipeline"] == "research_only"
    assert body["source_ids"] == [12, 7]
    assert body["subtask_count"] >= 1
    assert body["queries"] == [topic.title]

    again = client.post(f"/api/topics/{topic.id}/research-task", json={"start": False})
    assert again.status_code == 409
    details = again.json()["error"]["details"]
    assert details["reason"] == "task_in_progress"
    assert details["task_id"] == body["task_id"]


def test_api_rejects_unknown_topics_and_stray_fields(client: TestClient) -> None:
    missing = client.post("/api/topics/999999/research-task", json={"start": False})
    assert missing.status_code == 404

    stray = client.post("/api/topics/1/research-task", json={"pipeline": "x", "nope": 1})
    assert stray.status_code == 422


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client
