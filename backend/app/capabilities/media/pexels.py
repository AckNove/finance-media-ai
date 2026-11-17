"""Pexels 素材检索（官方 API）。

为什么单独一个模块
------------------
V2.0 任务书把"根据口播稿自动找素材"列为本轮重点功能，并且明确了两条底线：

1. **优先用 Pexels 官方 API**，遵守官方规则（页面 URL、摄影师、素材 ID、下载地址、
   类型、分辨率、授权来源都要落到登记表里）；
2. **没有 API Key 时标 BLOCKED**，不许抓站绕过限制，也不许假装素材搜索已经自动化。

所以这里的写法是：真的会发 HTTPS 请求，但**没有配置就一个请求都不发**，
抛 `pexels_not_configured` 并让上层如实汇报。密钥只从环境变量 / 仓库根 `.env` 读，
不落日志、不进返回值、不写进任何登记表。

官方文档：https://www.pexels.com/zh-cn/api/documentation/
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.app.core.config import dotenv_value

API_BASE = "https://api.pexels.com"
PHOTO_SEARCH = "/v1/search"
VIDEO_SEARCH = "/videos/search"

PHOTO_LICENSE = "Pexels License"
PHOTO_LICENSE_URL = "https://www.pexels.com/license/"
DEFAULT_PER_PAGE = 15
MAX_PER_PAGE = 80

# 允许的画幅：竖屏视频优先
ORIENTATIONS = ("portrait", "landscape", "square")


class PexelsError(RuntimeError):
    """把 Pexels 的失败整理成稳定原因码，方便上层照实汇报。"""

    def __init__(self, reason: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class PexelsAsset:
    """一条素材的全部可追溯信息（任务书要求记录的字段一个不少）。"""

    kind: str  # photo | video
    asset_id: str
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

    @property
    def orientation(self) -> str:
        if self.height > self.width:
            return "portrait"
        if self.width > self.height:
            return "landscape"
        return "square"

    @property
    def credit(self) -> str:
        return f"{self.photographer or 'Pexels 创作者'} · {self.license_name}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["orientation"] = self.orientation
        payload["credit"] = self.credit
        return payload


@dataclass
class PexelsStatus:
    configured: bool
    detail: str
    key_hint: str = ""  # 只有长度，永远不含密钥本身
    checked_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "detail": self.detail,
            "key_hint": self.key_hint,
            "checked_at": self.checked_at,
            "docs": "https://www.pexels.com/zh-cn/api/documentation/",
        }


def api_key() -> str:
    """从环境变量或仓库根 `.env` 取 Pexels 密钥（不打印、不回显）。"""

    raw = dotenv_value("PEXELS_API_KEY")
    return (raw or "").strip()


def status() -> PexelsStatus:
    key = api_key()
    if not key:
        return PexelsStatus(
            configured=False,
            detail=(
                "没有 PEXELS_API_KEY：素材检索处于 BLOCKED 状态。"
                "按任务书要求不做抓站绕过，可先在设置里配置官方 API Key，"
                "或走人工导入素材。"
            ),
        )
    return PexelsStatus(
        configured=True,
        detail="已配置 Pexels API Key，可通过官方 API 检索素材。",
        key_hint=f"len={len(key)}",
    )


def _require_key() -> str:
    key = api_key()
    if not key:
        raise PexelsError(
            "pexels_not_configured",
            "没有配置 PEXELS_API_KEY：素材检索暂停，不会发任何请求，也不会抓站绕过。",
        )
    return key


def _default_fetch(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 固定 https
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:  # 4xx/5xx
        if error.code == 401:
            raise PexelsError("pexels_unauthorized", "Pexels 拒绝了这次请求：API Key 无效或已失效。", status=401) from error
        if error.code == 429:
            raise PexelsError("pexels_rate_limited", "Pexels 返回 429：超出配额，稍后再试。", status=429) from error
        raise PexelsError("pexels_http_error", f"Pexels 返回 HTTP {error.code}。", status=error.code) from error
    except urllib.error.URLError as error:
        raise PexelsError("pexels_unreachable", f"连不上 Pexels：{error.reason}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise PexelsError("pexels_bad_payload", "Pexels 返回的不是合法 JSON。") from error


class PexelsClient:
    """最小可用的 Pexels 客户端：只做搜索与下载，不做别的。"""

    def __init__(
        self,
        key: str | None = None,
        *,
        timeout: float = 20.0,
        fetch: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
    ) -> None:
        self._key = key if key is not None else api_key()
        self._timeout = timeout
        self._fetch = fetch or _default_fetch

    # ---- 内部 ----

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._key:
            raise PexelsError(
                "pexels_not_configured",
                "没有配置 PEXELS_API_KEY：素材检索暂停，不会发任何请求。",
            )
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{API_BASE}{path}?{query}"
        headers = {"Authorization": self._key, "Accept": "application/json", "User-Agent": "ak-finance-media/1.0"}
        return self._fetch(url, headers, self._timeout)

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    # ---- 搜索 ----

    def search_photos(
        self,
        query: str,
        *,
        per_page: int = DEFAULT_PER_PAGE,
        orientation: str | None = "portrait",
    ) -> list[PexelsAsset]:
        if orientation and orientation not in ORIENTATIONS:
            raise PexelsError("pexels_bad_request", f"画幅只支持 {ORIENTATIONS}，收到 {orientation!r}。")
        payload = self._get(
            PHOTO_SEARCH,
            {"query": query, "per_page": min(per_page, MAX_PER_PAGE), "orientation": orientation},
        )
        photos = payload.get("photos")
        if not isinstance(photos, list):
            raise PexelsError("pexels_bad_payload", "Pexels 照片搜索结果里没有 photos 数组。")
        now = self._now()
        assets: list[PexelsAsset] = []
        for item in photos:
            try:
                assets.append(
                    PexelsAsset(
                        kind="photo",
                        asset_id=str(item["id"]),
                        page_url=str(item.get("url", "")),
                        photographer=str(item.get("photographer", "")),
                        photographer_url=str(item.get("photographer_url", "")),
                        download_url=str((item.get("src") or {}).get("large2x") or (item.get("src") or {}).get("original") or ""),
                        width=int(item.get("width") or 0),
                        height=int(item.get("height") or 0),
                        duration_s=0.0,
                        license_name=PHOTO_LICENSE,
                        license_url=PHOTO_LICENSE_URL,
                        query=query,
                        retrieved_at=now,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return [asset for asset in assets if asset.download_url]

    def search_videos(
        self,
        query: str,
        *,
        per_page: int = DEFAULT_PER_PAGE,
        orientation: str | None = "portrait",
    ) -> list[PexelsAsset]:
        if orientation and orientation not in ORIENTATIONS:
            raise PexelsError("pexels_bad_request", f"画幅只支持 {ORIENTATIONS}，收到 {orientation!r}。")
        payload = self._get(
            VIDEO_SEARCH,
            {"query": query, "per_page": min(per_page, MAX_PER_PAGE), "orientation": orientation},
        )
        videos = payload.get("videos")
        if not isinstance(videos, list):
            raise PexelsError("pexels_bad_payload", "Pexels 视频搜索结果里没有 videos 数组。")
        now = self._now()
        assets: list[PexelsAsset] = []
        for item in videos:
            files = item.get("video_files") or []
            best = _best_video_file(files)
            if best is None:
                continue
            assets.append(
                PexelsAsset(
                    kind="video",
                    asset_id=str(item.get("id", "")),
                    page_url=str(item.get("url", "")),
                    photographer=str((item.get("user") or {}).get("name", "")),
                    photographer_url=str((item.get("user") or {}).get("url", "")),
                    download_url=str(best.get("link", "")),
                    width=int(best.get("width") or 0),
                    height=int(best.get("height") or 0),
                    duration_s=float(item.get("duration") or 0.0),
                    license_name=PHOTO_LICENSE,
                    license_url=PHOTO_LICENSE_URL,
                    query=query,
                    retrieved_at=now,
                )
            )
        return [asset for asset in assets if asset.download_url]

    # ---- 下载 ----

    def download(self, asset: PexelsAsset, dest_dir: Path, *, suffix: str | None = None) -> PexelsAsset:
        """把素材落到本地并补上 sha256（登记表要这个字段）。"""

        if not asset.download_url:
            raise PexelsError("pexels_bad_request", "这条素材没有下载地址。")
        dest_dir.mkdir(parents=True, exist_ok=True)
        name_suffix = suffix or (".mp4" if asset.kind == "video" else ".jpg")
        target = dest_dir / f"pexels_{asset.kind}_{asset.asset_id}{name_suffix}"
        request = urllib.request.Request(asset.download_url, headers={"User-Agent": "ak-finance-media/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 官方 CDN
                data = response.read()
        except urllib.error.HTTPError as error:
            raise PexelsError("pexels_download_failed", f"下载素材失败：HTTP {error.code}。", status=error.code) from error
        except urllib.error.URLError as error:
            raise PexelsError("pexels_download_failed", f"下载素材失败：{error.reason}") from error
        target.write_bytes(data)
        return PexelsAsset(
            **{
                **asdict(asset),
                "local_path": str(target),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )


def _best_video_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """官方会返回多档清晰度，挑"竖屏 + 高度不超过 1920"里最大的那一档。"""

    def score(item: dict[str, Any]) -> tuple[int, int]:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        portrait = 1 if height >= width else 0
        fits = 1 if height <= 1920 else 0
        return (portrait * 2 + fits, height)

    usable = [item for item in files if item.get("link")]
    if not usable:
        return None
    return max(usable, key=score)


def summarise(assets: list[PexelsAsset]) -> dict[str, Any]:
    """给上层看的简报：数量、画幅分布、要不要署名。"""

    by_kind: dict[str, int] = {}
    for asset in assets:
        by_kind[asset.kind] = by_kind.get(asset.kind, 0) + 1
    return {
        "total": len(assets),
        "by_kind": by_kind,
        "portrait": sum(1 for asset in assets if asset.orientation == "portrait"),
        "needs_credit": len(assets),
        "license": PHOTO_LICENSE,
        "license_url": PHOTO_LICENSE_URL,
    }
