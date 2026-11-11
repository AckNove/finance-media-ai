"""首条视频生产流水线（文件型 job，不碰真实数据库）。

设计边界（对应 m04268 任务书）：

* **不写库**：所有产物落在 ``content/jobs/<job_id>/`` 里，只读源文件、只写文件。
* **不造轮子**：PDF/XLSX/CSV 解析走 ``capabilities.adapters.markitdown_adapter``（MarkItDown），
  图表走 matplotlib，配音走 Windows 本地 SAPI（Microsoft Huihui），
  编码与合片走本机 FFmpeg。
* **不假装**：没有音频就不生成音轨；解析失败就报错停下；每一步都写 ``_work/`` 下的日志。

用法::

    python -m backend.scripts.build_video_job --job 20261110_jgb_gold_repricing --stage all
    python -m backend.scripts.build_video_job --job <id> --stage collect
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backend.app.capabilities.tools import get_tool_registry
from backend.app.core.caller import CallerContext
from backend.app.core.config import PROJECT_ROOT

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"

CANVAS = (1080, 1920)
FPS = 30
FONT_FILE = Path(r"C:\Windows\Fonts\msyh.ttc")
VOICE_NAME = "Microsoft Huihui Desktop"

STORYBOARD_COLUMNS = [
    "scene_id",
    "order",
    "visual_type",
    "visual_asset",
    "narration",
    "caption",
    "duration_target_s",
]


# --- 基础工具 --------------------------------------------------------------


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_dir(job_id: str) -> Path:
    path = JOBS_ROOT / job_id
    if not path.is_dir():
        raise SystemExit(f"job 目录不存在: {path}")
    return path


def work_dir(job: Path) -> Path:
    path = job / "_work"
    path.mkdir(exist_ok=True)
    return path


def read_storyboard(job: Path) -> list[dict[str, str]]:
    path = job / "storyboard.csv"
    if not path.is_file():
        raise SystemExit(f"缺少分镜文件: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = [c for c in STORYBOARD_COLUMNS if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"storyboard.csv 缺少列: {missing}")
    return sorted(rows, key=lambda r: int(r["order"]))


def agent_caller(task_id: str) -> CallerContext:
    """流水线扮演"内部执行器"，不是外部 HTTP 请求。

    这是 CallerContext 唯一被允许的 agent 构造方式（source=executor），
    也只有本仓库内的代码能这样构造。
    """
    return CallerContext.for_executor(
        agent="financial_researcher",
        task_id=task_id,
        attempt_token="local-pipeline",
        db_scopes={"source_extractions"},
    )


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"命令失败({completed.returncode}): {' '.join(cmd)}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def find_ffmpeg() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("找不到 ffmpeg / ffprobe，请先安装并加入 PATH")
    return ffmpeg, ffprobe


# --- 阶段 1：采集与解析 ----------------------------------------------------


def stage_collect(job: Path) -> dict[str, object]:
    """用 MarkItDown 适配器把原始文件转成 markdown，并产出 source_register.csv。"""
    manifest_path = job / "sources.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺少来源清单: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    registry = get_tool_registry()
    caller = agent_caller(f"job-{job.name}")
    register_rows: list[dict[str, object]] = []

    for item in manifest:
        rel = item["path"]
        target = (job / rel).resolve()
        if not target.is_file():
            raise SystemExit(f"来源文件不存在: {target}")
        result = registry.dispatch(caller, item["tool"], path=str(target))
        if result.status != "ok":
            raise SystemExit(
                f"解析失败 {rel}: status={result.status} code={result.error_code} "
                f"msg={result.error_message}"
            )
        data = result.data or {}
        text = str(data["text"])
        out_name = f"{item['source_id']}_{item['slug']}.md"
        (job / "source_text" / out_name).write_text(
            f"<!-- 由 {data['parser']} {data['parser_version']} 解析 "
            f"{rel} 生成；字符数 {data['char_count']}，页数 {data.get('page_count')} -->\n\n{text}\n",
            encoding="utf-8",
        )
        register_rows.append(
            {
                "source_id": item["source_id"],
                "title": item["title"],
                "type": target.suffix.lstrip(".").upper(),
                "credibility": item["credibility"],
                "origin_path": rel,
                "converted_path": f"source_text/{out_name}",
                "sha256": data["sha256"],
                "byte_size": data["byte_size"],
                "char_count": data["char_count"],
                "page_count": data.get("page_count") or "",
                "parser": f"{data['parser']} {data['parser_version']}",
                "used_for": item["used_for"],
                "fetched_at": utcnow(),
            }
        )

    register_path = job / "source_register.csv"
    with register_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(register_rows[0]))
        writer.writeheader()
        writer.writerows(register_rows)

    return {"sources": len(register_rows), "register": str(register_path)}


# --- 阶段 2：图表素材 ------------------------------------------------------


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    if FONT_FILE.is_file():
        font_manager.fontManager.addfont(str(FONT_FILE))
        name = font_manager.FontProperties(fname=str(FONT_FILE)).get_name()
        plt.rcParams["font.family"] = name
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _fig(plt, *, width: float = 1080, height: float = 1920):
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor="#0f1116")
    return fig


def _caption(plt, fig, text: str) -> None:
    fig.text(0.5, 0.045, text, ha="center", va="bottom", fontsize=17, color="#8b93a7")


def _load_frames(job: Path) -> tuple[object, object]:
    import pandas as pd

    jgb = pd.read_csv(job / "source_text/raw/S01_jgb_yields.csv", encoding="utf-8-sig")
    jgb["日期"] = pd.to_datetime(jgb["日期"])
    jgb = jgb.sort_values("日期")

    gold = pd.read_csv(job / "source_text/raw/S02_gold_price.csv", encoding="utf-8-sig")
    gold["日期"] = pd.to_datetime(gold["日期"])
    gold["收盘"] = pd.to_numeric(gold["收盘"].astype(str).str.replace(",", ""), errors="coerce")
    gold = gold.dropna(subset=["收盘"]).sort_values("日期")
    return jgb, gold


def stage_charts(job: Path) -> dict[str, object]:
    plt = _setup_matplotlib()
    jgb, gold = _load_frames(job)
    assets = job / "assets"
    assets.mkdir(exist_ok=True)
    made: list[str] = []

    # 图 1：日本 10 年期国债收益率
    fig = _fig(plt)
    ax = fig.add_axes([0.12, 0.28, 0.80, 0.50])
    ax.set_facecolor("#0f1116")
    ax.plot(jgb["日期"], jgb["收盘"], color="#ff5c5c", linewidth=3)
    first, last = jgb.iloc[0], jgb.iloc[-1]
    ax.scatter([first["日期"], last["日期"]], [first["收盘"], last["收盘"]], color="#ffd166", zorder=5, s=90)
    ax.annotate(
        f"{first['收盘']:.3f}%",
        (first["日期"], first["收盘"]),
        textcoords="offset points",
        xytext=(12, -28),
        color="#ffd166",
        fontsize=20,
    )
    ax.annotate(
        f"{last['收盘']:.3f}%",
        (last["日期"], last["收盘"]),
        textcoords="offset points",
        xytext=(-120, 18),
        color="#ffd166",
        fontsize=22,
    )
    ax.set_title("日本 10 年期国债收益率（%）", color="#f5f7fa", fontsize=30, pad=26)
    ax.tick_params(colors="#8b93a7", labelsize=16)
    for spine in ax.spines.values():
        spine.set_color("#2a3040")
    ax.grid(color="#1e2431", linewidth=1)
    _caption(plt, fig, "数据来源：S01 日本国债收益率日线（本地数据文件）")
    path = assets / "chart_01_jgb.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    made.append(str(path))

    # 图 2：黄金
    fig = _fig(plt)
    ax = fig.add_axes([0.12, 0.28, 0.80, 0.50])
    ax.set_facecolor("#0f1116")
    ax.plot(gold["日期"], gold["收盘"], color="#ffc857", linewidth=3)
    g_first, g_last = gold.iloc[0], gold.iloc[-1]
    growth = (g_last["收盘"] / g_first["收盘"] - 1) * 100
    ax.annotate(
        f"{g_first['收盘']:.0f}",
        (g_first["日期"], g_first["收盘"]),
        textcoords="offset points",
        xytext=(12, -28),
        color="#9ad0ff",
        fontsize=20,
    )
    ax.annotate(
        f"{g_last['收盘']:.0f}（+{growth:.0f}%）",
        (g_last["日期"], g_last["收盘"]),
        textcoords="offset points",
        xytext=(-260, 6),
        color="#9ad0ff",
        fontsize=22,
    )
    ax.set_title("国际金价（美元/盎司）", color="#f5f7fa", fontsize=30, pad=26)
    ax.tick_params(colors="#8b93a7", labelsize=16)
    for spine in ax.spines.values():
        spine.set_color("#2a3040")
    ax.grid(color="#1e2431", linewidth=1)
    _caption(plt, fig, "数据来源：S02 黄金价格日线（本地数据文件）")
    path = assets / "chart_02_gold.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    made.append(str(path))

    # 图 3：两条曲线同起点归一化
    fig = _fig(plt)
    ax = fig.add_axes([0.12, 0.28, 0.80, 0.50])
    ax.set_facecolor("#0f1116")
    jgb_idx = jgb["收盘"] / jgb["收盘"].iloc[0] * 100
    gold_idx = gold["收盘"] / gold["收盘"].iloc[0] * 100
    ax.plot(jgb["日期"], jgb_idx, color="#ff5c5c", linewidth=3, label="日本 10Y 国债收益率")
    ax.plot(gold["日期"], gold_idx, color="#ffc857", linewidth=3, label="国际金价")
    ax.axhline(100, color="#2a3040", linewidth=1.5, linestyle="--")
    ax.legend(facecolor="#171b24", edgecolor="#2a3040", labelcolor="#f5f7fa", fontsize=18, loc="upper left")
    ax.set_title("同起点归一化（2024 年初 = 100）", color="#f5f7fa", fontsize=29, pad=26)
    ax.tick_params(colors="#8b93a7", labelsize=16)
    for spine in ax.spines.values():
        spine.set_color("#2a3040")
    ax.grid(color="#1e2431", linewidth=1)
    _caption(plt, fig, "数据来源：S01 / S02，本地自算，未做任何平滑")
    path = assets / "chart_03_index.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    made.append(str(path))

    stats = {
        "jgb_start": float(jgb["收盘"].iloc[0]),
        "jgb_end": float(jgb["收盘"].iloc[-1]),
        "jgb_start_date": str(jgb["日期"].iloc[0].date()),
        "jgb_end_date": str(jgb["日期"].iloc[-1].date()),
        "jgb_peak": float(jgb["收盘"].max()),
        "jgb_peak_date": str(jgb.loc[jgb["收盘"].idxmax(), "日期"].date()),
        "jgb_multiple": round(float(jgb["收盘"].iloc[-1] / jgb["收盘"].iloc[0]), 2),
        "gold_start": float(gold["收盘"].iloc[0]),
        "gold_end": float(gold["收盘"].iloc[-1]),
        "gold_growth_pct": round(growth, 1),
        "gold_start_date": str(gold["日期"].iloc[0].date()),
        "gold_end_date": str(gold["日期"].iloc[-1].date()),
    }
    (work_dir(job) / "chart_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"assets": [Path(p).name for p in made], "stats": stats}


# --- 阶段 3：配音（本地 SAPI）---------------------------------------------


TTS_PS1 = r"""
param([string]$TextFile, [string]$OutFile, [string]$Voice)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice($Voice)
$synth.Rate = 0
$synth.Volume = 100
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.Dispose()
"""


def stage_audio(job: Path) -> dict[str, object]:
    """逐场景配音。用 Windows 本地 SAPI 中文声音，不使用任何云服务。"""
    scenes = read_storyboard(job)
    audio_dir = job / "audio"
    audio_dir.mkdir(exist_ok=True)
    script_path = work_dir(job) / "tts.ps1"
    script_path.write_text(TTS_PS1, encoding="utf-8")

    ffmpeg, ffprobe = find_ffmpeg()
    durations: dict[str, float] = {}
    for scene in scenes:
        text_file = work_dir(job) / f"{scene['scene_id']}.txt"
        text_file.write_text(scene["narration"], encoding="utf-8")
        wav = audio_dir / f"{scene['scene_id']}.wav"
        run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TextFile",
                str(text_file),
                "-OutFile",
                str(wav),
                "-Voice",
                VOICE_NAME,
            ]
        )
        if not wav.is_file() or wav.stat().st_size < 1000:
            raise SystemExit(f"配音生成失败: {wav}")
        probe = run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(wav),
            ]
        )
        durations[scene["scene_id"]] = round(float(probe.stdout.strip()), 2)

    (work_dir(job) / "audio_durations.json").write_text(
        json.dumps(durations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"scenes": len(durations), "total_seconds": round(sum(durations.values()), 2), "durations": durations}


# --- 阶段 4：渲染 ----------------------------------------------------------


def _wrap_caption(text: str, per_line: int = 11) -> str:
    """把卡片文案折行。

    为什么必须手动折：matplotlib 的 ``wrap=True`` 在 figure.text 里不会按画布宽度断行，
    上限只能靠字符数控制。1080px 宽、44pt 字号下单个汉字约 61px，11 字 ≈ 671px，留足边距。
    """
    parts = [p for p in re.split(r"[\s\u3000]+", text.strip()) if p]
    lines: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}".strip() if current else part
        if len(candidate) <= per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(part) > per_line:
            lines.append(part[:per_line])
            part = part[per_line:]
        current = part
    if current:
        lines.append(current)
    return "\n".join(lines)


def _text_card(job: Path, scene: dict[str, str]) -> Path:
    plt = _setup_matplotlib()
    fig = _fig(plt)
    caption = scene["caption"] or scene["narration"][:24]
    wrapped = _wrap_caption(caption)
    fontsize = 44 if wrapped.count("\n") < 2 else 38
    fig.text(
        0.5,
        0.60,
        wrapped,
        ha="center",
        va="center",
        fontsize=fontsize,
        color="#f5f7fa",
        linespacing=1.7,
    )
    fig.text(0.5, 0.47, "AK 带你拆解世界", ha="center", va="center", fontsize=24, color="#8b93a7")
    _caption(plt, fig, "草稿 · 未经人工终审")
    path = job / "assets" / f"card_{scene['scene_id']}.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,0,0,0,0,100,100,0,0,3,{outline},0,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass(
    path: Path,
    cues: list[tuple[float, float, str]],
    *,
    width: int,
    height: int,
    font_size: int = 54,
    margin_h: int = 70,
    margin_v: int = 300,
    outline: int = 3,
) -> None:
    """写 ASS 字幕。

    为什么不用 SRT + force_style：SRT 没有 PlayRes，libass 会按 384x288 的默认脚本尺寸
    把字号和边距整体放大（实测 FontSize=16 会被放大成约 107px、MarginV 把字幕顶到画面上半部）。
    直接写 PlayResX/PlayResY = 画布尺寸，字号就是像素，位置可预期。
    """
    parts = [
        ASS_HEADER.format(
            width=width,
            height=height,
            font_size=font_size,
            margin_h=margin_h,
            margin_v=margin_v,
            outline=outline,
        )
    ]
    for start, end, text in cues:
        safe = text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")
        parts.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{safe}\n")
    path.write_text("".join(parts), encoding="utf-8")


def stage_render(job: Path) -> dict[str, object]:
    scenes = read_storyboard(job)
    durations = json.loads((work_dir(job) / "audio_durations.json").read_text(encoding="utf-8"))
    ffmpeg, ffprobe = find_ffmpeg()
    segments_dir = work_dir(job) / "segments"
    segments_dir.mkdir(exist_ok=True)

    segment_paths: list[Path] = []
    srt_lines: list[str] = []
    cues: list[tuple[float, float, str]] = []
    cursor = 0.0
    timeline: list[dict[str, object]] = []

    for index, scene in enumerate(scenes, start=1):
        asset = scene["visual_asset"].strip()
        frame = job / asset if asset else _text_card(job, scene)
        if not frame.is_file():
            raise SystemExit(f"视觉素材不存在: {frame}")
        audio = job / "audio" / f"{scene['scene_id']}.wav"
        segment = segments_dir / f"seg_{index:02d}.mp4"
        # 时长以音轨为准，用 -t 硬切：只靠 -shortest 时，-loop 1 的静帧会让每段比音轨长
        # 约 1.9 秒（实测 5 段共多出 9.65 秒），导致画面与口播逐段错位。
        scene_seconds = float(durations[scene["scene_id"]])
        run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-loop", "1", "-framerate", str(FPS), "-i", str(frame),
                "-i", str(audio),
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                "-t", f"{scene_seconds:.3f}",
                "-shortest", str(segment),
            ]
        )
        segment_paths.append(segment)
        dur = float(durations[scene["scene_id"]])
        # 卡片场景的大字就是 caption 本身，再叠一条字幕只会重影，所以只给图表场景配字幕。
        if scene["visual_type"] != "card":
            srt_lines.append(
                f"{index}\n{_srt_time(cursor)} --> {_srt_time(cursor + dur)}\n{scene['caption']}\n"
            )
            cues.append((cursor, cursor + dur, scene["caption"]))
        timeline.append(
            {"scene_id": scene["scene_id"], "start_s": round(cursor, 2), "duration_s": dur, "frame": str(frame.name)}
        )
        cursor += dur

    srt_path = job / "subtitles.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    ass_path = job / "subtitles.ass"
    _write_ass(ass_path, cues, width=CANVAS[0], height=CANVAS[1])

    concat_list = work_dir(job) / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in segment_paths) + "\n", encoding="utf-8"
    )
    joined = work_dir(job) / "joined.mp4"
    run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(joined)])

    draft = job / "draft.mp4"
    burn = str(ass_path).replace("\\", "/").replace(":", "\\:")
    try:
        run(
            [
                ffmpeg, "-y", "-loglevel", "error", "-i", str(joined),
                "-vf", f"subtitles='{burn}'",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "copy", str(draft),
            ]
        )
        subtitles_burned = True
    except RuntimeError as exc:  # 字幕烧录失败不隐藏：降级为无字幕并记录原因
        shutil.copyfile(joined, draft)
        subtitles_burned = False
        (work_dir(job) / "subtitle_burn_error.log").write_text(str(exc), encoding="utf-8")

    (work_dir(job) / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "draft": str(draft),
        "scenes": len(scene_paths := segment_paths),
        "duration_s": round(cursor, 2),
        "subtitles_burned": subtitles_burned,
        "segments": len(scene_paths),
    }


# --- 阶段 5：核验 ----------------------------------------------------------


def stage_probe(job: Path) -> dict[str, object]:
    ffmpeg, ffprobe = find_ffmpeg()
    draft = job / "draft.mp4"
    if not draft.is_file():
        raise SystemExit("draft.mp4 不存在，先跑 render 阶段")
    probe = run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
         "-of", "json", str(draft)]
    )
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    report = {
        "file": draft.name,
        "size_bytes": int(info["format"]["size"]),
        "duration_s": round(float(info["format"]["duration"]), 2),
        "video_streams": [s for s in streams if s.get("codec_type") == "video"],
        "audio_streams": [s for s in streams if s.get("codec_type") == "audio"],
    }
    (work_dir(job) / "probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AK 首条视频生产流水线（文件型 job）")
    parser.add_argument("--job", required=True)
    parser.add_argument(
        "--stage", default="all", choices=["collect", "charts", "audio", "render", "probe", "all"]
    )
    args = parser.parse_args(argv)
    job = job_dir(args.job)

    stages = ["collect", "charts", "audio", "render", "probe"] if args.stage == "all" else [args.stage]
    results: dict[str, object] = {}
    for stage in stages:
        handler = globals()[f"stage_{stage}"]
        results[stage] = handler(job)
        print(json.dumps({stage: results[stage]}, ensure_ascii=False, indent=2))

    log = work_dir(job) / f"run_{args.stage}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
