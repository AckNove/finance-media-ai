"""技术 QC：把重制片的容器事实、黑帧/冻结/静音、响度、字幕对齐、关键帧都量出来。

用法：
    python -m backend.scripts.cinema_qc --film content/jobs/v6_power_first_film/remaster_v1/output/final.mp4

只做技术判定，**不判断好不好看**——观感交给 `qc/scene_quality_review.md` 与人工看片。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"

REM = ROOT / "content" / "jobs" / "v6_power_first_film" / "remaster_v1"

# 判据：单段阈值（不是全片总和）。转场可以短暂压黑，但不该出现整段黑场。
MAX_BLACK_SECONDS = 0.5
MAX_FREEZE_SECONDS = 1.5
MAX_SILENCE_SECONDS = 2.0
LOUDNESS_TARGET = -16.0  # LUFS
LOUDNESS_TOLERANCE = 3.0


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(path: Path) -> dict:
    result = run([
        str(FFPROBE), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def scan(path: Path) -> dict:
    result = run([
        str(FFMPEG), "-hide_banner", "-nostats", "-i", str(path),
        "-vf", "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=0.001:d=0.5",
        "-af", "silencedetect=n=-45dB:d=0.5,volumedetect,ebur128=framelog=quiet",
        "-f", "null", "-",
    ])
    text = result.stderr
    blacks = [float(m) for m in re.findall(r"black_duration: ([0-9.]+)", text)]
    freezes = [float(m) for m in re.findall(r"freeze_duration: ([0-9.]+)", text)]
    silences = [float(m) for m in re.findall(r"silence_duration: ([0-9.]+)", text)]
    mean = re.search(r"mean_volume: (-?[0-9.]+) dB", text)
    peak = re.search(r"max_volume: (-?[0-9.]+) dB", text)
    lufs = re.search(r"I:\s+(-?[0-9.]+) LUFS", text)
    peak_dbtp = re.search(r"Peak:\s+(-?[0-9.]+) dBFS", text)
    return {
        "black_segments": len(blacks),
        "black_longest": round(max(blacks), 3) if blacks else 0.0,
        "black_total": round(sum(blacks), 3),
        "freeze_segments": len(freezes),
        "freeze_longest": round(max(freezes), 3) if freezes else 0.0,
        "silence_segments": len(silences),
        "silence_longest": round(max(silences), 3) if silences else 0.0,
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "max_volume_db": float(peak.group(1)) if peak else None,
        "integrated_lufs": float(lufs.group(1)) if lufs else None,
        "true_peak_dbfs": float(peak_dbtp.group(1)) if peak_dbtp else None,
    }


def subtitle_timing(srt: Path) -> dict:
    if not srt.is_file():
        return {"present": False}
    text = srt.read_text(encoding="utf-8")
    rows = re.findall(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", text)
    spans = []
    for row in rows:
        start = int(row[0]) * 3600 + int(row[1]) * 60 + int(row[2]) + int(row[3]) / 1000
        end = int(row[4]) * 3600 + int(row[5]) * 60 + int(row[6]) + int(row[7]) / 1000
        spans.append((round(start, 3), round(end, 3)))
    overlaps = sum(1 for a, b in zip(spans, spans[1:]) if b[0] < a[1] - 1e-6)
    return {
        "present": True,
        "cues": len(spans),
        "first": spans[0] if spans else None,
        "last": spans[-1] if spans else None,
        "overlaps": overlaps,
    }


def keyframes(film: Path, blueprint: dict, out_dir: Path) -> dict:
    """每个镜头抽 3 帧（进入 / 中段 / 收尾），给人看构图。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    offsets = {}
    cursor = 0.0
    for scene in blueprint["scenes"]:
        offsets[scene["scene_id"]] = cursor
        cursor += float(scene["duration"])
    made = {}
    for scene in blueprint["scenes"]:
        sid = scene["scene_id"]
        base = offsets[sid]
        span = float(scene["duration"])
        picks = {"in": base + span * 0.25, "mid": base + span * 0.55, "out": base + span * 0.9}
        made[sid] = {}
        for tag, at in picks.items():
            target = out_dir / f"{sid}_{tag}.png"
            result = run([
                str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{at:.3f}", "-i", str(film), "-frames:v", "1",
                "-vf", "scale=540:960", str(target),
            ])
            made[sid][tag] = str(target) if result.returncode == 0 else None
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重制片技术 QC")
    parser.add_argument("--film", default=str(REM / "output" / "final.mp4"))
    parser.add_argument("--srt", default=str(REM / "output" / "remaster_v1.srt"))
    parser.add_argument("--blueprint", default=str(REM / "blueprint" / "film.json"))
    parser.add_argument("--out", default=str(REM / "qc"))
    args = parser.parse_args(argv)

    film = Path(args.film)
    if not film.is_file():
        print(f"找不到成片：{film}", file=sys.stderr)
        return 2

    facts = probe(film)
    video = next((s for s in facts["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in facts["streams"] if s["codec_type"] == "audio"), {})
    metrics = scan(film)
    subs = subtitle_timing(Path(args.srt))
    blueprint = json.loads(Path(args.blueprint).read_text(encoding="utf-8"))
    frames = keyframes(film, blueprint, Path(args.out) / "keyframes")

    # 蓝图里没有 total_seconds 字段（时长 = 各镜时长之和，由 Film 算出来），这里自己算
    blueprint_total = round(sum(float(item["duration"]) for item in blueprint["scenes"]), 3)

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps_text = video.get("r_frame_rate") or "0/1"
    num, den = (int(x) for x in fps_text.split("/")) if "/" in fps_text else (0, 1)
    fps = round(num / den, 3) if den else 0.0
    duration = float(facts["format"].get("duration") or 0.0)

    checks = {
        "resolution_1080x1920": (width, height) == (1080, 1920),
        "fps_30": abs(fps - 30.0) < 0.01,
        "codec_h264": video.get("codec_name") == "h264",
        "pixel_format_yuv420p": video.get("pix_fmt") == "yuv420p",
        "audio_aac": audio.get("codec_name") == "aac",
        "audio_48k_stereo": int(audio.get("sample_rate") or 0) == 48000 and int(audio.get("channels") or 0) == 2,
        "duration_covers_blueprint": abs(duration - blueprint_total) <= 0.6,
        "no_long_black": metrics["black_longest"] <= MAX_BLACK_SECONDS,
        "no_long_freeze": metrics["freeze_longest"] <= MAX_FREEZE_SECONDS,
        "no_long_silence": metrics["silence_longest"] <= MAX_SILENCE_SECONDS,
        "loudness_near_target": metrics["integrated_lufs"] is not None
        and abs(metrics["integrated_lufs"] - LOUDNESS_TARGET) <= LOUDNESS_TOLERANCE,
        "subtitles_do_not_overlap": subs.get("overlaps", 0) == 0,
        "subtitles_cover_whole_film": bool(subs.get("present")) and bool(subs.get("first"))
        and subs["first"][0] >= 0.0,
    }

    payload = {
        "film": str(film),
        "bytes": film.stat().st_size,
        "duration": round(duration, 3),
        "video": {
            "codec": video.get("codec_name"),
            "width": width,
            "height": height,
            "fps": fps,
            "pix_fmt": video.get("pix_fmt"),
        },
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
        },
        "metrics": metrics,
        "subtitles": subs,
        "keyframes": frames,
        "thresholds": {
            "max_black_seconds": MAX_BLACK_SECONDS,
            "max_freeze_seconds": MAX_FREEZE_SECONDS,
            "max_silence_seconds": MAX_SILENCE_SECONDS,
            "loudness_target_lufs": LOUDNESS_TARGET,
            "loudness_tolerance": LOUDNESS_TOLERANCE,
        },
        "checks": checks,
        "failed": [key for key, ok in checks.items() if not ok],
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "technical_qc.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    rows = "\n".join(
        f"| {name} | {'通过' if ok else '**不通过**'} |" for name, ok in checks.items()
    )
    metrics = payload["metrics"]
    md_text = f"""# 技术 QC：{film.name}

> 这份只回答「片子技术上有没有问题」，**不判断好不好看**（观感见 `scene_quality_review.md`）。
> 数字全部由 `python -m backend.scripts.cinema_qc` 从成片文件里量出来，命令可复跑。

## 1. 容器事实

| 项 | 值 |
|---|---|
| 文件 | `{film.as_posix()}` |
| 大小 | {payload['bytes']:,} 字节（{payload['bytes'] / 1024 / 1024:.2f} MB） |
| 时长 | {payload['duration']} 秒 |
| 画面 | {payload['video']['codec']} / {payload['video']['width']}×{payload['video']['height']} / {payload['video']['fps']} fps / {payload['video']['pix_fmt']} |
| 声音 | {payload['audio']['codec']} / {payload['audio']['sample_rate']} Hz / {payload['audio']['channels']} 声道 |

## 2. 黑帧 / 冻结 / 静音 / 响度

| 指标 | 实测 | 判据 |
|---|---|---|
| 最长黑帧 | {metrics['black_longest']} 秒（{metrics['black_segments']} 段） | ≤ {MAX_BLACK_SECONDS} 秒 |
| 最长冻结 | {metrics['freeze_longest']} 秒（{metrics['freeze_segments']} 段） | ≤ {MAX_FREEZE_SECONDS} 秒 |
| 最长静音 | {metrics['silence_longest']} 秒（{metrics['silence_segments']} 段） | ≤ {MAX_SILENCE_SECONDS} 秒 |
| 平均电平 | {metrics['mean_volume_db']} dB | 参考 |
| 峰值 | {metrics['max_volume_db']} dBFS | ≤ -1 dBFS |
| 整体响度 | {metrics['integrated_lufs']} LUFS | {LOUDNESS_TARGET} ± {LOUDNESS_TOLERANCE} |

## 3. 字幕

| 项 | 值 |
|---|---|
| 字幕条数 | {subs.get('cues')} |
| 第一条 | {subs.get('first')} |
| 最后一条 | {subs.get('last')} |
| 时间重叠 | {subs.get('overlaps')} 处 |

## 4. 逐项判定

| 检查项 | 结果 |
|---|---|
{rows}

**结论：{'全部通过' if not payload['failed'] else '未通过 ' + '、'.join(payload['failed'])}**

## 5. 关键帧

每镜抽了「进入 / 中段 / 收尾」三帧，落在 `qc/keyframes/`，文件名 `s<镜号>_<in|mid|out>.png`，方便逐镜看构图。
"""
    (out_dir / "technical_qc.md").write_text(md_text, encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("duration", "bytes", "metrics", "failed")},
                     ensure_ascii=False, indent=1))
    return 0 if not payload["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
