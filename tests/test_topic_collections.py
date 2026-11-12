"""选题 ↔ 合集（多对多）写入的接口测试。

注意：整个测试会话共用一个临时库，栏目 code 有唯一约束，所以这里每个用例都用
带随机后缀的 code，不依赖"库里还没人建过 banking"。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.models.taxonomy import TopicCollection
from backend.app.services.topic_service import TopicService


def _code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _topic(client: TestClient, title: str = "商业银行是怎么赚钱的") -> int:
    response = client.post("/api/topics", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _column(db_session: Session, prefix: str) -> tuple[int, str]:
    code = _code(prefix)
    column = TopicService(db_session).create_column(code=code, name=f"{prefix} 合集")
    return column.id, code


def test_a_topic_can_join_several_collections(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    first, first_code = _column(db_session, "banking")
    second, second_code = _column(db_session, "macro")

    assert (
        client.post(
            f"/api/topics/{topic_id}/collections",
            json={"column_id": first, "weight": 0.6},
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/topics/{topic_id}/collections", json={"column_id": second}).status_code
        == 201
    )

    listed = client.get(f"/api/topics/{topic_id}/collections").json()
    # 权重高的排前面：第二个用默认 1.0，第一个是 0.6
    assert [item["column_code"] for item in listed] == [second_code, first_code]
    assert listed[1]["column_name"] == "banking 合集"
    assert listed[1]["weight"] == 0.6


def test_only_one_primary_collection_survives(client: TestClient, db_session: Session) -> None:
    """表上有「一个选题最多一个主合集」的条件唯一索引，业务侧要先把旧的降级。"""
    topic_id = _topic(client)
    first, _ = _column(db_session, "banking")
    second, _ = _column(db_session, "macro")

    client.post(
        f"/api/topics/{topic_id}/collections", json={"column_id": first, "is_primary": True}
    )
    response = client.post(
        f"/api/topics/{topic_id}/collections", json={"column_id": second, "is_primary": True}
    )
    assert response.status_code == 201
    assert response.json()["is_primary"] is True

    listed = client.get(f"/api/topics/{topic_id}/collections").json()
    primaries = [item for item in listed if item["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["column_id"] == second
    # 主合集排在最前面（界面第一眼看到的就是它）
    assert listed[0]["column_id"] == second


def test_attaching_twice_updates_instead_of_duplicating(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    column_id, _ = _column(db_session, "banking")

    client.post(f"/api/topics/{topic_id}/collections", json={"column_id": column_id, "weight": 0.4})
    again = client.post(
        f"/api/topics/{topic_id}/collections", json={"column_id": column_id, "weight": 0.7}
    )
    assert again.status_code == 201
    assert again.json()["weight"] == 0.7

    listed = client.get(f"/api/topics/{topic_id}/collections").json()
    assert len(listed) == 1


def test_detaching_removes_only_that_collection(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    first, _ = _column(db_session, "banking")
    second, _ = _column(db_session, "macro")
    for column_id in (first, second):
        client.post(f"/api/topics/{topic_id}/collections", json={"column_id": column_id})

    response = client.delete(f"/api/topics/{topic_id}/collections/{first}")
    assert response.status_code == 204

    listed = client.get(f"/api/topics/{topic_id}/collections").json()
    assert [item["column_id"] for item in listed] == [second]
    # 真正从表里删掉了，不是只标了个标记
    assert db_session.query(TopicCollection).filter_by(topic_id=topic_id).count() == 1


def test_unknown_column_or_topic_is_reported_not_silently_ignored(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    column_id, _ = _column(db_session, "banking")

    missing_column = client.post(
        f"/api/topics/{topic_id}/collections", json={"column_id": 9999}
    )
    assert missing_column.status_code == 422
    assert missing_column.json()["error"]["details"]["field"] == "column_id"

    assert (
        client.post("/api/topics/9999/collections", json={"column_id": column_id}).status_code
        == 404
    )
    assert client.get("/api/topics/9999/collections").status_code == 404
    # 没挂过就删：报 404，而不是假装删成功
    assert client.delete(f"/api/topics/{topic_id}/collections/{column_id}").status_code == 404


def test_weight_out_of_range_is_rejected(client: TestClient, db_session: Session) -> None:
    topic_id = _topic(client)
    column_id, _ = _column(db_session, "banking")

    response = client.post(
        f"/api/topics/{topic_id}/collections", json={"column_id": column_id, "weight": 1.5}
    )
    assert response.status_code == 422
