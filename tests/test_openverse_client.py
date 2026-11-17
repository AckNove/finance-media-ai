"""Openverse 适配器与"补素材闸门"的测试。

红线：不联网。`search_images()` 支持注入 `fetch`，测试用假响应验证
"许可过滤 / 字段完整性 / 5xx 重试 / 坏 JSON"；`fill_material_gaps.fill()`
用 monkeypatch 顶掉网络层，验证"下载成功 ≠ 能用"这条闸门真的会把不相关的素材退掉。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from backend.app.capabilities.media import openverse as ov
from backend.app.video import script_materials as sm
from backend.scripts import fill_material_gaps as fill_mod


def _payload(*items: dict) -> dict:
    return {"result_count": len(items), "results": list(items)}


def _item(**overrides) -> dict:
    base = {
        "id": "abc",
        "title": "electrical substation",
        "foreign_landing_url": "https://example.org/item/1",
        "creator": "ugod",
        "license": "by",
        "license_version": "2.0",
        "license_url": "https://creativecommons.org/licenses/by/2.0/",
        "source": "flickr",
        "width": 691,
        "height": 1024,
        "url": "https://cdn.example.org/a.jpg",
    }
    base.update(overrides)
    return base


def test_search_maps_all_traceable_fields() -> None:
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout: float) -> dict:
        calls.append(url)
        return _payload(_item())

    assets = ov.search_images("substation", fetch=fake_fetch)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.license_name == "CC BY 2.0"
    assert asset.creator == "ugod"
    assert asset.page_url.endswith("/item/1")
    assert asset.download_url == "https://cdn.example.org/a.jpg"
    assert asset.sha256 == ""
    assert "Openverse" in asset.credit
    # 请求里必须带许可白名单，且只允许 cc0/pdm/by
    assert "license=cc0%2Cpdm%2Cby" in calls[0]
    assert "mature=false" in calls[0]


@pytest.mark.parametrize("bad_license", ["by-sa", "by-nc", "by-nd", "gfdl", ""])
def test_licenses_that_are_not_clean_get_dropped(bad_license: str) -> None:
    def fake_fetch(url: str, *, timeout: float) -> dict:
        return _payload(_item(license=bad_license))

    assert ov.search_images("x", fetch=fake_fetch) == []


def test_items_without_download_url_are_skipped() -> None:
    def fake_fetch(url: str, *, timeout: float) -> dict:
        return _payload(_item(url="", thumbnail=""))

    assert ov.search_images("x", fetch=fake_fetch) == []


def test_portrait_query_asks_for_tall_aspect() -> None:
    seen: list[str] = []

    def fake_fetch(url: str, *, timeout: float) -> dict:
        seen.append(url)
        return _payload()

    ov.search_images("x", orientation="tall", fetch=fake_fetch)
    assert "aspect_ratio=tall" in seen[0]


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ov.OpenverseError) as error:
        ov.search_images("   ")
    assert error.value.reason == "openverse_bad_request"


def test_504_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.openverse.org", 504, "Gateway Timeout", {}, None)

    monkeypatch.setattr(ov.urllib.request, "urlopen", boom)
    monkeypatch.setattr(ov.time, "sleep", lambda *_: None)
    with pytest.raises(ov.OpenverseError) as error:
        ov.search_images("x")
    assert error.value.reason == "openverse_unavailable"
    assert error.value.status == 504


def test_bad_json_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b"<html>not json</html>"

    monkeypatch.setattr(ov.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    with pytest.raises(ov.OpenverseError) as error:
        ov.search_images("x")
    assert error.value.reason == "openverse_bad_payload"


def test_download_fills_local_path_and_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b"fake-image-bytes"

    monkeypatch.setattr(ov.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    asset = ov.OpenverseAsset(
        asset_id="OV-test",
        title="substation",
        page_url="https://example.org/1",
        creator="ugod",
        license_name="CC BY 2.0",
        license_url="",
        source="flickr",
        width=800,
        height=1200,
        download_url="https://cdn.example.org/a.jpg",
        query="substation",
        retrieved_at="2026-11-17T00:00:00+00:00",
    )
    saved = ov.download(asset, tmp_path)
    assert Path(saved.local_path).is_file()
    assert len(saved.sha256) == 64


def test_gap_fill_rejects_an_irrelevant_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """搜到但用不上：必须退掉文件、不写登记表、并记下原因。"""

    job = tmp_path / "job"
    (job / "storyboard").mkdir(parents=True)
    (job / "assets" / "real").mkdir(parents=True)
    register = job / "assets" / "real" / "source_register.csv"
    with register.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fill_mod.REGISTER_COLUMNS)
        writer.writeheader()

    plan = {
        "shots": [
            {
                "index": 8,
                "text": "本片为合规资料整理，不构成投资建议。",
                "seconds": 5.0,
                "intent": {"index": 8, "kind": "real_image", "queries": ["公告 文件"], "visual_goal": "合规声明"},
                "asset_id": "",
                "score": 0.0,
                "fallback": "manual",
            }
        ]
    }
    (job / "storyboard" / "materials_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def fake_search(query: str, *, limit: int = 6, timeout: float = 30.0, orientation: str | None = "tall"):
        return [
            ov.OpenverseAsset(
                asset_id="OV-bad",
                title="冯正虎向中国政府转呈日本官方文件",
                page_url="https://example.org/bad",
                creator="fzhenghu",
                license_name="CC BY 2.0",
                license_url="",
                source="flickr",
                width=800,
                height=1200,
                download_url="https://cdn.example.org/bad.jpg",
                query=query,
                retrieved_at="2026-11-17T00:00:00+00:00",
            )
        ]

    staged = job / "assets" / "real" / "_staging_online"
    staged.mkdir(parents=True)
    staged_file = staged / "bad.jpg"
    staged_file.write_bytes(b"x")

    def fake_download(asset: ov.OpenverseAsset, target_dir: Path, *, timeout: float = 60.0) -> ov.OpenverseAsset:
        asset.local_path = str(staged_file)
        asset.sha256 = "0" * 64
        return asset

    monkeypatch.setattr(fill_mod.ov, "search_images", fake_search)
    monkeypatch.setattr(fill_mod.ov, "download", fake_download)

    result = fill_mod.fill(job, per_shot=2, limit_shots=1)
    assert result["added"] == []
    assert any(item["reason"] == "rejected_low_score" for item in result["failures"])
    assert not staged_file.exists(), "被拒的素材必须从磁盘删掉"
    rows = list(csv.DictReader(register.read_text(encoding="utf-8").splitlines()))
    assert rows == [], "被拒的素材不能进登记表"


def test_score_asset_prefers_matching_tags() -> None:
    line = sm.ScriptLine(index=1, text="发电量下滑，电费收入却还能收上来", seconds=4.0)
    intent = sm.VisualIntent(index=1, kind="real_video", visual_goal="电厂现场", queries=["冷却塔", "电厂"], source="rule")
    matching = sm.LibraryAsset(asset_id="A", kind="video", title="电力 冷却塔", tags=("冷却塔", "电厂"), local_path="/x.mp4")
    unrelated = sm.LibraryAsset(asset_id="B", kind="video", title="冯正虎 文件", tags=("文件", "公告"), local_path="/y.mp4")
    assert sm.score_asset(intent, line, matching) > sm.score_asset(intent, line, unrelated)
