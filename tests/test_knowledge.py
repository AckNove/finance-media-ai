"""知识条目接口：写入、检索、更新与关联。"""

from __future__ import annotations

import uuid

import pytest


def make_item(**overrides):
    payload = {
        "title": f"可转债 {uuid.uuid4().hex[:8]}",
        "knowledge_domain": "financial_instruments",
        "definition": "可转换为发行公司股票的债券",
        "mechanism": "转股权 + 债券属性，价格 = 纯债价值 + 期权价值",
        "participants": "发行人、投资者、承销商",
        "profit_model": "发行人降低票息成本，投资者获取转股期权",
        "pricing_logic": "转股价值 = 100 / 转股价 * 正股价格",
        "risk_structure": "信用风险 + 股价下行风险 + 条款博弈风险",
        "jurisdiction": "CN",
        "currency": "CNY",
        "unit": "元",
    }
    payload.update(overrides)
    return payload


def test_create_knowledge_item_persists_fields(client):
    payload = make_item()
    response = client.post("/api/knowledge", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["id"] >= 1
    assert body["slug"]
    assert body["definition"] == payload["definition"]
    assert body["jurisdiction"] == "CN"
    assert body["currency"] == "CNY"
    assert body["verification_status"] == "unverified"
    assert body["version"] == 1
    assert body["last_verified_at"] is None

    again = client.get(f"/api/knowledge/{body['id']}")
    assert again.status_code == 200
    assert again.json()["mechanism"] == payload["mechanism"]


def test_analogy_requires_limitations_pair(client):
    analogy_only = client.post(
        "/api/knowledge",
        json=make_item(football_analogy="像球队买断球员合同"),
    )
    assert analogy_only.status_code == 422
    assert analogy_only.json()["error"]["code"] == "request_validation_error"

    both = client.post(
        "/api/knowledge",
        json=make_item(
            football_analogy="像球队买断球员合同",
            analogy_limitations="债券有固定到期日与优先清偿顺序，球员合同不存在同类兑付顺序",
        ),
    )
    assert both.status_code == 201
    assert both.json()["analogy_limitations"]


def test_search_matches_multiple_fields_and_ranks_title_first(client):
    marker = f"mk{uuid.uuid4().hex[:8]}"
    title_hit = client.post(
        "/api/knowledge",
        json=make_item(title=f"久期 {marker}"),
    ).json()
    body_hit = client.post(
        "/api/knowledge",
        json=make_item(
            title=f"利率风险 {uuid.uuid4().hex[:8]}",
            mechanism=f"久期衡量债券价格对 {marker} 变化的敏感度",
        ),
    ).json()

    response = client.get("/api/knowledge/search", params={"q": marker})
    assert response.status_code == 200

    body = response.json()
    assert body["query"] == marker
    assert body["total"] == 2
    matched_ids = [entry["item"]["id"] for entry in body["items"]]
    assert title_hit["id"] in matched_ids
    assert body_hit["id"] in matched_ids
    # 标题命中应排在前面，且带命中字段与分数
    assert matched_ids[0] == title_hit["id"]
    assert "title" in body["items"][0]["matched_fields"]
    assert body["items"][0]["score"] > 0


def test_search_requires_query(client):
    assert client.get("/api/knowledge/search").status_code == 422


def test_update_bumps_version_and_stamps_verification(client):
    item_id = client.post("/api/knowledge", json=make_item()).json()["id"]

    response = client.patch(
        f"/api/knowledge/{item_id}",
        json={"verification_status": "verified", "risk_structure": "信用风险 + 条款风险"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["verification_status"] == "verified"
    assert body["version"] == 2
    assert body["last_verified_at"] is not None
    assert body["risk_structure"] == "信用风险 + 条款风险"


def test_knowledge_relations_create_list_and_reject_self_loop(client):
    first = client.post("/api/knowledge", json=make_item()).json()
    second = client.post("/api/knowledge", json=make_item()).json()

    created = client.post(
        "/api/knowledge/relations",
        json={
            "source_item_id": first["id"],
            "target_item_id": second["id"],
            "relation_type": "prerequisite",
            "confidence": 0.8,
        },
    )
    assert created.status_code == 201
    assert created.json()["relation_type"] == "prerequisite"

    listed = client.get(f"/api/knowledge/{first['id']}/relations")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    self_loop = client.post(
        "/api/knowledge/relations",
        json={
            "source_item_id": first["id"],
            "target_item_id": first["id"],
            "relation_type": "related",
        },
    )
    assert self_loop.status_code == 422

    duplicate = client.post(
        "/api/knowledge/relations",
        json={
            "source_item_id": first["id"],
            "target_item_id": second["id"],
            "relation_type": "prerequisite",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("item_id", [999999])
def test_missing_knowledge_item_returns_404(client, item_id):
    response = client.get(f"/api/knowledge/{item_id}")
    assert response.status_code == 404
    assert response.json()["error"]["message"]
