"""工作台接口测试：视频产物只读、配置快照、搜索开关。

原则：这些接口只呈现磁盘上真实存在的东西。目录空就说空，文件不存在就 404，
绝不返回编造的示例数据。搜索受 ``features.search`` 约束，关着就明确拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.config import PROJECT_ROOT


def test_jobs_listing_reflects_the_disk(client: Any) -> None:
    body = client.get("/api/workspace/jobs").json()
    assert body["root"] == "content/jobs"
    names = {item["job_id"] for item in body["jobs"]}
    on_disk = {path.name for path in (PROJECT_ROOT / "content" / "jobs").iterdir() if path.is_dir()}
    assert names == on_disk
    for item in body["jobs"]:
        assert set(item["artifacts"]) and isinstance(item["has_output"], bool)


def test_job_detail_returns_storyboard_rows(client: Any) -> None:
    jobs = client.get("/api/workspace/jobs").json()["jobs"]
    with_storyboard = [
        item for item in jobs if item["artifacts"].get("storyboard/storyboard.csv")
    ]
    if not with_storyboard:
        pytest.skip("仓库里没有带分镜的任务目录")
    body = client.get(f"/api/workspace/jobs/{with_storyboard[0]['job_id']}").json()
    assert body["storyboard"]["columns"]
    assert body["storyboard"]["rows"]
    assert body["summary"]["job_id"] == with_storyboard[0]["job_id"]


def test_unknown_job_is_404(client: Any) -> None:
    response = client.get("/api/workspace/jobs/no-such-job")
    assert response.status_code == 404
    assert response.json()["error"]["details"]["reason"] == "unknown_job"


def test_path_escape_is_rejected(client: Any) -> None:
    jobs = client.get("/api/workspace/jobs").json()["jobs"]
    if not jobs:
        pytest.skip("仓库里没有任务目录")
    job_id = jobs[0]["job_id"]
    response = client.get(
        f"/api/workspace/jobs/{job_id}/file", params={"path": "../../../data/finance_media.db"}
    )
    assert response.status_code in (404, 422)
    body = response.json()["error"]
    assert body["details"]["reason"] in {"path_escape", "missing_file"}


def test_config_snapshot_matches_the_yaml(client: Any) -> None:
    body = client.get("/api/workspace/config").json()
    assert set(body["features"]) == {"model", "search", "execution"}
    assert body["cost"]["pricing_policy"] in {"reject_unknown", "allow_unknown"}
    for model in body["models"]:
        assert model["pricing_status"] in {"known", "missing", "approved_exception"}
        assert "api_key" not in json.dumps(model)


def test_search_is_gated_by_the_feature_flag(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.api.routes import workspace as workspace_module
    from backend.app.core.config_schema import FeaturesSettings

    monkeypatch.setattr(
        workspace_module,
        "get_features_settings",
        lambda: FeaturesSettings(model=False, search=False, execution=False),
    )
    body = client.get("/api/workspace/search", params={"q": "银行"}).json()
    assert body["ok"] is False
    assert body["reasons"] == ["feature_disabled:search"]
    assert "features.search" in body["message"]
    assert body["hits"] == []


def test_file_endpoint_serves_only_existing_artifacts(client: Any) -> None:
    jobs = client.get("/api/workspace/jobs").json()["jobs"]
    job_id = None
    relative = None
    for item in jobs:
        candidate = PROJECT_ROOT / "content" / "jobs" / item["job_id"] / "output"
        if candidate.is_dir():
            for path in candidate.glob("*.mp4"):
                job_id, relative = item["job_id"], f"output/{path.name}"
                break
        if job_id:
            break
    if not job_id:
        pytest.skip("仓库里没有成片可测")
    response = client.get(f"/api/workspace/jobs/{job_id}/file", params={"path": relative})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("video/")
    missing = client.get(
        f"/api/workspace/jobs/{job_id}/file", params={"path": "output/nope.mp4"}
    )
    assert missing.status_code == 404


def test_monitoring_endpoints_are_read_only_and_real(client: Any) -> None:
    costs = client.get("/api/system/costs").json()
    assert costs["model_calls"] >= 0
    assert isinstance(costs["by_model"], list)
    providers = client.get("/api/system/providers").json()
    assert {item["name"] for item in providers} >= {"deepseek", "openai", "local"}
    for item in providers:
        assert "api_key" not in json.dumps(item)
    agents = client.get("/api/system/agents").json()
    assert agents["count"] == 9
