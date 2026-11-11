"""Phase 2 demo build — approved sources, audio, compositions, timeline, render, QC.

Stages (each is safe to re-run):

``sources``       copy the four reviewed OpenKatana example assets into
                  ``input/approved_sources/`` and register them with provenance
``audio``         synthesise an original 40 s score, build sound effects, mux to AAC
``compositions``  author and render the animated chart, diagram and outro cards
``import``        create the engine project and import every visual asset
``timeline``      storyboard.csv -> timeline document (validate, save)
``render``        render the saved timeline and copy the MP4 into ``output/``
``qc``            probe the finished file and write ``qc/qc_report.md``
``evidence``      write ``assets_manifest.csv``, per-shot key frames and a timeline copy

Nothing in this script talks to the network, and no source outside
``input/approved_sources/`` can reach the render.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = JOB_ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.capabilities.media.ffmpeg import resolve_toolchain  # noqa: E402
from backend.app.capabilities.media.openkatana import OpenKatanaEngine  # noqa: E402
from backend.app.core.job_paths import ApprovedSources  # noqa: E402
from backend.app.media.manifest import MANIFEST_COLUMNS, build_manifest, write_manifest  # noqa: E402
from backend.app.media.storyboard import load_storyboard  # noqa: E402
from backend.app.media.timeline import (  # noqa: E402
    audio_consistency,
    audio_mix,
    build_timeline,
    subtitle_events,
)

JOB_ID = "phase2_media_engine_demo"
CANVAS = (1080, 1920)
FPS = 30
WORK = JOB_ROOT / "raw" / "_work"
PROJECT_WORKSPACE = JOB_ROOT / "project"
APPROVED = JOB_ROOT / "input" / "approved_sources"
ASSETS = JOB_ROOT / "assets"
AUDIO_DIR = JOB_ROOT / "audio"
OUTPUT = JOB_ROOT / "output"
QC_DIR = JOB_ROOT / "qc"
STORYBOARD = JOB_ROOT / "storyboard" / "storyboard.csv"

EXAMPLE_ASSETS = REPO_ROOT / "tools" / "openkatana" / "examples" / "quick-edit" / "assets"
NASA_POLICY = "https://www.nasa.gov/nasa-brand-center/images-and-media/"

#: Reviewed example assets. Everything here has a named licence, a credit line and
#: commercial use affirmatively allowed; anything less never reaches the render.
SOURCE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "source_id": "S01",
        "filename": "nasa_apollo17_earthrise.jpg",
        "origin": "apollo17.jpg",
        "source_name": "阿波罗17号拍摄的地球（蓝色弹珠）",
        "publisher": "NASA Scientific Visualization Studio",
        "source_url": "https://svs.gsfc.nasa.gov/vis/a030000/a030600/a030613/blue_marble_apollo_17_19721207_print.jpg",
        "published_at": "1972-12-07",
        "license_name": "NASA public-domain media (media-use policy applies)",
        "commercial_use": "allowed",
        "credit": "Earth Science and Remote Sensing Unit, NASA Johnson Space Center",
        "verification_status": "verified",
        "methodology": f"与 tools/openkatana/examples/quick-edit/assets/sources.json 记录的 sha256 逐字节比对；NASA 媒体使用政策 {NASA_POLICY}",
        "unit": "",
        "data_as_of": "",
    },
    {
        "source_id": "S02",
        "filename": "nasa_blue_marble_starfield.jpg",
        "origin": "blue-marble.jpg",
        "source_name": "蓝色弹珠（星空背景版）静帧",
        "publisher": "NASA Scientific Visualization Studio",
        "source_url": "https://svs.gsfc.nasa.gov/vis/a000000/a004500/a004550/BlueMarble_starfield_4k_2160p30_v2.01000_print.jpg",
        "published_at": "",
        "license_name": "NASA public-domain media (media-use policy applies)",
        "commercial_use": "allowed",
        "credit": "NASA Scientific Visualization Studio; Cheng Zhang",
        "verification_status": "verified",
        "methodology": "sha256 与 sources.json 一致；同一可视化项目 4550 的静帧",
        "unit": "",
        "data_as_of": "",
    },
    {
        "source_id": "S03",
        "filename": "nasa_earth_rotation.mp4",
        "origin": "earth-rotation.mp4",
        "source_name": "VIIRS 地球自转可视化片段（360p）",
        "publisher": "NASA Scientific Visualization Studio",
        "source_url": "https://svs.gsfc.nasa.gov/vis/a030000/a030700/a030763/R_earth_viirs_360p.mp4",
        "published_at": "",
        "license_name": "NASA public-domain media (media-use policy applies)",
        "commercial_use": "allowed",
        "credit": "NASA Scientific Visualization Studio; Amy Moran and Jesse Allen",
        "verification_status": "verified",
        "methodology": "sha256 与 sources.json 一致；ffprobe 实测 23.967 s / 640×360 / 30 fps / 无音轨",
        "unit": "秒",
        "data_as_of": "",
    },
    {
        "source_id": "S04",
        "filename": "openkatana_orbit_score.wav",
        "origin": "orbit.wav",
        "source_name": "OpenKatana 原创合成配乐 orbit.wav（8 秒）",
        "publisher": "OpenKatana contributors",
        "source_url": "https://github.com/rk0neki/OpenKatana/blob/v0.1.3/examples/quick-edit/assets/orbit.wav",
        "published_at": "2026",
        "license_name": "Apache-2.0",
        "commercial_use": "allowed",
        "credit": "OpenKatana contributors, Apache-2.0",
        "verification_status": "verified",
        "methodology": "sources.json 标注 Original synthesized score, no external recordings or samples；sha256 一致",
        "unit": "秒",
        "data_as_of": "",
    },
)


def log(message: str) -> None:
    print(f"[{JOB_ID}] {message}", flush=True)


def ensure_dirs() -> None:
    for directory in (
        APPROVED, JOB_ROOT / "raw", JOB_ROOT / "extracted", JOB_ROOT / "evidence",
        JOB_ROOT / "research", JOB_ROOT / "script", JOB_ROOT / "storyboard",
        ASSETS / "compositions", ASSETS / "frames", AUDIO_DIR,
        PROJECT_WORKSPACE, OUTPUT, QC_DIR, WORK,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def toolchain():
    return resolve_toolchain()


def ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    chain = toolchain()
    command = [str(chain.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {' '.join(args)}\n{result.stderr}")
    return result


def ffprobe_json(path: Path) -> dict[str, Any]:
    chain = toolchain()
    command = [str(chain.ffprobe), "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


# --------------------------------------------------------------------- sources
def stage_sources() -> dict[str, Any]:
    """Copy the reviewed example assets in, then register them with provenance."""
    ensure_dirs()
    sources = ApprovedSources(JOB_ROOT)
    registered: list[dict[str, Any]] = []
    for spec in SOURCE_SPECS:
        target = APPROVED / spec["filename"]
        origin = EXAMPLE_ASSETS / spec["origin"]
        if not origin.exists():
            raise SystemExit(f"missing upstream example asset: {origin}")
        shutil.copy2(origin, target)
        entry = sources.register(
            target,
            source_id=spec["source_id"],
            source_name=spec["source_name"],
            publisher=spec["publisher"],
            source_url=spec["source_url"],
            published_at=spec["published_at"],
            data_as_of=spec["data_as_of"],
            unit=spec["unit"],
            methodology=spec["methodology"],
            verification_status=spec["verification_status"],
            license_name=spec["license_name"],
            commercial_use=spec["commercial_use"],
            credit=spec["credit"],
        )
        sources.assert_renderable(spec["source_id"])
        registered.append({
            "source_id": entry.source_id,
            "file": entry.original_filename,
            "sha256": entry.sha256,
            "byte_size": entry.byte_size,
            "license": entry.license_name,
            "commercial_use": entry.commercial_use,
            "credit": entry.credit,
        })
        log(f"registered {entry.source_id} {entry.original_filename} ({entry.byte_size} B)")
    register_path = sources.write_register()
    log(f"source register -> {register_path.relative_to(JOB_ROOT)}")
    facts = {"registered": registered, "register": str(register_path.relative_to(JOB_ROOT))}
    (WORK / "sources.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return facts


CJK_FONT = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
FONT_FAMILY = "AKSC"
FONT_REL = "fonts/NotoSansSC-VF.ttf"

INK = "#0b1220"
PAPER = "#f2f5f7"
DIM = "#94a3b8"
LIME = "#b6ff1f"
CYAN = "#35e0ff"
MAGENTA = "#ff2bd6"


def sfx_schedule(storyboard: "Storyboard | None" = None) -> list[dict[str, Any]]:
    """One effect per storyboard cue — the sheet decides, this script only obeys.

    The storyboard's ``sound_effect`` column is the single source of truth: a shot
    that says ``none`` gets no cue, and every other name must exist in
    :data:`SFX_FILTERS` or the build stops. Deriving the schedule here (instead of
    restating it) is what keeps "cues", "files" and "tracks" from drifting apart.
    """
    board = storyboard if storyboard is not None else load_storyboard(STORYBOARD)
    schedule: list[dict[str, Any]] = []
    for event in board.sound_effect_events():
        if event.name not in SFX_FILTERS:
            raise SystemExit(
                f"{event.shot_id}: sound_effect {event.name!r} has no generator; "
                f"known effects are {sorted(SFX_FILTERS)}"
            )
        schedule.append({
            "name": event.name,
            "at": event.at,
            "shot": event.shot_id,
            "volume": SFX_VOLUMES.get(event.name, 0.45),
        })
    return schedule


def open_engine() -> OpenKatanaEngine:
    """Attach to the job's project, creating it (and its font folder) once."""
    engine = OpenKatanaEngine(PROJECT_WORKSPACE, log_path=WORK / "engine_calls.jsonl")
    state_path = WORK / "project.json"
    if state_path.exists():
        engine.project_id = json.loads(state_path.read_text(encoding="utf-8"))["project_id"]
        return engine
    health = engine.require_ready()
    log(f"engine {health.version.version} ready, ffmpeg {health.version.ffmpeg}")
    created = engine.create_project("Phase 2 media engine demo")
    project_dir = Path(created["directory"])
    fonts_dir = project_dir / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CJK_FONT, fonts_dir / CJK_FONT.name)
    (project_dir / "audio").mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"project_id": engine.project_id, "directory": str(project_dir)},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"project {engine.project_id} -> {project_dir}")
    return engine


def project_dir(engine: OpenKatanaEngine) -> Path:
    return Path(engine.get_project()["directory"])


# ----------------------------------------------------------------------- audio
# Mix targets, learned by measuring the rendered files (see qc/review_index.md):
# the bed sits near -25 dBFS mean and a -35 dBFS effect is simply not there. Each
# generator therefore boosts itself into the -20 dBFS range, and SFX_VOLUMES only
# trims the last couple of dB. ``whoosh_tail`` used to band-pass brown noise, which
# left it at -73 dBFS — i.e. a silent file that still counted as an effect.
SFX_FILTERS: dict[str, tuple[str, str]] = {
    "whoosh_low": ("anoisesrc=d=0.7:c=pink:a=0.9",
                   "highpass=f=180,lowpass=f=2600,afade=t=in:st=0:d=0.22,"
                   "afade=t=out:st=0.34:d=0.34,volume=2.3"),
    "impact_soft": ("sine=f=90:d=0.55",
                    "afade=t=out:st=0.04:d=0.5,volume=2.6"),
    "digital_rise": ("aevalsrc=0.35*sin(2*PI*(300+1600*t)*t):s=48000:d=0.65",
                     "afade=t=in:st=0:d=0.08,afade=t=out:st=0.45:d=0.2,volume=0.77"),
    "riser": ("aevalsrc=0.3*sin(2*PI*(120+520*t)*t):s=48000:d=1.5",
              "afade=t=in:st=0:d=0.7,afade=t=out:st=1.25:d=0.25,volume=0.94"),
    "whoosh_tail": ("anoisesrc=d=0.9:c=pink:a=0.9",
                    "highpass=f=300,lowpass=f=4200,afade=t=in:st=0:d=0.12,"
                    "afade=t=out:st=0.35:d=0.5,volume=2.4"),
    "soft_hit": ("sine=f=140:d=0.45",
                 "afade=t=out:st=0.03:d=0.4,volume=2.8"),
}

#: Mix level per effect name. Kept next to the generators so a new effect cannot
#: be added without deciding how loud it is allowed to be.
SFX_VOLUMES: dict[str, float] = {
    "whoosh_low": 0.9,
    "impact_soft": 0.9,
    "digital_rise": 0.9,
    "riser": 0.85,
    "whoosh_tail": 0.9,
    "soft_hit": 0.9,
}


def score_notes(duration: float = 40.0) -> list[dict[str, Any]]:
    """A deterministic 40 s bed: triangle bass, sine pad, noise ticks."""
    notes: list[dict[str, Any]] = []
    for index in range(int(duration // 2)):
        notes.append({"at": index * 2.0, "duration": 1.6, "midi": 45 if index % 4 else 43,
                      "gain": 0.30, "pan": 0.0, "attack": 0.02, "release": 0.5,
                      "waveform": "triangle"})
    for index in range(int(duration // 4)):
        notes.append({"at": index * 4.0 + 1.0, "duration": 2.4, "midi": 57 + (index % 3) * 2,
                      "gain": 0.14, "pan": -0.25, "attack": 0.45, "release": 0.9,
                      "waveform": "sine"})
    for index in range(int(duration // 0.5)):
        notes.append({"at": index * 0.5, "duration": 0.08, "midi": 84, "gain": 0.05,
                      "pan": 0.35, "attack": 0.005, "release": 0.05, "waveform": "noise"})
    return notes


def _to_aac(source: Path, target: Path) -> Path:
    ffmpeg("-i", str(source), "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", str(target))
    return target


def stage_audio() -> dict[str, Any]:
    """Synthesise the original score, build the effects, and mux everything to AAC."""
    engine = open_engine()
    storyboard = load_storyboard(STORYBOARD)
    duration = storyboard.duration
    target_dir = project_dir(engine) / "audio"
    target_dir.mkdir(parents=True, exist_ok=True)

    score_result = engine.synthesize_score(score_notes(duration), duration=duration,
                                           sample_rate=48000, seed=7)
    score_rel = str(score_result.get("audio") or score_result.get("path") or "")
    if not score_rel:
        raise SystemExit(f"synthesize_score returned no audio path: {json.dumps(score_result)[:400]}")
    score_wav = Path(score_rel) if Path(score_rel).is_absolute() else project_dir(engine) / score_rel
    log(f"score synthesized -> {score_wav.name} ({score_result.get('credits_used', '?')} credits)")

    bed_job = _to_aac(score_wav, AUDIO_DIR / "score_bed.m4a")
    shutil.copy2(bed_job, target_dir / bed_job.name)

    orbit_src = APPROVED / "openkatana_orbit_score.wav"
    orbit_job = AUDIO_DIR / "orbit_loop.m4a"
    ffmpeg("-stream_loop", "8", "-i", str(orbit_src), "-t", f"{duration:.3f}",
           "-af", f"afade=t=in:st=0:d=1.0,afade=t=out:st={duration - 2.5:.3f}:d=2.5,volume=0.5",
           "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "192k", str(orbit_job))
    shutil.copy2(orbit_job, target_dir / orbit_job.name)

    effects: list[dict[str, Any]] = []
    for item in sfx_schedule(storyboard):
        source, filters = SFX_FILTERS[item["name"]]
        raw = AUDIO_DIR / f"{item['name']}.wav"
        ffmpeg("-f", "lavfi", "-i", source, "-af", filters, "-ac", "2", "-ar", "48000", str(raw))
        m4a = _to_aac(raw, AUDIO_DIR / f"{item['name']}.m4a")
        shutil.copy2(m4a, target_dir / m4a.name)
        effects.append({**item, "file": m4a.name, "byte_size": m4a.stat().st_size})
        log(f"sfx {item['name']} at {item['at']}s -> {m4a.name}")

    facts = {
        "duration": duration,
        "score": {"file": bed_job.name, "duration": duration, "source": score_rel,
                  "credits_used": score_result.get("credits_used")},
        "licensed_bed": {"file": orbit_job.name, "source_id": "S04", "license": "Apache-2.0"},
        "effects": effects,
        "counts": {
            "effect_events": len(effects),
            "distinct_effects": len({item["name"] for item in effects}),
            "music_tracks": 2,
        },
        "narration": "none — no TTS was called and no human recording exists for this demo",
    }
    (WORK / "audio.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return facts


# ---------------------------------------------------------------- compositions
def _box(node_id: str, style: dict[str, Any], children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"id": node_id, "type": "box", "style": style}
    if children:
        node["children"] = children
    return node


def _text(node_id: str, text: str, style: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id, "type": "text", "text": text, "style": style}


def _tracks(canvas_frames: int, tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clamp every key into 0..frames-1 — the engine rejects out-of-range frames."""
    clean: list[dict[str, Any]] = []
    for track in tracks:
        keys: list[list[float]] = []
        for frame, value in track["keys"]:
            frame = max(0, min(canvas_frames - 1, int(frame)))
            keys.append([frame, value])
        if not keys:
            continue
        if len(keys) == 1:
            keys.append([min(canvas_frames - 1, keys[0][0] + 1), keys[0][1]])
        clean.append({"node": track["node"], "property": track["property"],
                      "keys": keys, "easing": track.get("easing", "smooth")})
    return clean


def chart_composition(counts: dict[str, int], frames: int = 210) -> dict[str, Any]:
    """Animated bar chart of this film's own storyboard counts (no external data)."""
    bars = [
        ("视频镜", counts["real_video"], CYAN),
        ("图片镜", counts["real_image"], LIME),
        ("合成镜", counts["composition"], MAGENTA),
        ("字幕条", counts["subtitles"], PAPER),
        ("音效轨", 2 + counts["sound_effects"], DIM),
    ]
    peak = max(value for _, value, _ in bars) or 1
    area = 880
    columns: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for index, (label, value, color) in enumerate(bars):
        bar_id = f"bar{index}"
        value_id = f"val{index}"
        label_id = f"lab{index}"
        height = max(60, int(area * value / peak))
        start = 10 + index * 9
        columns.append(_box(f"col{index}", {
            "display": "flex", "flex-direction": "column", "align-items": "center",
            "justify-content": "flex-end", "flex": "1", "height": f"{area + 120}px", "gap": "16px",
        }, [
            _text(value_id, f"{value}", {
                "font-family": FONT_FAMILY, "font-size": "52px", "font-weight": "900",
                "color": color, "line-height": "1", "opacity": "0",
            }),
            _box(bar_id, {
                "width": "96px", "height": f"{height}px", "background-color": color,
                "border-radius": "8px 8px 3px 3px", "transform-origin": "50% 100%",
                # No style transform: the `scale` track below is the only thing that scales
                # the bar. Setting scale(1, 0) here makes the track multiply into a zero height.
                "flex-shrink": "0",
            }),
            _text(label_id, label, {
                "font-family": FONT_FAMILY, "font-size": "30px", "font-weight": "500",
                "color": DIM, "line-height": "1.2", "text-align": "center",
            }),
        ]))
        tracks.append({"node": bar_id, "property": "scale",
                       "keys": [[start, 0], [start + 34, 1]], "easing": "smooth"})
        tracks.append({"node": value_id, "property": "opacity",
                       "keys": [[start + 20, 0], [start + 34, 1]], "easing": "smooth"})

    root = _box("root", {
        "display": "flex", "flex-direction": "column", "width": "1080px", "height": "1920px",
        "padding": "150px 90px 220px 90px", "box-sizing": "border-box",
        "background-color": INK, "gap": "38px", "justify-content": "center",
    }, [
        _text("kicker", "MEDIA ENGINE / SELF-TEST", {
            "font-family": FONT_FAMILY, "font-size": "30px", "font-weight": "700",
            "color": LIME, "letter-spacing": "6px", "opacity": "0",
        }),
        _text("title", "本片分镜统计", {
            "font-family": FONT_FAMILY, "font-size": "86px", "font-weight": "900",
            "color": PAPER, "line-height": "1.1", "opacity": "0",
        }),
        _box("chart", {
            "display": "flex", "flex-direction": "row", "align-items": "flex-end",
            "justify-content": "space-between", "gap": "26px", "height": "620px",
            "padding-bottom": "18px",
        }, columns),
        _box("baseline", {
            "height": "3px", "background-color": "#24304a", "width": "100%",
        }),
        _text("note", "数值取自本任务 storyboard.csv 的镜头与音轨计数，不含任何外部行情数据。", {
            "font-family": FONT_FAMILY, "font-size": "28px", "font-weight": "400",
            "color": DIM, "line-height": "1.5", "opacity": "0",
        }),
    ])
    tracks.extend([
        {"node": "kicker", "property": "opacity", "keys": [[2, 0], [14, 1]], "easing": "smooth"},
        {"node": "title", "property": "opacity", "keys": [[6, 0], [22, 1]], "easing": "smooth"},
        {"node": "chart", "property": "translateY", "keys": [[8, 26], [26, 0]], "easing": "smooth"},
        {"node": "note", "property": "opacity", "keys": [[70, 0], [86, 1]], "easing": "smooth"},
    ])
    return {
        "schema": 1,
        "canvas": {"w": CANVAS[0], "h": CANVAS[1], "fps": FPS, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_REL, "weight": "100 900"}],
        "root": root,
        "tracks": _tracks(frames, tracks),
    }


def diagram_composition(frames: int = 210) -> dict[str, Any]:
    """A five-step production chain that assembles itself node by node."""
    steps = [
        ("step0", "01", "approved_sources", "白名单登记的素材"),
        ("step1", "02", "import_media", "入库并转成帧序列"),
        ("step2", "03", "storyboard.csv", "每个镜头写清用途"),
        ("step3", "04", "timeline", "多轨合成与转场"),
        ("step4", "05", "draft.mp4", "渲染出片并质检"),
    ]
    nodes: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for index, (node_id, number, name, note) in enumerate(steps):
        start = 8 + index * 20
        nodes.append(_box(node_id, {
            "display": "flex", "flex-direction": "row", "align-items": "center",
            "gap": "24px", "padding": "26px 30px", "border-radius": "18px",
            "background-color": "#141d31", "border": f"2px solid {LIME if index == 0 else '#24304a'}",
            "opacity": "0", "transform": "translateY(-26px)",
        }, [
            _text(f"{node_id}_num", number, {
                "font-family": FONT_FAMILY, "font-size": "40px", "font-weight": "900",
                "color": LIME, "line-height": "1",
            }),
            _box(f"{node_id}_txt", {"display": "flex", "flex-direction": "column", "gap": "6px"}, [
                _text(f"{node_id}_name", name, {
                    "font-family": FONT_FAMILY, "font-size": "40px", "font-weight": "800",
                    "color": PAPER, "line-height": "1.1",
                }),
                _text(f"{node_id}_note", note, {
                    "font-family": FONT_FAMILY, "font-size": "26px", "font-weight": "400",
                    "color": DIM, "line-height": "1.2",
                }),
            ]),
        ]))
        tracks.append({"node": node_id, "property": "opacity",
                       "keys": [[start, 0], [start + 14, 1]], "easing": "smooth"})
        tracks.append({"node": node_id, "property": "translateY",
                       "keys": [[start, -26], [start + 16, 0]], "easing": "smooth"})
        if index < len(steps) - 1:
            link_id = f"link{index}"
            nodes.append(_box(link_id, {
                "width": "4px", "height": "54px", "background-color": LIME,
                "transform-origin": "50% 0%", "margin-left": "60px",
            }))
            tracks.append({"node": link_id, "property": "scale",
                           "keys": [[start + 14, 0], [start + 26, 1]], "easing": "smooth"})

    root = _box("root", {
        "display": "flex", "flex-direction": "column", "width": "1080px", "height": "1920px",
        "padding": "150px 90px 200px 90px", "box-sizing": "border-box",
        "background-color": INK, "gap": "26px", "justify-content": "center",
    }, [
        _text("kicker", "PIPELINE", {
            "font-family": FONT_FAMILY, "font-size": "30px", "font-weight": "700",
            "color": CYAN, "letter-spacing": "6px", "opacity": "0",
        }),
        _text("title", "一条片子怎么来的", {
            "font-family": FONT_FAMILY, "font-size": "82px", "font-weight": "900",
            "color": PAPER, "line-height": "1.1", "opacity": "0",
        }),
        _box("chain", {"display": "flex", "flex-direction": "column", "gap": "0px",
                       "margin-top": "46px"}, nodes),
    ])
    tracks.extend([
        {"node": "kicker", "property": "opacity", "keys": [[2, 0], [12, 1]], "easing": "smooth"},
        {"node": "title", "property": "opacity", "keys": [[4, 0], [18, 1]], "easing": "smooth"},
        {"node": "chain", "property": "translateY", "keys": [[150, 0], [frames - 1, -30]], "easing": "smooth"},
    ])
    return {
        "schema": 1,
        "canvas": {"w": CANVAS[0], "h": CANVAS[1], "fps": FPS, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_REL, "weight": "100 900"}],
        "root": root,
        "tracks": _tracks(frames, tracks),
    }


def outro_composition(storyboard_counts: dict[str, int],
                      credits: list[tuple[str, str, str]],
                      frames: int = 240) -> dict[str, Any]:
    """The closing card: what this film proves, what it used, and who made it.

    ``credits`` comes straight from ``source_register.csv`` (id, short label,
    credit string) — the card may not invent an author, and it may not summarise
    three different NASA authorships into one vague line.
    """
    lines: list[tuple[str, str, int, str, str]] = [
        ("title", "分镜 → 时间轴 → 成片", 62, PAPER, "900"),
        ("label", "素材署名（逐条对应 source_register.csv）", 26, CYAN, "600"),
    ]
    for index, (source_id, label, credit) in enumerate(credits, start=1):
        lines.append((f"c{index}", f"{source_id} {label} · {credit}", 27, PAPER, "400"))
    lines += [
        ("note", "NASA 未审查、未背书本片；素材依 NASA 媒体使用政策使用，"
                 "第三方内容可能另有版权限制。", 24, DIM, "400"),
        ("stats", f"{storyboard_counts['shots']} 个镜头 · "
                  f"{storyboard_counts['transitions']} 种转场 · "
                  f"{storyboard_counts['camera_motions']} 种运镜 · "
                  f"音效 {storyboard_counts['sound_effects']} 处", 26, DIM, "500"),
        ("closing", "本片是引擎自检，不含财经结论", 42, LIME, "700"),
    ]
    children = ["root"]
    rows: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    start = 6
    for index, (node_id, text, size, color, weight) in enumerate(lines):
        rows.append(_text(node_id, text, {
            "font-family": FONT_FAMILY, "font-size": f"{size}px", "font-weight": weight,
            "color": color, "line-height": "1.45", "opacity": "0",
        }))
        start = 6 + index * 15
        tracks.append({"node": node_id, "property": "opacity",
                       "keys": [[start, 0], [start + 14, 1]], "easing": "smooth"})
        tracks.append({"node": node_id, "property": "translateY",
                       "keys": [[start, 14], [start + 16, 0]], "easing": "smooth"})

    root = _box("root", {
        "display": "flex", "flex-direction": "column", "width": "1080px", "height": "1920px",
        "padding": "0 90px", "box-sizing": "border-box", "background-color": INK,
        "justify-content": "center", "align-items": "flex-start", "gap": "18px",
    }, rows)
    del children
    return {
        "schema": 1,
        "canvas": {"w": CANVAS[0], "h": CANVAS[1], "fps": FPS, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_REL, "weight": "100 900"}],
        "root": root,
        "tracks": _tracks(frames, tracks),
    }


CREDIT_LABELS = (
    ("S01", "阿波罗17号地球"),
    ("S02", "蓝色弹珠星空"),
    ("S03", "VIIRS 地球自转"),
    ("S04", "配乐"),
)


def outro_credit_lines() -> list[tuple[str, str, str]]:
    """Read the on-screen credits from the verified register, never from memory.

    Every line is (source id, short label, credit string). The credit string is
    copied byte for byte out of ``source_register.csv`` so the card and the receipt
    cannot disagree — and an unregistered or unrenderable source aborts the card
    instead of being papered over with a generic "NASA imagery" line.
    """
    register = ApprovedSources(JOB_ROOT).load_register()
    lines: list[tuple[str, str, str]] = []
    for source_id, label in CREDIT_LABELS:
        entry = register.assert_renderable(source_id)
        credit = entry.credit.strip()
        if not credit:
            raise SystemExit(f"{source_id}: the register has no credit line; the card may not invent one")
        lines.append((source_id, label, credit))
    lines.append(("字体", "Noto Sans SC", "SIL OFL 1.1"))
    return lines


def stage_compositions() -> dict[str, Any]:
    """Author and render the three original compositions this film uses."""
    engine = open_engine()
    storyboard = load_storyboard(STORYBOARD)
    counts = storyboard.counts()
    credits = outro_credit_lines()
    documents = {
        "C_CHART": ("compositions/chart.json", chart_composition(counts), 210),
        "C_DIAGRAM": ("compositions/diagram.json", diagram_composition(), 210),
        "C_OUTRO": ("compositions/outro.json", outro_composition(counts, credits), 240),
    }
    rendered: dict[str, Any] = {}
    for asset_id, (path, document, frames) in documents.items():
        (ASSETS / "compositions" / Path(path).name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        engine.save_composition_reloading(path, document)
        result = engine.render_composition(path)
        clip = result.get("clip") if isinstance(result.get("clip"), dict) else result
        rendered[asset_id] = {"path": path, "frames": frames, "clip": clip}
        log(f"composition {asset_id} -> {clip.get('frames_dir')} ({clip.get('n')} frames)")
    (WORK / "compositions.json").write_text(
        json.dumps(rendered, ensure_ascii=False, indent=2), encoding="utf-8")
    return rendered


# ---------------------------------------------------------------------- import
def stage_import() -> dict[str, Any]:
    """Import every picture into the project and record the clip the engine returns."""
    engine = open_engine()
    plan = [
        ("S01", APPROVED / "nasa_apollo17_earthrise.jpg", {"rights": "public_domain"}),
        ("S02", APPROVED / "nasa_blue_marble_starfield.jpg", {"rights": "public_domain"}),
        ("S03A", APPROVED / "nasa_earth_rotation.mp4",
         {"rights": "public_domain", "start": 0.0, "duration": 6.0, "max_width": 1080}),
        ("S03B", APPROVED / "nasa_earth_rotation.mp4",
         {"rights": "public_domain", "start": 12.0, "duration": 5.0, "max_width": 1080}),
    ]
    clips: dict[str, Any] = {}
    imported: list[dict[str, Any]] = []
    for asset_id, path, options in plan:
        result = engine.import_media(path, **options)
        clip = (result.raw.get("clip") if isinstance(result.raw, dict) else None) or {}
        if not clip:
            raise SystemExit(f"{asset_id}: engine returned no clip for {path.name}")
        clips[asset_id] = clip
        imported.append({"asset_id": asset_id, "file": path.name, "sha256": result.sha256,
                         "width": clip.get("w"), "height": clip.get("h"), "frames": clip.get("n"),
                         "fps": clip.get("fps"), "options": options})
        log(f"import {asset_id} {path.name} -> {clip.get('n')} frames {clip.get('w')}x{clip.get('h')}")

    # Audio import is part of the acceptance list too: the engine must accept a WAV source.
    audio_asset = engine.import_media(APPROVED / "openkatana_orbit_score.wav",
                                      rights="licensed", audio_only=True)
    imported.append({"asset_id": "S04", "file": "openkatana_orbit_score.wav",
                     "sha256": audio_asset.sha256, "kind": audio_asset.kind,
                     "duration": audio_asset.duration, "has_audio": audio_asset.has_audio})
    log(f"import S04 audio -> kind={audio_asset.kind} duration={audio_asset.duration}s")

    compositions = json.loads((WORK / "compositions.json").read_text(encoding="utf-8")) \
        if (WORK / "compositions.json").exists() else {}
    for asset_id, entry in compositions.items():
        clips[asset_id] = entry["clip"]

    (WORK / "clips.json").write_text(json.dumps({"clips": clips, "imported": imported},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    return {"clips": clips, "imported": imported}


# -------------------------------------------------------------------- timeline
#: Beds that run under the whole film. Everything else on the audio bus is an
#: effect cue and must be traceable to a storyboard row.
MUSIC_SRCS = ("audio/score_bed.m4a", "audio/orbit_loop.m4a")


def audio_tracks(storyboard: "Storyboard | None" = None) -> list[dict[str, Any]]:
    """Two music beds plus one track per storyboard effect cue, in time order."""
    board = storyboard if storyboard is not None else load_storyboard(STORYBOARD)
    tracks: list[dict[str, Any]] = [
        {"src": MUSIC_SRCS[0], "at": 0.0, "volume": 0.55, "fade_in": 0.6, "fade_out": 1.5},
        {"src": MUSIC_SRCS[1], "at": 0.0, "volume": 0.35, "fade_in": 1.0, "fade_out": 2.0},
    ]
    for item in sfx_schedule(board):
        tracks.append({"src": f"audio/{item['name']}.m4a", "at": item["at"], "volume": item["volume"]})
    return tracks


def build_document(*, narration: Path | None = None, duck: float = -9.0) -> dict[str, Any]:
    storyboard = load_storyboard(STORYBOARD)
    payload = json.loads((WORK / "clips.json").read_text(encoding="utf-8"))
    clips = payload["clips"]
    tracks = audio_tracks(storyboard)
    audio = audio_mix(tracks, gain=0.9)
    if narration is not None:
        tracks.append({"src": f"audio/{narration.name}", "at": 0.5, "volume": 1.0})
        audio = audio_mix(tracks, gain=0.9)
        audio["duck"] = [{"track": len(tracks) - 1, "under": [0, 1], "db": duck,
                          "threshold_db": -34, "attack": 0.12, "release": 0.45}]
    return build_timeline(
        storyboard,
        clips,
        audio=audio,
        fonts=[{"family": FONT_FAMILY, "url": FONT_REL}],
        # Captions come from the storyboard's `subtitle` column only. `narration` is
        # the spoken script for a voice this demo does not have; it is never drawn.
        text=subtitle_events(storyboard, family=FONT_FAMILY, size=44, source="subtitle"),
        bg=INK,
    )


def stage_timeline(*, narration: Path | None = None) -> dict[str, Any]:
    engine = open_engine()
    storyboard = load_storyboard(STORYBOARD)
    document = build_document(narration=narration)
    (WORK / "timeline.json").write_text(json.dumps(document, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    audio_facts = json.loads((WORK / "audio.json").read_text(encoding="utf-8"))
    generated = {item["name"]: f"audio/{item['file']}" for item in audio_facts["effects"]}
    consistency = audio_consistency(
        events=storyboard.sound_effect_events(),
        generated=generated,
        music_srcs=MUSIC_SRCS,
        audio=document["audio"],
    )
    QC_DIR.mkdir(parents=True, exist_ok=True)
    log(f"audio consistency -> events {consistency['events']}, files "
        f"{consistency['generated_files']}, music {consistency['music_tracks']}, "
        f"tracks {consistency['total_tracks']}, ok={consistency['ok']}")
    if not consistency["ok"]:
        raise SystemExit("audio bookkeeping does not match the timeline: "
                         + json.dumps(consistency["problems"], ensure_ascii=False))
    validation = engine.validate_timeline(document)
    log(f"validate_timeline -> {json.dumps(validation, ensure_ascii=False)[:200]}")
    saved = engine.save_timeline_with_retry(document)
    revision = str(saved.get("revision") or saved.get("timeline_revision") or "")
    log(f"save_timeline revision={revision[:12]}")
    (WORK / "timeline_saved.json").write_text(
        json.dumps({"revision": revision, "saved": saved}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (QC_DIR / "audio_consistency.json").write_text(
        json.dumps({"timeline_revision": revision, **consistency}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"revision": revision}


def stage_render() -> dict[str, Any]:
    engine = open_engine()
    state = json.loads((WORK / "timeline_saved.json").read_text(encoding="utf-8"))
    revision = state["revision"]
    request_id = f"phase2-{revision[:8]}"
    log(f"render request {request_id}")
    result = engine.render(request_id=request_id, timeout=2400.0, poll=2.0)
    relative = str(result.get("video") or result.get("output") or result.get("path") or "")
    if not relative:
        raise SystemExit(f"render finished without an output file: {json.dumps(result)[:400]}")
    candidate = Path(relative)
    # The engine reports paths relative to the project directory.
    source = candidate if candidate.is_absolute() else project_dir(engine) / candidate
    if not source.is_file():
        raise SystemExit(f"render reported {relative!r} but no such file at {source}")
    target = OUTPUT / "preview_30_45s.mp4"
    shutil.copy2(source, target)
    facts = {
        "request_id": request_id,
        "revision": revision,
        "engine_output": str(source),
        "file": target.name,
        "byte_size": target.stat().st_size,
        "render": {k: v for k, v in result.items() if isinstance(v, (str, int, float, bool))},
    }
    (WORK / "render.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"render -> {target.relative_to(JOB_ROOT)} ({facts['byte_size']} B)")
    return facts


def _filter_log(*args: str) -> str:
    chain = toolchain()
    command = [str(chain.ffmpeg), "-hide_banner", "-nostats", *args]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (result.stdout or "") + (result.stderr or "")


def _spans(pattern: str, text: str, start_key: str = "start", end_key: str = "end") -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for line in text.splitlines():
        if pattern not in line:
            continue
        start = None
        end = None
        for token in line.split():
            if token.startswith(f"{start_key}:"):
                start = float(token.split(":", 1)[1])
            elif token.startswith(f"{end_key}:"):
                end = float(token.split(":", 1)[1])
        if start is not None and end is not None:
            spans.append((start, end))
    return spans


def stage_qc() -> dict[str, Any]:
    """Measure the finished file. Technical facts only — taste is a human call."""
    storyboard = load_storyboard(STORYBOARD)
    target = OUTPUT / "preview_30_45s.mp4"
    if not target.exists():
        raise SystemExit(f"no rendered file at {target}")
    probe = ffprobe_json(target)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    video = streams.get("video", {})
    audio = streams.get("audio", {})
    duration = float(probe["format"]["duration"])
    video_duration = float(video.get("duration") or duration)
    audio_duration = float(audio.get("duration") or duration)

    black_log = _filter_log("-i", str(target), "-vf", "blackdetect=d=0.2:pix_th=0.10", "-f", "null", "-")
    freeze_log = _filter_log("-i", str(target), "-vf", "freezedetect=n=0.001:d=1.0", "-f", "null", "-")
    silence_log = _filter_log("-i", str(target), "-af", "silencedetect=n=-50dB:d=1.5", "-f", "null", "-")
    volume_log = _filter_log("-i", str(target), "-af", "volumedetect", "-f", "null", "-")
    loudness_log = _filter_log("-i", str(target), "-af", "loudnorm=print_format=json", "-f", "null", "-")

    blacks = _spans("black_start", black_log)
    freezes = _spans("freeze_start", freeze_log)
    silences = _spans("silence_start", silence_log)
    mean_volume = _db(volume_log, "mean_volume")
    max_volume = _db(volume_log, "max_volume")
    loudness = _json_tail(loudness_log)

    counts = storyboard.counts()
    audio_report = {}
    if (QC_DIR / "audio_consistency.json").exists():
        audio_report = json.loads((QC_DIR / "audio_consistency.json").read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("canvas", video.get("width") == CANVAS[0] and video.get("height") == CANVAS[1],
          f"{video.get('width')}x{video.get('height')} (expected {CANVAS[0]}x{CANVAS[1]})")
    frame_rate = video.get("r_frame_rate") or ""
    check("frame_rate", frame_rate in {"30/1", "30.0"}, f"{frame_rate} (expected 30/1)")
    check("video_codec", video.get("codec_name") == "h264", str(video.get("codec_name")))
    check("audio_codec", audio.get("codec_name") == "aac", str(audio.get("codec_name")))
    check("duration_matches_storyboard", abs(duration - storyboard.duration) <= 0.2,
          f"{duration:.3f}s vs storyboard {storyboard.duration:.3f}s")
    check("av_equal_length", abs(video_duration - audio_duration) <= 0.10,
          f"video {video_duration:.3f}s / audio {audio_duration:.3f}s")
    longest_black = max((end - start for start, end in blacks), default=0.0)
    check("no_black_over_0.45s", longest_black <= 0.45,
          f"{len(blacks)} segment(s), longest {longest_black:.2f}s")
    longest_freeze = max((end - start for start, end in freezes), default=0.0)
    check("no_freeze_over_1.0s", longest_freeze <= 1.0,
          f"{len(freezes)} segment(s), longest {longest_freeze:.2f}s")
    longest_silence = max((end - start for start, end in silences), default=0.0)
    check("no_silence_over_1.5s", longest_silence <= 1.5,
          f"{len(silences)} segment(s), longest {longest_silence:.2f}s")
    check("no_clipping", max_volume is not None and max_volume <= -0.5,
          f"max_volume {max_volume} dB")
    check("audible", mean_volume is not None and mean_volume > -40.0, f"mean_volume {mean_volume} dB")
    check("shot_count", counts["shots"] >= 6, f"{counts['shots']} shots (need >= 6)")
    check("real_media", counts["real_video"] >= 2 and counts["real_image"] >= 2,
          f"real_video {counts['real_video']}, real_image {counts['real_image']}")
    check("transition_variety", counts["transitions"] >= 3, f"{counts['transitions']} transition types")
    check("camera_variety", counts["camera_motions"] >= 2, f"{counts['camera_motions']} camera motions")
    check("multi_track_audio", len(audio_tracks(storyboard)) >= 2,
          f"{len(audio_tracks(storyboard))} audio tracks in the timeline")
    check("audio_counts_consistent", bool(audio_report.get("ok")),
          f"{audio_report.get('events', 'n/a')} 处音效事件 / "
          f"{audio_report.get('generated_files', 'n/a')} 个音效文件 / "
          f"{audio_report.get('music_tracks', 'n/a')} 条配乐 / "
          f"{audio_report.get('total_tracks', 'n/a')} 条总音轨")

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file": str(target.relative_to(JOB_ROOT)),
        "byte_size": target.stat().st_size,
        "duration": duration,
        "video": {"codec": video.get("codec_name"), "width": video.get("width"),
                  "height": video.get("height"), "fps": frame_rate,
                  "duration": video_duration, "frames": video.get("nb_frames")},
        "audio": {"codec": audio.get("codec_name"), "sample_rate": audio.get("sample_rate"),
                  "channels": audio.get("channels"), "duration": audio_duration},
        "black_segments": blacks, "freeze_segments": freezes, "silence_segments": silences,
        "mean_volume_db": mean_volume, "max_volume_db": max_volume,
        "loudness": loudness,
        "storyboard_counts": counts,
        "audio_counts": {
            "effect_events": audio_report.get("events"),
            "distinct_effects": audio_report.get("distinct_effects"),
            "generated_files": audio_report.get("generated_files"),
            "music_tracks": audio_report.get("music_tracks"),
            "effect_tracks": audio_report.get("effect_tracks"),
            "total_tracks": audio_report.get("total_tracks"),
            "consistent": audio_report.get("ok"),
            "problems": audio_report.get("problems", []),
        },
        "checks": checks,
        "all_checks_ok": all(item["ok"] for item in checks),
        "note": "Programmatic checks only. Whether the film is good is not measurable here.",
    }
    (QC_DIR / "qc_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    (QC_DIR / "qc_report.md").write_text(_qc_markdown(metrics), encoding="utf-8")
    failed = [item for item in checks if not item["ok"]]
    log(f"qc: {len(checks) - len(failed)}/{len(checks)} checks passed")
    for item in failed:
        log(f"  FAIL {item['check']}: {item['detail']}")
    return metrics


def _db(text: str, key: str) -> float | None:
    for line in text.splitlines():
        if key in line:
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    return float(parts[1].strip().split()[0])
                except (ValueError, IndexError):
                    return None
    return None


def _json_tail(text: str) -> dict[str, Any]:
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _qc_markdown(metrics: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 质检报告",
        "",
        f"- 生成时间：{metrics['generated_at']}",
        f"- 文件：`{metrics['file']}`（{metrics['byte_size']:,} B）",
        f"- 时长：{metrics['duration']:.3f} 秒",
        f"- 画面：{metrics['video']['width']}×{metrics['video']['height']} / "
        f"{metrics['video']['fps']} / {metrics['video']['codec']}",
        f"- 声音：{metrics['audio']['codec']} / {metrics['audio']['sample_rate']} Hz / "
        f"{metrics['audio']['channels']} ch / {metrics['audio']['duration']:.3f} 秒",
        f"- 响度：mean {metrics['mean_volume_db']} dB，max {metrics['max_volume_db']} dB，"
        f"integrated {metrics['loudness'].get('input_i', 'n/a')} LUFS",
        "",
        "## 逐项检查",
        "",
        "| 检查 | 结果 | 实测 |",
        "| --- | --- | --- |",
    ]
    for item in metrics["checks"]:
        lines.append(f"| {item['check']} | {'通过' if item['ok'] else '**未通过**'} | {item['detail']} |")
    lines += [
        "",
        "## 分镜事实（来自 storyboard.csv）",
        "",
        f"- 镜头 {metrics['storyboard_counts']['shots']} 个；真实视频 "
        f"{metrics['storyboard_counts']['real_video']}、真实图片 "
        f"{metrics['storyboard_counts']['real_image']}、动态合成 "
        f"{metrics['storyboard_counts']['composition']}",
        f"- 字幕 {metrics['storyboard_counts']['subtitles']} 条；转场 "
        f"{metrics['storyboard_counts']['transitions']} 种；运镜 "
        f"{metrics['storyboard_counts']['camera_motions']} 种",
        "",
        "## 音频计数口径（四个数字分开记）",
        "",
        "| 口径 | 数量 | 含义 |",
        "| --- | --- | --- |",
        f"| 音效事件 | {metrics['audio_counts']['effect_events']} | storyboard 里 `sound_effect` "
        "非 `none` 的镜头数 |",
        f"| 独立音效文件 | {metrics['audio_counts']['generated_files']} | 实际用 ffmpeg 合成并导入的 "
        "音效文件数 |",
        f"| 配乐轨 | {metrics['audio_counts']['music_tracks']} | 贯穿全片的配乐层（原创合成 + "
        "Apache-2.0 授权曲） |",
        f"| 时间轴总音轨 | {metrics['audio_counts']['total_tracks']} | 时间轴 audio.tracks 的条数 = "
        "配乐 + 音效 |",
        "",
        f"- 一致性：{'通过' if metrics['audio_counts']['consistent'] else '**未通过**'}"
        f"（口径不同的 {metrics['audio_counts']['distinct_effects']} 个独立音效名，"
        "详见 `qc/audio_consistency.json`）",
        "",
        "## 黑帧 / 冻结 / 静音明细",
        "",
        f"- 黑帧段：{metrics['black_segments'] or '无'}",
        f"- 冻结段：{metrics['freeze_segments'] or '无'}",
        f"- 静音段：{metrics['silence_segments'] or '无'}",
        "",
        f"> {metrics['note']}",
        "",
    ]
    return "\n".join(lines)


def stage_evidence() -> dict[str, Any]:
    """Write what a human reviewer needs: manifest, key frames, timeline snapshot."""
    storyboard = load_storyboard(STORYBOARD)
    sources = ApprovedSources(JOB_ROOT).load_register()

    usage: dict[str, list[str]] = {}
    classes: dict[str, str] = {}
    kinds: dict[str, str] = {}
    purposes: dict[str, str] = {}
    # Two shots use different in-points of the same NASA clip; they are one source.
    source_of = {"S03A": "S03", "S03B": "S03"}
    for shot in storyboard.shots:
        asset_id = shot.asset_id
        if shot.asset_type == "composition":
            continue
        source_id = source_of.get(asset_id, asset_id)
        usage.setdefault(source_id, []).append(shot.shot_id)
        purposes.setdefault(source_id, shot.narrative_purpose)
        if shot.asset_type == "real_video":
            classes[source_id], kinds[source_id] = "A", "video"
        elif shot.asset_type == "real_image":
            classes[source_id], kinds[source_id] = "A", "image"
    usage["S04"] = ["(audio bed)"]
    classes["S04"], kinds["S04"] = "A", "audio"
    purposes["S04"] = "授权配乐底噪层（Apache-2.0 原创合成）"

    # Original visuals: the three rendered compositions plus the synthesised score.
    compositions = json.loads((WORK / "compositions.json").read_text(encoding="utf-8"))
    shots_by_asset: dict[str, list[str]] = {}
    purposes_by_asset: dict[str, str] = {}
    for shot in storyboard.shots:
        shots_by_asset.setdefault(shot.asset_id, []).append(shot.shot_id)
        purposes_by_asset.setdefault(shot.asset_id, shot.narrative_purpose)
    rows = build_manifest(sources, usage, classes=classes, kinds=kinds, purposes=purposes)
    manifest_path = write_manifest(JOB_ROOT / "assets_manifest.csv", rows)

    original_rows: list[dict[str, str]] = []
    for asset_id, entry in compositions.items():
        local = ASSETS / "compositions" / Path(entry["path"]).name
        original_rows.append({
            "asset_id": asset_id, "asset_class": "B", "kind": "composition",
            "file": str(local.relative_to(JOB_ROOT)).replace("\\", "/"),
            "source_id": "", "publisher": "本任务作者（HTML 合成，OpenKatana 渲染）",
            "source_url": "", "retrieved_at": "", "sha256": _sha256_file(local),
            "license": "原创（本仓库内容）", "commercial_use": "allowed", "credit": "",
            "verification_status": "verified",
            "used_in_shots": ", ".join(shots_by_asset.get(asset_id, [])),
            "purpose": purposes_by_asset.get(asset_id, ""),
        })
    extra_rows: list[dict[str, str]] = []
    orbit_credit = sources.assert_renderable("S04").credit
    for asset_id, relative, kind, purpose in (
        ("B01", "audio/score_bed.m4a", "audio",
         f"原创合成配乐（synthesize_score，{storyboard.duration:g} 秒）"),
        ("A01", "audio/orbit_loop.m4a", "audio",
         "S04 的加工版：-stream_loop 铺满全片 + 淡入淡出，授权与署名沿用 S04"),
        ("C01", "fonts/NotoSansSC-VF.ttf", "font", "中文字体（项目内副本）"),
    ):
        local = JOB_ROOT / relative
        if not local.exists():
            local = next(iter(PROJECT_WORKSPACE.rglob(Path(relative).name)), local)
        is_orbit = asset_id == "A01"
        extra_rows.append({
            "asset_id": asset_id,
            "asset_class": "C" if kind == "font" else ("A" if is_orbit else "B"),
            "kind": kind,
            "file": relative,
            "source_id": "S04" if is_orbit else "",
            "publisher": ("Google / Adobe" if kind == "font"
                          else ("OpenKatana contributors" if is_orbit else "本任务作者")),
            "source_url": ("https://fonts.google.com/noto/sans-serif-sc" if kind == "font" else ""),
            "retrieved_at": "", "sha256": _sha256_file(local),
            "license": ("SIL Open Font License 1.1" if kind == "font"
                        else ("Apache-2.0" if is_orbit else "原创（本仓库内容）")),
            "commercial_use": "allowed",
            "credit": ("Noto Sans SC, SIL OFL 1.1" if kind == "font"
                       else (orbit_credit if is_orbit else "")),
            "verification_status": "verified",
            "used_in_shots": ("(all captions)" if kind == "font"
                              else f"(all {storyboard.duration:g} s)"),
            "purpose": purpose,
        })
    original_rows.extend(extra_rows)
    with manifest_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        for row in original_rows:
            writer.writerow(row)
        # One program-generated effect file per storyboard cue, each hashed on its own.
        sfx_rows = 0
        for index, item in enumerate(sfx_schedule(storyboard), start=2):
            local = AUDIO_DIR / f"{item['name']}.m4a"
            writer.writerow({
                "asset_id": f"B{index:02d}", "asset_class": "B", "kind": "audio",
                "file": f"audio/{item['name']}.m4a", "source_id": "",
                "publisher": "本任务作者（ffmpeg 合成）", "source_url": "",
                "retrieved_at": "", "sha256": _sha256_file(local),
                "license": "原创（本仓库内容）", "commercial_use": "allowed", "credit": "",
                "verification_status": "verified",
                "used_in_shots": item["shot"], "purpose": f"镜头 {item['shot']} 切入音效",
            })
            sfx_rows += 1

    # Key frames: one still per shot, taken a little past its midpoint.
    frames_dir = QC_DIR / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    film = OUTPUT / "preview_30_45s.mp4"
    extracted: list[dict[str, Any]] = []
    if film.exists():
        for index, shot in enumerate(storyboard.shots, start=1):
            moment = shot.start_time + (shot.end_time - shot.start_time) * 0.6
            name = f"{index:02d}_{shot.shot_id}.png"
            ffmpeg("-ss", f"{moment:.3f}", "-i", str(film), "-frames:v", "1", "-y",
                   str(frames_dir / name))
            extracted.append({"shot_id": shot.shot_id, "at": round(moment, 3), "file": name})

    timeline_src = None
    for candidate in PROJECT_WORKSPACE.rglob("timeline.json"):
        timeline_src = candidate
    timeline_copy = None
    if timeline_src is not None:
        timeline_copy = PROJECT_WORKSPACE / "timeline.json"
        shutil.copy2(timeline_src, timeline_copy)

    facts = {"manifest": str(manifest_path.relative_to(JOB_ROOT)),
             "manifest_rows": len(rows) + len(original_rows) + sfx_rows,
             "manifest_source_rows": len(rows),
             "manifest_original_rows": len(original_rows),
             "manifest_sfx_rows": sfx_rows,
             "captions_from": "subtitle",
             "narration_rendered": False,
             "frames": extracted,
             "timeline": str(timeline_copy.relative_to(JOB_ROOT)) if timeline_copy else None}
    (WORK / "evidence.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"evidence: manifest {facts['manifest_rows']} rows "
        f"({len(rows)} 源 + {len(original_rows)} 原创 + {sfx_rows} 音效), "
        f"{len(extracted)} key frames")
    return facts


def _mean_db_at(film: Path, start: float, duration: float) -> float | None:
    """Mean volume of one window, measured with the same filter as the QC pass."""
    if duration <= 0:
        return None
    log_text = _filter_log("-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(film),
                           "-af", "volumedetect", "-f", "null", "-")
    return _db(log_text, "mean_volume")


def stage_review() -> dict[str, Any]:
    """Build the material a human needs to judge the film, not just measure it.

    Nothing here decides whether the film is good. It extracts the frames a reviewer
    must look at (opening, every shot, every transition, both animations, the credit
    card) and measures one thing the ear is bad at: whether an effect is buried in
    the music or shouting over it.
    """
    storyboard = load_storyboard(STORYBOARD)
    film = OUTPUT / "preview_30_45s.mp4"
    if not film.exists():
        raise SystemExit(f"no rendered file at {film}")
    review_dir = QC_DIR / "review"
    sheet_dir = review_dir / "_sheet"
    for folder in (review_dir, sheet_dir):
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("*.png"):
            stale.unlink()

    plan: list[tuple[str, float, str]] = [("00_opening", 0.6, "开场第一秒：画面立即有内容，不是黑底空转")]
    for shot in storyboard.shots:
        middle = shot.start_time + (shot.end_time - shot.start_time) * 0.5
        plan.append((f"shot_{shot.shot_id}", middle, f"{shot.shot_id} 主体：{shot.visual_description[:28]}"))
    for shot in storyboard.shots:
        if shot.transition in {"", "none"}:
            continue
        plan.append((f"trans_{shot.shot_id}", shot.start_time + 0.15,
                     f"{shot.shot_id} 入场转场 {shot.transition}：有无撕裂/闪烁/黑边"))
    for shot in storyboard.shots:
        if shot.asset_type != "composition":
            continue
        late = shot.end_time - 0.5
        plan.append((f"anim_{shot.shot_id}", late, f"{shot.shot_id} 动画收尾：{shot.animation[:24]}"))
    plan.append(("99_credits", storyboard.duration - 0.7,
                 "结尾卡署名：逐条可读、不裁切、不被字幕遮挡"))

    frames: list[dict[str, Any]] = []
    for index, (label, moment, purpose) in enumerate(plan):
        target = review_dir / f"{index:02d}_{label}.png"
        ffmpeg("-ss", f"{moment:.3f}", "-i", str(film), "-frames:v", "1", "-y", str(target))
        sheet_copy = sheet_dir / f"review_{index:02d}.png"
        shutil.copy2(target, sheet_copy)
        frames.append({"index": index, "label": label, "at": round(moment, 3),
                       "file": str(target.relative_to(JOB_ROOT)).replace("\\", "/"),
                       "purpose": purpose})

    contact_sheet = review_dir / "contact_sheet.png"
    ffmpeg("-y", "-framerate", "1", "-i", str(sheet_dir / "review_%02d.png"),
           "-vf", "scale=360:640,tile=4x5:margin=8:padding=6:color=0x0b1220",
           "-frames:v", "1", str(contact_sheet))
    for stale in sheet_dir.glob("*.png"):
        stale.unlink()

    # Is each effect audible without being harsh? Compare the music-only window that
    # ends just before the cue with the first second of the cue.
    effect_windows: list[dict[str, Any]] = []
    for item in sfx_schedule(storyboard):
        before = _mean_db_at(film, max(0.0, item["at"] - 1.4), 1.2)
        during = _mean_db_at(film, item["at"], 1.0)
        delta = None if before is None or during is None else round(during - before, 2)
        if delta is None:
            verdict = "无法测量"
        elif delta < 0.5:
            verdict = "可能被配乐掩盖"
        elif delta > 9.0:
            verdict = "可能偏响"
        else:
            verdict = "可闻且不刺耳"
        effect_windows.append({"shot": item["shot"], "effect": item["name"], "at": item["at"],
                               "music_only_db": before, "with_effect_db": during,
                               "delta_db": delta, "verdict": verdict})

    loudness_window = _mean_db_at(film, 0.0, storyboard.duration)
    facts = {
        "film": str(film.relative_to(JOB_ROOT)),
        "contact_sheet": str(contact_sheet.relative_to(JOB_ROOT)),
        "frames": frames,
        "effect_windows": effect_windows,
        "mean_volume_db": loudness_window,
        "note": "抽帧与分窗测量只提供复核材料；好不好看、听感是否舒服必须由人播放整片判断。",
    }
    (QC_DIR / "review_index.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    (QC_DIR / "review_index.md").write_text(_review_markdown(facts), encoding="utf-8")
    log(f"review: {len(frames)} frames -> {contact_sheet.name}, {len(effect_windows)} effect windows")
    return facts


def _review_markdown(facts: dict[str, Any]) -> str:
    lines = [
        "# Phase 2 人工复核材料索引",
        "",
        f"- 成片：`{facts['film']}`",
        f"- 联系表（4×5 缩略）：`{facts['contact_sheet']}`",
        f"- 全片平均音量：{facts['mean_volume_db']} dB",
        "",
        "## 逐帧索引",
        "",
        "| # | 时间 | 文件 | 看什么 |",
        "| --- | --- | --- | --- |",
    ]
    for frame in facts["frames"]:
        lines.append(f"| {frame['index']} | {frame['at']:.2f}s | `{frame['file']}` | {frame['purpose']} |")
    lines += [
        "",
        "## 音效可闻度（客观分窗测量，不等于听感）",
        "",
        "| 镜头 | 音效 | 落点 | 前 1.4 秒（只有配乐） | 音效后 1 秒 | 差值 | 判断 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in facts["effect_windows"]:
        lines.append(
            f"| {item['shot']} | {item['effect']} | {item['at']:.1f}s | "
            f"{item['music_only_db']} dB | {item['with_effect_db']} dB | "
            f"{item['delta_db']} dB | {item['verdict']} |")
    lines += ["", f"> {facts['note']}", ""]
    return "\n".join(lines)


def _sha256_file(path: Path) -> str:
    """Hash one support file; empty string when the file is absent."""
    import hashlib

    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", default="all",
                        choices=["sources", "audio", "compositions", "import",
                                 "timeline", "render", "qc", "review", "evidence", "all"])
    args = parser.parse_args(argv)
    ensure_dirs()
    if args.stage in {"sources", "all"}:
        stage_sources()
    if args.stage in {"audio", "all"}:
        stage_audio()
    if args.stage in {"compositions", "all"}:
        stage_compositions()
    if args.stage in {"import", "all"}:
        stage_import()
    if args.stage in {"timeline", "all"}:
        stage_timeline()
    if args.stage in {"render", "all"}:
        stage_render()
    if args.stage in {"qc", "all"}:
        stage_qc()
    if args.stage in {"review", "all"}:
        stage_review()
    if args.stage in {"evidence", "all"}:
        stage_evidence()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
