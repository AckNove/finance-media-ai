"""选题接口：写入、读取、更新、校验与分页过滤。"""

from __future__ import annotations

import uuid

import pytest

from backend.app.core.errors import DomainValidationError
from backend.app.schemas.topic import TopicCreate
from backend.app.services.topic_service import TopicService


def make_payload(**overrides):
    payload = {
        "title": f"可转债溢价率为何长期存在 {uuid.uuid4().hex[:8]}",
        "description": "从转股权与条款博弈看定价",
        "knowledge_domain": "financial_instruments",
        "audience_level": "intermediate",
        "priority": 2,
        "football_analogy_required": True,
        "estimated_duration": 150,
    }
    payload.update(overrides)
    return payload


def test_create_topic_persists_and_returns_defaults(client):
    payload = make_payload()
    response = client.post("/api/topics", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["id"] >= 1
    assert body["slug"]
    assert body["title"] == payload["title"]
    assert body["research_status"] == "pending"
    assert body["content_status"] == "idea"
    assert body["priority"] == 2

    # 真的落库：重新按 id 读回
    again = client.get(f"/api/topics/{body['id']}")
    assert again.status_code == 200
    assert again.json()["title"] == payload["title"]


def test_same_title_gets_distinct_unique_slugs(client):
    payload = make_payload(title=f"重复标题测试 {uuid.uuid4().hex[:6]}")
    first = client.post("/api/topics", json=payload)
    second = client.post("/api/topics", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slug"] != second.json()["slug"]


def test_list_topics_pagination_and_domain_filter(client):
    client.post("/api/topics", json=make_payload(knowledge_domain="football_business"))

    response = client.get("/api/topics", params={"knowledge_domain": "football_business", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 5
    assert body["total"] >= 1
    assert all(item["knowledge_domain"] == "football_business" for item in body["items"])


def test_update_topic_content_status(client):
    topic_id = client.post("/api/topics", json=make_payload()).json()["id"]

    response = client.patch(
        f"/api/topics/{topic_id}",
        json={"content_status": "research", "priority": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content_status"] == "research"
    assert body["priority"] == 1


def test_get_missing_topic_returns_structured_404(client):
    response = client.get("/api/topics/987654")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"]
    assert body["error"]["message"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"priority": 9},
        {"priority": 0},
        {"title": "   "},
        {"estimated_duration": -5},
    ],
)
def test_invalid_topic_payload_rejected(client, overrides):
    response = client.post("/api/topics", json=make_payload(**overrides))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"


def test_topic_with_existing_column_via_service(db_session):
    service = TopicService(db_session)
    column = service.create_column(
        code=f"col_{uuid.uuid4().hex[:6]}", name="金融基础", display_order=1
    )
    topic = service.create(TopicCreate(title="栏目归属测试", column_id=column.id))
    assert topic.column_id == column.id
    assert any(item.id == column.id for item in service.list_columns())


def test_topic_with_missing_column_rejected(db_session):
    service = TopicService(db_session)
    with pytest.raises(DomainValidationError):
        service.create(TopicCreate(title="栏目不存在测试", column_id=999999))
