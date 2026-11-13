"""运营层（内容日历 / 平台数据）测试。

这一层最容易写成"看起来有功能"：建一堆字段、回一堆 0、图表画得很热闹。
测试盯着三条底线：

1. **不自动发布**：没给 `published_url` 就不许标 `published`（422），撤回时清时间。
2. **不许覆盖历史**：同一条排期同一时刻只许一份快照，重复回填要 409。
3. **没数据就说没数据**：汇总里的 0 必须配一句"确实没有回填"的说明，
   不许把 0 说成"表现不好"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def clean_operations_tables() -> Any:
    """测试库整场共享：每个用例先把自己写进去的排期与快照清掉。"""
    from sqlalchemy import delete

    from backend.app.database.session import get_session_factory
    from backend.app.models.operations import ContentCalendar, PlatformMetric

    def _clean() -> None:
        session = get_session_factory()()
        try:
            session.execute(delete(PlatformMetric))
            session.execute(delete(ContentCalendar))
            session.commit()
        finally:
            session.close()

    _clean()
    yield
    _clean()


def _create(client: Any, **overrides: Any) -> dict:
    payload = {
        "title": "光伏组件价格战为什么吃掉现金流",
        "platform": "douyin",
        "status": "planned",
        "scheduled_for": "2026-11-20T20:00:00",
        **overrides,
    }
    response = client.post("/api/calendar", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- 建与列


def test_create_and_list_entries(client: Any) -> None:
    entry = _create(client)
    assert entry["status"] == "planned"
    assert entry["publish_state"] == "not_published"

    listed = client.get("/api/calendar")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == entry["id"]


def test_list_can_filter_by_platform_and_status(client: Any) -> None:
    _create(client, title="抖音那条", platform="douyin", status="rendered")
    _create(client, title="B站那条", platform="bilibili", status="planned")

    only_bili = client.get("/api/calendar", params={"platform": "bilibili"}).json()
    assert only_bili["total"] == 1
    assert only_bili["items"][0]["title"] == "B站那条"

    only_rendered = client.get("/api/calendar", params={"status": "rendered"}).json()
    assert only_rendered["total"] == 1
    assert only_rendered["items"][0]["platform"] == "douyin"


def test_unknown_topic_reference_is_rejected(client: Any) -> None:
    response = client.post(
        "/api/calendar",
        json={"title": "挂不存在的选题", "topic_id": 99999},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "topic_id"


def test_video_job_id_cannot_escape_the_jobs_directory(client: Any) -> None:
    response = client.post(
        "/api/calendar",
        json={"title": "想穿越目录", "video_job_id": "../etc/passwd"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- 发布纪律


def test_marking_published_requires_a_url(client: Any) -> None:
    entry = _create(client)
    response = client.patch(f"/api/calendar/{entry['id']}", json={"publish_state": "published"})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "published_url"


def test_marking_published_stamps_the_time(client: Any) -> None:
    entry = _create(client)
    response = client.patch(
        f"/api/calendar/{entry['id']}",
        json={"publish_state": "published", "published_url": "https://example.com/v/1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["publish_state"] == "published"
    assert body["published_at"] is not None


def test_taking_it_back_clears_the_publish_time_but_keeps_the_url(client: Any) -> None:
    entry = _create(client)
    client.patch(
        f"/api/calendar/{entry['id']}",
        json={"publish_state": "published", "published_url": "https://example.com/v/1"},
    )
    back = client.patch(f"/api/calendar/{entry['id']}", json={"publish_state": "removed"})
    assert back.status_code == 200, back.text
    body = back.json()
    assert body["publish_state"] == "removed"
    assert body["published_at"] is None
    assert body["published_url"] == "https://example.com/v/1"


# ---------------------------------------------------------------- 数据回填


def test_metric_snapshot_is_stored_and_listed(client: Any) -> None:
    entry = _create(client)
    created = client.post(
        f"/api/calendar/{entry['id']}/metrics",
        json={
            "captured_at": "2026-11-21T10:00:00",
            "views": 12000,
            "likes": 800,
            "comments": 45,
            "shares": 130,
            "followers_delta": 210,
            "source": "manual",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["views"] == 12000

    listed = client.get(f"/api/calendar/{entry['id']}/metrics")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_same_captured_at_cannot_overwrite_history(client: Any) -> None:
    entry = _create(client)
    payload = {"captured_at": "2026-11-21T10:00:00", "views": 100}
    first = client.post(f"/api/calendar/{entry['id']}/metrics", json=payload)
    assert first.status_code == 201
    second = client.post(f"/api/calendar/{entry['id']}/metrics", json={**payload, "views": 999})
    assert second.status_code == 409
    assert second.json()["error"]["details"]["field"] == "captured_at"

    listed = client.get(f"/api/calendar/{entry['id']}/metrics").json()
    assert [row["views"] for row in listed] == [100]


def test_negative_views_are_rejected(client: Any) -> None:
    entry = _create(client)
    response = client.post(
        f"/api/calendar/{entry['id']}/metrics",
        json={"captured_at": "2026-11-21T10:00:00", "views": -1},
    )
    assert response.status_code == 422


def test_metrics_of_an_unknown_entry_is_404(client: Any) -> None:
    assert client.get("/api/calendar/99999/metrics").status_code == 404
    assert client.post(
        "/api/calendar/99999/metrics", json={"captured_at": "2026-11-21T10:00:00"}
    ).status_code == 404


# ---------------------------------------------------------------- 汇总


def test_analytics_says_zero_means_no_data(client: Any) -> None:
    _create(client, platform="douyin")
    rows = client.get("/api/calendar/analytics").json()
    douyin = next(row for row in rows if row["platform"] == "douyin")
    assert douyin["entries"] == 1
    assert douyin["snapshots"] == 0
    assert douyin["latest_views"] == 0
    assert "没有" in douyin["note"]


def test_analytics_uses_the_latest_snapshot_not_a_sum(client: Any) -> None:
    entry = _create(client, platform="bilibili")
    base = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    for index, views in enumerate((100, 500, 900)):
        response = client.post(
            f"/api/calendar/{entry['id']}/metrics",
            json={
                "captured_at": (base + timedelta(days=index)).isoformat(),
                "views": views,
                "likes": views // 10,
            },
        )
        assert response.status_code == 201, response.text

    rows = client.get("/api/calendar/analytics").json()
    bili = next(row for row in rows if row["platform"] == "bilibili")
    assert bili["snapshots"] == 3
    assert bili["latest_views"] == 900  # 最新一次，不是 1500 的累计
    assert "最新一次" in bili["note"]


def test_from_script_and_report_links_are_kept(client: Any) -> None:
    """排期要能指回"这条内容是从哪份报告/脚本来的"，否则运营数据没法归因。"""
    topic = client.post("/api/topics", json={"title": "归因用选题"}).json()
    entry = _create(client, topic_id=topic["id"], title="带选题的排期")
    assert entry["topic_id"] == topic["id"]
