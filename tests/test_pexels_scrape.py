"""Pexels 站点接口适配器的离线测试（一次网络都不发）。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from backend.app.capabilities.media import pexels_scrape as px


PHOTO_ITEM = {
    "attributes": {
        "id": 5203849,
        "slug": "black-server-racks-5203849",
        "title": "Black Server Racks",
        "width": 4024,
        "height": 6048,
        "tags": ["server", "data center"],
        "user": {"first_name": "Brett", "last_name": "Sayles", "slug": "brett-sayles"},
        "image": {"large2x": "https://images.pexels.com/photos/5203849/photo.jpeg?w=1200"},
    }
}

VIDEO_ITEM = {
    "attributes": {
        "id": 37544749,
        "slug": "cooling-tower-37544749",
        "title": "Cooling Tower Emissions",
        "width": 2160,
        "height": 3840,
        "duration": 12.0,
        "tags": ["cooling tower", "power plant"],
        "user": {"first_name": "Richard", "last_name": "Pan", "slug": "richard-pan"},
        "video": {"src": "https://videos.pexels.com/video-files/37544749/1080.mp4",
                  "preview_src": "https://videos.pexels.com/video-files/37544749/360.mp4"},
    }
}


def _payload(items: list[dict]) -> dict:
    return {"data": items}


def test_photo_search_keeps_every_traceable_field(monkeypatch):
    monkeypatch.setattr(px, "_get_json", lambda url, timeout: _payload([PHOTO_ITEM]))
    assets = px.search_photos("data center", per_page=1)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.kind == "photo"
    assert asset.asset_id == "PX-P5203849"
    assert asset.title == "Black Server Racks"
    assert asset.width == 4024 and asset.height == 6048
    assert asset.license_name == "Pexels License"
    assert asset.license_url.startswith("https://www.pexels.com/license")
    assert asset.photographer == "Brett Sayles"
    assert asset.page_url.endswith("/photo/black-server-racks-5203849/")
    assert asset.query == "data center"
    assert asset.retrieved_at  # 抓取时间必须留
    assert "Brett Sayles" in asset.credit


def test_video_search_prefers_full_file_and_records_duration(monkeypatch):
    monkeypatch.setattr(px, "_get_json", lambda url, timeout: _payload([VIDEO_ITEM]))
    assets = px.search_videos("cooling tower", per_page=1)
    asset = assets[0]
    assert asset.kind == "video"
    assert asset.download_url.endswith("1080.mp4")  # 有正片就不要 360 预览
    assert asset.duration_s == 12.0
    assert asset.asset_id == "PX-V37544749"


def test_items_without_download_url_are_skipped(monkeypatch):
    broken = {"attributes": {"id": 1, "title": "x", "user": {}, "image": {}}}
    monkeypatch.setattr(px, "_get_json", lambda url, timeout: _payload([broken]))
    assert px.search_photos("anything", per_page=1) == []


def test_bad_orientation_is_rejected_before_any_request(monkeypatch):
    calls = []
    monkeypatch.setattr(px, "_get_json", lambda url, timeout: calls.append(url) or _payload([]))
    with pytest.raises(px.PexelsScrapeError) as info:
        px.search_photos("x", orientation="diagonal")
    assert info.value.reason == "pexels_bad_request"
    assert calls == []


def test_empty_query_is_rejected(monkeypatch):
    monkeypatch.setattr(px, "_get_json", lambda url, timeout: _payload([]))
    with pytest.raises(px.PexelsScrapeError) as info:
        px.search_videos("   ")
    assert info.value.reason == "pexels_bad_request"


def test_site_secret_is_sent_with_every_search(monkeypatch):
    seen = {}

    def fake_get_json(url, timeout):
        seen["url"] = url
        return _payload([])

    monkeypatch.setattr(px, "_get_json", fake_get_json)
    px.search_photos("server room", per_page=3, page=2)
    assert "query=server+room" in seen["url"]
    assert "per_page=3" in seen["url"]
    assert "page=2" in seen["url"]
    assert seen["url"].startswith(px.API_BASE + px.PHOTO_SEARCH)


def test_download_fills_sha256_and_local_path(tmp_path, monkeypatch):
    asset = px.PexelsAsset(
        kind="photo", asset_id="PX-P1", title="t", page_url="", photographer="",
        photographer_url="", download_url="https://images.pexels.com/photos/1/a.jpeg",
        width=10, height=10, duration_s=0.0, license_name=px.LICENSE_NAME,
        license_url=px.LICENSE_URL, query="q", retrieved_at="2026-11-17T00:00:00+00:00",
    )

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(px.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"jpeg-bytes"))
    monkeypatch.setattr(px.time, "sleep", lambda seconds: None)  # 测试不等 2 秒

    got = px.download(asset, tmp_path)
    assert Path(got.local_path).is_file()
    assert got.sha256 == __import__("hashlib").sha256(b"jpeg-bytes").hexdigest()


def test_download_failure_has_stable_reason(tmp_path, monkeypatch):
    asset = px.PexelsAsset(
        kind="photo", asset_id="PX-P2", title="t", page_url="", photographer="",
        photographer_url="", download_url="https://images.pexels.com/photos/2/a.jpeg",
        width=10, height=10, duration_s=0.0, license_name=px.LICENSE_NAME,
        license_url=px.LICENSE_URL, query="q", retrieved_at="2026-11-17T00:00:00+00:00",
    )

    def boom(request, timeout):
        raise px.urllib.error.URLError("no route")

    monkeypatch.setattr(px.urllib.request, "urlopen", boom)
    monkeypatch.setattr(px.time, "sleep", lambda seconds: None)
    with pytest.raises(px.PexelsScrapeError) as info:
        px.download(asset, tmp_path)
    assert info.value.reason == "pexels_download_failed"


def test_bad_json_raises_bad_payload(monkeypatch):
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(px.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"<html>busy</html>"))
    monkeypatch.setattr(px.time, "sleep", lambda seconds: None)
    with pytest.raises(px.PexelsScrapeError) as info:
        px._get_json("https://www.pexels.com/x", timeout=1.0)
    assert info.value.reason == "pexels_bad_payload"


def test_http_403_maps_to_unauthorized(monkeypatch):
    def boom(request, timeout):
        raise px.urllib.error.HTTPError("https://x", 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(px.urllib.request, "urlopen", boom)
    monkeypatch.setattr(px.time, "sleep", lambda seconds: None)
    with pytest.raises(px.PexelsScrapeError) as info:
        px._get_json("https://www.pexels.com/x", timeout=1.0)
    assert info.value.reason == "pexels_unauthorized"


def test_throttle_keeps_two_seconds_between_requests(monkeypatch):
    slept: list[float] = []
    clock = {"now": 100.0}
    monkeypatch.setattr(px.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(px.time, "sleep", lambda seconds: (slept.append(seconds), clock.__setitem__("now", clock["now"] + seconds)))

    px._last_request_at = 0.0
    px._throttle()          # 第一次不睡
    clock["now"] += 0.5
    px._throttle()          # 隔了 0.5 秒 → 补 1.5 秒
    assert slept and abs(slept[-1] - 1.5) < 0.01
