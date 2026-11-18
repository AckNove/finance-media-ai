"""按真人录音拼片：以 `voiceover/lines.json` 为唯一时间轴。

和 `auto_film.py` 的区别：
- 时间轴来自**用户录音**（每句的真实起止与时长），不是 TTS 估算；
- 字幕文本就是录音里说出来的话（`align_voiceover.py` 产出），不是脚本原文；
- 画面按句子内容分配：有具体数字的走数据动画，讲机制的走流程动画，
  讲真实世界的走已登记素材（图片或视频）；
- 声音是三层：人声（主）+ 程序化配乐（垫）+ 音效（切点与元素出现时）。

用法：
    python -m backend.scripts.voiceover_film --job v6_power_first_film --stage all
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
RENDER = ROOT / "backend" / "scripts" / "cinema_render.py"
NK_PYTHON = ROOT / "tools" / "openkatana" / ".venv" / "Scripts" / "python.exe"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
GAP = 0.18  # 与 align_voiceover 的句间距一致，字幕才能对上人声

ACCENT = "#b6ff1f"
COOL = "#5aa9d6"
PAPER = "#f2f5f7"

# 每镜目标时长：太短像闪屏，太长会变成"一张图讲三句"
SHOT_MIN_SECONDS = 4.2
SHOT_MAX_SECONDS = 8.5

NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|亿千瓦|万千瓦|亿千瓦时|千瓦时|亿|万|元|倍|度)")
UNIT_CANON = {"％": "%", "亿度": "亿千瓦时"}


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"命令失败（{result.returncode}）：{' '.join(args[:5])}\n{result.stderr[-2000:]}")
    return result.stdout


@dataclass
class Line:
    index: int
    text: str
    seconds: float
    take: str = ""


@dataclass
class Shot:
    shot_id: str
    kind: str  # real_video | real_image | motion
    seconds: float
    lines: list[Line]
    kicker: str = ""
    title: str = ""
    template: str = "flow"
    items: tuple[tuple[str, str, float, str], ...] = ()
    asset_id: str = ""
    asset_path: str = ""
    credit: str = ""
    grade: str = "cinema"


# ---------------------------------------------------------------- 读输入


def load_lines(job: Path) -> list[Line]:
    path = job / "voiceover" / "lines.json"
    if not path.is_file():
        raise SystemExit(f"没有找到 {path}，先跑 align_voiceover")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Line(index=int(r["index"]), text=str(r["text"]), seconds=float(r["seconds"]), take=str(r.get("take", ""))) for r in rows]


def line_timeline(lines: list[Line], *, gap: float = GAP) -> list[float]:
    """每句在成片里的起始秒（与拼接好的 voiceover.wav 完全一致）。"""

    starts: list[float] = []
    cursor = 0.0
    for line in lines:
        starts.append(cursor)
        cursor += line.seconds + gap
    return starts


def _library(job: Path) -> list[dict]:
    """已登记、可商用的素材（图片/视频）。"""

    entries: list[dict] = []
    for table in sorted(job.rglob("source_register.csv"), key=lambda p: len(str(p))):
        try:
            import csv

            with table.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("commercial_use", "")).strip().lower() != "allowed":
                        continue
                    local = str(row.get("local_path") or "").strip()
                    path = Path(local) if local else None
                    if path is not None and not path.is_absolute():
                        path = ROOT / path
                    if path is None or not path.is_file():
                        continue
                    suffix = path.suffix.lower()
                    entries.append(
                        {
                            "asset_id": str(row.get("asset_id") or "").strip(),
                            "path": path,
                            "kind": "real_video" if suffix in {".mp4", ".webm", ".mov", ".mkv"} else "real_image",
                            "credit": str(row.get("credit") or "").strip(),
                            "tags": " ".join(
                                str(row.get(key) or "") for key in ("used_in_shot", "note", "original_file")
                            ).lower(),
                        }
                    )
        except Exception:  # noqa: BLE001 - 登记表坏了不该拖垮整条片
            continue
    return entries


# ---------------------------------------------------------------- 分镜


KEYWORDS: dict[str, tuple[str, ...]] = {
    "plant": ("plant", "电厂", "cooling", "chimney", "moorburg", "煤电", "发电"),
    "line": ("line", "pylon", "transmission", "输电", "铁塔", "检修", "线路"),
    "station": ("station", "substation", "变电站", "变压器", "开关"),
    "wind": ("wind", "风", "风机"),
    "city": ("city", "night", "城市", "夜景", "工业"),
}


def _asset_for(text: str, library: list[dict], used: dict[str, int], *, prefer_video: bool = False) -> dict | None:
    """选一镜真实素材：先看关键词命中，再退回"用得最少"的那条。

    为什么允许轮转：纪录片里同一类场景（电厂/线路/变电站）反复出现是常态，
    总比让观众盯着第 40 张纯文字动画强。用得越少优先级越高，避免同一张图连着出现。
    """

    if not library:
        return None
    matched: list[tuple[float, dict]] = []
    for entry in library:
        score = 0.0
        haystack = f"{entry['asset_id']} {entry['tags']}".lower()
        for _tag, words in KEYWORDS.items():
            if any(word in text for word in words) and any(word.lower() in haystack for word in words):
                score += 1.0
        if score <= 0:
            continue
        score -= 0.15 * used.get(entry["asset_id"], 0)
        matched.append((score, entry))
    if matched:
        matched.sort(key=lambda pair: pair[0], reverse=True)
        return matched[0][1]

    pool = [entry for entry in library if entry["kind"] == ("real_video" if prefer_video else "real_image")]
    if not pool:
        pool = list(library)
    pool.sort(key=lambda entry: (used.get(entry["asset_id"], 0), -int(entry["kind"] == "real_video")))
    return pool[0]


def _numbers(text: str) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    for match in NUMBER_RE.finditer(text):
        raw_value = match.group(1)
        unit = UNIT_CANON.get(match.group(2), match.group(2))
        value = float(raw_value)
        rows.append((f"{raw_value}{unit}", unit, value))
    return rows


def _kicker(text: str) -> str:
    if re.search(r"结论|所以|一句话", text):
        return "结论"
    if re.search(r"但|反证|不可能", text):
        return "反证"
    if re.search(r"证据|数据|同比|亿千瓦|%", text):
        return "数据"
    if re.search(r"机制|因为|原因|所以.*变|定价", text):
        return "机制"
    return "叙述"


def _title(text: str, limit: int = 14) -> str:
    """画面上的短钩子：只取第一句，超长截断。

    为什么只取第一句：一镜里可能并了 2–3 句话，整段拼上去会出现
    「大家好，我是AK2025年上半年…」这种把两句粘在一起的痕迹。
    """

    clean = text.strip()
    for mark in ("。", "！", "？", "，", "；"):
        head, sep, _rest = clean.partition(mark)
        if sep and head.strip():
            clean = head.strip()
            break
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def build_shots(job: Path) -> list[Shot]:
    lines = load_lines(job)
    library = _library(job)
    used: dict[str, int] = {}

    shots: list[Shot] = []
    buffer: list[Line] = []
    shot_no = 0

    def flush() -> None:
        nonlocal buffer, shot_no
        if not buffer:
            return
        shot_no += 1
        text = "".join(line.text for line in buffer)
        seconds = sum(line.seconds for line in buffer) + GAP * len(buffer)
        numbers = _numbers(text)

        if len(numbers) >= 2:
            template = "bars"
        elif len(numbers) == 1:
            template = "number"
        else:
            template = "flow"

        prefer_video = bool(re.search(r"电厂|煤电|输电|线路|风机|现场|工地", text))
        asset = None if len(numbers) >= 2 else _asset_for(text, library, used, prefer_video=prefer_video)
        if asset is not None:
            used[asset["asset_id"]] = used.get(asset["asset_id"], 0) + 1
            kind = asset["kind"]
        else:
            kind = "motion"

        items: tuple[tuple[str, str, float, str], ...] = ()
        if kind == "motion":
            if template == "bars":
                items = tuple(
                    (label, "", value, ACCENT if position % 2 else COOL)
                    for position, (label, _unit, value) in enumerate(numbers[:4])
                )
            elif template == "number":
                label, _unit, value = numbers[0]
                items = ((label, "", value, ACCENT),)
            else:
                items = ()

        shots.append(
            Shot(
                shot_id=f"S{shot_no:02d}",
                kind=kind,
                seconds=round(seconds, 3),
                lines=list(buffer),
                kicker=_kicker(text),
                title=_title(text),
                template=template,
                items=items,
                asset_id=(asset or {}).get("asset_id", ""),
                asset_path=str((asset or {}).get("path", "")),
                credit=(asset or {}).get("credit", ""),
                grade="industrial" if (asset or {}).get("kind") == "real_video" else "cinema",
            )
        )
        buffer = []

    for line in lines:
        buffer.append(line)
        seconds = sum(item.seconds for item in buffer) + GAP * len(buffer)
        if seconds >= SHOT_MIN_SECONDS:
            flush()
    flush()

    # 太长的镜头砍成两半（避免一句话挂 8 秒不出新信息）
    final: list[Shot] = []
    for shot in shots:
        if shot.seconds <= SHOT_MAX_SECONDS or len(shot.lines) < 2:
            final.append(shot)
            continue
        half = len(shot.lines) // 2
        for part_no, part in enumerate((shot.lines[:half], shot.lines[half:]), start=1):
            text = "".join(line.text for line in part)
            seconds = sum(line.seconds for line in part) + GAP * len(part)
            final.append(
                Shot(
                    shot_id=f"{shot.shot_id}{'ab'[part_no - 1]}",
                    kind=shot.kind,
                    seconds=round(seconds, 3),
                    lines=part,
                    kicker=shot.kicker,
                    title=_title(text),
                    template=shot.template,
                    items=shot.items,
                    asset_id=shot.asset_id,
                    asset_path=shot.asset_path,
                    credit=shot.credit,
                    grade=shot.grade,
                )
            )
    for position, shot in enumerate(final, start=1):
        shot.shot_id = f"S{position:02d}"
    return final


# ---------------------------------------------------------------- 场景


def stage_scenes(job: Path) -> dict:
    out = job / "v2vo"
    scenes = out / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)

    shots = build_shots(job)
    receipts: list[dict] = []
    for shot in shots:
        scene_dir = scenes / shot.shot_id
        if shot.kind == "motion":
            receipt = _motion_scene(shot, scene_dir)
        else:
            source = Path(shot.asset_path)
            if shot.kind == "real_video":
                clip = media / f"{shot.shot_id}.mp4"
                _prepare_clip(source, clip, shot.seconds)
                receipt = _photo_scene(shot, scene_dir, clip, video=True)
            else:
                receipt = _photo_scene(shot, scene_dir, source, video=False)
        receipts.append(receipt)

    (out / "shots.json").write_text(json.dumps([shot.__dict__ for shot in shots], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"shots": len(shots), "scenes": str(scenes)}


def _prepare_clip(source: Path, target: Path, seconds: float) -> None:
    """把素材裁成 9:16 并截到镜头时长（多给 0.5s 余量，防止尾帧不够）。"""

    _run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "1.0", "-i", str(source), "-t", f"{seconds + 0.6:.3f}",
            "-vf", "crop=min(iw\\,ih*9/16):ih,scale=1080:1920:flags=lanczos,setsar=1,fps=30",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "15", "-keyint_min", "15",
            str(target),
        ]
    )


def _photo_scene(shot: Shot, scene_dir: Path, media: Path, *, video: bool) -> dict:
    from backend.app.video.cinema import photo as photo_mod

    scene = photo_mod.PhotoScene(
        scene_id=shot.shot_id,
        asset_id=shot.asset_id or shot.shot_id,
        photo_rel=media.name,
        duration=shot.seconds,
        move=photo_mod.PhotoMove(z0=1.04, z1=video and 1.14 or 1.10, x0=-0.01, x1=0.012),
        texts=(
            photo_mod.PhotoText(role="kicker", text=shot.kicker, y=0.60, at=0.10),
            photo_mod.PhotoText(role="title", text="", y=0.66, at=0.25),
        ),
        credit=shot.credit or "已登记素材",
        grade=shot.grade if shot.grade in photo_mod.GRADES else "cinema",
        video=video,
    )
    return photo_mod.build_photo_scene(scene, project_root=ROOT, scene_dir=scene_dir, media_src=media)


def _motion_scene(shot: Shot, scene_dir: Path) -> dict:
    from backend.app.video.cinema import motion as motion_mod

    items = tuple(
        motion_mod.MotionItem(label=label, note=note, value=value, unit="", color=color)
        for label, note, value, color in shot.items
    )
    scene = motion_mod.MotionScene(
        scene_id=shot.shot_id,
        template=shot.template,
        seconds=shot.seconds,
        kicker=shot.kicker,
        title=shot.title,
        items=items,
        source="依据已确认结论绘制（非实拍）",
    )
    return motion_mod.build_motion_scene(scene, scene_dir=scene_dir)


# ---------------------------------------------------------------- 渲染/音频/合成


def stage_render(job: Path, *, only: set[str] | None = None) -> dict:
    out = job / "v2vo"
    scenes = out / "scenes"
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    for scene_dir in sorted(path for path in scenes.iterdir() if path.is_dir()):
        if only and scene_dir.name not in only:
            continue
        clip = clips / f"{scene_dir.name}.mp4"
        _run(
            [
                str(NK_PYTHON), str(RENDER), "--scene", str(scene_dir), "--out", str(clips / scene_dir.name),
                "--fps", str(FPS), "--width", str(WIDTH), "--height", str(HEIGHT), "--mp4", str(clip), "--quiet",
            ]
        )
        rendered.append({"scene": scene_dir.name, "clip": str(clip)})
    return {"rendered": rendered}


def stage_audio(job: Path) -> dict:
    from backend.app.video.cinema import audio as audio_mod

    out = job / "v2vo"
    shots = build_shots(job)
    total = sum(shot.seconds for shot in shots)
    voice = job / "voiceover" / "voiceover.wav"
    if not voice.is_file():
        raise SystemExit("没有 voiceover.wav，先跑 align_voiceover")

    # 配乐：压得很低，只做"有人在后面放音乐"的底
    music = audio_mod.music_samples(total, bpm=64, seed=11, level=0.05)
    audio_mod.write_wav(out / "music.wav", music, sample_rate=48000, channels=1)

    # 音效：每个切点一记 whoosh；数据镜在元素长出时加 tick/spark；段落转折处加 impact
    cues: list[tuple[float, str]] = []
    cursor = 0.0
    for position, shot in enumerate(shots):
        if position:
            cues.append((max(0.0, cursor - 0.10), "whoosh"))
        if shot.kind == "motion":
            cues.append((cursor + 0.45, "tick"))
            if shot.template in {"bars", "number"}:
                cues.append((cursor + 1.05, "spark"))
        if shot.kicker in {"结论", "反证"}:
            cues.append((cursor + 0.20, "impact"))
        cursor += shot.seconds
    track = audio_mod.place_cues(total, cues)
    audio_mod.write_wav(out / "sfx.wav", track, sample_rate=48000, channels=1)
    return {"seconds": round(total, 3), "cues": len(cues)}


def _ass(job: Path, shots: list[Shot]) -> Path:
    """字幕按句子走（不是按镜头），时间轴与 voiceover.wav 一致。"""

    out = job / "v2vo" / "output"
    out.mkdir(parents=True, exist_ok=True)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: AK,Microsoft YaHei,44,&H00F5F2F2,&H00FFFFFF,&H90000000,&H00000000,-1,0,0,0,100,100,0.6,0,1,2,0,2,66,66,110,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    rows: list[str] = []
    cursor = 0.0
    for shot in shots:
        for line in shot.lines:
            start, end = cursor, cursor + line.seconds
            rows.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},AK,,0,0,0,,{line.text}")
            cursor = end + GAP
    path = out / "final.ass"
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _ass_time(seconds: float) -> str:
    total = int(round(seconds * 100))
    hours, total = divmod(total, 360000)
    minutes, total = divmod(total, 6000)
    secs, centis = divmod(total, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _srt(shots: Sequence[Shot]) -> str:
    """把每句话的时间轴写成 SRT（与 ASS 同一份数据，便于 QC 与外部播放器核对）。"""

    def stamp(seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, rest = divmod(millis, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, ms = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    blocks: list[str] = []
    index = 0
    clock = 0.0
    for shot in shots:
        for line in shot.lines:
            index += 1
            start = clock
            end = clock + line.seconds
            blocks.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{line.text}\n")
            clock = end + GAP
    return "\n".join(blocks)


def stage_assemble(job: Path) -> dict:
    out = job / "v2vo"
    shots = build_shots(job)
    clips = [out / "clips" / f"{shot.shot_id}.mp4" for shot in shots]
    missing = [clip.name for clip in clips if not clip.is_file()]
    if missing:
        raise SystemExit(f"这些镜头还没渲染：{missing}")

    listing = out / "clips" / "concat.txt"
    # 走 concat 的路径必须是绝对路径：下面 ffmpeg 的 cwd 是 output 目录
    listing.write_text(
        "\n".join(f"file '{clip.resolve().as_posix()}'" for clip in clips) + "\n", encoding="utf-8"
    )
    ass = _ass(job, shots)

    output = out / "output"
    output.mkdir(parents=True, exist_ok=True)
    # ffmpeg 的 cwd 已经是 output 目录：输入/输出都必须给绝对路径，否则解析到 output 之下又套一层相对前缀
    target = (output / "final.mp4").resolve()
    _run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing.resolve()),
            "-i", str((job / "voiceover" / "voiceover.wav").resolve()),
            "-i", str((out / "music.wav").resolve()),
            "-i", str((out / "sfx.wav").resolve()),
            "-filter_complex",
            # 字幕烧在画面上，再压暗角与颗粒
            f"[0:v]subtitles={ass.name},vignette=PI/4.8,noise=alls=4:allf=t+u[vout];"
            # 人声为主，配乐 0.16、音效 0.30；loudnorm 收在链路最后
            "[1:a][2:a][3:a]amix=inputs=3:duration=first:weights=1 0.16 0.30,loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(target),
        ],
        cwd=output,
    )
    srt = output / "final.srt"
    srt.write_text(_srt(shots), encoding="utf-8")
    return {"video": str(target), "bytes": target.stat().st_size, "shots": len(shots)}


def stage_qc(job: Path) -> dict:
    out = job / "v2vo"
    blueprint = out / "qc" / "blueprint.json"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    shots = build_shots(job)
    blueprint.write_text(
        json.dumps(
            {"scenes": [{"scene_id": shot.shot_id, "duration": shot.seconds} for shot in shots]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(
        [
            str(PYTHON), "-m", "backend.scripts.cinema_qc",
            "--film", str(out / "output" / "final.mp4"),
            "--blueprint", str(blueprint),
            "--out", str(out / "qc"),
        ]
    )
    return json.loads(result[result.find("{") :])


STAGES = ("scenes", "render", "audio", "assemble", "qc")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="按真人录音拼片")
    parser.add_argument("--job", required=True)
    parser.add_argument("--stage", default="all", choices=(*STAGES, "all"))
    parser.add_argument("--only", default="", help="只渲染指定镜头，逗号分隔")
    parser.add_argument("--jobs-root", default="content/jobs")
    args = parser.parse_args(argv)

    job = Path(args.jobs_root) / args.job
    only = {item.strip() for item in args.only.split(",") if item.strip()} or None

    payload: dict[str, object] = {}
    if args.stage in {"scenes", "all"}:
        payload["scenes"] = stage_scenes(job)
    if args.stage in {"render", "all"}:
        payload["render"] = stage_render(job, only=only)
    if args.stage in {"audio", "all"}:
        payload["audio"] = stage_audio(job)
    if args.stage in {"assemble", "all"}:
        payload["assemble"] = stage_assemble(job)
    if args.stage in {"qc", "all"}:
        payload["qc"] = stage_qc(job)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
