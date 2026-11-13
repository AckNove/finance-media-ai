"""把一个任务目录的**分镜**跑成时间轴、渲染成片、并做技术质检。

这条链路刻意做成"引擎可替换"的三段：

1. `prepare_clips`：把分镜里声明的素材交给引擎导入（拿到帧序列描述）；
2. `build_and_save`：分镜 + 帧序列 → 时间轴文档（复用 `backend.app.media.timeline`）；
3. `render_and_qc`：交给引擎渲染，再用 ffprobe + 三类检测做技术体检。

**分镜只能由人（或模型）先写出来**：这里不生成脚本、不猜镜头，
分镜缺失就直接报 `missing_storyboard`，不拿模板文字凑数。

**副作用要说清**：真机渲染会让引擎在自己的工程目录里记账——`project/<pid>/project.json`
会追加本次导入的素材、`assets/`、`media/`、`plates/` 会被写出。后三者在 `.gitignore` 里，
但 `project.json` 是入库的：对**已冻结**的任务（如 `phase2_media_engine_demo`）跑完真机渲染后
要 `git checkout -- project/<pid>/project.json` 恢复，否则冻结记录会被累计的素材列表污染。
`project/timeline.json` 不会被覆盖，本次时间轴写进 `project/timeline.render.json`。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from backend.app.capabilities.media.engine import MediaEngineError, VideoEngine
from backend.app.media.manifest import read_manifest
from backend.app.media.storyboard import Storyboard, StoryboardError, load_storyboard
from backend.app.video import textfit
from backend.app.video.cards import INK as CARD_INK
from backend.app.media.timeline import (
    TimelineError,
    audio_mix,
    build_timeline,
    solid_clip_id,
    subtitle_events,
)

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
    narration: dict[str, Any] = field(default_factory=dict)

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
            "narration": self.narration,
            "ok": self.ok,
        }


# ------------------------------------------------------------------ 素材


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_font(job_root: Path, font_path: str) -> Path | None:
    """在任务目录里找字体：先看相对路径，再按文件名搜一遍任务目录。

    任务目录的布局有两种：字体放 ``<job>/fonts/``，或者放在引擎工程目录里
    （``<job>/project/<project_id>/fonts/``）。两种都认，但都限定在任务目录内。
    """
    direct = job_root / font_path
    if direct.is_file():
        return direct
    name = Path(font_path).name
    for candidate in sorted(job_root.rglob(name)):
        if candidate.is_file():
            return candidate
    return None


#: 本机可能已经装了同一个开源字体的位置。只在任务目录里没有字体时**复制**一份进来，
#: 不修改系统字体、也不把 17MB 的二进制提交进仓库。
SYSTEM_FONT_CANDIDATES: tuple[Path, ...] = (
    Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansSC-VF.ttf"),
)


def ensure_font(job_root: Path, font_path: str, *, candidates: Iterable[Path] = ()) -> Path | None:
    """确保任务目录里有分镜要用的字体；没有就从本机已知位置复制一份。"""
    found = _find_font(job_root, font_path)
    if found is not None:
        return found
    for candidate in tuple(candidates) + SYSTEM_FONT_CANDIDATES:
        if Path(candidate).is_file():
            target = job_root / font_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            return target
    return None


def _sync_audio(job_root: Path, project_dir: Path, tracks: Iterable[Mapping[str, Any]]) -> list[str]:
    """把时间轴引用的音频复制进工程目录，返回复制过的相对路径。

    时间轴里的 ``src`` 相对**工程目录**解析（引擎的规则），而旁白和配乐是任务
    目录生成的，所以渲染前要放进工程里，否则引擎报 ``Audio track missing``。
    """
    copied: list[str] = []
    for track in tracks:
        relative = str(track.get("src") or "").strip()
        if not relative:
            continue
        source = (job_root / relative).resolve()
        if not source.is_file() or not source.is_relative_to(job_root.resolve()):
            continue
        target = project_dir / relative
        if target.is_file() and target.stat().st_size == source.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative.replace("\\", "/"))
    return copied


def _sync_fonts(job_root: Path, project_dir: Path) -> list[str]:
    """把任务目录 fonts/ 下的字体复制进工程目录，返回复制过的文件名。

    组合文档用 ``fonts/xxx.ttf`` 这样的相对路径引用字体，引擎相对工程目录解析，
    所以字体必须在工程里存在。已经存在且大小一致就不重复复制。
    """
    source_dir = job_root / "fonts"
    if not source_dir.is_dir():
        return []
    target_dir = project_dir / "fonts"
    copied: list[str] = []
    for item in sorted(source_dir.iterdir()):
        if not item.is_file():
            continue
        target = target_dir / item.name
        if target.is_file() and target.stat().st_size == item.stat().st_size:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(item.name)
    return copied


def _import_composition(
    asset_id: str,
    document_path: Path,
    *,
    job_root: Path,
    engine: VideoEngine,
) -> dict[str, Any]:
    """把一份组合文档交给引擎保存并渲染成帧序列。

    组合是"程序画出来的画面"，不是普通媒体文件，所以不能走 import_media。
    文档里的字体路径写的是任务目录内的相对路径，引擎只认工程目录内的文件，
    因此这里把任务目录的 fonts/ 复制进工程再渲染。
    """
    try:
        document = json.loads(document_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RenderError(
            "bad_composition", f"{asset_id}: 组合文档不是合法 JSON（{document_path}）：{error}"
        ) from error
    relative = document_path.relative_to(job_root).as_posix()
    try:
        engine.save_composition_reloading(relative, document)
        result = engine.render_composition(relative)
    except MediaEngineError as error:
        raise RenderError(error.code, f"{asset_id}: 组合渲染失败：{error}") from error
    clip = dict((result.get("clip") if isinstance(result, dict) else None) or {})
    if not clip:
        raise RenderError("empty_clip", f"{asset_id}: 组合渲染没有返回帧序列")
    return clip


def _solid_plate(job_root: Path, colour: str, *, width: int, height: int) -> Path:
    """为纯色底镜头生成一张单帧图（引擎要求每个镜头都有真实 clip）。"""
    from PIL import Image

    safe = (colour or "#0b1220").strip().lstrip("#") or "0b1220"
    target = job_root / "assets" / "plates" / f"solid_{safe}.png"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    rgb = tuple(int(safe[i : i + 2], 16) for i in (0, 2, 4)) if len(safe) == 6 else (11, 18, 32)
    Image.new("RGB", (width, height), rgb).save(target, format="PNG")
    return target


PLATE_SECONDS = 4.0


def _mix(colour: str, other: str, ratio: float) -> str:
    """把两种 #rrggbb 按比例混合，用来给渐变底挑一个相邻色。"""
    left = tuple(int(colour[i : i + 2], 16) for i in (0, 2, 4))
    right = tuple(int(other[i : i + 2], 16) for i in (0, 2, 4))
    mixed = tuple(int(round(a + (b - a) * ratio)) for a, b in zip(left, right))
    return "".join(f"{value:02x}" for value in mixed)


def _solid_gradient(
    job_root: Path,
    colour: str,
    *,
    width: int,
    height: int,
    fps: int,
    seconds: float = PLATE_SECONDS,
) -> Path | None:
    """生成一段缓慢流动的渐变底片。

    单帧纯色底 + 运镜仍会被 `freezedetect` 判成冻结（实测 66.9 秒）：均匀色块
    缩放/平移后像素完全一样。所以纯色卡也做成"会动的渐变"，让运镜有东西可带动。
    ffmpeg 不可用时返回 None，调用方退回单帧 PNG（并在 notes 里说明）。
    """
    safe = (colour or "#1d2a44").strip().lstrip("#") or "1d2a44"
    if len(safe) != 6:
        return None
    target = job_root / "assets" / "plates" / f"solid_{safe}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    second = _mix(safe, "05070d", 0.85)
    source = (
        f"gradients=s={width}x{height}:c0=0x{safe}:c1=0x{second}"
        f":speed=0.06:d={seconds:.2f}:r={fps}"
    )
    try:
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain
        from backend.app.capabilities.media.ffmpeg import run_ffmpeg

        toolchain = resolve_toolchain()
        run_ffmpeg(
            toolchain,
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                source,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(target),
            ],
            timeout=120,
        )
    except Exception as error:  # noqa: BLE001 - 退回单帧图比直接失败更有用
        logger.warning("渐变底片生成失败，退回单帧纯色：%s", error)
        return None
    return target if target.is_file() and target.stat().st_size > 0 else None


def prepare_clips(
    storyboard: Storyboard,
    *,
    job_root: Path,
    engine: VideoEngine,
    animate_plates: bool = True,
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
    elif any(shot.asset_type != "solid" and shot.asset_id for shot in storyboard.shots):
        # 只有"确实要用外部素材"时清单才是必需的：纯色底镜头没有素材可登记，
        # 逼人写一张空清单只会让交接流程假装完整。
        raise RenderError("missing_manifest", f"缺少素材清单：{manifest_path}")

    needed: list[tuple[str, float, float]] = []
    for shot in storyboard.shots:
        if shot.asset_type == "solid" or not shot.asset_id:
            continue
        key = (shot.asset_id, shot.source_start, shot.duration)
        if key not in needed:
            needed.append(key)

    clips: dict[str, dict[str, Any]] = {}
    asset_rows: list[dict[str, Any]] = []
    calls: list[str] = []

    # 纯色底镜头：引擎不接受"只有 bg 没有 clip"的镜头，所以给它一张同色单帧图。
    width, height = storyboard.width, storyboard.height
    solids: list[str] = []
    for shot in storyboard.shots:
        if shot.asset_type != "solid":
            continue
        colour = shot.asset_source or "#0b1220"
        if colour not in solids:
            solids.append(colour)
    for colour in solids:
        # 先试"会动的渐变底片"，不行再退回单帧纯色图。
        plate = None
        if animate_plates:
            plate = _solid_gradient(
                job_root,
                colour,
                width=width,
                height=height,
                fps=storyboard.fps,
            )
        if plate is None:
            plate = _solid_plate(job_root, colour, width=width, height=height)
        key = solid_clip_id(colour)
        result = engine.import_media(source=plate, rights="self-generated")
        calls.append(f"import_media:{key}")
        clip = dict(result.raw.get("clip") or {})
        if not clip:
            raise RenderError("empty_clip", f"{key}: 引擎没有返回帧序列")
        clips[key] = clip
        asset_rows.append(
            {
                "asset_id": key,
                "registered_as": key,
                "file": str(plate.relative_to(job_root)).replace("\\", "/"),
                "sha256": _sha256(plate),
                "asset_class": "B",
                "license": "self-generated",
                "commercial_use": "allowed",
                "source_start": 0.0,
                "frames": int(clip.get("n") or 1),
            }
        )

    for asset_id, source_start, duration in needed:
        row = rows.get(asset_id)
        registered_as = asset_id
        if row is None:
            # 同一份登记素材常被拆成多个入点用（S03A / S03B）。只允许退回到
            # 去掉末尾大写字母后确实存在的登记项，不猜任何别的路径。
            base = asset_id.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            if base and base != asset_id and base in rows:
                row = rows[base]
                registered_as = base
            else:
                raise RenderError("unknown_asset", f"分镜用了未登记的素材：{asset_id}")
        relative = row.get("file") or ""
        path = (job_root / relative).resolve()
        if not path.is_file():
            raise RenderError("missing_asset_file", f"{asset_id}: 文件不存在 {path}")
        kind = (row.get("kind") or "").strip()
        if kind == "composition":
            clip = _import_composition(asset_id, path, job_root=job_root, engine=engine)
            calls.append(f"render_composition:{asset_id}")
            asset_rows.append(
                {
                    "asset_id": asset_id,
                    "registered_as": registered_as,
                    "file": relative,
                    "sha256": _sha256(path),
                    "asset_class": row.get("asset_class", ""),
                    "license": row.get("license", ""),
                    "commercial_use": row.get("commercial_use", ""),
                    "source_start": source_start,
                    "frames": int(clip.get("n") or 1),
                }
            )
            clips[asset_id] = clip
            continue
        window: dict[str, Any] = {"rights": row.get("license") or None}
        if source_start:
            window["start"] = source_start
        if kind == "video" and duration:
            window["duration"] = duration
        result = engine.import_media(source=path, **window)
        calls.append(f"import_media:{asset_id}" + (f"@{source_start:g}s" if source_start else ""))
        clip = dict(result.raw.get("clip") or {})
        if not clip:
            raise RenderError("empty_clip", f"{asset_id}: 引擎没有返回帧序列")
        clips[asset_id] = clip
        asset_rows.append(
            {
                "asset_id": asset_id,
                "registered_as": registered_as,
                "file": relative,
                "sha256": _sha256(path),
                "asset_class": row.get("asset_class", ""),
                "license": row.get("license", ""),
                "commercial_use": row.get("commercial_use", ""),
                "source_start": source_start,
                "frames": int(clip.get("n") or 1),
            }
        )
    return clips, asset_rows, calls


# ------------------------------------------------------------------ 质检


def probe_media(path: Path, *, run_ffprobe: Callable[[list[str]], str] | None = None) -> dict[str, Any]:
    """读容器事实：分辨率、帧率、时长、流数量与编码。"""
    if run_ffprobe is None:
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain
        from backend.app.capabilities.media.ffmpeg import run_ffprobe as _run

        toolchain = resolve_toolchain()
        run_ffprobe = lambda args: _run(toolchain, args)  # noqa: E731 - 绑定工具链的薄壳
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
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain
        from backend.app.capabilities.media.ffmpeg import run_ffmpeg as _run

        toolchain = resolve_toolchain()
        run_ffmpeg = lambda args: _run(toolchain, args)  # noqa: E731 - 绑定工具链的薄壳

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
    # 阈值名字叫 max_*_seconds，就该按"最长的一段"判，而不是把整片所有短段加总。
    # 71 秒的片子里有 6 段各 0.6 秒的短暂停顿并不算"画面冻住"，加总判会冤枉它。
    return {
        "black_segments": len(black),
        "black_seconds": round(sum(black), 3),
        "black_longest": round(max(black, default=0.0), 3),
        "freeze_segments": len(freeze),
        "freeze_seconds": round(sum(freeze), 3),
        "freeze_longest": round(max(freeze, default=0.0), 3),
        "silence_segments": len(silence),
        "silence_seconds": round(sum(silence), 3),
        "silence_longest": round(max(silence, default=0.0), 3),
    }


# ------------------------------------------------------------------ 主流程


#: 字幕占画幅宽度的上限。留出左右边距，长句才不会顶到屏幕边缘。
CAPTION_WIDTH_BUDGET = textfit.WIDTH_BUDGET


def caption_font_size(
    storyboard: Any,
    *,
    base: int,
    width: int,
    budget: float = CAPTION_WIDTH_BUDGET,
) -> int:
    """给这一份分镜挑一个放得下的字幕字号（只降不升，最短 32）。

    为什么不是固定字号：字幕 46px 时一行只放得下 18 个全角字，脚本写出来的句子
    经常正好顶到边缘（抽帧实拍"全产业链亏损：2024年三季度起主…"贴住左右两边）。
    短字幕保持原字号，长字幕自动降到放得下为止。宽度估算与数据卡共用
    `backend/app/video/textfit.py`，避免两处各算一套。
    """
    texts = [
        (getattr(shot, "subtitle", "") or "").strip()
        for shot in getattr(storyboard, "shots", ()) or ()
    ]
    return textfit.fit_text_size(
        [text for text in texts if text], base=base, canvas_width=width, budget=budget
    )


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
    narration: bool = False,
    narration_provider: str = "auto",
    narration_voice: str | None = None,
    synthesize_narration_fn: Callable[..., Any] | None = None,
    fit_narration: bool = True,
    animate_plates: bool = True,
) -> RenderReport:
    """分镜 → 时间轴 → 成片 → 质检。任何一步缺东西都如实报 blocked，不伪造产物。"""
    root = Path(job_root).resolve()
    job_id = root.name
    project_dir: Path | None = None
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
        from backend.app.capabilities.media.engine import MediaEngineError as _EngineError
        from backend.app.capabilities.media.openkatana import OpenKatanaEngine
        workspace = root / "project"
        workspace.mkdir(parents=True, exist_ok=True)
        # 引擎每次调用都要 project_id。任务目录第一次跑时还没有工程，这里建一个；
        # 已经有工程就复用磁盘上那一个，不重复建（重复建会直接报错）。
        existing = sorted(
            item for item in workspace.iterdir() if item.is_dir() and not item.name.startswith(".")
        )
        # 工程 id 是引擎生成的哈希，不是任务名。挑工程目录时优先用真正有
        # project.json 的那个；多个都有就取最近改动的，剩下的当历史工程留着不动。
        ready = [item for item in existing if (item / "project.json").is_file()]
        if ready:
            ready.sort(key=lambda item: (item / "project.json").stat().st_mtime, reverse=True)
            project_id = ready[0].name
        elif job_id in {item.name for item in existing}:
            project_id = job_id
        else:
            project_id = existing[0].name if existing else job_id
        engine = OpenKatanaEngine(workspace, project_id=project_id)
        try:
            engine.get_project()
        except _EngineError:
            try:
                engine.create_project(job_id, project_id=project_id)
            except _EngineError as error:
                raise RenderError("engine_unavailable", f"{job_id}: 建工程失败：{error}") from error
        # 组合文档里的字体路径是相对工程目录的，而字体文件可能在任务目录里。
        # 缺字体引擎会直接拒收（ContractError: Asset missing or unsupported）。
        # 顺序很重要：先把字体放进任务目录（本机没有就复制一份），再同步进工程目录，
        # 然后才导入素材——组合画板在这一步就会被 save_composition 落到工程里。
        project_dir = workspace / project_id
        if font_path:
            ensure_font(root, font_path)
        _sync_fonts(root, project_dir)

    try:
        clips, asset_rows, calls = prepare_clips(
            storyboard, job_root=root, engine=engine, animate_plates=animate_plates
        )
    except MediaEngineError as error:
        raise RenderError(error.code, str(error)) from error

    narration_payload: dict[str, Any] = {}
    narration_audio: dict[str, Any] | None = None
    notes: list[str] = []
    if narration:
        from backend.app.video.narration import narration_notes, synthesize_narration

        result = synthesize_narration(
            storyboard,
            job_root=root,
            provider=narration_provider,
            voice=narration_voice,
            synthesize=synthesize_narration_fn,
        )
        narration_payload = result.as_dict()
        notes.extend(narration_notes(result, shot_count=len(storyboard.shots)))
        if result.tracks:
            narration_audio = audio_mix(list(result.tracks))
        if fit_narration and result.clips:
            # 口播稿多长，镜头就该多长：按真实配音时长重排分镜，再重新读一遍。
            from backend.app.media.storyboard import write_storyboard
            from backend.app.video.outline import retime_storyboard

            durations = {clip.shot_id: clip.duration_seconds for clip in result.clips}
            rows, changed = retime_storyboard(storyboard, durations)
            if changed:
                storyboard_path = root / "storyboard" / "storyboard.csv"
                write_storyboard(
                    storyboard_path,
                    rows,
                    fps=storyboard.fps,
                    width=storyboard.width,
                    height=storyboard.height,
                )
                storyboard = load_storyboard(storyboard_path)
                narration_payload["retimed"] = changed
                notes.append(
                    f"分镜已按配音重排（{len(changed)} 个镜头改了时长，长了拉长、短了缩短），总时长 "
                    f"{storyboard.duration:g} 秒；改的是时间，不改文案与素材"
                )
                # 音频起播点要跟着新的镜头起点走，重算一次轨道。
                again = synthesize_narration(
                    storyboard,
                    job_root=root,
                    provider=narration_provider,
                    voice=narration_voice,
                    synthesize=synthesize_narration_fn,
                )
                if again.tracks:
                    narration_audio = audio_mix(list(again.tracks))
                # 镜头变长了，数据卡画板也要按新时长重写，否则后半段是静止的最后一帧。
                from backend.app.video.cards import resize_cards
                from backend.app.video.chain import resize_chains

                resized = resize_cards(root, storyboard)
                resized_chains = resize_chains(root, storyboard)
                if resized:
                    notes.append(f"按新时长重写了 {len(resized)} 张数据卡画板")
                if resized_chains:
                    notes.append(f"按新时长重写了 {len(resized_chains)} 张产业链画板")
                if resized or resized_chains:
                    clips, asset_rows, calls = prepare_clips(
                        storyboard, job_root=root, engine=engine,
                        animate_plates=animate_plates,
                    )

    captions = subtitle_events(
        storyboard,
        family=font_family,
        size=caption_font_size(storyboard, base=font_size, width=storyboard.width),
        source="subtitle",
    )
    fonts: list[dict[str, Any]] = []
    # 字幕要有字体才能渲染。新任务目录里通常还没有字体文件，就从本机已知位置复制
    # 一份进来（不改系统字体）；实在找不到才报 missing_font。
    font_file = ensure_font(root, font_path) if (font_path and captions) else None
    if font_file is not None and project_dir is not None:
        target = project_dir / "fonts" / font_file.name
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(font_file, target)
    if font_file is not None:
        fonts.append({"family": font_family, "url": f"fonts/{font_file.name}"})
    elif captions:
        # 有字幕却没有字体文件时，引擎会直接拒收两份产物。与其让它报一句英文
        # ContractError，不如在这里说清楚缺什么。
        raise RenderError(
            "missing_font",
            f"{job_id}: 分镜里带了字幕，但任务目录里找不到字体 {font_path}，"
            f"本机也没有可复制的同名字体；把字体放进任务目录的 fonts/ 再跑"
            "（组合文档里的字体路径也相对工程目录）",
        )
    if narration_audio and project_dir is not None:
        # 音频轨的 src 也是相对工程目录解析的，旁白生成在任务目录里，得搬过去。
        copied_audio = _sync_audio(root, project_dir, narration_audio.get("tracks") or [])
        if copied_audio:
            notes.append(f"旁白音轨已复制进工程目录：{len(copied_audio)} 个文件")
    try:
        timeline = build_timeline(
            storyboard,
            clips,
            audio=narration_audio,
            text=captions,
            fonts=fonts or None,
            # 画布底色必须和数据卡底色一致：运镜平移会把画面挪出素材边界，
            # 底色不同就会在边上露出一条异色竖条（抽帧实测过）。
            bg=CARD_INK,
        )
    except TimelineError as error:
        raise RenderError(error.code, str(error)) from error

    # 时间轴默认写到 project/timeline.json；那儿已经有别人写的文件时不要覆盖，
    # 改写到时间轴旁边另存一份，并在报告里说明（Phase 2 的交付物就是这样被保护住的）。
    timeline_path = root / "project" / "timeline.json"
    payload = json.dumps(timeline, ensure_ascii=False, indent=1)
    if timeline_path.is_file():
        existing = timeline_path.read_text(encoding="utf-8", errors="replace")
        if existing.strip() != payload.strip():
            timeline_path = root / "project" / "timeline.render.json"
            notes.append("project/timeline.json 已存在且内容不同，本次写到 timeline.render.json，没有覆盖原文件")
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text(payload, encoding="utf-8")
    notes.append(f"时间轴已写入 {timeline_path}")

    try:
        validation = engine.validate_timeline(timeline)
        calls.append("validate_timeline")
    except MediaEngineError as error:
        raise RenderError(error.code, f"{job_id}: 引擎校验时间轴失败：{error}") from error
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
        "narration": narration_payload,
    }

    if not do_render:
        return RenderReport(**report_kwargs)

    # 前面的 import_media 已经改过工程，revision 早就变了；直接存会被判"工程已变更"。
    save = getattr(engine, "save_timeline_with_retry", None) or engine.save_timeline
    saved = save(timeline)
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
        # 引擎返回的路径相对**工程目录**（`<job>/project/<project_id>/`），不是任务根。
        candidates = []
        if project_dir is not None:
            candidates.append((project_dir / produced).resolve())
        candidates.append((root / "project" / produced).resolve())
        candidates.append((root / produced).resolve())
        produced = next((item for item in candidates if item.is_file()), candidates[0])
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
            "no_long_black": scan["black_longest"] <= QC_THRESHOLDS["max_black_seconds"],
            "no_long_freeze": scan["freeze_longest"] <= QC_THRESHOLDS["max_freeze_seconds"],
            "no_long_silence": scan["silence_longest"] <= QC_THRESHOLDS["max_silence_seconds"],
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
