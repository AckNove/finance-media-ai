"""按素材计划自动装配成片：`python -m backend.scripts.auto_film --job <任务号>`

输入
----
- `storyboard/materials_plan.json`：每段口播稿 + 视觉意图 + 已选素材 + 缺口标记
- `source_register.csv`：素材登记表（拿本地路径、许可、作者）
- `audio/narration/sh*.mp3`：每句配音（已实测时长）

输出
----
`content/jobs/<job>/v2auto/output/final.mp4` 与 `v2auto/qc/final_qc.json`

四条规矩（都来自前面几轮的教训）
--------------------------------
1. 数据层/动画镜**不硬塞真实素材**：计划里标 animation 的就出程序化动画，标 manual 的直接报缺口；
2. 每段时长以**配音实测**为准（`materials_plan.json` 的 `seconds` 已经来自实测）；
3. 画面必须**逐帧确定性**（`motion.py` / `photo.py` 的 seek 都是 t 的纯函数）；
4. 成片过一次技术 QC（黑帧/冻结/静音/响度/时长），不给"看着还行"放行。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.app.video.cinema import motion
from backend.app.video.cinema import photo as photo_mod
from backend.app.video.cinema.audio import music_samples, write_wav

ROOT = Path(__file__).resolve().parents[2]
JOBS = ROOT / "content" / "jobs"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
RENDER = ROOT / "backend" / "scripts" / "cinema_render.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
NK_PYTHON = ROOT / "tools" / "openkatana" / ".venv" / "Scripts" / "python.exe"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
ACCENT = "#b6ff1f"
COOL = "#5aa9d6"


@dataclass
class Segment:
    index: int
    text: str
    seconds: float
    kind: str          # real_video | real_image | data | animation | missing
    scene_id: str
    asset_id: str = ""
    local_path: str = ""
    credit: str = ""


def _run(args: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(args, cwd=str(cwd) if cwd else None,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"命令失败（{result.returncode}）：{' '.join(args[:4])}\n{result.stderr[-1500:]}")


def _register_index(job: Path) -> dict[str, dict]:
    tables = sorted(job.rglob("source_register.csv"), key=lambda p: len(str(p)))
    if not tables:
        return {}
    rows: dict[str, dict] = {}
    with tables[0].open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = str(row.get("asset_id") or "").strip()
            if key:
                rows[key] = row
    return rows


def _local_path(job: Path, row: dict) -> Path:
    raw = str(row.get("local_path") or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / raw
    if path.is_file():
        return path
    # 登记表里写的是相对路径时，按文件名在任务目录里找
    for candidate in job.rglob(Path(raw).name):
        return candidate
    return path


def _plan(job: Path) -> dict:
    path = job / "storyboard" / "materials_plan.json"
    if not path.is_file():
        raise SystemExit(f"没有素材计划：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _hook(text: str, limit: int = 12) -> str:
    """画面上只放短钩子（整句由字幕承担），避免画面文字与字幕重复。"""

    clean = " ".join(str(text or "").split())
    clean = clean.split("，")[0].split("。")[0]
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _pick_template(text: str, kind: str) -> str:
    if re.search(r"\d+(\.\d+)?%", text or ""):
        return "number"
    if kind == "animation" and "对比" in (text or ""):
        return "bars"
    return "flow"


def _motion_items(text: str) -> tuple[motion.MotionItem, ...]:
    number = re.search(r"(\d+(?:\.\d+)?)(%|亿元|万亿|个百分点|元)", text or "")
    if number:
        return (motion.MotionItem(label=text[:18], value=float(number.group(1)), unit=number.group(2)),)
    return ()


def build_segments(job: Path) -> tuple[list[Segment], list[str]]:
    plan = _plan(job)
    register = _register_index(job)
    segments: list[Segment] = []
    holes: list[str] = []

    for shot in plan.get("shots", []):
        index = int(shot.get("index") or 0)
        text = str(shot.get("text") or "")
        seconds = float(shot.get("seconds") or 0.0)
        intent = shot.get("intent") or {}
        kind = str(intent.get("kind") or "real_image")
        asset_id = str(shot.get("asset_id") or "").strip()
        fallback = str(shot.get("fallback") or "").strip()

        if fallback == "manual" or not (asset_id or kind == "animation"):
            holes.append(f"第 {index} 段没有可用素材：{shot.get('note')}")
            segments.append(Segment(index=index, text=text, seconds=seconds, kind="missing",
                                    scene_id=f"S{index:02d}"))
            continue

        if kind == "animation" and not asset_id:
            segments.append(Segment(index=index, text=text, seconds=seconds, kind="animation",
                                    scene_id=f"A{index:02d}"))
            continue

        row = register.get(asset_id)
        if row is None:
            holes.append(f"第 {index} 段的素材 {asset_id} 不在登记表里")
            segments.append(Segment(index=index, text=text, seconds=seconds, kind="missing",
                                    scene_id=f"S{index:02d}"))
            continue

        path = _local_path(job, row)
        if not path.is_file():
            holes.append(f"第 {index} 段的素材文件不存在：{path}")
            segments.append(Segment(index=index, text=text, seconds=seconds, kind="missing",
                                    scene_id=f"S{index:02d}"))
            continue

        is_video = path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
        segments.append(Segment(
            index=index, text=text, seconds=seconds,
            kind="real_video" if is_video else "real_image",
            scene_id=f"V{index:02d}",
            asset_id=asset_id,
            local_path=str(path),
            credit=f"{row.get('publisher_or_subject', '')} · {row.get('license', '')}",
        ))
    return segments, holes


def _prepare_video_clip(segment: Segment, target: Path) -> Path:
    """把素材裁成竖屏并锁时长（setsar=1：否则容器会变成 1440×1920 再被二次裁切）。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    duration = round(segment.seconds + 0.4, 3)
    start = 0.0
    # 素材中段通常比开头更有信息量；短视频就从头开始
    probe = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", segment.local_path],
                           capture_output=True, text=True)
    try:
        total = float(probe.stdout.strip() or 0)
    except ValueError:
        total = 0.0
    if total > duration * 2.2:
        start = max(0.0, (total - duration) * 0.42)

    _run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-t", f"{duration:.2f}", "-i", segment.local_path,
        "-vf", f"crop=min(iw\\,ih*9/16):ih,scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1,fps={FPS}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "15", "-keyint_min", "15",
        "-pix_fmt", "yuv420p", str(target),
    ])
    return target


def stage_scenes(job: Path) -> dict:
    out = job / "v2auto"
    scenes = out / "scenes"
    media = out / "media"
    segments, holes = build_segments(job)
    built: list[dict] = []

    for segment in segments:
        scene_dir = scenes / segment.scene_id
        if segment.kind == "missing":
            scene = motion.MotionScene(
                scene_id=segment.scene_id, template="flow", seconds=segment.seconds,
                kicker="缺素材", title="这一段还没配到画面", source="待补素材后重跑",
                items=(motion.MotionItem(label="缺口", note="计划里标了待补"),),
            )
            built.append(motion.build_motion_scene(scene, scene_dir=scene_dir))
            continue

        if segment.kind == "animation":
            template = _pick_template(segment.text, "animation")
            scene = motion.MotionScene(
                scene_id=segment.scene_id, template=template, seconds=segment.seconds,
                kicker="机制", title=segment.text[:26],
                items=_motion_items(segment.text) or (
                    motion.MotionItem(label="发电", note="按电量卖"),
                    motion.MotionItem(label="输送", note="过网费"),
                    motion.MotionItem(label="待命", note="按容量结算", color=COOL),
                ),
                source="依据已确认结论绘制（非实拍）",
            )
            built.append(motion.build_motion_scene(scene, scene_dir=scene_dir))
            continue

        if segment.kind == "real_video":
            clip = _prepare_video_clip(segment, media / f"{segment.scene_id}.mp4")
            scene = photo_mod.PhotoScene(
                scene_id=segment.scene_id, asset_id=segment.asset_id,
                photo_rel="clip.mp4",
                duration=segment.seconds,
                # 推得狠一点：源片本身很静（慢摇镜头），推得太轻会被 freezedetect 判定格
                move=photo_mod.PhotoMove(z0=1.10, z1=1.34, x0=0.0, x1=0.02, y0=0.01, y1=-0.03),
                texts=(
                    photo_mod.PhotoText(role="title", text=_hook(segment.text), y=0.70, at=0.15),
                ),
                credit=segment.credit,
                video=True, video_start=0.0, video_rate=1.0,
            )
            built.append(photo_mod.build_photo_scene(scene, register_csv=None, scene_dir=scene_dir, media_src=clip))
            continue

        # real_image
        scene = photo_mod.PhotoScene(
            scene_id=segment.scene_id, asset_id=segment.asset_id, photo_rel="photo.jpg",
            duration=segment.seconds,
            move=photo_mod.PhotoMove(z0=1.06, z1=1.26, x0=0.0, x1=0.02, y0=0.01, y1=-0.02),
            texts=(photo_mod.PhotoText(role="title", text=_hook(segment.text), y=0.70, at=0.15),),
            credit=segment.credit,
        )
        built.append(photo_mod.build_photo_scene(scene, register_csv=None, scene_dir=scene_dir,
                                                 media_src=Path(segment.local_path)))

    (out / "segments.json").write_text(
        json.dumps({"segments": [s.__dict__ for s in segments], "holes": holes,
                    "scenes": built}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scenes": built, "holes": holes, "dir": str(scenes)}


def stage_render(job: Path, *, only: set[str] | None = None) -> dict:
    out = job / "v2auto"
    scenes = out / "scenes"
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    for scene_dir in sorted(p for p in scenes.iterdir() if p.is_dir()):
        if only and scene_dir.name not in only:
            continue
        clip = clips / f"{scene_dir.name}.mp4"
        if clip.is_file() and not only:
            rendered.append({"scene": scene_dir.name, "skipped": True, "clip": str(clip)})
            continue
        index_html = scene_dir / "index.html"
        if not index_html.is_file():
            raise SystemExit(f"镜头目录里没有 index.html：{scene_dir}")
        _run([str(NK_PYTHON), str(RENDER), "--scene", str(scene_dir), "--out", str(out / "frames" / scene_dir.name),
              "--fps", str(FPS), "--width", str(WIDTH), "--height", str(HEIGHT),
              "--mp4", str(clip), "--quiet"])
        rendered.append({"scene": scene_dir.name, "skipped": False, "clip": str(clip)})
    return {"rendered": rendered}


def _ass_time(seconds: float) -> str:
    total = max(0.0, seconds)
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    rest = total % 60
    return f"{hours}:{minutes:02d}:{rest:05.2f}"


def stage_audio_and_subs(job: Path) -> dict:
    out = job / "v2auto"
    segments, _ = build_segments(job)
    narration_dir = job / "audio" / "narration"

    cursor = 0.0
    lines: list[tuple[float, float, str]] = []
    pieces: list[Path] = []
    for segment in segments:
        source = narration_dir / f"sh{segment.index:02d}.mp3"
        if not source.is_file():
            raise SystemExit(f"缺配音：{source}")
        piece = out / "audio" / f"sh{segment.index:02d}.wav"
        piece.parent.mkdir(parents=True, exist_ok=True)
        _run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
              "-ar", "48000", "-ac", "2", str(piece)])
        pieces.append(piece)
        lines.append((cursor, cursor + segment.seconds, segment.text))
        cursor += segment.seconds

    total = cursor
    concat_list = out / "audio" / "narration.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8")
    narration_wav = out / "audio" / "narration.wav"
    _run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(concat_list), "-ar", "48000", "-ac", "2", str(narration_wav)])

    music = music_samples(total, level=0.06)
    music_wav = out / "audio" / "music.wav"
    write_wav(music_wav, music, sample_rate=48000, channels=2)

    ass = out / "output" / "final.ass"
    ass.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: AK,Microsoft YaHei,40,&H00E8F0F6,&H00FFFFFF,&H8C000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,60,60,110,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    body = "".join(
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},AK,,0,0,0,,{text}\n"
        for start, end, text in lines
    )
    ass.write_text(header + body, encoding="utf-8")

    def _srt_time(seconds: float) -> str:
        ms = int(round(max(0.0, seconds) * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    srt = ass.with_suffix(".srt")
    srt.write_text("".join(
        f"{order}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n\n"
        for order, (start, end, text) in enumerate(lines, start=1)
    ), encoding="utf-8")
    return {"seconds": round(total, 3), "narration": str(narration_wav), "music": str(music_wav),
            "subtitles": str(ass), "lines": len(lines)}


def stage_assemble(job: Path) -> dict:
    out = job / "v2auto"
    segments, _ = build_segments(job)
    ordered = [out / "clips" / f"{segment.scene_id}.mp4" for segment in segments]
    clips = [clip for clip in ordered if clip.is_file()]
    if len(clips) != len(segments):
        missing = [c.name for c in ordered if not c.is_file()]
        raise SystemExit(f"这些镜头还没渲染：{missing}")
    stage_audio_and_subs(job)

    concat_list = out / "clips" / "concat.txt"
    concat_list.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    target = out / "output" / "final.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    _run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
          "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-i", str(out / "audio" / "narration.wav"), "-i", str(out / "audio" / "music.wav"),
          "-filter_complex",
          "[0:v]subtitles=final.ass,vignette=PI/4.8,noise=alls=5:allf=t+u[vout];"
          # loudnorm 必须写在 filter_complex 里：同一个音频流不能既走复杂滤镜又用 -af 简单滤镜
          "[1:a][2:a]amix=inputs=2:duration=first:weights=1 0.35,loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
          "-map", "[vout]", "-map", "[aout]",
          "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          str(target)], cwd=out / "output")
    return {"video": str(target), "bytes": target.stat().st_size}



def stage_qc(job: Path) -> dict:
    """跑一遍技术 QC（黑帧/冻结/静音/响度/分辨率/时长）。"""

    out = job / "v2auto"
    film = out / "output" / "final.mp4"
    if not film.is_file():
        raise SystemExit(f"还没有成片：{film}")
    segments, _ = build_segments(job)
    blueprint = out / "qc" / "blueprint.json"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    blueprint.write_text(json.dumps({
        "scenes": [{"scene_id": s.scene_id, "duration": round(s.seconds, 3)} for s in segments]
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    qc_dir = out / "qc"
    _run([str(PYTHON), "-m", "backend.scripts.cinema_qc",
          "--film", str(film),
          "--srt", str(out / "output" / "final.srt"),
          "--blueprint", str(blueprint),
          "--out", str(qc_dir)], cwd=ROOT)
    payload = json.loads((qc_dir / "technical_qc.json").read_text(encoding="utf-8"))
    return {"qc": str(qc_dir / "technical_qc.json"), "failed": payload.get("failed", [])}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按素材计划自动装配成片")
    parser.add_argument("--job", required=True)
    parser.add_argument("--jobs-root", default=str(JOBS))
    parser.add_argument("--stage", default="all", choices=["scenes", "render", "audio", "assemble", "qc", "all"])
    parser.add_argument("--only", default="", help="只渲染这些镜头（逗号分隔，如 A03,A04）")
    args = parser.parse_args(argv)

    job = Path(args.jobs_root) / args.job
    if not job.is_dir():
        print(f"任务目录不存在：{job}", file=sys.stderr)
        return 2

    payload: dict = {}
    if args.stage in {"scenes", "all"}:
        payload["scenes"] = stage_scenes(job)
    if args.stage in {"render", "all"}:
        only = {name.strip() for name in str(args.only or "").split(",") if name.strip()} or None
        payload["render"] = stage_render(job, only=only)
    if args.stage in {"audio", "all"}:
        payload["audio"] = stage_audio_and_subs(job)
    if args.stage in {"assemble", "all"}:
        payload["assemble"] = stage_assemble(job)
    if args.stage in {"qc", "all"}:
        payload["qc"] = stage_qc(job)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
