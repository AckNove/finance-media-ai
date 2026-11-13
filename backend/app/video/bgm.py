"""背景音乐库：曲目登记 + 授权检查。

为什么要有这个模块：自动配乐最大的风险不是"不好听"，是**授权不明**。
一条商用视频用了来源不清的 BGM，比没有背景音乐严重得多。

所以这里的规矩很硬：

1. 曲目必须在 ``content/assets/bgm/library.yaml`` 里登记，字段缺一不可；
2. 授权不在白名单里的曲目，``require_usable`` 直接拒绝，渲染流程不许"先用了再说"；
3. 需要署名的曲目必须写 ``attribution``，否则同样拒绝；
4. 曲库为空是正常状态——默认走本地合成配乐（``synthesize_score``，无版权素材），
   而不是随便找一首歌填进去。

本模块不联网、不下载任何音频。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from backend.app.core.config import PROJECT_ROOT

#: 默认曲库位置
DEFAULT_LIBRARY = PROJECT_ROOT / "content" / "assets" / "bgm" / "library.yaml"

#: 可以直接商用的授权标识（大小写不敏感）。**不在此列的，一律拒绝。**
COMMERCIAL_LICENCES: frozenset[str] = frozenset(
    {
        "cc0",
        "public-domain",
        "public_domain",
        "cc-by",
        "cc-by-4.0",
        "cc-by-sa",
        "royalty-free",
        "purchased",
        "own-work",
    }
)

#: 这些授权允许使用，但必须署名
ATTRIBUTION_REQUIRED: frozenset[str] = frozenset({"cc-by", "cc-by-4.0", "cc-by-sa"})

#: 登记表里每条曲目必须有的字段
REQUIRED_FIELDS: tuple[str, ...] = ("id", "title", "file", "licence")


class BgmError(RuntimeError):
    """曲库或曲目不可用。``reason`` 是稳定码，方便前端/脚本判断。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class BgmTrack:
    """一首已登记的曲目。"""

    id: str
    title: str
    file: str
    licence: str
    attribution: str = ""
    source_url: str = ""
    duration_seconds: float | None = None
    mood: str = ""
    notes: str = ""

    @property
    def licence_key(self) -> str:
        return self.licence.strip().lower()

    @property
    def commercial_ok(self) -> bool:
        return self.licence_key in COMMERCIAL_LICENCES

    @property
    def needs_attribution(self) -> bool:
        return self.licence_key in ATTRIBUTION_REQUIRED

    def path(self, root: Path | None = None) -> Path:
        candidate = Path(self.file)
        if candidate.is_absolute():
            return candidate
        base = (root or DEFAULT_LIBRARY.parent)
        return (base / candidate).resolve()

    def as_dict(self, root: Path | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "file": self.file,
            "path": str(self.path(root)),
            "exists": self.path(root).is_file(),
            "licence": self.licence,
            "licence_key": self.licence_key,
            "commercial_ok": self.commercial_ok,
            "needs_attribution": self.needs_attribution,
            "attribution": self.attribution,
            "source_url": self.source_url,
            "duration_seconds": self.duration_seconds,
            "mood": self.mood,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class BgmLibrary:
    """整本曲库。``tracks`` 可以为空——那表示"还没有可用的曲目"。"""

    root: Path
    tracks: tuple[BgmTrack, ...] = ()
    note: str = ""

    def get(self, track_id: str) -> BgmTrack | None:
        for track in self.tracks:
            if track.id == track_id:
                return track
        return None

    def usable(self) -> list[BgmTrack]:
        return [track for track in self.tracks if track.commercial_ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "count": len(self.tracks),
            "note": self.note or self.default_note(),
            "items": [track.as_dict(self.root) for track in self.tracks],
        }

    def default_note(self) -> str:
        if not self.tracks:
            return (
                "曲库是空的。这不是故障：默认配乐走本地合成（synthesize_score，"
                "不含版权素材）。要加曲目就往 library.yaml 里登记，并把授权写清楚。"
            )
        return "只列已登记的曲目；授权不明的一律不许用。"


def _track_of(raw: Mapping[str, Any], *, index: int) -> BgmTrack:
    missing = [name for name in REQUIRED_FIELDS if not str(raw.get(name) or "").strip()]
    if missing:
        raise BgmError(
            "missing_field",
            f"第 {index} 条曲目缺少字段：{'、'.join(missing)}（登记表里每一条都必须写全）",
        )
    duration = raw.get("duration_seconds")
    return BgmTrack(
        id=str(raw["id"]).strip(),
        title=str(raw["title"]).strip(),
        file=str(raw["file"]).strip(),
        licence=str(raw["licence"]).strip(),
        attribution=str(raw.get("attribution") or "").strip(),
        source_url=str(raw.get("source_url") or "").strip(),
        duration_seconds=float(duration) if duration not in (None, "") else None,
        mood=str(raw.get("mood") or "").strip(),
        notes=str(raw.get("notes") or "").strip(),
    )


def load_library(path: Path | str | None = None) -> BgmLibrary:
    """读曲库。文件不存在等价于空曲库（不是错误）。"""
    target = Path(path) if path is not None else DEFAULT_LIBRARY
    if not target.is_file():
        return BgmLibrary(root=target.parent)
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 只有写坏 YAML 才走
        raise BgmError("unreadable", f"曲库 YAML 解析失败：{exc}") from exc
    items = payload.get("tracks") or []
    if not isinstance(items, list):
        raise BgmError("unreadable", "曲库的 tracks 必须是列表")
    tracks: list[BgmTrack] = []
    seen: set[str] = set()
    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, Mapping):
            raise BgmError("unreadable", f"第 {index} 条曲目不是键值对")
        track = _track_of(raw, index=index)
        if track.id in seen:
            raise BgmError("duplicate_id", f"曲目 id 重复：{track.id}")
        seen.add(track.id)
        tracks.append(track)
    return BgmLibrary(root=target.parent, tracks=tuple(tracks), note=str(payload.get("note") or ""))


def require_usable(track: BgmTrack, *, root: Path | None = None) -> BgmTrack:
    """确认这首曲目真的能用，否则抛 ``BgmError``（调用方不许吞掉）。"""
    if not track.commercial_ok:
        raise BgmError(
            "licence_not_commercial",
            f"曲目 {track.id} 的授权是「{track.licence}」，不在可商用白名单里，不能用于发布成片",
        )
    if track.needs_attribution and not track.attribution:
        raise BgmError(
            "attribution_missing",
            f"曲目 {track.id} 的授权（{track.licence}）要求署名，但登记表里没有写 attribution",
        )
    path = track.path(root)
    if not path.is_file():
        raise BgmError("missing_file", f"曲目 {track.id} 登记的音频文件不存在：{path}")
    if path.suffix.lower() not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        raise BgmError(
            "unsupported_format",
            f"曲目 {track.id} 的格式 {path.suffix} 不是 ffmpeg 好处理的音频格式",
        )
    return track


def check_library(library: BgmLibrary | None = None) -> list[dict[str, Any]]:
    """逐条体检：能用 / 为什么不能用。只读，不改任何文件。"""
    lib = library or load_library()
    report: list[dict[str, Any]] = []
    for track in lib.tracks:
        try:
            require_usable(track, root=lib.root)
        except BgmError as error:
            report.append({**track.as_dict(lib.root), "usable": False, "reason": error.reason, "message": error.message})
        else:
            report.append({**track.as_dict(lib.root), "usable": True, "reason": "", "message": ""})
    return report


def pick(track_id: str, *, library: BgmLibrary | None = None) -> BgmTrack:
    """按 id 取一首**可用**曲目；取不到或不可用就抛 ``BgmError``。"""
    lib = library or load_library()
    track = lib.get(track_id)
    if track is None:
        known = "、".join(item.id for item in lib.tracks) or "（曲库为空）"
        raise BgmError("unknown_track", f"曲库里没有 {track_id}；已登记：{known}")
    return require_usable(track, root=lib.root)


def licence_summary(tracks: Iterable[BgmTrack]) -> dict[str, int]:
    """按授权统计条数（给曲库页做概览）。"""
    counts: dict[str, int] = {}
    for track in tracks:
        counts[track.licence_key or "unknown"] = counts.get(track.licence_key or "unknown", 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "ATTRIBUTION_REQUIRED",
    "BgmError",
    "BgmLibrary",
    "BgmTrack",
    "COMMERCIAL_LICENCES",
    "DEFAULT_LIBRARY",
    "REQUIRED_FIELDS",
    "check_library",
    "licence_summary",
    "load_library",
    "pick",
    "require_usable",
]
