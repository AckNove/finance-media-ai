"""项目独立音视频工具链：定位、版本校验与能力探测（V2.2 Phase 1）。

设计边界（重要）：

- 本模块只回答两个问题：**当前该用哪个 ffmpeg / ffprobe**、**它能做什么**。
  它不负责剪辑编排、时间轴、素材调度——那些属于后续阶段与 Video Engine Adapter。
- 解析顺序永远是「项目配置 > 项目独立构建 > 系统 PATH」，且**只读**：
  不修改系统 PATH，不覆盖系统已安装的 FFmpeg，不在 PATH 上安装东西。
- 能力探测分两层：本模块做**二进制自报**（`-encoders` / `-decoders` / `-filters`
  / `-muxers` / `-demuxers`），真实产物由 `backend.scripts.media_selftest` 生成并核验。
  自报能做什么 ≠ 实际跑通，两者都要，缺一不可。
- 探测结果不落盘缓存。版本与编译选项会随二进制变化，缓存只会制造"看起来能用"的假象。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from backend.app.core.config import PROJECT_ROOT

#: OpenKatana 0.1.3 的 `prepare()` 用到 `-fps_mode`，该参数自 FFmpeg 5.1 起可用，
#: 这是本项目对 FFmpeg 的**硬下限**，低于它就不能走视频素材导入路径。
MIN_FFMPEG_VERSION: tuple[int, int] = (5, 1)
MIN_FFMPEG_VERSION_TEXT = f"{MIN_FFMPEG_VERSION[0]}.{MIN_FFMPEG_VERSION[1]}"

#: 项目独立构建的默认位置（由 `backend/scripts/install_ffmpeg.py` 写入）。
PROJECT_BIN_DIR = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"

_VERSION_PATTERN = re.compile(r"ffmpeg version (\S+)")
_NUMBER_PATTERN = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")
#: 列表输出第一列是能力标记。encoders/filters 用多字符（如 `V....D`、`T..`），
#: 而 muxers/demuxers 可能只有一个字母（`E`、`D`），因此这里必须允许 1 个字符。
_FLAG_LINE = re.compile(r"^[A-Z.]{1,8}$")

PROBE_TIMEOUT_SECONDS = 60


class MediaToolError(RuntimeError):
    """命令执行失败。保留命令与 stderr 尾部，便于追踪（不得静默吞掉）。"""

    def __init__(self, message: str, *, command: Sequence[str] = (), stderr: str = "") -> None:
        super().__init__(message)
        self.command = tuple(str(part) for part in command)
        self.stderr = stderr

    def describe(self) -> dict[str, object]:
        return {
            "message": str(self),
            "command": list(self.command),
            "stderr_tail": self.stderr.strip().splitlines()[-6:],
        }


@dataclass(frozen=True)
class MediaToolchain:
    """一次解析得到的工具链。路径缺失即表示不可用，不做任何隐式回退。"""

    ffmpeg: Path | None
    ffprobe: Path | None
    source: str
    version: tuple[int, ...] | None = None
    version_line: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return (
            self.ffmpeg is not None
            and self.ffprobe is not None
            and self.ffmpeg.exists()
            and self.ffprobe.exists()
        )

    @property
    def satisfies_minimum(self) -> bool:
        return self.version is not None and self.version[:2] >= MIN_FFMPEG_VERSION

    @property
    def version_text(self) -> str:
        if self.version is None:
            return "unknown"
        return ".".join(str(part) for part in self.version)

    def describe(self) -> dict[str, object]:
        return {
            "available": self.available,
            "source": self.source,
            "ffmpeg": str(self.ffmpeg) if self.ffmpeg else None,
            "ffprobe": str(self.ffprobe) if self.ffprobe else None,
            "version": self.version_text,
            "version_line": self.version_line,
            "minimum_version": MIN_FFMPEG_VERSION_TEXT,
            "satisfies_minimum": self.satisfies_minimum,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    required: bool
    present: bool
    note: str = ""

    def describe(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "required": self.required,
            "present": self.present,
            "note": self.note,
        }


@dataclass(frozen=True)
class CapabilityReport:
    toolchain: MediaToolchain
    capabilities: tuple[Capability, ...]

    @property
    def missing_required(self) -> tuple[Capability, ...]:
        return tuple(item for item in self.capabilities if item.required and not item.present)

    @property
    def missing_recommended(self) -> tuple[Capability, ...]:
        return tuple(item for item in self.capabilities if not item.required and not item.present)

    @property
    def ok(self) -> bool:
        return self.toolchain.available and self.toolchain.satisfies_minimum and not self.missing_required

    def describe(self) -> dict[str, object]:
        return {
            "toolchain": self.toolchain.describe(),
            "ok": self.ok,
            "missing_required": [item.key for item in self.missing_required],
            "missing_recommended": [item.key for item in self.missing_recommended],
            "capabilities": [item.describe() for item in self.capabilities],
        }


@dataclass(frozen=True)
class _Requirement:
    key: str
    label: str
    kind: str  # encoder | decoder | filter | muxer | demuxer | tool
    token: str
    required: bool
    note: str = ""


#: 任务书 Phase 1 明确要求检查的能力（required=True），外加本项目渲染链路实际依赖的
#: 检测类滤镜（required=False，缺失只降级提示——判定能力少一条不等于工具链不可用）。
REQUIREMENTS: tuple[_Requirement, ...] = (
    _Requirement("h264_decode", "H.264 解码", "decoder", "h264", True),
    _Requirement("h264_encode", "H.264 编码（libx264）", "encoder", "libx264", True),
    _Requirement("aac_encode", "AAC 编码", "encoder", "aac", True),
    _Requirement("ass_filter", "libass 字幕烧录（ass）", "filter", "ass", True),
    _Requirement("subtitles_filter", "字幕滤镜（subtitles）", "filter", "subtitles", True),
    _Requirement("mp4_mux", "MP4 封装", "muxer", "mp4", True),
    _Requirement("mp4_demux", "MP4 解封装（真实素材导入）", "demuxer", "mp4", True),
    _Requirement("audio_mix", "音频混合（amix）", "filter", "amix", True),
    _Requirement("video_trim", "视频裁剪（trim）", "filter", "trim", True),
    _Requirement("audio_trim", "音频裁剪（atrim）", "filter", "atrim", True),
    _Requirement("video_concat", "视频拼接（concat 滤镜）", "filter", "concat", True),
    _Requirement("concat_demux", "拼接解封装（concat demuxer）", "demuxer", "concat", True),
    _Requirement("fps_convert", "帧率转换（fps）", "filter", "fps", True),
    _Requirement("volume", "音量处理（volume）", "filter", "volume", True),
    _Requirement("loudnorm", "响度归一（loudnorm）", "filter", "loudnorm", True),
    _Requirement("ffprobe_metadata", "元数据读取（ffprobe）", "tool", "ffprobe", True),
    _Requirement("drawtext", "文字烧录（drawtext）", "filter", "drawtext", False),
    _Requirement("scale", "缩放（scale）", "filter", "scale", False),
    _Requirement("overlay", "图层叠加（overlay）", "filter", "overlay", False),
    _Requirement("xfade", "转场（xfade）", "filter", "xfade", False),
    _Requirement("afade", "音频淡入淡出（afade）", "filter", "afade", False),
    _Requirement("zoompan", "推拉镜（zoompan）", "filter", "zoompan", False),
    _Requirement("blackdetect", "黑帧检测（blackdetect）", "filter", "blackdetect", False),
    _Requirement("freezedetect", "冻结帧检测（freezedetect）", "filter", "freezedetect", False),
    _Requirement("silencedetect", "静音检测（silencedetect）", "filter", "silencedetect", False),
    _Requirement("volumedetect", "音量检测（volumedetect）", "filter", "volumedetect", False),
)


def _run(binary: Path, args: Sequence[str], *, timeout: int = PROBE_TIMEOUT_SECONDS) -> str:
    """执行二进制并返回合并输出。失败抛 MediaToolError（不返回空字符串装成功）。"""
    command = [str(binary), *args]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover - 环境相关
        raise MediaToolError(f"无法执行 {binary.name}: {exc}", command=command) from exc
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        raise MediaToolError(
            f"{binary.name} 返回码 {done.returncode}",
            command=command,
            stderr=output,
        )
    return output


def read_version(binary: Path) -> tuple[str | None, tuple[int, ...] | None]:
    """读取 `ffmpeg -version` 的首行与数字版本。git 快照（N-xxxxx）解析为 None。"""
    output = _run(binary, ["-hide_banner", "-version"])
    first_line = output.splitlines()[0].strip() if output.strip() else ""
    match = _VERSION_PATTERN.search(first_line)
    if not match:
        return first_line or None, None
    numbers = _NUMBER_PATTERN.match(match.group(1))
    if not numbers:
        return first_line, None
    parts = tuple(int(group) for group in numbers.groups() if group is not None)
    return first_line, parts


def _config_video_section(app_config: dict | None) -> dict:
    if app_config is None:
        try:
            from backend.app.core.config import get_app_config

            app_config = get_app_config()
        except Exception:  # pragma: no cover - 配置缺失时退回默认位置
            return {}
    section = app_config.get("video") if isinstance(app_config, dict) else None
    return section if isinstance(section, dict) else {}


def _candidate_dirs(app_config: dict | None, explicit_dir: str | Path | None) -> list[tuple[str, Path]]:
    video = _config_video_section(app_config)
    candidates: list[tuple[str, Path]] = []
    if explicit_dir:
        candidates.append(("explicit", Path(explicit_dir)))
    configured = video.get("ffmpeg_dir")
    if configured:
        path = Path(str(configured))
        candidates.append(("config", path if path.is_absolute() else PROJECT_ROOT / path))
    candidates.append(("project", PROJECT_BIN_DIR))
    return candidates


def resolve_toolchain(
    explicit_dir: str | Path | None = None,
    *,
    app_config: dict | None = None,
) -> MediaToolchain:
    """按「显式参数 > 项目配置 > 项目独立构建 > PATH」解析工具链。"""
    checked: list[str] = []
    for label, directory in _candidate_dirs(app_config, explicit_dir):
        ffmpeg = directory / "ffmpeg.exe"
        ffprobe = directory / "ffprobe.exe"
        if not (ffmpeg.is_file() and ffprobe.is_file()):
            checked.append(f"{label}:{directory} (missing)")
            continue
        version_line, version = read_version(ffmpeg)
        return MediaToolchain(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            source=label,
            version=version,
            version_line=version_line,
            candidates=tuple(checked + [f"{label}:{directory}"]),
        )

    path_ffmpeg = shutil.which("ffmpeg")
    path_ffprobe = shutil.which("ffprobe")
    if path_ffmpeg and path_ffprobe:
        ffmpeg_path = Path(path_ffmpeg)
        version_line, version = read_version(ffmpeg_path)
        return MediaToolchain(
            ffmpeg=ffmpeg_path,
            ffprobe=Path(path_ffprobe),
            source="path",
            version=version,
            version_line=version_line,
            candidates=tuple(checked + [f"path:{path_ffmpeg}"]),
        )

    return MediaToolchain(None, None, "none", candidates=tuple(checked))


def _listing_names(text: str) -> set[str]:
    """从 `-encoders` / `-filters` 这类列表输出里取名字列（第二列）。"""
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not _FLAG_LINE.match(parts[0]):
            continue
        names.add(parts[1].split()[0].strip())
    return names


def _listings(toolchain: MediaToolchain) -> dict[str, set[str]]:
    assert toolchain.ffmpeg is not None
    ffmpeg = toolchain.ffmpeg
    return {
        "encoder": _listing_names(_run(ffmpeg, ["-hide_banner", "-encoders"])),
        "decoder": _listing_names(_run(ffmpeg, ["-hide_banner", "-decoders"])),
        "filter": _listing_names(_run(ffmpeg, ["-hide_banner", "-filters"])),
        "muxer": _listing_names(_run(ffmpeg, ["-hide_banner", "-muxers"])),
        "demuxer": _listing_names(_run(ffmpeg, ["-hide_banner", "-demuxers"])),
    }


def _present(requirement: _Requirement, listings: dict[str, set[str]], toolchain: MediaToolchain) -> bool:
    if requirement.kind == "tool":
        return toolchain.ffprobe is not None and toolchain.ffprobe.exists()
    names = listings.get(requirement.kind, set())
    if requirement.kind in {"muxer", "demuxer"}:
        # 容器名可能是逗号串（mov,mp4,m4a,3gp,3g2,mj2），按子串匹配
        return any(requirement.token in name for name in names)
    return requirement.token in names


def probe_capabilities(toolchain: MediaToolchain | None = None) -> CapabilityReport:
    """探测工具链能力。工具链不可用时全部标记为缺失，而不是抛异常。"""
    resolved = toolchain or resolve_toolchain()
    if not resolved.available or resolved.ffmpeg is None:
        capabilities = tuple(
            Capability(item.key, item.label, item.required, False, "工具链不可用") for item in REQUIREMENTS
        )
        return CapabilityReport(resolved, capabilities)

    listings = _listings(resolved)
    capabilities = tuple(
        Capability(item.key, item.label, item.required, _present(item, listings, resolved), item.note)
        for item in REQUIREMENTS
    )
    return CapabilityReport(resolved, capabilities)


def require_toolchain(toolchain: MediaToolchain | None = None) -> MediaToolchain:
    """拿到一个可用且版本达标的工具链，否则抛 MediaToolError（带原因码）。"""
    resolved = toolchain or resolve_toolchain()
    if not resolved.available:
        raise MediaToolError(
            "找不到可用的 ffmpeg / ffprobe：项目独立构建缺失，PATH 上也没有。"
            "请先运行 python -m backend.scripts.install_ffmpeg",
            command=(),
        )
    if not resolved.satisfies_minimum:
        raise MediaToolError(
            f"ffmpeg {resolved.version_text} 低于要求的 {MIN_FFMPEG_VERSION_TEXT}"
            f"（OpenKatana 的 -fps_mode 需要 5.1+）：{resolved.ffmpeg}",
            command=(str(resolved.ffmpeg),),
        )
    return resolved


def run_ffmpeg(toolchain: MediaToolchain, args: Sequence[str], *, timeout: int = 900) -> str:
    """在当前工具链上执行一次 ffmpeg，失败抛 MediaToolError。供自检与后续阶段复用。"""
    if toolchain.ffmpeg is None:
        raise MediaToolError("ffmpeg 不可用", command=("ffmpeg", *args))
    return _run(toolchain.ffmpeg, ["-hide_banner", "-nostdin", *args], timeout=timeout)


def run_ffprobe(toolchain: MediaToolchain, args: Sequence[str], *, timeout: int = 300) -> str:
    if toolchain.ffprobe is None:
        raise MediaToolError("ffprobe 不可用", command=("ffprobe", *args))
    return _run(toolchain.ffprobe, ["-hide_banner", *args], timeout=timeout)


def summarise(report: CapabilityReport) -> Iterable[str]:
    """人类可读的逐行摘要（CLI 与报告共用）。"""
    yield f"toolchain: {report.toolchain.source} ffmpeg={report.toolchain.ffmpeg} version={report.toolchain.version_text}"
    for item in report.capabilities:
        flag = "OK  " if item.present else ("MISS" if item.required else "warn")
        yield f"  [{flag}] {item.key:18s} {item.label}"


__all__ = [
    "MIN_FFMPEG_VERSION",
    "MIN_FFMPEG_VERSION_TEXT",
    "PROJECT_BIN_DIR",
    "Capability",
    "CapabilityReport",
    "MediaToolError",
    "MediaToolchain",
    "probe_capabilities",
    "read_version",
    "require_toolchain",
    "resolve_toolchain",
    "run_ffmpeg",
    "run_ffprobe",
    "summarise",
]
