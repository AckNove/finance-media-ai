"""把一个任务目录的**分镜**跑成时间轴、渲染成片、并做技术质检。

这条链路刻意做成"引擎可替换"的三段：

1. `prepare_clips`：把分镜里声明的素材交给引擎导入（拿到帧序列描述）；
2. `build_and_save`：分镜 + 帧序列 → 时间轴文档（复用 `backend.app.media.timeline`）；
3. `render_and_qc`：交给引擎渲染，再用 ffprobe + 三类检测做技术体检。

**分镜只能由人（或模型）先写出来**：这里不生成脚本、不猜镜头，
分镜缺失就直接报 `missing_storyboard`，不拿模板文字凑数。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.app.capabilities.media.engine import MediaEngineError, VideoEngine
from backend.app.media.manifest import read_manifest
from backend.app.media.storyboard import Storyboard, StoryboardError, load_storyboard
from backend.app.media.timeline import TimelineError, build_timeline, subtitle_events

__all__ = [
    "QC_THRESHOLDS",
    "RenderError",
    "RenderReport",
    "prepare_clips",
    "probe_media",
    "render_job",
    "scan_media",
]

logger = logging.getLogger(__name__)

QC_THRESHOLDS = {
    "max_black_seconds": 0.45,
    "max_freeze_seconds": 1.5,
    "max_silence_seconds": 2.0,
    "clip_warn_db": -1.0,
}

_DURATION_RE = {
    "black": re.compile(r"black_duration:\s*([\d.]+)"),
    "freeze": re.compile(r"freeze_duration:\s*([\d.]+)"),
    "silence": re.compile(r"silence_duration:\s*([\d.]+)"),
}


class RenderError(RuntimeError):
    """渲染链路上可预期的失败：缺分镜、缺素材、引擎报错、产物不合格。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": str(self)}


@dataclass(frozen=True)
class RenderReport:
    """一次渲染的完整回执：产物、哈希、引擎调用、质检结果都写在这里。"""

    job_id: str
    stage: str
    blocked: bool = False
    blocked_reason: str = ""
    storyboard_shots: int = 0
    frames: int = 0
    fps: int = 0
    canvas: tuple[int, int] = (0, 0)
    assets: tuple[dict[str, Any], ...] = ()
    timeline_path: Path | None = None
    timeline_revision: str = ""
    output_path: Path | None = None
    output_sha256: str = ""
    output_byte_size: int = 0
    engine_calls: tuple[str, ...] = field(default=())
    qc: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.blocked and bool(self.output_path) and bool(self.qc.get("ok"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "stage": self.stage,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "storyboard_shots": self.storyboard_shots,
            "frames": self.frames,
            "fps": self.fps,
            "canvas": {"w": self.canvas[0], "h": self.canvas[1]},
            "assets": list(self.assets),
            "timeline_path": str(self.timeline_path) if self.timeline_path else "",
            "timeline_revision": self.timeline_revision,
            "output_path": str(self.output_path) if self.output_path else "",
            "output_sha256": self.output_sha256,
            "output_byte_size": self.output_byte_size,
            "engine_calls": list(self.engine_calls),
            "qc": self.qc,
            "notes": list(self.notes),
            "ok": self.ok,
        }


# ------------------------------------------------------------------ 素材


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_clips(
    storyboard: Storyboard,
    *,
    job_root: Path,
    engine: VideoEngine,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按分镜里出现的 asset_id 逐个导入素材，返回 (clips, asset_rows, 调用记录)。

    素材一律来自任务目录自己的 `assets_manifest.csv`：分镜里写了但清单里没有的
    素材直接报错，避免"用了一个来路不明的文件"。
    """
    manifest_path = job_root / "assets_manifest.csv"
    rows: dict[str, dict[str, str]] = {}
    if manifest_path.is_file():
        for row in read_manifest(manifest_path):
            rows[str(row.get("asset_id", ""))] = row
    else:
        raise RenderError("missing_manifest", f"缺少素材清单：{manifest_path}")

    needed: list[str] = []
    for shot in storyboard.shots:
        if shot.asset_type == "solid" or not shot.asset_id:
            continue
        if shot.asset_id not in needed:
            needed.append(shot.asset_id)

    clips: dict[str, dict[str, Any]] = {}
    asset_rows: list[dict[str, Any]] = []
    calls: list[str] = []
    for asset_id in needed:
        row = rows.get(asset_id)
        if row is None:
            raise RenderError("unknown_asset", f"分镜用了未登记的素材：{asset_id}")
        relative = row.get("file") or ""
        path = (job_root / relative).resolve()
        if not path.is_file():
            raise RenderError("missing_asset_file", f"{asset_id}: 文件不存在 {path}")
        result = engine.import_media(
            source=path,
            rights=row.get("license") or None,
        )
        calls.append(f"import_media:{asset_id}")
        clip = dict(result.raw.get("clip") or {})
        if not clip:
            raise RenderError("empty_clip", f"{asset_id}: 引擎没有返回帧序列")
        clips[asset_id] = clip
        asset_rows.append(
            {
                "asset_id": asset_id,
                "file": relative,
                "sha256": _sha256(path),
                "asset_class": row.get("asset_class", ""),
                "license": row.get("license", ""),
                "commercial_use": row.get("commercial_use", ""),
                "frames": int(clip.get("n") or 1),
            }
        )
    return clips, asset_rows, calls


# ------------------------------------------------------------------ 质检


def probe_media(path: Path, *, run_ffprobe: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """读容器事实：分辨率、帧率、时长、流数量与编码。"""
    if run_ffprobe is None:
        from backend.app.capabilities.media.ffmpeg import run_ffprobe as _run

        run_ffprobe = _run
    payload = json.loads(
        run_ffprobe(["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    )
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    rate = video.get("avg_frame_rate") or "0/1"
    try:
        numerator, denominator = rate.split("/")
        fps = round(float(numerator) / float(denominator), 3) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration": round(float(payload.get("format", {}).get("duration") or 0.0), 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "streams": len(streams),
        "has_audio": bool(audio),
        "byte_size": path.stat().st_size,
    }


def scan_media(path: Path, *, run_ffmpeg: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """黑帧 / 冻结 / 静音检测。

    三个滤镜都会把命中区间打到 stderr，这里把每段时长加起来看是否超过阈值。
    注意：**转场压黑是正常的**，所以阈值给到 0.45 秒而不是要求零黑帧。
    """
    if run_ffmpeg is None:
        from backend.app.capabilities.media.ffmpeg import run_ffmpeg as _run

        run_ffmpeg = _run

    def _segments(output: str, prefix: str) -> list[float]:
        # ffmpeg 把命中区间拆成多个 token：black_start:1 black_end:2.4 black_duration:1.4
        # 只有 *_duration 是我们需要的时长
        return [float(value) for value in _DURATION_RE[prefix].findall(output)]

    black_out = run_ffmpeg(
        ["-hide_banner", "-nostats", "-i", str(path),
         "-vf", "blackdetect=d=0.1:pix_th=0.10", "-an", "-f", "null", "-"]
    )
    freeze_out = run_ffmpeg(
        ["-hide_banner", "-nostats", "-i", str(path),
         "-vf", "freezedetect=n=0.001:d=0.5", "-an", "-f", "null", "-"]
    )
    silence_out = run_ffmpeg(
        ["-hide_banner", "-nostats", "-i", str(path),
         "-af", "silencedetect=n=-50dB:d=1.0", "-vn", "-f", "null", "-"]
    )
    black = _segments(black_out, "black")
    freeze = _segments(freeze_out, "freeze")
    silence = _segments(silence_out, "silence")
    return {
        "black_segments": len(black),
        "black_seconds": round(sum(black), 3),
        "freeze_segments": len(freeze),
        "freeze_seconds": round(sum(freeze), 3),
        "silence_segments": len(silence),
        "silence_seconds": round(sum(silence), 3),
    }


# ------------------------------------------------------------------ 主流程


def render_job(
    job_root: Path | str,
    *,
    engine: VideoEngine | None = None,
    fps: int | None = None,
    do_render: bool = True,
    do_qc: bool = True,
    output_name: str = "output/final.mp4",
    font_family: str = "AKSC",
    font_size: int = 46,
    font_path: str = "fonts/NotoSansSC-VF.ttf",
    ffprobe: Callable[[list[str]], str] | None = None,
    ffmpeg: Callable[[list[str]], str] | None = None,
) -> RenderReport:
    """分镜 → 时间轴 → 成片 → 质检。任何一步缺东西都如实报 blocked，不伪造产物。"""
    root = Path(job_root).resolve()
    job_id = root.name
    storyboard_path = root / "storyboard" / "storyboard.csv"
    if not storyboard_path.is_file():
        raise RenderError(
            "missing_storyboard",
            f"{job_id}: 没有分镜文件 {storyboard_path}，先让人（或模型）把分镜写出来",
        )

    try:
        storyboard = load_storyboard(storyboard_path, fps=fps)
    except StoryboardError as error:
        raise RenderError("invalid_storyboard", f"{job_id}: 分镜不合法（{error.code}）：{error}") from error

    if engine is None:
        from backend.app.capabilities.media.openkatana import OpenKatanaEngine

        engine = OpenKatanaEngine(root / "project")

    try:
        clips, asset_rows, calls = prepare_clips(
            storyboard, job_root=root, engine=engine
        )
    except MediaEngineError as error:
        raise RenderError(error.code, str(error)) from error

    captions = subtitle_events(
        storyboard, family=font_family, size=font_size, source="subtitle"
    )
    fonts: list[dict[str, Any]] = []
    font_file = root / font_path if font_path else None
    if font_file and font_file.is_file():
        fonts.append({"family": font_family, "url": font_path})
    try:
        timeline = build_timeline(storyboard, clips, text=captions, fonts=fonts or None)
    except TimelineError as error:
        raise RenderError(error.code, str(error)) from error

    notes: list[str] = []
    timeline_path = root / "project" / "timeline.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    notes.append(f"时间轴已写入 {timeline_path}")

    validation = engine.validate_timeline(timeline)
    calls.append("validate_timeline")
    if not validation.get("valid", True):
        raise RenderError(
            "invalid_timeline",
            f"{job_id}: 引擎拒绝时间轴：{validation.get('warnings') or validation}",
        )

    report_kwargs: dict[str, Any] = {
        "job_id": job_id,
        "stage": "timeline_ready",
        "storyboard_shots": len(storyboard.shots),
        "frames": storyboard.total_frames,
        "fps": storyboard.fps,
        "canvas": (storyboard.width, storyboard.height),
        "assets": tuple(asset_rows),
        "timeline_path": timeline_path,
        "engine_calls": tuple(calls),
        "notes": tuple(notes),
    }

    if not do_render:
        return RenderReport(**report_kwargs)

    saved = engine.save_timeline(timeline)
    calls.append("save_timeline")
    revision = str(saved.get("revision") or "")
    # request_id 绑定时间轴版本：复用旧 ID 会被引擎拒绝
    rendered = engine.render(request_id=f"job-{job_id}-{(revision or 'r')[:8]}")
    calls.append("render")

    rendered_path = rendered.get("video") or rendered.get("output") or ""
    if not rendered_path:
        raise RenderError("no_output", f"{job_id}: 引擎渲染结束但没有返回视频路径")
    produced = Path(rendered_path)
    if not produced.is_absolute():
        produced = (root / "project" / produced).resolve()
    if not produced.is_file():
        raise RenderError("no_output", f"{job_id}: 引擎声称产出 {produced}，但文件不存在")

    target = root / output_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(produced.read_bytes())
    notes.append(f"成片已复制到 {target}")

    qc: dict[str, Any] = {}
    if do_qc:
        media = probe_media(target, run_ffprobe=ffprobe)
        scan = scan_media(target, run_ffmpeg=ffmpeg)
        checks = {
            "canvas_matches_storyboard": (media["width"], media["height"])
            == (storyboard.width, storyboard.height),
            "fps_matches_storyboard": abs(media["fps"] - storyboard.fps) < 0.01,
            "duration_matches_storyboard": abs(media["duration"] - storyboard.duration) <= 0.2,
            "has_audio_stream": media["has_audio"],
            "no_long_black": scan["black_seconds"] <= QC_THRESHOLDS["max_black_seconds"],
            "no_long_freeze": scan["freeze_seconds"] <= QC_THRESHOLDS["max_freeze_seconds"],
            "no_long_silence": scan["silence_seconds"] <= QC_THRESHOLDS["max_silence_seconds"],
        }
        qc = {
            "media": media,
            "scan": scan,
            "checks": checks,
            "failures": [name for name, passed in checks.items() if not passed],
            "ok": all(checks.values()),
            "thresholds": dict(QC_THRESHOLDS),
            "note": "这里只判技术指标（尺寸/帧率/时长/黑帧/冻结/静音），画面好不好看要人看",
        }

    final = {
        **report_kwargs,
        "stage": "rendered",
        "notes": tuple(notes),
        "engine_calls": tuple(calls),
        "timeline_revision": revision,
        "output_path": target,
        "output_sha256": _sha256(target),
        "output_byte_size": target.stat().st_size,
        "qc": qc,
    }
    return RenderReport(**final)
