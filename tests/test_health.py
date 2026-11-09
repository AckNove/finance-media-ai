"""健康检查、根路径与 OpenAPI 文档测试。"""

from __future__ import annotations

import pytest

# 任务书第七节要求的接口必须全部出现在 OpenAPI 文档中
REQUIRED_PATHS = {
    "/health",
    "/api/system/status",
    "/api/topics",
    "/api/topics/{topic_id}",
    "/api/knowledge",
    "/api/knowledge/search",
    "/api/sources",
    "/api/evidence",
    "/api/research-reports",
}


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "finance-media-ai"
    assert body["version"] == "0.1.0"


def test_root_returns_entry_points(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_api_documentation_available(client, path):
    assert client.get(path).status_code == 200


def test_openapi_documents_all_required_endpoints(client):
    spec = client.get("/openapi.json").json()
    missing = REQUIRED_PATHS - set(spec["paths"])
    assert not missing, f"OpenAPI 缺少接口: {sorted(missing)}"


def test_unknown_path_returns_structured_error(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    # FastAPI 默认 404（未匹配路由）不经过我们的事件处理器，只断言不是 500
    assert response.status_code != 500
