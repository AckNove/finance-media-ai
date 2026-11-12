"""选题标签（tags / topic_tags）的写入路径。

之前 `tags` 与 `topic_tags` 两张表建好了却没有任何写入接口，标签体系只是纸面设计；
这组用例守住四件事：打标签会真的落两张表、标签按 `namespace+name` 复用而不是重复插入、
摘标签会真的删行并把 `usage_count` 减回去（不假装成功）、名字撞车时不会 500。

注意：整个测试会话共用一个临时库，`tags` 上有 `uq_tag_namespace_name` 与唯一 `slug`，
所以每个用例都用带随机后缀的标签名，不依赖"库里还没有某个标签"。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.models.enums import TagNamespace


def _name(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _topic(client: TestClient, title: str = "商业银行是怎么赚钱的") -> int:
    response = client.post("/api/topics", json={"title": f"{title}-{uuid4().hex[:6]}"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_attach_tag_creates_tag_and_link(client: TestClient) -> None:
    topic_id = _topic(client)
    response = client.post(
        f"/api/topics/{topic_id}/tags",
        json={"name": _name("净息差"), "namespace": "mechanism", "weight": 0.8},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["namespace"] == "mechanism"
    assert body["weight"] == pytest.approx(0.8)
    assert body["topic_id"] == topic_id
    assert body["usage_count"] == 1
    assert body["slug"]


def test_same_name_is_reused_instead_of_duplicated(client: TestClient) -> None:
    name = _name("息差")
    first_topic = _topic(client)
    second_topic = _topic(client, "银行中间业务")
    one = client.post(f"/api/topics/{first_topic}/tags", json={"name": name}).json()
    two = client.post(f"/api/topics/{second_topic}/tags", json={"name": name}).json()
    # 同一个标签挂到两个选题上：tag_id 相同，usage_count 变成 2
    assert one["tag_id"] == two["tag_id"]
    assert two["usage_count"] == 2


def test_same_name_in_another_namespace_is_a_different_tag(client: TestClient) -> None:
    name = _name("银行")
    topic_id = _topic(client)
    industry = client.post(
        f"/api/topics/{topic_id}/tags", json={"name": name, "namespace": "industry"}
    ).json()
    period = client.post(
        f"/api/topics/{topic_id}/tags", json={"name": name, "namespace": "period"}
    ).json()
    assert industry["tag_id"] != period["tag_id"]


def test_weight_can_be_updated_without_a_second_row(client: TestClient) -> None:
    name = _name("重挂")
    topic_id = _topic(client)
    client.post(f"/api/topics/{topic_id}/tags", json={"name": name, "weight": 0.3})
    again = client.post(
        f"/api/topics/{topic_id}/tags", json={"name": name, "weight": 0.9}
    ).json()
    listed = client.get(f"/api/topics/{topic_id}/tags").json()
    assert len(listed) == 1
    assert again["weight"] == pytest.approx(0.9)
    assert again["usage_count"] == 1


def test_tags_are_sorted_by_weight_then_name(client: TestClient) -> None:
    topic_id = _topic(client)
    suffix = uuid4().hex[:6]
    client.post(f"/api/topics/{topic_id}/tags", json={"name": f"低-{suffix}", "weight": 0.2})
    client.post(f"/api/topics/{topic_id}/tags", json={"name": f"高-{suffix}", "weight": 0.9})
    listed = client.get(f"/api/topics/{topic_id}/tags").json()
    assert [row["weight"] for row in listed] == pytest.approx([0.9, 0.2])


def test_detach_removes_the_row_and_decrements_usage(client: TestClient) -> None:
    name = _name("摘掉")
    topic_id = _topic(client)
    attached = client.post(f"/api/topics/{topic_id}/tags", json={"name": name}).json()

    removed = client.delete(f"/api/topics/{topic_id}/tags/{attached['tag_id']}")
    assert removed.status_code == 204
    assert client.get(f"/api/topics/{topic_id}/tags").json() == []

    # 再摘一次：没有这行就说没有（404），不能假装删成功
    again = client.delete(f"/api/topics/{topic_id}/tags/{attached['tag_id']}")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "not_found"

    # 重新挂回来：usage_count 从 1 重新涨回，而不是记成 2
    back = client.post(f"/api/topics/{topic_id}/tags", json={"name": name}).json()
    assert back["usage_count"] == 1


def test_unknown_topic_and_blank_name_are_rejected(client: TestClient) -> None:
    missing = client.post("/api/topics/999999/tags", json={"name": "随便"})
    assert missing.status_code == 404

    topic_id = _topic(client)
    blank = client.post(f"/api/topics/{topic_id}/tags", json={"name": "   "})
    assert blank.status_code == 422

    too_heavy = client.post(
        f"/api/topics/{topic_id}/tags", json={"name": _name("权重越界"), "weight": 1.5}
    )
    assert too_heavy.status_code == 422


def test_create_missing_false_rejects_an_unknown_tag(client: TestClient) -> None:
    topic_id = _topic(client)
    response = client.post(
        f"/api/topics/{topic_id}/tags",
        json={"name": _name("不存在"), "create_missing": False},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "name"


def test_default_namespace_is_other(client: TestClient) -> None:
    topic_id = _topic(client)
    body = client.post(
        f"/api/topics/{topic_id}/tags", json={"name": _name("默认")}
    ).json()
    assert body["namespace"] == TagNamespace.OTHER.value


def test_topics_can_be_filtered_by_tag_id(client: TestClient) -> None:
    name = _name("按标签")
    tagged = _topic(client, "按标签查的选题")
    untagged = _topic(client, "没打标签的选题")
    attached = client.post(f"/api/topics/{tagged}/tags", json={"name": name}).json()

    listed = client.get(f"/api/topics?tag_id={attached['tag_id']}&limit=100").json()
    ids = {row["id"] for row in listed["items"]}
    assert tagged in ids
    assert untagged not in ids
    assert listed["total"] == len(listed["items"])


def test_topics_can_be_filtered_by_tag_name(client: TestClient) -> None:
    name = _name("按名字")
    topic_id = _topic(client, "按标签名查的选题")
    client.post(f"/api/topics/{topic_id}/tags", json={"name": name, "namespace": "industry"})

    listed = client.get(
        "/api/topics", params={"tag": name, "tag_namespace": "industry", "limit": 100}
    ).json()
    assert topic_id in {row["id"] for row in listed["items"]}

    # 名字存在但命名空间不对 → 404（不是静默返回空列表）
    wrong = client.get("/api/topics", params={"tag": name, "tag_namespace": "period"})
    assert wrong.status_code == 404


def test_an_ambiguous_tag_name_asks_for_the_namespace(client: TestClient) -> None:
    name = _name("重名")
    topic_id = _topic(client)
    client.post(f"/api/topics/{topic_id}/tags", json={"name": name, "namespace": "industry"})
    client.post(f"/api/topics/{topic_id}/tags", json={"name": name, "namespace": "period"})

    ambiguous = client.get("/api/topics", params={"tag": name})
    assert ambiguous.status_code == 422
    details = ambiguous.json()["error"]["details"]
    assert details["field"] == "namespace"
    assert details["candidates"] == ["industry", "period"]

    # 指定命名空间后能查
    okay = client.get("/api/topics", params={"tag": name, "tag_namespace": "period"})
    assert okay.status_code == 200
