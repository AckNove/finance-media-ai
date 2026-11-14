"""发布链接的校验，以及"这条链接像不像这个平台"的提醒。

这里守的是两件不同的事：

1. **必然错的必须挡住**：`abc`、`javascript:alert(1)`、`https://` 这种值如果存进
   `published_url`，它在页面上会被当成链接直接渲染，"已发布"这件事就没人信了；
2. **可疑的只提醒**：平台会换域名、会有短链（`v.douyin.com`、`b23.tv`），
   所以域名对不上时只写一条 warning，不拦人。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.schemas.operations import CalendarCreate
from backend.app.services import daily_service
from backend.app.services.operations_service import OperationsService
from backend.app.services.publish_links import (
    MAX_URL_CHARS,
    PublishLinkError,
    host_of,
    looks_like_platform,
    normalise_url,
    platform_label,
)


# ---- 纯函数：链接本身 ----------------------------------------------------


def test_a_normal_link_passes_and_whitespace_is_trimmed() -> None:
    url = "  https://www.douyin.com/video/7300000000000000000  "
    assert normalise_url(url) == "https://www.douyin.com/video/7300000000000000000"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty_url"),
        ("   ", "empty_url"),
        ("abc", "bad_scheme"),
        ("123", "bad_scheme"),
        ("javascript:alert(1)", "bad_scheme"),
        ("data:text/html,<b>x</b>", "bad_scheme"),
        ("ftp://example.com/a.mp4", "bad_scheme"),
        ("https://", "no_host"),
        ("https:///video/1", "no_host"),
        ("https://a.com/x y", "has_space"),
        ("https://a.com/" + "x" * MAX_URL_CHARS, "too_long"),
    ],
)
def test_broken_links_are_refused_with_a_stable_reason(value: str, reason: str) -> None:
    with pytest.raises(PublishLinkError) as caught:
        normalise_url(value)
    assert caught.value.reason == reason
    assert caught.value.message


def test_host_extraction_handles_ports_and_case() -> None:
    assert host_of("https://WWW.DouYin.com:443/video/1") == "www.douyin.com"
    assert host_of("not a url") == ""


@pytest.mark.parametrize(
    ("url", "platform", "expected"),
    [
        ("https://www.douyin.com/video/1", "douyin", True),
        ("https://v.douyin.com/abcd/", "douyin", True),
        ("https://www.bilibili.com/video/BV1", "bilibili", True),
        ("https://b23.tv/abcd", "bilibili", True),
        ("https://mp.weixin.qq.com/s/abc", "wechat", True),
        ("https://www.xiaohongshu.com/explore/1", "xiaohongshu", True),
        # 对不上：只提醒，不拦
        ("https://www.bilibili.com/video/BV1", "douyin", False),
        # 骗人的：把平台名放进路径里不算数
        ("https://evil.example.com/douyin.com/video/1", "douyin", False),
        # 查不到域名表的平台不猜
        ("https://anything.example.com/x", "other", True),
        ("https://anything.example.com/x", "", True),
    ],
)
def test_platform_host_matching(url: str, platform: str, expected: bool) -> None:
    assert looks_like_platform(url, platform) is expected


def test_platform_labels_are_for_humans() -> None:
    assert platform_label("douyin") == "抖音"
    assert platform_label("unknown_platform") == "unknown_platform"


# ---- 服务层与接口 --------------------------------------------------------


def _entry(db_session, **overrides):
    payload = CalendarCreate(title="一条要发的片子", platform="douyin")
    entry = OperationsService(db_session).create(payload)
    for key, value in overrides.items():
        setattr(entry, key, value)
    db_session.commit()
    return entry


def test_marking_published_with_junk_url_is_refused(db_session) -> None:
    entry = _entry(db_session)
    service = OperationsService(db_session)
    from backend.app.core.errors import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        service.update(
            entry.id,
            __import__("backend.app.schemas.operations", fromlist=["CalendarUpdate"]).CalendarUpdate(
                publish_state="published", published_url="javascript:alert(1)"
            ),
        )
    details = caught.value.details or {}
    assert details.get("field") == "published_url"
    assert details.get("reason") == "bad_scheme"
    db_session.refresh(entry)
    assert entry.publish_state != "published"


def test_marking_published_with_a_real_link_records_time(db_session) -> None:
    entry = _entry(db_session)
    service = OperationsService(db_session)
    from backend.app.schemas.operations import CalendarUpdate

    updated = service.update(
        entry.id,
        CalendarUpdate(publish_state="published", published_url=" https://v.douyin.com/abcd/ "),
    )
    assert updated.publish_state == "published"
    assert updated.published_url == "https://v.douyin.com/abcd/"
    assert updated.published_at is not None


def test_a_link_saved_before_publishing_is_checked_too(db_session) -> None:
    entry = _entry(db_session)
    service = OperationsService(db_session)
    from backend.app.core.errors import DomainValidationError
    from backend.app.schemas.operations import CalendarUpdate

    with pytest.raises(DomainValidationError):
        service.update(entry.id, CalendarUpdate(published_url="abc"))


def test_interface_refuses_junk_url_with_the_reason(client, db_session) -> None:
    entry = _entry(db_session)
    response = client.patch(
        f"/api/calendar/{entry.id}",
        json={"publish_state": "published", "published_url": "abc"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["details"]["reason"] == "bad_scheme"
    assert body["error"]["details"]["field"] == "published_url"


def test_interface_warns_when_the_link_is_for_another_platform(client, db_session) -> None:
    entry = _entry(db_session, platform="douyin")
    response = client.patch(
        f"/api/calendar/{entry.id}",
        json={"publish_state": "published", "published_url": "https://www.bilibili.com/video/BV1"},
    )
    assert response.status_code == 200
    warnings = response.json()["consistency"]["warnings"]
    assert any("看着不像抖音" in item for item in warnings)


def test_interface_stays_quiet_when_the_link_matches(client, db_session) -> None:
    entry = _entry(db_session, platform="douyin")
    response = client.patch(
        f"/api/calendar/{entry.id}",
        json={"publish_state": "published", "published_url": "https://v.douyin.com/abcd/"},
    )
    assert response.status_code == 200
    warnings = response.json()["consistency"]["warnings"]
    assert not any("看着不像" in item for item in warnings)


# ---- 每日体检：发布回填的欠账 --------------------------------------------


def test_daily_check_counts_films_that_were_never_marked_published(db_session) -> None:
    from backend.app.models.enums import CalendarStatus

    done = OperationsService(db_session).create(CalendarCreate(title="做好了没发", platform="douyin"))
    done.status = CalendarStatus.EXPORTED.value
    done.publish_state = "not_published"
    published = OperationsService(db_session).create(
        CalendarCreate(title="已经发了", platform="douyin")
    )
    published.status = CalendarStatus.EXPORTED.value
    published.publish_state = "published"
    published.published_url = "https://v.douyin.com/abcd/"
    published.published_at = datetime.now(UTC)
    planned = OperationsService(db_session).create(CalendarCreate(title="还没做", platform="douyin"))
    planned.status = CalendarStatus.PLANNED.value
    db_session.commit()

    section = daily_service.publish_section(db_session)
    assert section["count"] == 1
    assert [item["title"] for item in section["items"]] == ["做好了没发"]
    assert "还没标已发布" in section["note"]

    report = daily_service.collect_daily(db_session, days=1)
    assert report["publish"]["count"] == 1
    assert any("还没标已发布" in item for item in report["attention"])
    assert "发布回填：1 条片子做好了还没标已发布" in daily_service.render_text(report)
