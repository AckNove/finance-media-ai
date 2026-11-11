"""V2.2 Phase 1：音视频能力**真机自检**——每一项都产出真实文件并核验。

与 `check_media_toolchain` 的分工：
- `check_media_toolchain` 读二进制自报的能力清单（快，但只能证明"编译进去了"）；
- 本脚本真的跑一遍：编码、解码、裁剪、拼接、帧率转换、音频混合、字幕烧录、
  响度测量、黑帧/冻结帧/静音检测，并把产物写进任务目录供人工核验。

产物目录（任务书要求"测试视频及音频"可追溯）：

    content/jobs/media_toolchain_check/
      work/    中间文件（分段、字幕、字体副本）
      audio/   音频产物
      output/  最终测试视频
      qc/      报告（json + md）

用法：

    python -m backend.scripts.media_selftest
    python -m backend.scripts.media_selftest --keep-work   # 保留中间文件（默认保留）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.app.capabilities.media import ffmpeg as media
from backend.app.core.config import PROJECT_ROOT

JOB_ID = "media_toolchain_check"
JOB_DIR = PROJECT_ROOT / "content" / "jobs" / JOB_ID
WORK_DIR = JOB_DIR / "work"
AUDIO_DIR = JOB_DIR / "audio"
OUTPUT_DIR = JOB_DIR / "output"
QC_DIR = JOB_DIR / "qc"

WIDTH, HEIGHT, FPS = 1080, 1920, 30
FONT_SOURCE = Path(r"C:\Windows\Fonts\msyh.ttc")

ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,72,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,80,80,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.20,0:00:01.30,Default,,0,0,0,,FFmpeg 8.1.2 字幕烧录自检
Dialogue: 0,0:00:01.30,0:00:02.45,Default,,0,0,0,,libass + 微软雅黑 + 安全区
"""


@dataclass
class Step:
    key: str
    title: str
    required: bool = True
    ok: bool = False
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    def describe(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "required": self.required,
            "ok": self.ok,
            "detail": self.detail,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
        }


class Runner:
    def __init__(self, toolchain: media.MediaToolchain) -> None:
        self.toolchain = toolchain
        self.steps: list[Step] = []

    # --- 基础动作 -----------------------------------------------------
    def ffmpeg(self, args: list[str], *, timeout: int = 600) -> str:
        assert self.toolchain.ffmpeg is not None
        done = subprocess.run(
            [str(self.toolchain.ffmpeg), "-hide_banner", "-nostdin", "-y", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(WORK_DIR),
        )
        if done.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 失败 rc={done.returncode}: "
                + " | ".join((done.stderr or "").strip().splitlines()[-4:])
            )
        return (done.stderr or "") + (done.stdout or "")

    def ffprobe_json(self, path: Path) -> dict:
        assert self.toolchain.ffprobe is not None
        done = subprocess.run(
            [
                str(self.toolchain.ffprobe),
                "-hide_banner",
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
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if done.returncode != 0:
            raise RuntimeError(f"ffprobe 失败 rc={done.returncode}: {done.stderr.strip()[:300]}")
        return json.loads(done.stdout)

    def record(self, step: Step) -> Step:
        self.steps.append(step)
        flag = "OK  " if step.ok else "FAIL"
        print(f"[{flag}] {step.key:16s} {step.detail}", flush=True)
        return step


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _rel(path: Path) -> str:
    return str(path.relative_to(JOB_DIR)).replace("\\", "/")


def _stream_summary(info: dict) -> dict[str, object]:
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})
    return {
        "duration": float(fmt.get("duration", 0.0) or 0.0),
        "size": int(fmt.get("size", 0) or 0),
        "bit_rate": int(fmt.get("bit_rate", 0) or 0),
        "video": None
        if video is None
        else {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "avg_frame_rate": video.get("avg_frame_rate"),
            "pix_fmt": video.get("pix_fmt"),
        },
        "audio": None
        if audio is None
        else {
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
        },
    }


def prepare_dirs(*, reset: bool) -> None:
    for directory in (WORK_DIR, AUDIO_DIR, OUTPUT_DIR, QC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    if reset:
        for directory in (WORK_DIR, AUDIO_DIR, OUTPUT_DIR, QC_DIR):
            for item in directory.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
    fonts = WORK_DIR / "fonts"
    fonts.mkdir(exist_ok=True)
    if FONT_SOURCE.is_file():
        target = fonts / FONT_SOURCE.name
        if not target.is_file():
            shutil.copy2(FONT_SOURCE, target)


def run_all(*, reset: bool = True) -> dict[str, object]:
    toolchain = media.require_toolchain()
    prepare_dirs(reset=reset)
    runner = Runner(toolchain)

    clip_a = WORK_DIR / "clip_a.mp4"
    clip_trim = WORK_DIR / "clip_trim.mp4"
    clip_24 = WORK_DIR / "clip_24fps.mp4"
    clip_joined = WORK_DIR / "clip_joined.mp4"
    mix = AUDIO_DIR / "mix.m4a"
    final = OUTPUT_DIR / "toolchain_check.mp4"
    final_subs = OUTPUT_DIR / "toolchain_check_subs.mp4"

    # 1) 编码：H.264 + AAC，竖屏 1080×1920
    step = Step("h264_aac_encode", "编码 H.264(libx264) + AAC 竖屏 1080×1920")
    try:
        runner.ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=3",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(clip_a),
            ]
        )
        info = _stream_summary(runner.ffprobe_json(clip_a))
        step.ok = (
            info["video"] is not None
            and info["video"]["codec"] == "h264"
            and info["audio"] is not None
            and info["audio"]["codec"] == "aac"
            and info["video"]["width"] == WIDTH
            and info["video"]["height"] == HEIGHT
        )
        step.metrics = info
        step.artifacts = [_rel(clip_a)]
        step.detail = f"{info['duration']:.2f}s video={info['video'] and info['video']['codec']} audio={info['audio'] and info['audio']['codec']}"
    except Exception as exc:  # noqa: BLE001 - 自检要把失败记录下来而不是中断
        step.detail = str(exc)
    runner.record(step)

    # 2) 解码：完整解码一遍（不写文件），证明 h264 解码链路可用
    step = Step("h264_decode", "完整解码 H.264 视频流")
    try:
        output = runner.ffmpeg(["-i", "clip_a.mp4", "-f", "null", "-"], timeout=300)
        frames = re.findall(r"frame=\s*(\d+)", output)
        decoded = int(frames[-1]) if frames else 0
        step.ok = decoded >= FPS * 2
        step.metrics = {"decoded_frames": decoded}
        step.detail = f"解码 {decoded} 帧"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 3) 裁剪
    step = Step("trim", "视频裁剪 0.5s→1.5s")
    try:
        runner.ffmpeg(
            [
                "-ss",
                "0.5",
                "-i",
                "clip_a.mp4",
                "-t",
                "1.0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(clip_trim),
            ]
        )
        info = _stream_summary(runner.ffprobe_json(clip_trim))
        step.ok = 0.8 <= info["duration"] <= 1.3
        step.metrics = info
        step.artifacts = [_rel(clip_trim)]
        step.detail = f"裁剪后 {info['duration']:.2f}s"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 4) 帧率转换 30 → 24
    step = Step("fps_convert", "帧率转换 30fps → 24fps")
    try:
        runner.ffmpeg(
            [
                "-i",
                "clip_a.mp4",
                "-vf",
                "fps=24",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(clip_24),
            ]
        )
        info = _stream_summary(runner.ffprobe_json(clip_24))
        step.ok = info["video"]["avg_frame_rate"] == "24/1"
        step.metrics = info
        step.artifacts = [_rel(clip_24)]
        step.detail = f"avg_frame_rate={info['video']['avg_frame_rate']}"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 5) 拼接（concat demuxer，先转成同一参数再拼）
    step = Step("concat", "视频拼接（concat demuxer，两段参数一致）")
    try:
        (WORK_DIR / "concat_list.txt").write_text(
            "file 'clip_trim.mp4'\nfile 'clip_24fps_same.mp4'\n", encoding="utf-8"
        )
        runner.ffmpeg(
            [
                "-i",
                "clip_24fps.mp4",
                "-vf",
                f"fps={FPS},scale={WIDTH}:{HEIGHT}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(WORK_DIR / "clip_24fps_same.mp4"),
            ]
        )
        runner.ffmpeg(["-f", "concat", "-safe", "0", "-i", "concat_list.txt", "-c", "copy", str(clip_joined)])
        info = _stream_summary(runner.ffprobe_json(clip_joined))
        expected = _stream_summary(runner.ffprobe_json(clip_trim))["duration"] + _stream_summary(
            runner.ffprobe_json(WORK_DIR / "clip_24fps_same.mp4")
        )["duration"]
        step.ok = abs(info["duration"] - expected) < 0.35
        step.metrics = {"joined": info, "expected_duration": round(expected, 3)}
        step.artifacts = [_rel(clip_joined)]
        step.detail = f"拼接后 {info['duration']:.2f}s（期望 {expected:.2f}s）"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 6) 音频混合（amix 两路正弦）
    step = Step("audio_mix", "音频混合（amix 440Hz + 660Hz → AAC）")
    try:
        runner.ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=2.5",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=660:sample_rate=48000:duration=2.5",
                "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[mixed]",
                "-map",
                "[mixed]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(mix),
            ]
        )
        info = _stream_summary(runner.ffprobe_json(mix))
        step.ok = info["audio"] is not None and info["audio"]["codec"] == "aac" and info["duration"] > 2.0
        step.metrics = info
        step.artifacts = [_rel(mix)]
        step.detail = f"{info['duration']:.2f}s {info['audio'] and info['audio']['codec']}"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 7) 合片：拼接视频 + 混合音频
    step = Step("mux_av", "音视频合片（-map 视频 + 混合音轨）")
    try:
        runner.ffmpeg(
            [
                "-i",
                "clip_joined.mp4",
                "-i",
                str(mix.relative_to(WORK_DIR)) if mix.is_relative_to(WORK_DIR) else str(mix),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(final),
            ]
        )
        info = _stream_summary(runner.ffprobe_json(final))
        step.ok = info["video"] is not None and info["audio"] is not None
        step.metrics = info
        step.artifacts = [_rel(final)]
        step.detail = f"{info['duration']:.2f}s 含音视频双流"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 8) 字幕烧录（libass + 微软雅黑）
    step = Step("subtitle_burn", "libass 字幕烧录（.ass + 微软雅黑 + 安全区内）")
    try:
        (WORK_DIR / "subs.ass").write_text(
            ASS_TEMPLATE.format(width=WIDTH, height=HEIGHT), encoding="utf-8"
        )
        runner.ffmpeg(
            [
                "-i",
                str(final),
                "-vf",
                "ass=subs.ass:fontsdir=fonts",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(final_subs),
            ],
            timeout=900,
        )
        info = _stream_summary(runner.ffprobe_json(final_subs))
        step.ok = info["video"] is not None and abs(info["duration"] - _stream_summary(runner.ffprobe_json(final))["duration"]) < 0.2
        step.metrics = info
        step.artifacts = [_rel(final_subs), _rel(WORK_DIR / "subs.ass")]
        step.detail = f"烧字幕成功 {info['duration']:.2f}s（字体目录 {_rel(WORK_DIR / 'fonts')}）"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 9) 响度测量（EBU R128）
    step = Step("loudness", "响度测量（loudnorm 两遍法第一遍 print_format=json）")
    try:
        output = runner.ffmpeg(
            [
                "-i",
                str(final),
                "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            timeout=600,
        )
        block = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", output, re.S)
        data = json.loads(block.group(0)) if block else {}
        step.ok = bool(data.get("input_i"))
        step.metrics = {
            "input_i_lufs": data.get("input_i"),
            "input_tp_dbtp": data.get("input_tp"),
            "input_lra": data.get("input_lra"),
            "target": "-14 LUFS",
        }
        step.detail = f"integrated={data.get('input_i')} LUFS, true peak={data.get('input_tp')} dBTP"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 10) 音量统计
    step = Step("volume_stats", "音量统计（volumedetect）")
    try:
        output = runner.ffmpeg(["-i", str(mix), "-af", "volumedetect", "-f", "null", "-"], timeout=300)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", output)
        peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", output)
        step.ok = bool(mean and peak)
        step.metrics = {
            "mean_volume_db": float(mean.group(1)) if mean else None,
            "max_volume_db": float(peak.group(1)) if peak else None,
        }
        step.detail = f"mean={step.metrics['mean_volume_db']} dB, max={step.metrics['max_volume_db']} dB"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 11) 质检类滤镜（黑帧/冻结/静音）——本项目现有 QC 依赖
    step = Step("qc_filters", "质检滤镜（blackdetect / freezedetect / silencedetect）")
    try:
        output = runner.ffmpeg(
            [
                "-i",
                str(final_subs),
                "-vf",
                "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=-60dB:d=0.5",
                "-af",
                "silencedetect=n=-50dB:d=0.5",
                "-f",
                "null",
                "-",
            ],
            timeout=600,
        )
        blacks = len(re.findall(r"black_start:", output))
        freezes = len(re.findall(r"freeze_start:", output))
        silences = len(re.findall(r"silence_start:", output))
        step.ok = True  # 三个滤镜都成功执行即算通过；段数写进指标供人判读
        step.metrics = {"black_segments": blacks, "freeze_segments": freezes, "silence_segments": silences}
        step.detail = f"black={blacks} freeze={freezes} silence={silences}"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    # 12) 元数据读取
    step = Step("metadata_read", "元数据读取（ffprobe JSON）")
    try:
        info = runner.ffprobe_json(final_subs)
        step.ok = bool(info.get("format", {}).get("duration")) and len(info.get("streams", [])) >= 2
        step.metrics = _stream_summary(info)
        step.detail = f"streams={len(info.get('streams', []))}"
    except Exception as exc:  # noqa: BLE001
        step.detail = str(exc)
    runner.record(step)

    artifacts = []
    for path in [clip_a, clip_trim, clip_24, clip_joined, mix, final, final_subs]:
        if path.is_file():
            artifacts.append(
                {
                    "path": _rel(path),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_of(path),
                }
            )

    payload = {
        "job": JOB_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "toolchain": toolchain.describe(),
        "ok": all(step.ok for step in runner.steps if step.required),
        "steps": [step.describe() for step in runner.steps],
        "artifacts": artifacts,
        "notes": [
            "本自检证明的是「工具链能力」，不是「真实素材剪辑工作流」。",
            "素材来源为 lavfi 合成画面与正弦音，不含任何真实拍摄素材或历史数据。",
            "响度与音量数字只说明测量链路可用，不代表正式作品的混音标准。",
        ],
    }
    QC_DIR.mkdir(parents=True, exist_ok=True)
    (QC_DIR / "toolchain_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (QC_DIR / "toolchain_report.md").write_text(render_markdown(payload), encoding="utf-8")
    return payload


def render_markdown(payload: dict[str, object]) -> str:
    toolchain = payload["toolchain"]
    lines = [
        "# 音视频工具链自检报告（V2.2 Phase 1）",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 工具链来源：`{toolchain['source']}`",
        f"- ffmpeg：`{toolchain['ffmpeg']}`",
        f"- ffprobe：`{toolchain['ffprobe']}`",
        f"- 版本：`{toolchain['version']}`（下限 {toolchain['minimum_version']}）",
        f"- 总体结果：**{'通过' if payload['ok'] else '未通过'}**",
        "",
        "## 逐项结果",
        "",
        "| 项 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for step in payload["steps"]:
        mark = "✅" if step["ok"] else "❌"
        lines.append(f"| `{step['key']}` {step['title']} | {mark} | {step['detail']} |")
    lines += ["", "## 产物", "", "| 文件 | 大小 | SHA-256 |", "| --- | --- | --- |"]
    for item in payload["artifacts"]:
        lines.append(f"| `{item['path']}` | {item['bytes']:,} B | `{item['sha256'][:16]}…` |")
    lines += ["", "## 结论边界", ""]
    for note in payload["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="音视频能力真机自检")
    parser.add_argument("--no-reset", action="store_true", help="不清理上次产物")
    args = parser.parse_args(argv)
    payload = run_all(reset=not args.no_reset)
    print(json.dumps({"ok": payload["ok"], "report": "qc/toolchain_report.json"}, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
