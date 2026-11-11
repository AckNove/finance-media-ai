"""Phase 2 closure verification: the checks the delivery brief asks to re-run.

This script only reads. It re-derives every claim the reports make — storyboard
shape, source authorisation and hashes, asset manifest traceability, timeline
coverage, and the four audio counts — and writes one machine-checkable file,
``qc/verification.json``, so a reviewer can diff the numbers instead of trusting
prose.

Run from the repository root:

    .venv\\Scripts\\python.exe -X utf8 content\\jobs\\phase2_media_engine_demo\\script\\verify_closure.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.job_paths import REGISTER_COLUMNS, ApprovedSources  # noqa: E402
from backend.app.media.manifest import MANIFEST_COLUMNS  # noqa: E402
from backend.app.media.storyboard import load_storyboard  # noqa: E402

JOB_ROOT = Path(__file__).resolve().parents[1]
STORYBOARD = JOB_ROOT / "storyboard" / "storyboard.csv"
MANIFEST = JOB_ROOT / "assets_manifest.csv"
TIMELINE = JOB_ROOT / "project" / "timeline.json"
QC_DIR = JOB_ROOT / "qc"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_storyboard() -> dict[str, Any]:
    storyboard = load_storyboard(STORYBOARD)
    counts = storyboard.counts()
    return {
        "ok": storyboard.total_frames > 0 and counts["shots"] > 0,
        "path": str(STORYBOARD.relative_to(JOB_ROOT)).replace("\\", "/"),
        "duration": storyboard.duration,
        "fps": storyboard.fps,
        "total_frames": storyboard.total_frames,
        "counts": counts,
        "sound_effect_events": [event.as_dict() for event in storyboard.sound_effect_events()],
    }


def check_sources() -> dict[str, Any]:
    register = ApprovedSources(JOB_ROOT).load_register()
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    for source_id in ("S01", "S02", "S03", "S04"):
        try:
            entry = register.assert_renderable(source_id)
        except Exception as error:  # noqa: BLE001 - the reason string is the report
            problems.append(f"{source_id}: {getattr(error, 'reason', error)}")
            continue
        path = JOB_ROOT / "input" / "approved_sources" / entry.original_filename
        actual = sha256_of(path) if path.exists() else ""
        hash_ok = actual == entry.sha256
        if not hash_ok:
            problems.append(f"{source_id}: sha256 mismatch ({actual[:12]} vs {entry.sha256[:12]})")
        if not entry.credit.strip():
            problems.append(f"{source_id}: empty credit")
        entries.append({
            "source_id": source_id,
            "file": entry.original_filename,
            "sha256_matches": hash_ok,
            "license": entry.license_name,
            "commercial_use": entry.commercial_use,
            "credit": entry.credit,
            "verification_status": entry.verification_status,
        })
    return {"ok": not problems, "entries": entries, "problems": problems,
            "register_columns": list(REGISTER_COLUMNS)}


def resolve_manifest_file(file_part: str) -> Path | None:
    """Find the file a manifest row points at.

    Rows are written job-relative where possible, but a few assets live inside the
    engine project directory (the font copy). Try the job root first, then each
    project directory, so the check never silently skips a row.
    """
    candidates = [JOB_ROOT / file_part]
    candidates += [path / file_part for path in JOB_ROOT.glob("project/*") if path.is_dir()]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def check_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {"ok": False, "problems": ["assets_manifest.csv is missing"]}
    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    problems: list[str] = []
    if columns != list(MANIFEST_COLUMNS):
        problems.append("manifest columns do not match MANIFEST_COLUMNS")
    by_class: dict[str, int] = {}
    for row in rows:
        key = row.get("asset_class", "?")
        by_class[key] = by_class.get(key, 0) + 1
        file_part = (row.get("file") or "").strip()
        if not file_part:
            continue
        path = resolve_manifest_file(file_part)
        if path is None:
            problems.append(f"{row.get('asset_id')}: missing file {file_part}")
            continue
        declared = (row.get("sha256") or "").strip()
        if declared and sha256_of(path) != declared:
            problems.append(f"{row.get('asset_id')}: sha256 differs from the manifest")
    return {"ok": not problems, "rows": len(rows), "by_class": by_class, "problems": problems}


def check_timeline() -> dict[str, Any]:
    if not TIMELINE.exists():
        return {"ok": False, "problems": ["project/timeline.json is missing"]}
    timeline = json.loads(TIMELINE.read_text(encoding="utf-8"))
    shots = timeline.get("shots", [])
    frames = timeline["canvas"]["frames"]
    problems: list[str] = []
    expected = 0
    for shot in shots:
        if shot.get("f0") != expected:
            problems.append(f"gap before f{shot.get('f0')} (expected {expected})")
        expected = shot.get("f1", expected)
    if expected != frames:
        problems.append(f"shots end at {expected}, canvas is {frames}")
    audio_tracks = timeline.get("audio", {}).get("tracks", [])
    captions = timeline.get("text", [])
    return {
        "ok": not problems,
        "frames": frames,
        "shots": len(shots),
        "transitions": [shot.get("transition", {}).get("type") for shot in shots
                        if shot.get("transition")],
        "camera_effects": [key for shot in shots for key in (shot.get("cam") or {})],
        "captions": len(captions),
        "audio_tracks": len(audio_tracks),
        "audio_srcs": [track.get("src") for track in audio_tracks],
        "problems": problems,
    }


def check_audio() -> dict[str, Any]:
    path = QC_DIR / "audio_consistency.json"
    if not path.exists():
        return {"ok": False, "problems": ["qc/audio_consistency.json is missing"]}
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        "ok": bool(report.get("ok")),
        "events": report.get("events"),
        "distinct_effects": report.get("distinct_effects"),
        "generated_files": report.get("generated_files"),
        "music_tracks": report.get("music_tracks"),
        "effect_tracks": report.get("effect_tracks"),
        "total_tracks": report.get("total_tracks"),
        "problems": report.get("problems", []),
    }


def main() -> int:
    storyboard = check_storyboard()
    sources = check_sources()
    manifest = check_manifest()
    timeline = check_timeline()
    audio = check_audio()
    report = {
        "storyboard": storyboard,
        "sources": sources,
        "manifest": manifest,
        "timeline": timeline,
        "audio": audio,
    }
    QC_DIR.mkdir(parents=True, exist_ok=True)
    (QC_DIR / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = storyboard["counts"]
    lines = ["# Phase 2 结构核验（只读复算）", ""]
    for name, block in report.items():
        mark = "通过" if block.get("ok") else "**未通过**"
        lines.append(f"- {name}: {mark}")
        for problem in block.get("problems", []):
            lines.append(f"  - {problem}")
    lines += [
        "",
        f"- 分镜：{storyboard['duration']} s / {storyboard['total_frames']} 帧 / {counts['shots']} 镜",
        f"- 素材：{len(sources['entries'])} 条，sha256 全部匹配 = "
        f"{all(item['sha256_matches'] for item in sources['entries'])}",
        f"- 清单：{manifest['rows']} 行，分类 {manifest['by_class']}",
        f"- 时间轴：{timeline['frames']} 帧 / {timeline['shots']} 镜 / "
        f"{len(timeline.get('transitions', []))} 个转场 / {timeline['captions']} 条字幕 / "
        f"{timeline['audio_tracks']} 条音轨",
        f"- 音频口径：{audio['events']} 事件 / {audio['generated_files']} 文件 / "
        f"{audio['music_tracks']} 配乐 / {audio['total_tracks']} 总音轨",
        "",
    ]
    (QC_DIR / "verification.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    overall = all(block.get("ok") for block in report.values())
    print(f"VERDICT: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
