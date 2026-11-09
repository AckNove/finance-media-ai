"""研究报告接口：创建、必填边界、关联选题与查询。"""

from __future__ import annotations

import uuid


def test_create_research_report_with_findings(client):
    topic_id = client.post(
        "/api/topics",
        json={"title": f"研究报告选题 {uuid.uuid4().hex[:8]}"},
    ).json()["id"]

    payload = {
        "title": f"可转债定价机制研究 {uuid.uuid4().hex[:6]}",
        "topic_id": topic_id,
        "summary": "可转债价格由纯债价值与转股期权价值共同决定",
        "findings": [
            {"statement": "转股价值 = 100 / 转股价 * 正股价格", "nature": "fact", "evidence_ids": []},
            {"statement": "溢价率长期为正与下修条款相关", "nature": "inference", "evidence_ids": []},
        ],
        "limitations": "仅覆盖境内公募可转债，未覆盖境外样本",
        "verification_status": "in_review",
        "jurisdiction": "CN",
        "currency": "CNY",
        "author_agent": "financial_researcher",
    }
    response = client.post("/api/research-reports", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["topic_id"] == topic_id
    assert body["author_agent"] == "financial_researcher"
    assert body["version"] == 1
    assert len(body["findings"]) == 2
    assert body["findings"][1]["nature"] == "inference"

    detail = client.get(f"/api/research-reports/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["limitations"] == payload["limitations"]

    listed = client.get("/api/research-reports", params={"limit": 10})
    assert listed.status_code == 200
    assert any(item["id"] == body["id"] for item in listed.json()["items"])


def test_research_report_requires_limitations(client):
    response = client.post(
        "/api/research-reports",
        json={"title": "缺少边界的报告", "summary": "摘要", "limitations": "   "},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"


def test_research_report_with_missing_topic_is_rejected(client):
    """创建时引用不存在的选题属于请求体业务校验失败（422）。"""
    response = client.post(
        "/api/research-reports",
        json={
            "title": "选题不存在的报告",
            "topic_id": 987654,
            "summary": "摘要",
            "limitations": "无",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_error"
    assert response.json()["error"]["details"]["field"] == "topic_id"


def test_get_missing_research_report_returns_404(client):
    response = client.get("/api/research-reports/987654")
    assert response.status_code == 404
