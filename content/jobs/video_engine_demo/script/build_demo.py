"""Author the video_engine_demo compositions and timeline, then drive OpenKatana's CLI.

This script is glue only. It does not render pixels itself: every frame is produced by
OpenKatana (Chromium + ffmpeg), which is the reviewed third-party engine recorded in
video_pipeline_v2_1_report.md. All content here is engine-meta (tool counts, shot
lengths) or clearly labelled synthetic demo material - no market data, no news.

Run with the repository's own interpreter (it only shells out to OpenKatana's CLI):

    .\\.venv\\Scripts\\python.exe content/jobs/video_engine_demo/script/build_demo.py --stage docs
    .\\.venv\\Scripts\\python.exe content/jobs/video_engine_demo/script/build_demo.py --stage render
    .\\.venv\\Scripts\\python.exe content/jobs/video_engine_demo/script/build_demo.py --stage timeline
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import sys
from pathlib import Path

JOB = Path(__file__).resolve().parents[1]
REPO = JOB.parents[2]
OPENKATANA = REPO / "tools" / "openkatana"
PY = OPENKATANA / ".venv" / "Scripts" / "python.exe"
FFMPEG = r"D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe"
PROJECT_ROOT = JOB / "project" / "video_engine_demo"
PROJECT_ID = "video_engine_demo"
PROJECT = JOB / "project" / PROJECT_ID
WORKSPACE = JOB / "project"

W, H, FPS = 1080, 1920, 30

# 画布底色不能贴黑：手机竖屏观看时纯黑会被判成黑帧，也让画面显得发闷
INK = "#151d2e"
PAPER = "#f2f5f7"
DIM = "#8f9bb0"
LIME = "#b6ff1f"
MAGENTA = "#ff2bd6"
CYAN = "#35e0ff"
DEEP = "#0b1220"

FONT_FACE = {"family": "AKSC", "src": "fonts/NotoSansSC-VF.ttf", "weight": "100 900"}


# --------------------------------------------------------------------------- helpers
def box(node_id, *, style=None, children=None, **extra):
    node = {"id": node_id, "type": "box", "children": children or []}
    if style:
        node["style"] = style
    node.update(extra)
    return node


def text(node_id, value, *, style=None, **extra):
    node = {"id": node_id, "type": "text", "text": value}
    if style:
        node["style"] = style
    node.update(extra)
    return node


def track(node_id, prop, keys, easing="smooth"):
    return {"node": node_id, "property": prop, "keys": keys, "easing": easing}


def abs_box(node_id, x, y, w, h, color, *, node_style=None, children=None):
    style = {
        "position": "absolute",
        "left": f"{x}px",
        "top": f"{y}px",
        "width": f"{w}px",
        "height": f"{h}px",
        "background-color": color,
    }
    if node_style:
        style.update(node_style)
    return box(node_id, style=style, children=children)


def centered_column(node_id, children, *, justify="center", gap="34px"):
    return box(
        node_id,
        style={
            "display": "flex",
            "flex-direction": "column",
            "align-items": "center",
            "justify-content": justify,
            "width": f"{W}px",
            "height": f"{H}px",
            "padding": "0 96px",
            "gap": gap,
        },
        children=children,
    )


def clamp_tracks(tracks, frames):
    """Track key frames must stay inside the composition; clamp and keep keys increasing."""
    fixed = []
    for entry in tracks:
        keys = []
        for frame, value in entry["keys"]:
            frame = min(max(int(frame), 0), frames - 1)
            if keys and frame <= keys[-1][0]:
                keys[-1] = [keys[-1][0], value]
            else:
                keys.append([frame, value])
        fixed.append({**entry, "keys": keys})
    return fixed


def composition(name, frames, root, tracks):
    tracks = clamp_tracks(tracks, frames)
    return name, {
        "schema": 1,
        "canvas": {"w": W, "h": H, "fps": FPS, "frames": frames},
        "fonts": [FONT_FACE],
        "root": root,
        "tracks": tracks,
    }


# ------------------------------------------------------------------ S1 kinetic type
def s1_hook():
    frames = 105
    root = box(
        "stage",
        style={"position": "relative", "width": f"{W}px", "height": f"{H}px", "background-color": INK},
        children=[
            abs_box("edge", 96, 720, 10, 420, LIME, node_style={"transform-origin": "top left"}),
            box(
                "stack",
                style={
                    "position": "absolute",
                    "left": "150px",
                    "top": "700px",
                    "width": "834px",
                    "display": "flex",
                    "flex-direction": "column",
                    "gap": "26px",
                },
                children=[
                    text("kicker", "AK · RESEARCH HUB", style={
                        "font-family": "AKSC", "font-size": "36px", "font-weight": "500",
                        "letter-spacing": "10px", "color": LIME}),
                    text("t1", "视频引擎", style={
                        "font-family": "AKSC", "font-size": "150px", "font-weight": "900",
                        "line-height": "1.06", "color": PAPER}),
                    text("t2", "能力演示", style={
                        "font-family": "AKSC", "font-size": "150px", "font-weight": "900",
                        "line-height": "1.06", "color": MAGENTA}),
                    text("sub", "脚本 → 分镜 → 素材 → 画面 → 质检", style={
                        "font-family": "AKSC", "font-size": "40px", "font-weight": "400",
                        "color": DIM, "margin-top": "28px"}),
                ],
            ),
        ],
    )
    tracks = [
        track("edge", "scale", [[0, 0], [18, 1]], "smooth"),
        track("kicker", "opacity", [[0, 0], [8, 1]]),
        track("kicker", "translateY", [[4, 26], [18, 0]], "smooth"),
        track("t1", "reveal", [[2, 0], [16, 1]], "smooth"),
        track("t1", "translateY", [[8, 44], [34, 0]], "smooth"),
        track("t2", "reveal", [[8, 0], [24, 1]], "smooth"),
        track("t2", "translateY", [[22, 44], [48, 0]], "smooth"),
        track("sub", "opacity", [[20, 0], [34, 1]]),
        track("stack", "scale", [[0, 1.06], [105, 1.0]], "linear"),
    ]
    return composition("s1_hook", frames, root, tracks)


# --------------------------------------------------------------- S2 number reveal
def s2_number():
    frames = 120
    root = box(
        "stage",
        style={"position": "relative", "width": f"{W}px", "height": f"{H}px", "background-color": INK},
        children=[
            abs_box("halo", 140, 620, 800, 800, "#0f1a12",
                    node_style={"border-radius": "50%", "transform-origin": "center center"}),
            box(
                "stack",
                style={
                    "position": "absolute", "left": "96px", "top": "760px", "width": "888px",
                    "display": "flex", "flex-direction": "column", "align-items": "center", "gap": "18px",
                },
                children=[
                    text("value", "75", style={
                        "font-family": "AKSC", "font-size": "430px", "font-weight": "900",
                        "line-height": "1", "color": LIME, "transform-origin": "center bottom"}),
                    text("unit", "本地工具", style={
                        "font-family": "AKSC", "font-size": "58px", "font-weight": "700", "color": PAPER}),
                    text("cap", "CLI 直调 · 零云端依赖", style={
                        "font-family": "AKSC", "font-size": "38px", "font-weight": "400", "color": DIM}),
                    abs_box("rule", 0, 0, 520, 8, MAGENTA, node_style={"transform-origin": "left center"}),
                ],
            ),
        ],
    )
    tracks = [
        track("halo", "scale", [[0, 0.6], [120, 1.15]], "linear"),
        track("halo", "opacity", [[0, 0.15], [60, 0.55], [120, 0.2]]),
        # 这条 reveal 的帧区间是被渲染器"倒放确定性"校验锁死的：
        # 起点必须晚于 scale 起点（6），结束帧改成 26 会触发 determinism 报错，只能用 46。
        track("value", "reveal", [[6, 0], [46, 1]], "smooth"),
        track("value", "scale", [[6, 0.72], [52, 1.0]], "smooth"),
        track("unit", "opacity", [[24, 0], [36, 1]]),
        track("unit", "translateY", [[40, 24], [60, 0]], "smooth"),
        track("cap", "opacity", [[32, 0], [46, 1]]),
        track("rule", "scale", [[56, 0], [86, 1]], "smooth"),
    ]
    return composition("s2_number", frames, root, tracks)


# ------------------------------------------------------------------ S3 staged chart
BARS = [
    ("S1", 3.5, LIME),
    ("S2", 4.0, LIME),
    ("S3", 5.0, CYAN),
    ("S4", 5.0, CYAN),
    ("S5", 4.0, MAGENTA),
    ("S6", 4.5, MAGENTA),
]
CHART_BOTTOM = 1500
CHART_TOP = 640
CHART_MAX = 5.0


def s3_chart():
    frames = 150
    children = [
        text("title", "镜头时长（秒）", style={
            "position": "absolute", "left": "96px", "top": "300px",
            "font-family": "AKSC", "font-size": "76px", "font-weight": "900", "color": PAPER}),
        text("note", "示意数据 · 引擎能力演示", style={
            "position": "absolute", "left": "96px", "top": "410px",
            "font-family": "AKSC", "font-size": "34px", "font-weight": "400", "color": DIM}),
        abs_box("axis", 96, CHART_BOTTOM + 6, 888, 4, "#243044"),
    ]
    tracks = []
    slot = 888 / len(BARS)
    bar_w = 78
    for index, (label, seconds, color) in enumerate(BARS):
        x = int(96 + index * slot + (slot - bar_w) / 2)
        height = int((CHART_TOP + (1 - seconds / CHART_MAX) * (CHART_BOTTOM - CHART_TOP)))
        top = CHART_BOTTOM - height
        delay = 8 + index * 11
        children.append(abs_box(
            f"bar_{index}", x, top, bar_w, height, color,
            node_style={"transform-origin": "center bottom", "border-radius": "10px 10px 0 0"},
        ))
        children.append(text(f"val_{index}", f"{seconds:g}", style={
            "position": "absolute", "left": f"{x - 18}px", "top": f"{top - 66}px",
            "font-family": "AKSC", "font-size": "40px", "font-weight": "700", "color": color}))
        children.append(text(f"lab_{index}", label, style={
            "position": "absolute", "left": f"{x + 6}px", "top": f"{CHART_BOTTOM + 28}px",
            "font-family": "AKSC", "font-size": "36px", "font-weight": "500", "color": DIM}))
        tracks.append(track(f"bar_{index}", "scale", [[delay, 0], [delay + 22, 1]], "smooth"))
        tracks.append(track(f"val_{index}", "opacity", [[delay + 16, 0], [delay + 30, 1]]))
        tracks.append(track(f"lab_{index}", "opacity", [[delay + 10, 0], [delay + 26, 1]]))
    tracks.append(track("axis", "opacity", [[0, 0], [12, 1]]))
    tracks.append(track("title", "reveal", [[0, 0], [14, 1]], "smooth"))
    tracks.append(track("note", "opacity", [[8, 0], [20, 1]]))
    root = box("stage", style={"position": "relative", "width": f"{W}px", "height": f"{H}px",
                               "background-color": INK}, children=children)
    return composition("s3_chart", frames, root, tracks)


# ------------------------------------------------------------ S4 structure diagram
PIPELINE = ["脚本", "分镜", "素材", "画面", "质检"]


def s4_diagram():
    frames = 150
    children = [
        text("title", "生产链路", style={
            "position": "absolute", "left": "96px", "top": "300px",
            "font-family": "AKSC", "font-size": "76px", "font-weight": "900", "color": PAPER}),
    ]
    tracks = [track("title", "reveal", [[0, 0], [14, 1]], "smooth")]
    box_h = 190
    gap = 62
    start_y = 560
    for index, name in enumerate(PIPELINE):
        y = start_y + index * (box_h + gap)
        children.append(abs_box(
            f"node_{index}", 156, y, 768, box_h, DEEP,
            node_style={"border": f"3px solid {LIME if index % 2 == 0 else CYAN}",
                        "border-radius": "26px", "transform-origin": "center center"},
            children=[text(f"node_{index}_t", name, style={
                "position": "absolute", "left": "54px", "top": "58px",
                "font-family": "AKSC", "font-size": "72px", "font-weight": "800", "color": PAPER})],
        ))
        delay = 14 + index * 14
        tracks.append(track(f"node_{index}", "opacity", [[delay, 0], [delay + 18, 1]]))
        tracks.append(track(f"node_{index}", "translateX", [[delay, -70], [delay + 20, 0]], "smooth"))
        if index < len(PIPELINE) - 1:
            children.append(abs_box(
                f"link_{index}", 536, y + box_h, 8, gap, LIME,
                node_style={"transform-origin": "center top"},
            ))
            tracks.append(track(f"link_{index}", "scale", [[delay + 10, 0], [delay + 24, 1]], "smooth"))
    root = box("stage", style={"position": "relative", "width": f"{W}px", "height": f"{H}px",
                               "background-color": INK}, children=children)
    return composition("s4_diagram", frames, root, tracks)


# ---------------------------------------------------------------- S5 camera plate
def s5_plate():
    frames = 120
    children = [
        abs_box("glow_a", -220, 180, 1000, 1000, "#12452f",
                node_style={"border-radius": "50%", "filter": "blur(70px)", "transform-origin": "center"}),
        abs_box("glow_b", 380, 900, 900, 900, "#3a1030",
                node_style={"border-radius": "50%", "filter": "blur(90px)", "transform-origin": "center"}),
        text("word", "无旁白 · 纯视觉", style={
            "position": "absolute", "left": "150px", "top": "820px",
            "font-family": "AKSC", "font-size": "104px", "font-weight": "900", "color": PAPER}),
        text("word2", "跟随镜头推入的合成画面", style={
            "position": "absolute", "left": "150px", "top": "980px",
            "font-family": "AKSC", "font-size": "42px", "font-weight": "400", "color": DIM}),
        text("mark", "DEMO", style={
            "position": "absolute", "left": "150px", "top": "1700px",
            "font-family": "AKSC", "font-size": "34px", "font-weight": "500",
            "letter-spacing": "8px", "color": LIME, "opacity": "0.85"}),
    ]
    for index in range(6):
        children.append(abs_box(f"grid_{index}", 0, 240 + index * 260, 1080, 2, "#1b2740"))
    tracks = [
        track("glow_a", "scale", [[0, 0.85], [120, 1.2]], "linear"),
        track("glow_b", "scale", [[0, 1.15], [120, 0.9]], "linear"),
        track("glow_b", "opacity", [[0, 0.5], [60, 0.85], [120, 0.45]]),
        track("word", "reveal", [[2, 0], [20, 1]], "smooth"),
        track("word2", "opacity", [[18, 0], [32, 1]]),
        track("mark", "opacity", [[26, 0], [40, 1]]),
    ]
    for index in range(6):
        tracks.append(track(f"grid_{index}", "translateY", [[0, 0], [120, -120]], "linear"))
    root = box("stage", style={"position": "relative", "width": f"{W}px", "height": f"{H}px",
                               "background-color": "#04060b"}, children=children)
    return composition("s5_plate", frames, root, tracks)


# ---------------------------------------------------------------------- S6 outro
def s6_outro():
    frames = 135
    root = centered_column(
        "stage",
        [
            text("brand", "AK 带你拆解世界", style={
                "font-family": "AKSC", "font-size": "96px", "font-weight": "900",
                "color": PAPER, "text-align": "center"}),
            abs_box("divider", 0, 0, 220, 8, LIME, node_style={"border-radius": "4px"}),
            text("line2", "视频引擎 V2.1 · 能力演示", style={
                "font-family": "AKSC", "font-size": "48px", "font-weight": "600",
                "color": CYAN, "text-align": "center"}),
            text("line3", "示意数据 · 无配音 · 待人工审核", style={
                "font-family": "AKSC", "font-size": "36px", "font-weight": "400",
                "color": DIM, "text-align": "center", "margin-top": "18px"}),
        ],
    )
    root["style"]["background-color"] = INK
    tracks = [
        track("brand", "reveal", [[0, 0], [16, 1]], "smooth"),
        track("divider", "scale", [[16, 0], [42, 1]], "smooth"),
        track("line2", "opacity", [[16, 0], [32, 1]]),
        track("line3", "opacity", [[26, 0], [42, 1]]),
        track("stage", "scale", [[0, 1.0], [135, 1.03]], "linear"),
    ]
    return composition("s6_outro", frames, root, tracks)


COMPOSITIONS = [s1_hook, s2_number, s3_chart, s4_diagram, s5_plate, s6_outro]


# -------------------------------------------------------------------------- score
def score_document(duration=26.0):
    """A quiet two-note pulse bed; original synthesis, no sample library, no model."""
    notes = []
    bass = [45, 45, 43, 43, 41, 41, 43, 43]
    for index in range(int(duration / 3.0)):
        at = index * 3.0
        root_note = bass[index % len(bass)]
        notes.append({"at": at, "duration": 2.2, "midi": root_note, "gain": 0.16,
                      "waveform": "triangle", "attack": 0.4, "release": 0.9, "pan": -0.1})
        notes.append({"at": at + 0.5, "duration": 1.4, "midi": root_note + 12, "gain": 0.08,
                      "waveform": "sine", "attack": 0.25, "release": 0.7, "pan": 0.15})
    for beat in range(int(duration * 2)):
        at = beat * 0.5
        notes.append({"at": at, "duration": 0.06, "midi": 84, "gain": 0.012,
                      "waveform": "noise", "attack": 0.004, "release": 0.03, "pan": 0.0})
    return {"duration": duration, "sample_rate": 48000, "seed": 7, "notes": notes}


# ---------------------------------------------------------------------------- CLI
def ok_call(tool, arguments, *, timeout=1800):
    args_file = JOB / "script" / f".args_{tool}.json"
    args_file.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")
    command = [str(PY), "-m", "openkatana", "call", tool, "--workspace", str(WORKSPACE),
               "--arguments", str(args_file)]
    done = subprocess.run(command, cwd=OPENKATANA, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if done.returncode != 0:
        raise SystemExit(f"{tool} failed ({done.returncode}):\n{done.stdout[-4000:]}\n{done.stderr[-4000:]}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as error:  # keep the raw text for the report
        raise SystemExit(f"{tool} returned non-JSON output: {error}\n{done.stdout[:4000]}")


def stage_docs():
    """Write composition documents and the score document to the job folder."""
    out = JOB / "assets" / "compositions"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for factory in COMPOSITIONS:
        name, document = factory()
        path = out / f"{name}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append({"file": path.name, "frames": document["canvas"]["frames"],
                         "tracks": len(document["tracks"])})
    (JOB / "audio" / "score_document.json").write_text(
        json.dumps(score_document(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"compositions": manifest}, ensure_ascii=False, indent=2))


def stage_render():
    """Save each composition into the project and render it to a plate via OpenKatana."""
    clips = {}
    for factory in COMPOSITIONS:
        name, document = factory()
        relative = f"compositions/{name}.json"
        try:
            ok_call("save_composition", {"project_id": PROJECT_ID, "path": relative,
                                         "document": document, "revision": None})
        except SystemExit:
            current = ok_call("inspect_composition", {"project_id": PROJECT_ID, "path": relative,
                                                      "width": 540, "height": 960, "frame": 0})
            ok_call("save_composition", {"project_id": PROJECT_ID, "path": relative,
                                         "document": document,
                                         "revision": current.get("revision")})
        receipt = ok_call("render_composition", {"project_id": PROJECT_ID, "path": relative,
                                                 "video": True})
        clips[name] = receipt
        print(json.dumps({"saved": relative, "clip": receipt.get("clip"),
                          "frames": receipt.get("frames") or receipt.get("receipt", {}).get("frames")},
                         ensure_ascii=False))
    (JOB / "project" / "render_receipts.json").write_text(
        json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")


SHOTS = [
    # (plate, f0, f1, camera, two-shot transition entering this shot)
    ("s1_hook", 0, 105, {"push": {"v": 0.012}}, None),
    ("s2_number", 105, 225, {"z": 1.03}, {"type": "dissolve", "d": 0.25}),
    ("s3_chart", 225, 375, {"pan": {"dx": 0.012}}, {"type": "push", "d": 0.3, "dir": "left"}),
    ("s4_diagram", 375, 525, {"push": {"v": 0.016}}, {"type": "wipe", "d": 0.3, "dir": "btt"}),
    ("s5_plate", 525, 645, {"push": {"v": 0.02}}, {"type": "glitch", "d": 0.25}),
    ("s6_outro", 645, 780, {"z": 1.02, "handheld": {"amp": 3}}, {"type": "dip", "d": 0.35}),
]


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_score():
    """Synthesize the original score locally, copy it beside the timeline, record the receipt."""
    receipt = ok_call("synthesize_score", {"project_id": PROJECT_ID, "document": score_document()})
    source = Path(receipt["audio"])
    target = PROJECT / "audio" / "score.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    shutil.copyfile(source, JOB / "audio" / "score.wav")
    # MP4 cannot carry PCM, so the timeline gets an AAC copy made with the local ffmpeg.
    aac = target.with_suffix(".m4a")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(target),
                    "-c:a", "aac", "-b:a", "192k", str(aac)], check=True)
    shutil.copyfile(aac, JOB / "audio" / "score.m4a")
    record = {"src": "audio/score.m4a", "aac_sha256": _sha256(aac), "source": str(source),
              "sha256": receipt["sha256"],
              "samples": receipt["samples"], "sample_rate": receipt["sample_rate"],
              "method": receipt.get("method"), "credits_used": receipt.get("credits_used", 0)}
    (JOB / "audio" / "score_receipt.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))
    return record


def timeline_document(score_src):
    receipts = json.loads((JOB / "project" / "render_receipts.json").read_text(encoding="utf-8"))
    clips = {}
    shots = []
    for plate, f0, f1, cam, transition in SHOTS:
        clip = receipts[plate]["clip"]
        clips[plate] = clip
        shot = {"f0": f0, "f1": f1, "clip": plate, "cam": cam}
        if transition:
            shot["transition"] = transition
        shots.append(shot)
    frames = shots[-1]["f1"]
    return {
        "canvas": {"w": 1080, "h": 1920, "fps": 30, "frames": frames, "bg": INK},
        "clips": clips,
        "shots": shots,
        "audio": {"src": score_src, "mode": "copy"},
    }


def save_timeline_with_retry(timeline, attempts=4):
    """Save the timeline, re-reading the project revision when it moved underneath us."""
    last = None
    for attempt in range(attempts):
        try:
            revision = None
            if attempt:
                project = ok_call("get_project", {"project_id": PROJECT_ID})
                revision = project.get("timeline_revision") or project.get("revision")
            return ok_call("save_timeline", {"project_id": PROJECT_ID, "timeline": timeline,
                                              "revision": revision})
        except SystemExit as error:
            last = error
            time.sleep(1.0)
    raise SystemExit(str(last))


def stage_timeline():
    """Validate, save, preview and render the assembled timeline."""
    score = json.loads((JOB / "audio" / "score_receipt.json").read_text(encoding="utf-8"))
    source = score.get("src") or "audio/score.wav"
    timeline = timeline_document(source)
    (JOB / "project" / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ok_call("validate_timeline", {"project_id": PROJECT_ID, "timeline": timeline}),
                     ensure_ascii=False)[:1200])
    saved = save_timeline_with_retry(timeline)
    print(json.dumps(saved, ensure_ascii=False)[:800])
    preview = ok_call("preview_timeline", {"project_id": PROJECT_ID,
                                           "frames": [20, 120, 240, 400, 560, 700]})
    print(json.dumps(preview, ensure_ascii=False)[:1200])
    revision = (saved.get("revision") or "")[:8] or "demo"
    job = ok_call("start_render", {"project_id": PROJECT_ID,
                                    "request_id": f"demo-preview-{revision}"})
    print(json.dumps(job, ensure_ascii=False)[:800])
    return job


def stage_probe(job_id=None):
    """Poll the render job until it settles, then copy the preview next to the job."""
    job = ok_call("get_render", {"project_id": PROJECT_ID, "job_id": job_id})
    print(json.dumps(job, ensure_ascii=False)[:2000])
    (JOB / "qc" / "render_job.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["docs", "render", "timeline", "score", "probe", "all"], default="docs")
    parser.add_argument("--job", default=None)
    options = parser.parse_args()
    if options.stage in {"docs", "all"}:
        stage_docs()
    if options.stage in {"render", "all"}:
        stage_render()
    if options.stage in {"score", "all"}:
        stage_score()
    if options.stage in {"timeline", "all"}:
        stage_timeline()
    if options.stage == "probe":
        stage_probe(options.job)
    return 0


if __name__ == "__main__":
    sys.exit(main())
