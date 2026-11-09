"""资料来源与事实证据接口：登记、指纹、关联查询与校验。"""

from __future__ import annotations

import hashlib
import uuid


def make_source(**overrides):
    payload = {
        "title": f"中国证监会年报 {uuid.uuid4().hex[:8]}",
        "url": "https://example.org/report.pdf",
        "publisher": "中国证监会",
        "source_type": "regulator_filing",
        "accessed_at": "2026-06-30T10:00:00+00:00",
        "jurisdiction": "CN",
        "credibility_level": "high",
        "license_status": "unclear",
        "raw_excerpt": "本报告披露了可转债发行与转股的相关统计口径。",
    }
    payload.update(overrides)
    return payload


def test_create_source_computes_content_hash(client):
    payload = make_source()
    response = client.post("/api/sources", json=payload)
    assert response.status_code == 201

    body = response.json()
    expected = hashlib.sha256(payload["raw_excerpt"].encode("utf-8")).hexdigest()
    assert body["content_hash"] == expected
    assert len(body["content_hash"]) == 64
    assert body["source_type"] == "regulator_filing"
    assert body["accessed_at"].startswith("2026-06-30")


def test_list_sources_filters_by_type(client):
    client.post("/api/sources", json=make_source(source_type="academic_paper"))

    response = client.get("/api/sources", params={"source_type": "academic_paper"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all(item["source_type"] == "academic_paper" for item in items)


def test_evidence_links_source_to_knowledge_item(client):
    source_id = client.post("/api/sources", json=make_source()).json()["id"]
    item_id = client.post(
        "/api/knowledge",
        json={"title": f"证据关联测试 {uuid.uuid4().hex[:8]}", "definition": "定义"},
    ).json()["id"]

    created = client.post(
        "/api/evidence",
        json={
            "source_id": source_id,
            "claim": "可转债溢价率长期为正",
            "original_excerpt": "统计显示转股溢价率中位数高于零。",
            "page_number": "P.42",
            "support_type": "supports",
            "claim_nature": "fact",
            "verification_status": "verified",
            "knowledge_item_id": item_id,
        },
    )
    assert created.status_code == 201
    evidence = created.json()
    assert evidence["knowledge_item_id"] == item_id
    assert evidence["verified_at"] is not None

    # 关联查询：带来源摘要
    listed = client.get("/api/evidence", params={"knowledge_item_id": item_id})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    detail = body["items"][0]
    assert detail["source_id"] == source_id
    assert detail["source_publisher"] == "中国证监会"
    assert detail["claim"] == "可转债溢价率长期为正"


def test_evidence_requires_exactly_one_target(client):
    source_id = client.post("/api/sources", json=make_source()).json()["id"]

    no_target = client.post(
        "/api/evidence",
        json={"source_id": source_id, "claim": "无目标主张"},
    )
    assert no_target.status_code == 422
    assert no_target.json()["error"]["code"] == "request_validation_error"

    both_targets = client.post(
        "/api/evidence",
        json={"source_id": source_id, "claim": "两个目标", "knowledge_item_id": 1, "research_report_id": 1},
    )
    assert both_targets.status_code == 422


def test_evidence_with_missing_source_is_rejected(client):
    """创建时引用不存在的来源属于请求体业务校验失败（422），不是读取不存在的资源。"""
    response = client.post(
        "/api/evidence",
        json={"source_id": 999999, "claim": "来源不存在", "knowledge_item_id": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_error"
    assert response.json()["error"]["details"]["field"] == "source_id"


def test_evidence_query_requires_a_target(client):
    response = client.get("/api/evidence")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_error"
