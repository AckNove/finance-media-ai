"""排期自己的账对不对得上：进度（status）与发布（publish_state）是两条独立状态线。

这几条用例守的就是那个最容易出现的错账——数据都回填了，却忘了标已发布；
或者标了已发布，进度还停在"计划中"。系统不替人改，但必须如实说出来。
"""

from __future__ import annotations

from typing import Any


def _entry(client: Any, **overrides: Any) -> dict:
    payload = {
        "title": "储能装机增速放缓这条片子",
        "platform": "douyin",
        "status": "planned",
        "scheduled_for": "2026-11-20T20:00:00",
        **overrides,
    }
    response = client.post("/api/calendar", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _snapshot(client: Any, entry_id: int, views: int = 100) -> None:
    response = client.post(
        f"/api/calendar/{entry_id}/metrics",
        json={"captured_at": "2026-11-21T10:00:00", "views": views},
    )
    assert response.status_code == 201, response.text


def _publish(client: Any, entry_id: int) -> dict:
    response = client.patch(
        f"/api/calendar/{entry_id}",
        json={
            "publish_state": "published",
            "published_url": "https://www.douyin.com/video/7000000000000000000",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_clean_entry_has_nothing_to_report(client: Any) -> None:
    entry = _entry(client, status="rendered")
    _snapshot(client, entry["id"])
    published = _publish(client, entry["id"])

    consistency = published["consistency"]
    assert consistency["metric_count"] == 1
    assert consistency["warnings"] == []


def test_data_recorded_before_publishing_is_flagged(client: Any) -> None:
    entry = _entry(client)
    _snapshot(client, entry["id"], views=888)

    consistency = client.get(f"/api/calendar/{entry['id']}").json()["consistency"]
    assert consistency["metric_count"] == 1
    assert len(consistency["warnings"]) == 1
    assert "还没标已发布" in consistency["warnings"][0]
    assert "不会计入" in consistency["warnings"][0]


def test_publishing_without_moving_the_status_is_flagged(client: Any) -> None:
    entry = _entry(client, status="planned")
    published = _publish(client, entry["id"])

    warnings = published["consistency"]["warnings"]
    assert len(warnings) == 1
    assert "进度还停在「planned」" in warnings[0]
    assert "不会自己同步" in warnings[0]


def test_the_list_endpoint_counts_snapshots_per_row(client: Any) -> None:
    busy = _entry(client, title="回填过两次的那条", platform="bilibili")
    _snapshot(client, busy["id"], views=10)
    quiet = _entry(client, title="一次都没回填的那条", platform="bilibili")

    rows = client.get("/api/calendar").json()["items"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[busy["id"]]["consistency"]["metric_count"] == 1
    assert by_id[quiet["id"]]["consistency"]["metric_count"] == 0
    # 计数是按行算的，不是把整页总数抄给每一行
    assert by_id[quiet["id"]]["consistency"]["warnings"] == []
