"""系统状态接口：数据库探测、特性开关与 Agent 名册。"""

from __future__ import annotations


def test_system_status_reports_real_database(client):
    response = client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()

    assert body["app"] == "finance-media-ai"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"

    database = body["database"]
    assert database["dialect"] == "sqlite"
    assert database["reachable"] is True
    # 18 张业务表（test 库由 create_all 建立，可能没有 alembic_version）
    assert database["table_count"] >= 18


def test_system_status_masks_database_path(client):
    url = client.get("/api/system/status").json()["database"]["url"]
    # 只暴露文件名，不泄露本机绝对路径
    assert "\\" not in url
    assert "C:" not in url
    assert url.startswith("sqlite:///")


def test_system_status_reports_features_disabled(client):
    features = client.get("/api/system/status").json()["features"]
    # 2.0 阶段没有接入任何外部能力，七个开关必须全部为 false
    assert features == {
        "llm_providers": False,
        "web_search": False,
        "vector_store": False,
        "video_rendering": False,
        "model": False,
        "search": False,
        "execution": False,
    }


def test_system_status_reports_sqlite_pragmas(client):
    """T03：状态接口必须回读真实的 PRAGMA，而不是硬编码字符串。

    M2.0 专项整改后，``journal_mode`` 只能由 ``backend.app.database.wal.enable_wal``
    显式切换。本用例的测试库是新建的临时文件，从未做过 WAL 初始化，
    因此状态接口必须如实报 ``delete`` —— 如果这里读到 ``wal``，
    就说明"随便查一下状态就把库改了"。
    """
    database = client.get("/api/system/status").json()["database"]
    assert database["journal_mode"].lower() == "delete"
    assert database["busy_timeout"] == "5000"


def test_system_status_discovers_nine_agents(client):
    agents = client.get("/api/system/status").json()["agents"]
    assert agents["discovered"] == 9
    assert len(agents["roles"]) == 9
    assert "orchestrator" in agents["roles"]
    assert "video_producer" in agents["roles"]
