"""把 V2 的样片段落拼成 15–20 秒真实视频（渲染 → 音频 → 拼片 → 质检）。

这一段必须是最终片的一部分，所以镜头顺序与整片一致：

    A1 真实电厂（实拍） → B1 真实变电站 + 数据层（实拍融合） → C1 写实三维开关场

三镜合计 5.4 + 6.2 + 6.4 = 18.0 秒，落在任务书要求的 15–20 秒内。

音频三轨：
- 旁白：复用已批准脚本的 edge-tts 片段（sh01 / sh02 / sh03），按镜头起点摆放；
- 配乐：`backend/app/video/cinema/audio.py` 程序化合成（来源就是这份代码，许可即本仓库）；
- 音效：镜头切换处的 whoosh / 数据生长时的 tick。

用法：
    python -m backend.scripts.build_v2_prototype --stage all
    python -m backend.scripts.build_v2_prototype --stage render --only B1
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.video.cinema.audio import (  # noqa: E402
    music_samples,
    place_cues,
    sfx_samples,
    write_wav,
)

JOB = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film"
REM = JOB / "remaster_v2"
SCENES = REM / "production" / "scenes"
FRAMES = REM / "production" / "frames"
CLIPS = REM / "production" / "clips"
AUDIO = REM / "assets" / "audio"
OUT = REM / "output"
QC = REM / "qc"
LOGS = REM / "logs"
NARRATION = JOB / "audio" / "narration"

OPENKATANA_PYTHON = PROJECT_ROOT / "tools" / "openkatana" / ".venv" / "Scripts" / "python.exe"
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
RENDERER = PROJECT_ROOT / "backend" / "scripts" / "cinema_render.py"

FPS = 30
WIDTH = 1080
HEIGHT = 1920
SAMPLE_RATE = 48000


@dataclass(frozen=True)
class Segment:
    scene_id: str
    duration: float
    narration: str  # audio/narration 下的文件名（不含扩展名）
    subtitle: str
    cues: tuple[tuple[float, str], ...] = ()  # (相对本镜起点, 音效类型)


# 顺序＝成片顺序；字幕与旁白一一对应（旁白是已批准的脚本原文）
SEGMENTS: tuple[Segment, ...] = (
    Segment(
        scene_id="A1_plant",
        duration=5.4,
        narration="sh01",
        subtitle="发电量下滑，电费收入却还能收上来，钱从哪来？",
        cues=((0.35, "whoosh"),),
    ),
    Segment(
        scene_id="B1_substation",
        duration=6.2,
        narration="sh02",
        subtitle="发电量下滑，收入却还能收上来——这就是第二张收入表。",
        cues=((0.3, "whoosh"), (0.9, "tick"), (1.5, "tick")),
    ),
    Segment(
        scene_id="C1_substation",
        duration=6.4,
        narration="sh03",
        subtitle="它叫容量电价，利润跟电量脱钩，不再绑在一根绳上。",
        cues=((0.3, "whoosh"), (2.2, "impact")),
    ),
)


def _run(
    command: list[str], *, log_name: str, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    LOGS.mkdir(parents=True, exist_ok=True)
    # 字幕滤镜里的相对路径按 ffmpeg 的工作目录解析，所以拼片时把 cwd 设到 output 目录
    proc = subprocess.run(command, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    (LOGS / log_name).write_text(
        "$ " + " ".join(str(part) for part in command) + "\n" + (proc.stdout or "") + (proc.stderr or ""),
        encoding="utf-8",
    )
    return proc


def stage_scenes() -> dict:
    from backend.scripts.build_v2_scenes import build as build_photo
    from backend.app.video.cinema.v2_three_d import build_substation_scene

    receipts = build_photo()
    receipts.append(build_substation_scene())
    return {"scenes": [item["scene"] for item in receipts]}


def render_segment(segment: Segment, *, force: bool = False) -> dict:
    out_dir = FRAMES / segment.scene_id
    clip_path = CLIPS / f"{segment.scene_id}.mp4"
    if clip_path.exists() and not force:
        return {"scene": segment.scene_id, "skipped": True, "clip": str(clip_path)}

    started = time.time()
    proc = _run(
        [
            str(OPENKATANA_PYTHON),
            str(RENDERER),
            "--scene",
            str(SCENES / segment.scene_id),
            "--out",
            str(out_dir),
            "--fps",
            str(FPS),
            "--mp4",
            str(clip_path),
            "--quiet",
        ],
        log_name=f"render_{segment.scene_id}.log",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{segment.scene_id} 渲染失败：{proc.stderr[-600:]}")
    return {
        "scene": segment.scene_id,
        "skipped": False,
        "clip": str(clip_path),
        "seconds": round(time.time() - started, 1),
    }


def stage_render(only: tuple[str, ...] = (), *, force: bool = False) -> dict:
    results = []
    for segment in SEGMENTS:
        if only and segment.scene_id not in only:
            continue
        results.append(render_segment(segment, force=force))
    return {"rendered": results}


def _narration_seconds(path: Path) -> float:
    proc = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return float(proc.stdout.strip())


def stage_audio() -> dict:
    AUDIO.mkdir(parents=True, exist_ok=True)
    total = sum(item.duration for item in SEGMENTS)

    music = music_samples(total + 0.5, bpm=64, seed=11, sample_rate=SAMPLE_RATE, level=0.075)
    write_wav(AUDIO / "music.wav", music, sample_rate=SAMPLE_RATE)

    cues: list[tuple[float, str]] = []
    offset = 0.0
    for segment in SEGMENTS:
        for at, kind in segment.cues:
            cues.append((offset + at, kind))
        offset += segment.duration
    sfx = place_cues(total + 0.5, [(t, kind) for t, kind in cues])
    write_wav(AUDIO / "sfx.wav", sfx, sample_rate=SAMPLE_RATE)

    offset = 0.0
    narration_rows = []
    for segment in SEGMENTS:
        source = NARRATION / f"{segment.narration}.mp3"
        if not source.is_file():
            raise FileNotFoundError(f"缺旁白：{source}")
        seconds = _narration_seconds(source)
        narration_rows.append(
            {
                "scene": segment.scene_id,
                "file": str(source.relative_to(PROJECT_ROOT)),
                "start": round(offset, 3),
                "seconds": round(seconds, 3),
                "fits": seconds <= segment.duration,
            }
        )
        offset += segment.duration
    return {
        "total_seconds": round(total, 3),
        "cues": len(cues),
        "narration": narration_rows,
        "music": str((AUDIO / "music.wav").relative_to(PROJECT_ROOT)),
        "sfx": str((AUDIO / "sfx.wav").relative_to(PROJECT_ROOT)),
    }


def _ass_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours}:{minutes:02d}:{secs:02d}.{ms // 10:02d}"


def write_ass(path: Path) -> Path:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: AK,Microsoft YaHei,40,&H00F2F5F7,&H00FFFFFF,&H8C000000,&H00000000,-1,0,0,0,100,100,0.5,0,"
        "1,2,0,2,60,60,110,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    offset = 0.0
    for segment in SEGMENTS:
        start = offset + 0.4
        end = offset + segment.duration - 0.15
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},AK,,0,0,0,,{segment.subtitle}"
        )
        offset += segment.duration
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def stage_assemble() -> dict:
    CLIPS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ass_path = write_ass(OUT / "prototype.ass")

    missing = [seg.scene_id for seg in SEGMENTS if not (CLIPS / f"{seg.scene_id}.mp4").exists()]
    if missing:
        raise FileNotFoundError(f"这些镜头还没渲染：{missing}")

    concat_list = OUT / "prototype_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{(CLIPS / f'{seg.scene_id}.mp4').as_posix()}'" for seg in SEGMENTS) + "\n",
        encoding="utf-8",
    )

    total = sum(item.duration for item in SEGMENTS)
    narration_inputs: list[str] = []
    filters: list[str] = []
    offset = 0.0
    index = 0
    for segment in SEGMENTS:
        narration_inputs += ["-i", str(NARRATION / f"{segment.narration}.mp3")]
        delay_ms = int(round(offset * 1000))
        filters.append(f"[{index + 1}:a]adelay={delay_ms}|{delay_ms}[n{index}]")
        offset += segment.duration
        index += 1

    narration_mix = "".join(f"[n{i}]" for i in range(index))
    filter_complex = (
        "[0:v]subtitles=prototype.ass[vout];"
        + ";".join(filters)
        + f";{narration_mix}amix=inputs={index}:duration=longest:normalize=0[narr];"
        + "[1:a]volume=1.0[music];[2:a]volume=0.85[sfx];"
        + "[narr][music][sfx]amix=inputs=3:duration=longest:normalize=0,"
        + "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    )

    command = [
        str(FFMPEG),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(AUDIO / "music.wav"),
        "-i",
        str(AUDIO / "sfx.wav"),
        *narration_inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        f"{total:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(OUT / "prototype_15_20s.mp4"),
    ]
    proc = _run(command, log_name="assemble_prototype.log", cwd=OUT)
    if proc.returncode != 0:
        raise RuntimeError(f"拼片失败：{proc.stderr[-800:]}")

    target = OUT / "prototype_15_20s.mp4"
    return {
        "video": str(target.relative_to(PROJECT_ROOT)),
        "bytes": target.stat().st_size,
        "seconds": round(total, 3),
        "subtitles": str(ass_path.relative_to(PROJECT_ROOT)),
    }


def _probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout or "{}")


def _scan(path: Path, pattern: str) -> list[float]:
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path), "-vf", pattern, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    durations = []
    for line in (proc.stderr or "").splitlines():
        if "black_start" in line or "freeze_start" in line or "silence_start" in line:
            continue
        marker = "black_duration:" if "black_duration" in line else (
            "freeze_duration:" if "freeze_duration" in line else ("silence_duration:" if "silence_duration" in line else "")
        )
        if marker:
            durations.append(float(line.split(marker)[1].strip().rstrip("s")))
    return durations


def _loudness(path: Path) -> dict:
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a", "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    integrated = None
    for line in (proc.stderr or "").splitlines():
        text = line.strip()
        if text.startswith("I:") and "LUFS" in text:
            integrated = float(text.split("I:")[1].split("LUFS")[0].strip())
    return {"integrated_lufs": integrated}


def stage_qc() -> dict:
    QC.mkdir(parents=True, exist_ok=True)
    target = OUT / "prototype_15_20s.mp4"
    info = _probe(target)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})

    black = _scan(target, "blackdetect=d=0.2:pix_th=0.10")
    freeze = _scan(target, "freezedetect=n=0.001:d=1.0")
    silence = _scan(target, "silencedetect=n=-40dB:d=1.2")
    loud = _loudness(target)

    duration = float(info.get("format", {}).get("duration", 0))
    checks = {
        "duration_in_range": 15.0 <= duration <= 20.5,
        "portrait_1080x1920": (video.get("width"), video.get("height")) == (WIDTH, HEIGHT),
        "fps_30": str(video.get("r_frame_rate")) in {"30/1"},
        "has_audio": audio.get("codec_name") is not None,
        "no_long_black": max(black, default=0.0) <= 0.5,
        "no_long_freeze": max(freeze, default=0.0) <= 1.5,
        "no_long_silence": max(silence, default=0.0) <= 2.0,
        "loudness_near_target": loud["integrated_lufs"] is not None and abs(loud["integrated_lufs"] + 16) <= 3,
    }
    report = {
        "video": str(target.relative_to(PROJECT_ROOT)),
        "bytes": target.stat().st_size,
        "duration": round(duration, 3),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "audio_channels": audio.get("channels"),
        "metrics": {
            "black_segments": len(black),
            "black_longest": round(max(black, default=0.0), 3),
            "freeze_segments": len(freeze),
            "freeze_longest": round(max(freeze, default=0.0), 3),
            "silence_segments": len(silence),
            "silence_longest": round(max(silence, default=0.0), 3),
            "integrated_lufs": loud["integrated_lufs"],
        },
        "checks": checks,
        "failed": [name for name, ok in checks.items() if not ok],
    }
    (QC / "prototype_qc.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 V2 的 15–20 秒真实视频样片")
    parser.add_argument(
        "--stage", default="all", choices=["scenes", "render", "audio", "assemble", "qc", "all"]
    )
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--force", action="store_true", help="已存在的镜头也重渲")
    args = parser.parse_args(argv)

    payload: dict = {}
    if args.stage in {"scenes", "all"}:
        payload["scenes"] = stage_scenes()
    if args.stage in {"render", "all"}:
        payload["render"] = stage_render(tuple(args.only), force=args.force)
    if args.stage in {"audio", "all"}:
        payload["audio"] = stage_audio()
    if args.stage in {"assemble", "all"}:
        payload["assemble"] = stage_assemble()
    if args.stage in {"qc", "all"}:
        payload["qc"] = stage_qc()

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
