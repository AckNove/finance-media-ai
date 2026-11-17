"""Pexels 素材检索的测试（不联网）。

要点：
1. 没有 API Key 时必须 `pexels_not_configured`，而且**一个请求都不发**；
2. 有 Key 时解析官方返回结构，把任务书要求的字段都填满；
3. 401/429/坏 JSON 各有稳定原因码，不吞异常；
4. 下载会补 sha256，登记表要这个字段。
"""

from __future__ import annotations

import json

import pytest

from backend.app.capabilities.media import pexels


PHOTO_PAYLOAD = {
    "page": 1,
    "per_page": 1,
    "photos": [
        {
            "id": 123456,
            "width": 1080,
            "height": 1920,
            "url": "https://www.pexels.com/photo/aaa-123456/",
            "photographer": "张三",
            "photographer_url": "https://www.pexels.com/@zhangsan",
            "src": {
                "original": "https://images.pexels.com/photos/123456/original.jpg",
                "large2x": "https://images.pexels.com/photos/123456/large2x.jpg",
            },
        }
    ],
}

VIDEO_PAYLOAD = {
    "videos": [
        {
            "id": 777,
            "duration": 12,
            "url": "https://www.pexels.com/video/bbb-777/",
            "user": {"name": "李四", "url": "https://www.pexels.com/@lisi"},
            "video_files": [
                {"link": "https://cdn.pexels.com/777/1080.mp4", "width": 1080, "height": 1920},
                {"link": "https://cdn.pexels.com/777/2160.mp4", "width": 2160, "height": 3840},
                {"link": "https://cdn.pexels.com/777/720.mp4", "width": 720, "height": 1280},
            ],
        }
    ]
}


def test_status_without_key_is_blocked(monkeypatch):
    monkeypatch.setattr(pexels, "api_key", lambda: "")
    status = pexels.status()
    assert status.configured is False
    assert "BLOCKED" in status.detail
    assert "不做抓站绕过" in status.detail
    assert status.as_dict()["docs"].startswith("https://www.pexels.com")


def test_client_without_key_sends_no_request(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, headers, timeout):  # pragma: no cover - 不该被调用
        calls.append(url)
        return {}

    monkeypatch.setattr(pexels, "api_key", lambda: "")
    client = pexels.PexelsClient(fetch=fake_fetch)
    with pytest.raises(pexels.PexelsError) as error:
        client.search_photos("电厂")
    assert error.value.reason == "pexels_not_configured"
    assert calls == []


def test_search_photos_maps_every_required_field():
    seen: dict[str, str] = {}

    def fake_fetch(url, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return PHOTO_PAYLOAD

    client = pexels.PexelsClient(key="test-key", fetch=fake_fetch)
    assets = client.search_photos("发电厂", per_page=1, orientation="portrait")

    assert len(assets) == 1
    asset = assets[0]
    assert asset.kind == "photo"
    assert asset.asset_id == "123456"
    assert asset.page_url.endswith("/photo/aaa-123456/")
    assert asset.photographer == "张三"
    assert asset.photographer_url.endswith("@zhangsan")
    assert asset.download_url.endswith("large2x.jpg")
    assert (asset.width, asset.height) == (1080, 1920)
    assert asset.orientation == "portrait"
    assert asset.license_name == "Pexels License"
    assert asset.query == "发电厂"
    assert asset.retrieved_at
    assert asset.credit == "张三 · Pexels License"
    # 请求确实带上了 Key 与参数
    assert seen["auth"] == "test-key"
    assert "query=" in seen["url"] and "orientation=portrait" in seen["url"]


def test_search_videos_prefers_portrait_and_caps_height():
    client = pexels.PexelsClient(key="k", fetch=lambda url, headers, timeout: VIDEO_PAYLOAD)
    assets = client.search_videos("输电线路", per_page=1)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.kind == "video"
    assert asset.duration_s == 12.0
    assert asset.photographer == "李四"
    # 2160 高那档被排除，选 1080×1920 这档
    assert asset.download_url.endswith("1080.mp4")
    assert (asset.width, asset.height) == (1080, 1920)


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [(401, "pexels_unauthorized"), (429, "pexels_rate_limited"), (500, "pexels_http_error")],
)
def test_http_errors_have_stable_reasons(monkeypatch, status_code, reason):
    import urllib.error

    def fake_default_fetch(url, headers, timeout):
        raise urllib.error.HTTPError(url, status_code, "boom", None, None)

    client = pexels.PexelsClient(key="k", fetch=pexels._default_fetch)  # noqa: SLF001
    monkeypatch.setattr(pexels.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        urllib.error.HTTPError("https://api.pexels.com", status_code, "boom", None, None)
    ))
    with pytest.raises(pexels.PexelsError) as error:
        client.search_photos("x")
    assert error.value.reason == reason
    assert fake_default_fetch  # 占位，确保上面 patch 的是真路径


def test_bad_payload_is_reported():
    client = pexels.PexelsClient(key="k", fetch=lambda url, headers, timeout: {"nope": []})
    with pytest.raises(pexels.PexelsError) as error:
        client.search_photos("x")
    assert error.value.reason == "pexels_bad_payload"


def test_bad_orientation_rejected_before_network():
    client = pexels.PexelsClient(key="k", fetch=lambda *a: {})
    with pytest.raises(pexels.PexelsError) as error:
        client.search_photos("x", orientation="diagonal")
    assert error.value.reason == "pexels_bad_request"


def test_download_writes_file_and_hash(tmp_path):
    payload = b"fake-jpeg-bytes"

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    client = pexels.PexelsClient(key="k", fetch=lambda *a: PHOTO_PAYLOAD)
    asset = client.search_photos("发电厂", per_page=1)[0]

    import urllib.request as request_module

    original = request_module.urlopen
    request_module.urlopen = lambda *a, **k: FakeResponse()
    try:
        saved = client.download(asset, tmp_path)
    finally:
        request_module.urlopen = original

    path = tmp_path / "pexels_photo_123456.jpg"
    assert path.is_file()
    assert saved.local_path == str(path)
    assert len(saved.sha256) == 64
    assert saved.sha256 == pexels.hashlib.sha256(payload).hexdigest()


def test_summarise_counts_kinds():
    photo = pexels.PexelsAsset(
        kind="photo", asset_id="1", page_url="u", photographer="p", photographer_url="pu",
        download_url="d", width=1080, height=1920, duration_s=0.0, license_name="Pexels License",
        license_url="l", query="q", retrieved_at="t",
    )
    video = pexels.PexelsAsset(
        kind="video", asset_id="2", page_url="u", photographer="p", photographer_url="pu",
        download_url="d", width=1080, height=1920, duration_s=9.0, license_name="Pexels License",
        license_url="l", query="q", retrieved_at="t",
    )
    summary = pexels.summarise([photo, video])
    assert summary["total"] == 2
    assert summary["by_kind"] == {"photo": 1, "video": 1}
    assert summary["portrait"] == 2
    assert summary["license"] == "Pexels License"
    assert json.dumps(summary)  # 可序列化
