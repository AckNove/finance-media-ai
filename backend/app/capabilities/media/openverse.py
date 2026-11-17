"""Openverse 素材检索（无需 API Key 的合法公开素材源）。

为什么用它
----------
Pexels 的官方 API 需要 Key；没有 Key 时**不能去抓 Pexels 的网页**绕开限制
（违反他们的服务条款，而且抓回来的素材拿不到可核验的授权字段）。
Openverse 是 WordPress 基金会维护的开放素材聚合服务，**公开 API、无需 Key**，
每条结果自带 `license` / `license_version` / `creator` / `foreign_landing_url`，
正好满足"每个素材必须可追溯授权"的要求。

许可策略（比源站更严）
----------------------
只收三类：`cc0`、`pdm`（公有领域标记）、`by`（CC BY，需署名）。
**明确排除** `by-sa`（传染性相同方式共享，商用成片会被牵连）、`by-nc`、`by-nd`。
宁可少收，也不要让一条授权不明的素材混进成片。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

API_BASE = "https://api.openverse.org/v1"
IMAGE_SEARCH = "/images/"
AUDIO_SEARCH = "/audio/"

# 只收可商用且不传染的许可
ALLOWED_LICENSES = ("cc0", "pdm", "by")
LICENSE_LABEL = {
    "cc0": "CC0",
    "pdm": "Public Domain Mark",
    "by": "CC BY",
}
LICENSE_URL = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "pdm": "https://creativecommons.org/publicdomain/mark/1.0/",
}

DEFAULT_TIMEOUT = 30.0
RETRY_STATUSES = {429, 500, 502, 503, 504}


class OpenverseError(RuntimeError):
    """检索失败。`reason` 是稳定原因码，界面与日志靠它说话。"""

    def __init__(self, reason: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


@dataclass
class OpenverseAsset:
    """一条可追溯的公开素材。字段对齐 Pexels 那条链的 11 项要求。"""

    asset_id: str
    title: str
    page_url: str
    creator: str
    license_name: str
    license_url: str
    source: str
    width: int
    height: int
    download_url: str
    query: str
    retrieved_at: str
    local_path: str = ""
    sha256: str = ""
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def credit(self) -> str:
        return f"{self.creator} · {self.license_name}（Openverse）" if self.creator else f"{self.license_name}（Openverse）"

    def as_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "title": self.title,
            "page_url": self.page_url,
            "creator": self.creator,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "download_url": self.download_url,
            "query": self.query,
            "retrieved_at": self.retrieved_at,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "note": self.note,
        }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _fetch_json(url: str, *, timeout: float, attempts: int = 3) -> dict:
    last: Exception | None = None
    for index in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AK-finance-media/1.0 (research; local tool)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError as error:
                raise OpenverseError("openverse_bad_payload", f"返回不是 JSON：{body[:160]}") from error
        except urllib.error.HTTPError as error:
            if error.code in RETRY_STATUSES and index + 1 < attempts:
                time.sleep(1.5 * (index + 1))
                last = error
                continue
            raise OpenverseError(
                "openverse_unavailable",
                f"Openverse 返回 HTTP {error.code}（这是公共聚合服务，偶发 5xx，可稍后重试）",
                status=error.code,
            ) from error
        except urllib.error.URLError as error:
            if index + 1 < attempts:
                time.sleep(1.5 * (index + 1))
                last = error
                continue
            raise OpenverseError("openverse_unreachable", f"连不上 Openverse：{error}") from error
    raise OpenverseError("openverse_unreachable", f"连不上 Openverse：{last}")


def _pick_download(item: dict) -> str:
    for key in ("url", "thumbnail"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def search_images(
    query: str,
    *,
    limit: int = 12,
    timeout: float = DEFAULT_TIMEOUT,
    orientation: str | None = "tall",
    fetch: object | None = None,
) -> list[OpenverseAsset]:
    """按关键词搜公开图片。只返回**许可合格**的条目。"""

    query = str(query or "").strip()
    if not query:
        raise OpenverseError("openverse_bad_request", "检索词不能为空")

    params = {
        "q": query,
        "license": ",".join(ALLOWED_LICENSES),
        "page_size": str(max(1, min(int(limit), 50))),
        "mature": "false",
    }
    if orientation in {"tall", "wide", "square"}:
        params["aspect_ratio"] = {"tall": "tall", "wide": "wide", "square": "square"}[orientation]
    url = API_BASE + IMAGE_SEARCH + "?" + urllib.parse.urlencode(params)

    payload = (fetch or _fetch_json)(url, timeout=timeout) if callable(fetch) else _fetch_json(url, timeout=timeout)
    results = payload.get("results") or []

    assets: list[OpenverseAsset] = []
    stamp = _now()
    for item in results:
        license_slug = str(item.get("license") or "").lower()
        if license_slug not in ALLOWED_LICENSES:
            continue
        download = _pick_download(item)
        if not download:
            continue
        version = str(item.get("license_version") or "").strip()
        license_name = LICENSE_LABEL.get(license_slug, license_slug.upper())
        if version:
            license_name = f"{license_name} {version}"
        url_license = str(item.get("license_url") or "").strip() or LICENSE_URL.get(license_slug, "")
        source = str(item.get("source") or "").strip()
        creator = str(item.get("creator") or "").strip()
        asset = OpenverseAsset(
            asset_id=f"OV-{hashlib.sha1(download.encode('utf-8')).hexdigest()[:10]}",
            title=str(item.get("title") or "").strip() or query,
            page_url=str(item.get("foreign_landing_url") or "").strip(),
            creator=creator,
            license_name=license_name,
            license_url=url_license,
            source=source,
            width=int(item.get("width") or 0),
            height=int(item.get("height") or 0),
            download_url=download,
            query=query,
            retrieved_at=stamp,
        )
        assets.append(asset)
    return assets


def download(asset: OpenverseAsset, target_dir: Path, *, timeout: float = 60.0) -> OpenverseAsset:
    """下载到本地并补 sha256。文件名带许可与人名，便于登记表追溯。"""

    if not asset.download_url:
        raise OpenverseError("openverse_bad_request", "这条素材没有下载地址")
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(asset.download_url).path).suffix or ".jpg"
    safe_title = "".join(ch for ch in asset.title if ch.isalnum() or ch in "-_")[:40] or "asset"
    target = target_dir / f"{asset.asset_id}_{safe_title}{suffix}"

    if not target.is_file():
        request = urllib.request.Request(asset.download_url, headers={"User-Agent": "AK-finance-media/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            raise OpenverseError("openverse_download_failed", f"下载失败：{error}") from error
        target.write_bytes(data)

    asset.local_path = str(target)
    asset.sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    return asset


__all__ = [
    "ALLOWED_LICENSES",
    "OpenverseAsset",
    "OpenverseError",
    "download",
    "search_images",
]
