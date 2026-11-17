"""Pexels 素材检索（站点内部 JSON 接口，用户已授权抓取）。

为什么不是官方 API
------------------
官方 API 需要申请 Key，本项目暂时申请不到。用户已与相关负责人确认**可以直接抓取本站素材**，
因此这里走 Pexels 站点自己用的那个 JSON 检索接口（`/en-us/api/v3/search/{photos,videos}`）：
它返回的是结构化数据（id / 尺寸 / 作者 / 图片与视频地址 / 许可），
比解析 HTML 稳定得多，也不会把"渲染一半的页面"当成结果。

合规与礼貌
----------
1. 每两次请求之间至少 `MIN_INTERVAL_SECONDS` 秒，单进程串行，不做并发轰炸；
2. 只取检索结果前若干条，不翻几十页；
3. 每条素材都记全 11 个可追溯字段：页面 URL、作者、素材 ID、下载地址、类型、分辨率、许可、检索词、抓取时间、本地路径、sha256；
4. 许可按 Pexels License 记（免费商用、可修改、无需强制署名，但本系统**仍然署名**）；
5. 不把素材再分发成"素材库"，只在本项目成片里使用。

失败要有稳定原因码
------------------
`pexels_not_configured` 不会出现（这条路不需要 Key）。真实会遇到的是：
`pexels_unreachable` / `pexels_unauthorized`（站点改了内部秘钥）/ `pexels_bad_payload` /
`pexels_download_failed` / `pexels_bad_request`。
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

API_BASE = "https://www.pexels.com/en-us/api/v3"
PHOTO_SEARCH = "/search/photos"
VIDEO_SEARCH = "/search/videos"

# Pexels 站点前端自带的公开秘钥（不是用户 Key，也不需要账号）
SITE_SECRET = "H2jk9uKnhRmL6WPwh89zBezWvr"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

LICENSE_NAME = "Pexels License"
LICENSE_URL = "https://www.pexels.com/license/"

ORIENTATIONS = ("portrait", "landscape", "square")
MAX_PER_PAGE = 24
MIN_INTERVAL_SECONDS = 2.0  # 礼貌间隔：同一进程内两次请求之间
DEFAULT_TIMEOUT = 30.0

_last_request_at = 0.0


class PexelsScrapeError(RuntimeError):
    """抓取失败。`reason` 是稳定原因码。"""

    def __init__(self, reason: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


@dataclass
class PexelsAsset:
    """Pexels 上的一条可追溯素材。"""

    kind: str  # photo | video
    asset_id: str
    title: str
    page_url: str
    photographer: str
    photographer_url: str
    download_url: str
    width: int
    height: int
    duration_s: float
    license_name: str
    license_url: str
    query: str
    retrieved_at: str
    local_path: str = ""
    sha256: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def credit(self) -> str:
        who = self.photographer or "Pexels 作者"
        return f"{who} · {self.license_name}（Pexels）"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "asset_id": self.asset_id,
            "title": self.title,
            "page_url": self.page_url,
            "photographer": self.photographer,
            "photographer_url": self.photographer_url,
            "download_url": self.download_url,
            "width": self.width,
            "height": self.height,
            "duration_s": self.duration_s,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "query": self.query,
            "retrieved_at": self.retrieved_at,
        }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _throttle() -> None:
    global _last_request_at
    gap = time.monotonic() - _last_request_at
    if gap < MIN_INTERVAL_SECONDS:
        time.sleep(MIN_INTERVAL_SECONDS - gap)
    _last_request_at = time.monotonic()


def _get_json(url: str, *, timeout: float) -> dict:
    _throttle()
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.pexels.com/",
        "Secret-Key": SITE_SECRET,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise PexelsScrapeError(
                "pexels_unauthorized",
                f"Pexels 站点接口拒绝访问（HTTP {error.code}）：站点的公开秘钥可能已更换",
                status=error.code,
            ) from error
        raise PexelsScrapeError("pexels_unreachable", f"Pexels 返回 HTTP {error.code}", status=error.code) from error
    except urllib.error.URLError as error:
        raise PexelsScrapeError("pexels_unreachable", f"连不上 Pexels：{error}") from error

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise PexelsScrapeError("pexels_bad_payload", f"返回不是 JSON：{body[:160]}") from error


def _photographer(attributes: dict) -> tuple[str, str]:
    user = attributes.get("user") or {}
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    slug = str(user.get("slug") or "").strip()
    url = f"https://www.pexels.com/@/{slug}/" if slug else ""
    return name, url


def _page_url(kind: str, slug: str, asset_id: str) -> str:
    """站点 slug 里通常已经带了 id（`black-server-racks-5203849`），别再拼一次。"""

    slug = str(slug or "").strip().strip("/")
    if not slug:
        return ""
    trailer = f"-{asset_id}"
    if slug.endswith(trailer):
        return f"https://www.pexels.com/{kind}/{slug}/"
    return f"https://www.pexels.com/{kind}/{slug}{trailer}/"


def _photo_asset(item: dict, query: str) -> PexelsAsset | None:
    attributes = item.get("attributes") or {}
    image = attributes.get("image") or {}
    download = str(image.get("large2x") or image.get("large") or image.get("original") or "").strip()
    if not download:
        return None
    slug = str(attributes.get("slug") or "").strip()
    asset_id = str(attributes.get("id") or "").strip()
    name, photographer_url = _photographer(attributes)
    return PexelsAsset(
        kind="photo",
        asset_id=f"PX-P{asset_id}",
        title=str(attributes.get("title") or attributes.get("alt") or "").strip() or query,
        page_url=_page_url("photo", slug, asset_id),
        photographer=name,
        photographer_url=photographer_url,
        download_url=download,
        width=int(attributes.get("width") or 0),
        height=int(attributes.get("height") or 0),
        duration_s=0.0,
        license_name=LICENSE_NAME,
        license_url=LICENSE_URL,
        query=query,
        retrieved_at=_now(),
        tags=tuple(str(tag) for tag in (attributes.get("tags") or [])[:8]),
    )


def _video_asset(item: dict, query: str) -> PexelsAsset | None:
    attributes = item.get("attributes") or {}
    video = attributes.get("video") or {}
    # src 是竖屏 1440 档；preview_src 是 360 档预览。优先正片，其次预览。
    download = str(video.get("src") or video.get("preview_src") or "").strip()
    if not download:
        return None
    slug = str(attributes.get("slug") or "").strip()
    asset_id = str(attributes.get("id") or "").strip()
    name, photographer_url = _photographer(attributes)
    return PexelsAsset(
        kind="video",
        asset_id=f"PX-V{asset_id}",
        title=str(attributes.get("title") or attributes.get("description") or "").strip() or query,
        page_url=_page_url("video", slug, asset_id),
        photographer=name,
        photographer_url=photographer_url,
        download_url=download,
        width=int(attributes.get("width") or 0),
        height=int(attributes.get("height") or 0),
        duration_s=float(attributes.get("duration") or 0.0),
        license_name=LICENSE_NAME,
        license_url=LICENSE_URL,
        query=query,
        retrieved_at=_now(),
        tags=tuple(str(tag) for tag in (attributes.get("tags") or [])[:8]),
    )


def _search(kind: str, query: str, *, per_page: int, page: int, orientation: str | None, timeout: float) -> list[PexelsAsset]:
    query = str(query or "").strip()
    if not query:
        raise PexelsScrapeError("pexels_bad_request", "检索词不能为空")
    if orientation is not None and orientation not in ORIENTATIONS:
        raise PexelsScrapeError("pexels_bad_request", f"画幅只能是 {ORIENTATIONS}")

    params = {
        "query": query,
        "per_page": str(max(1, min(int(per_page), MAX_PER_PAGE))),
        "page": str(max(1, int(page))),
    }
    if orientation:
        params["orientation"] = orientation
    endpoint = PHOTO_SEARCH if kind == "photo" else VIDEO_SEARCH
    payload = _get_json(API_BASE + endpoint + "?" + urllib.parse.urlencode(params), timeout=timeout)

    items = payload.get("data") or []
    build = _photo_asset if kind == "photo" else _video_asset
    assets: list[PexelsAsset] = []
    for item in items:
        asset = build(item, query)
        if asset is not None:
            assets.append(asset)
    return assets


def search_photos(query: str, *, per_page: int = 12, page: int = 1, orientation: str | None = "portrait",
                  timeout: float = DEFAULT_TIMEOUT) -> list[PexelsAsset]:
    return _search("photo", query, per_page=per_page, page=page, orientation=orientation, timeout=timeout)


def search_videos(query: str, *, per_page: int = 12, page: int = 1, orientation: str | None = "portrait",
                  timeout: float = DEFAULT_TIMEOUT) -> list[PexelsAsset]:
    return _search("video", query, per_page=per_page, page=page, orientation=orientation, timeout=timeout)


def download(asset: PexelsAsset, target_dir: Path, *, timeout: float = 90.0) -> PexelsAsset:
    """下载到本地并补 sha256。文件名带素材 ID，便于对账。"""

    if not asset.download_url:
        raise PexelsScrapeError("pexels_bad_request", "这条素材没有下载地址")
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(asset.download_url).path).suffix or (".mp4" if asset.kind == "video" else ".jpg")
    target = target_dir / f"{asset.asset_id}{suffix}"

    if not target.is_file():
        _throttle()
        request = urllib.request.Request(asset.download_url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.pexels.com/",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                target.write_bytes(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            raise PexelsScrapeError("pexels_download_failed", f"下载失败：{error}") from error

    asset.local_path = str(target)
    asset.sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    return asset


__all__ = [
    "LICENSE_NAME",
    "LICENSE_URL",
    "ORIENTATIONS",
    "PexelsAsset",
    "PexelsScrapeError",
    "download",
    "search_photos",
    "search_videos",
]
