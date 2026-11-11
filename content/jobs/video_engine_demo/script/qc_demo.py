"""Technical QC for the video_engine_demo preview.

This only measures things a machine can measure: container facts, black frames,
silence, frozen stretches, cut density. It cannot judge whether the video is good,
and it does not pretend to: the report says so out loud.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

JOB = Path(__file__).resolve().parents[1]
OUTPUT = JOB / "output" / "preview_20_30s.mp4"
QC_DIR = JOB / "qc"
FFMPEG = Path(r"D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe")
FFPROBE = Path(r"D:\DeliveryOptimization\miniconda\Library\bin\ffprobe.exe")

EXPECTED = {"width": 1080, "height": 1920, "fps": 30.0, "min_seconds": 20.0, "max_seconds": 30.0}
MIN_SHOTS = 6


def probe(path: Path) -> dict:
    done = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries",
                           "format=duration,size,bit_rate",
                           "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                           "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def run_filter(path: Path, filter_graph: str) -> str:
    done = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(path), "-vf", filter_graph,
                           "-an", "-f", "null", "-"], capture_output=True, text=True)
    return done.stderr


def shots_from_timeline() -> int:
    """The timeline is the source of truth for how many shots the edit declares."""
    path = JOB / "project" / "video_engine_demo" / "timeline.json"
    if not path.exists():
        return 0
    return len(json.loads(path.read_text(encoding="utf-8"))["shots"])


def scene_cuts(path: Path, threshold: float = 0.25) -> list[float]:
    log = run_filter(path, f"select='gt(scene,{threshold})',showinfo")
    return [round(float(value), 3) for value in re.findall(r"pts_time:([0-9.]+)", log)]


def black_spans(log: str) -> list[dict]:
    return [{"start": float(s), "end": float(e)}
            for s, e in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", log)]


def freeze_spans(log: str) -> list[dict]:
    return [{"start": float(s), "duration": float(d)}
            for s, d in re.findall(r"freeze_start: ([0-9.]+)\s+freeze_duration: ([0-9.]+)", log)]


def silence_spans(log: str) -> list[dict]:
    return [{"start": float(s), "end": float(e)}
            for s, e in re.findall(r"silence_start: ([0-9.]+)\s+silence_end: ([0-9.]+)", log)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", default=str(OUTPUT))
    options = parser.parse_args()
    video = Path(options.video)
    if not video.exists():
        raise SystemExit(f"missing preview: {video}")

    data = probe(video)
    streams = {stream["codec_type"]: stream for stream in data["streams"]}
    video_stream, audio_stream = streams.get("video"), streams.get("audio")
    duration = float(data["format"]["duration"])

    black_log = run_filter(video, "blackdetect=d=0.1:pix_th=0.10")
    freeze_log = run_filter(video, "freezedetect=n=-60dB:d=0.6")
    audio_log = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(video), "-af",
                                "silencedetect=n=-50dB:d=0.8,volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True).stderr
    cuts = scene_cuts(video)
    silence = silence_spans(audio_log)
    mean_match = re.search(r"mean_volume: (-?[0-9.]+) dB", audio_log)
    max_match = re.search(r"max_volume: (-?[0-9.]+) dB", audio_log)

    checks = []
    checks.append({"check": "分辨率", "expected": f"{EXPECTED['width']}x{EXPECTED['height']}",
                   "actual": f"{video_stream['width']}x{video_stream['height']}" if video_stream else "无视频流",
                   "passed": bool(video_stream and video_stream["width"] == EXPECTED["width"]
                                  and video_stream["height"] == EXPECTED["height"])})
    fps = 0.0
    if video_stream:
        numerator, _, denominator = video_stream["r_frame_rate"].partition("/")
        fps = float(numerator) / float(denominator or 1)
    checks.append({"check": "帧率", "expected": "30 fps", "actual": f"{fps:g} fps",
                   "passed": abs(fps - EXPECTED["fps"]) < 0.01})
    checks.append({"check": "时长", "expected": "20–30 秒",
                   "actual": f"{duration:.3f} 秒",
                   "passed": EXPECTED["min_seconds"] <= duration <= EXPECTED["max_seconds"]})
    checks.append({"check": "音频流", "expected": "aac 存在",
                   "actual": f"{audio_stream['codec_name']} {audio_stream['sample_rate']}Hz "
                             f"{audio_stream['channels']}ch" if audio_stream else "无音频流",
                   "passed": bool(audio_stream)})
    checks.append({"check": "音量", "expected": "mean 在 -35 ~ -12 dBFS",
                   "actual": f"mean {mean_match.group(1) if mean_match else '?'} dB / "
                             f"max {max_match.group(1) if max_match else '?'} dB",
                   "passed": bool(mean_match and -35.0 <= float(mean_match.group(1)) <= -12.0)})
    black = black_spans(black_log)
    longest_black = max([item["end"] - item["start"] for item in black], default=0.0)
    checks.append({"check": "黑帧", "expected": "无超过 0.45 秒的纯黑段（转场可短暂压黑）",
                   "actual": f"{len(black)} 段，最长 {longest_black:.2f} 秒",
                   "passed": longest_black <= 0.45})
    checks.append({"check": "静音", "expected": "无 ≥0.8 秒静音段",
                   "actual": f"{len(silence)} 段",
                   "passed": not silence})
    freeze = freeze_spans(freeze_log)
    longest = max([item["duration"] for item in freeze], default=0.0)
    checks.append({"check": "画面冻结", "expected": "无 ≥0.6 秒完全静止",
                   "actual": f"{len(freeze)} 段，最长 {longest:.2f} 秒",
                   "passed": longest < 0.6})
    timeline_shots = shots_from_timeline()
    checks.append({"check": "镜头数", "expected": f"≥{MIN_SHOTS} 个镜头",
                   "actual": f"时间轴声明 {timeline_shots} 个镜头；场景检测另测到 {len(cuts)} 处画面突变",
                   "passed": timeline_shots >= MIN_SHOTS})

    cuts_path = QC_DIR / "scene_cuts.json"
    cuts_path.write_text(json.dumps({"threshold": 0.25, "cuts": cuts}, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    report = {
        "video": str(video),
        "format": data["format"],
        "streams": data["streams"],
        "scene_cuts_seconds": cuts,
        "black_spans": black_spans(black_log),
        "freeze_spans": freeze,
        "silence_spans": silence,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }
    (QC_DIR / "qc_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks}, ensure_ascii=True, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
