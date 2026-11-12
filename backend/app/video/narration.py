"""旁白：把分镜里的口播稿变成音轨与可核对的字幕（V5.0）。

分镜里有两个不同的文本列，这里只碰 ``narration``（口播稿）：

* ``narration`` —— 说给耳朵听的。合成成音频，作为时间轴上的音轨。
* ``subtitle`` —— 给人看的。屏幕字幕由 :mod:`backend.app.media.timeline` 从这一列来。

两者**互不替代**：没有配音时不会把字幕当配音，反过来也不拿口播稿自动盖满屏幕。

这个模块只做三件事：

1. 逐镜头调用 TTS，把音频写进 ``audio/narration/<shot_id>.mp3``（真实文件 + SHA-256）；
2. 每段音频按镜头起始时间放到时间轴上（``at``），**不拉伸镜头去迁就音频**——
   超长就记 ``overrun_seconds`` 并在报告里说出来，让人去改稿或改分镜；
3. 用词级时间生成一份 SRT 供人核对（不是自动上屏的字幕）。

TTS 不可用时**不伪造配音**：返回空音轨 + 明确原因，由调用方写进报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.app.capabilities.tts import TtsError, synthesize as default_synthesize
from backend.app.capabilities.tts.provider import WordTiming
from backend.app.capabilities.tts.subtitle import cues_to_srt, group_words_into_cues, shift_cues
from backend.app.media.storyboard import Shot, Storyboard

NARRATION_DIR = Path("audio") / "narration"
OVERRUN_TOLERANCE_S = 0.05


class NarrationError(RuntimeError):
    """旁白阶段出现了不能含糊带过的问题。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class NarrationClip:
    shot_id: str
    file: str
    duration_seconds: float
    shot_seconds: float
    overrun_seconds: float
    sha256: str
    byte_size: int
    provider: str
    voice: str
    word_count: int
    cue_count: int
    estimated_timing: bool
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "file": self.file,
            "duration_seconds": round(self.duration_seconds, 3),
            "shot_seconds": round(self.shot_seconds, 3),
            "overrun_seconds": round(self.overrun_seconds, 3),
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "provider": self.provider,
            "voice": self.voice,
            "word_count": self.word_count,
            "cue_count": self.cue_count,
            "estimated_timing": self.estimated_timing,
            "text": self.text,
        }


@dataclass(frozen=True)
class NarrationResult:
    clips: tuple[NarrationClip, ...] = ()
    tracks: tuple[dict[str, Any], ...] = ()
    cues: tuple[Any, ...] = ()
    srt_path: Path | None = None
    json_path: Path | None = None
    provider: str = ""
    voice: str = ""
    total_seconds: float = 0.0
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.clips)

    @property
    def overruns(self) -> tuple[NarrationClip, ...]:
        return tuple(clip for clip in self.clips if clip.overrun_seconds > OVERRUN_TOLERANCE_S)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "voice": self.voice,
            "clip_count": len(self.clips),
            "total_seconds": round(self.total_seconds, 3),
            "overrun_count": len(self.overruns),
            "clips": [clip.as_dict() for clip in self.clips],
            "tracks": [dict(track) for track in self.tracks],
            "srt": str(self.srt_path) if self.srt_path else "",
            "problems": list(self.problems),
        }


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


TTS_SILENCE_DB = -35
TTS_SILENCE_MIN = 0.25
TRIM_KEEP_SECONDS = 0.06


def measure_edge_silence(path: Path) -> tuple[float, float]:
    """返回 (开头静音秒数, 结尾静音秒数)。

    TTS 产出的音频两头常带 0.4–0.6 秒静音，直接拼到时间轴上会造成"每个镜头之间
    空一拍"。这里用 ffmpeg 实测，不估算；量不出来就返回 (0, 0) 保持原样。
    """
    import re
    import subprocess

    try:
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain

        toolchain = resolve_toolchain()
    except Exception:  # noqa: BLE001 - 没装 ffmpeg 就跳过修剪
        return (0.0, 0.0)
    command = [
        str(toolchain.ffmpeg),
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={TTS_SILENCE_DB}dB:d={TTS_SILENCE_MIN}",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
    text = f"{completed.stderr or ''}\n{completed.stdout or ''}"
    starts = [float(item) for item in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends = [float(item) for item in re.findall(r"silence_end:\s*([\d.]+)", text)]
    if not starts:
        return (0.0, 0.0)
    head = starts[0] if starts[0] <= 0.05 else 0.0
    tail = 0.0
    if ends:
        # 结尾静音 = 最后一段静音的结束点缺失时，用总时长减去最后一段起点。
        if len(starts) > len(ends):
            total = _duration_of(path)
            if total > 0:
                tail = max(0.0, total - starts[-1])
        else:
            total = _duration_of(path)
            if total > 0 and ends[-1] >= total - 0.05:
                tail = max(0.0, total - starts[-1])
    return (max(0.0, head), max(0.0, tail))


def _duration_of(path: Path) -> float:
    try:
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain, run_ffprobe

        toolchain = resolve_toolchain()
        output = run_ffprobe(
            toolchain,
            [
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
        )
        return float((output or "").strip() or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def trim_edge_silence(path: Path) -> tuple[float, float]:
    """把首尾静音剪掉（留 60ms 呼吸），返回 (剪掉的头, 剪掉的尾) 秒数。"""
    head, tail = measure_edge_silence(path)
    if head <= TTS_SILENCE_MIN and tail <= TTS_SILENCE_MIN:
        return (0.0, 0.0)
    total = _duration_of(path)
    if total <= 0:
        return (0.0, 0.0)
    start = max(0.0, head - TRIM_KEEP_SECONDS)
    end = min(total, total - tail + TRIM_KEEP_SECONDS)
    if end <= start + 0.2:
        return (0.0, 0.0)
    try:
        from backend.app.capabilities.media.ffmpeg import resolve_toolchain

        toolchain = resolve_toolchain()
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
    trimmed = path.with_name(f"{path.stem}.trimmed{path.suffix}")
    command = [
        str(toolchain.ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(path),
        "-c",
        "copy",
        str(trimmed),
    ]
    import subprocess

    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)
    if completed.returncode != 0 or not trimmed.is_file() or trimmed.stat().st_size == 0:
        trimmed.unlink(missing_ok=True)
        return (0.0, 0.0)
    trimmed.replace(path)
    return (start, max(0.0, total - end))


def _narration_text(shot: Shot) -> str:
    return (getattr(shot, "narration", "") or "").strip()


def synthesize_narration(
    storyboard: Storyboard,
    *,
    job_root: Path | str,
    provider: str = "auto",
    voice: str | None = None,
    rate: str = "+0%",
    volume: str = "+0%",
    synthesize: Callable[..., Any] | None = None,
    trim: bool = True,
    trim_fn: Callable[[Path], tuple[float, float]] | None = None,
) -> NarrationResult:
    """逐个镜头合成旁白。一个镜头失败不影响其他镜头，但会记进 ``problems``。"""
    root = Path(job_root).resolve()
    target_dir = root / NARRATION_DIR
    synthesize_fn = synthesize or default_synthesize
    trimmer = trim_fn or trim_edge_silence

    clips: list[NarrationClip] = []
    tracks: list[dict[str, Any]] = []
    all_cues: list[Any] = []
    problems: list[str] = []
    used_provider = ""
    used_voice = ""

    for shot in storyboard.shots:
        text = _narration_text(shot)
        if not text:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".wav" if provider == "sapi" else ".mp3"
        out_path = target_dir / f"{shot.shot_id}{suffix}"
        try:
            result = synthesize_fn(
                text,
                out_path,
                provider=provider,
                voice=voice,
                rate=rate,
                volume=volume,
            )
        except TtsError as error:
            problems.append(f"{shot.shot_id}: 旁白合成失败（{error.reason}）：{error}")
            continue
        except Exception as error:  # pragma: no cover - 兜底，仍要如实记录
            problems.append(f"{shot.shot_id}: 旁白合成异常：{error}")
            continue

        produced = Path(getattr(result, "path", out_path))
        if not produced.is_file():
            problems.append(f"{shot.shot_id}: 合成声称产出 {produced}，但文件不存在")
            continue

        shot_seconds = float(shot.end_time) - float(shot.start_time)
        reported = float(getattr(result, "duration_seconds", 0.0) or 0.0)
        words = tuple(getattr(result, "words", ()) or ())
        head_trimmed = 0.0
        if trim:
            # TTS 两头常带静音，剪掉之后镜头之间的空拍才不会攒成"没有声音的几秒"。
            try:
                head_trimmed, _tail_trimmed = trimmer(produced)
            except Exception as error:  # noqa: BLE001 - 修剪失败不拦合成
                problems.append(f"{shot.shot_id}: 首尾静音修剪失败（保留原音频）：{error}")
                head_trimmed = 0.0
            if head_trimmed > 0:
                words = tuple(
                    WordTiming(
                        text=word.text,
                        start=max(0.0, float(word.start) - head_trimmed),
                        end=max(0.0, float(word.end) - head_trimmed),
                    )
                    for word in words
                    if float(word.end) - head_trimmed > 0
                )
        # 时长以磁盘上的真实文件为准（修剪之后再量），合成器报的数字可能偏大。
        duration = _duration_of(produced) or reported
        cues = shift_cues(
            group_words_into_cues(words), float(shot.start_time)
        ) if words else ()
        all_cues.extend(cues)
        used_provider = str(getattr(result, "provider", provider))
        used_voice = str(getattr(result, "voice", voice or ""))

        clips.append(
            NarrationClip(
                shot_id=shot.shot_id,
                file=produced.relative_to(root).as_posix(),
                duration_seconds=duration,
                shot_seconds=shot_seconds,
                overrun_seconds=max(0.0, duration - shot_seconds),
                sha256=_sha256(produced),
                byte_size=produced.stat().st_size,
                provider=used_provider,
                voice=used_voice,
                word_count=len(words),
                cue_count=len(cues),
                estimated_timing=bool(getattr(result, "estimated", False)),
                text=text,
            )
        )
        tracks.append(
            {
                "src": produced.relative_to(root).as_posix(),
                "at": round(float(shot.start_time), 3),
                "volume": 1.0,
            }
        )

    srt_path = json_path = None
    if all_cues:
        srt_path = root / NARRATION_DIR / "narration.srt"
        srt_path.write_text(cues_to_srt(list(all_cues)), encoding="utf-8")

    result_payload = NarrationResult(
        clips=tuple(clips),
        tracks=tuple(tracks),
        cues=tuple(all_cues),
        srt_path=srt_path,
        json_path=None,
        provider=used_provider,
        voice=used_voice,
        total_seconds=sum(clip.duration_seconds for clip in clips),
        problems=tuple(problems),
    )
    if clips:
        json_path = root / NARRATION_DIR / "narration.json"
        import json

        json_path.write_text(
            json.dumps(result_payload.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result_payload = NarrationResult(
            clips=result_payload.clips,
            tracks=result_payload.tracks,
            cues=result_payload.cues,
            srt_path=result_payload.srt_path,
            json_path=json_path,
            provider=result_payload.provider,
            voice=result_payload.voice,
            total_seconds=result_payload.total_seconds,
            problems=result_payload.problems,
        )
    return result_payload


def narration_notes(result: NarrationResult, *, shot_count: int) -> list[str]:
    """把旁白阶段的事实写成给人看的几句话（报告里直接粘贴）。"""
    if not result.ok:
        reasons = "；".join(result.problems) or "分镜里没有 narration 文本"
        return [f"没有生成旁白音轨：{reasons}。时间轴里不含机器配音，成片没有旁白。"]
    notes = [
        f"旁白：{len(result.clips)}/{shot_count} 个镜头有配音，"
        f"共 {result.total_seconds:.1f} 秒（{result.provider} / {result.voice}）"
    ]
    if result.overruns:
        detail = "、".join(
            f"{clip.shot_id} 超 {clip.overrun_seconds:.2f}s" for clip in result.overruns
        )
        notes.append(f"其中 {len(result.overruns)} 段比镜头长（{detail}）：没有拉伸镜头，需改稿或改分镜")
    if result.problems:
        notes.append("部分镜头没配上音：" + "；".join(result.problems))
    return notes


__all__ = [
    "NARRATION_DIR",
    "NarrationClip",
    "NarrationError",
    "NarrationResult",
    "narration_notes",
    "synthesize_narration",
]
