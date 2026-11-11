"""Probe: why is a bar with a scale track invisible in the chart composition?"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\25803\finance-media-ai")
sys.path.insert(0, str(ROOT))

from backend.app.capabilities.media.openkatana import OpenKatanaEngine  # noqa: E402

WORKSPACE = ROOT / "content/jobs/phase2_media_engine_demo/project"
engine = OpenKatanaEngine(WORKSPACE, log_path=ROOT / "content/jobs/phase2_media_engine_demo/raw/_work/probe_calls.jsonl")
engine.project_id = json.loads(
    (ROOT / "content/jobs/phase2_media_engine_demo/raw/_work/project.json").read_text(encoding="utf-8")
)["project_id"]

FONT = "AKSC"


def document(style_extra: dict, track_property: str | None, track_keys: list | None,
             *, style_transform: str | None = "scale(1, 0)") -> dict:
    style = {
        "display": "flex", "flex-direction": "column", "align-items": "center",
        "justify-content": "flex-end", "width": "540px", "height": "960px",
        "padding": "40px", "box-sizing": "border-box", "background-color": "#101826",
    }
    style.update(style_extra)
    bar_style = {
        "width": "200px", "height": "500px", "background-color": "#35e0ff",
        "flex-shrink": "0",
    }
    tracks = []
    if track_property and track_keys:
        if style_transform:
            bar_style["transform"] = style_transform
        tracks.append({"node": "bar", "property": track_property, "keys": track_keys, "easing": "smooth"})
    return {
        "schema": 1,
        "canvas": {"w": 540, "h": 960, "fps": 30, "frames": 60},
        "fonts": [{"family": FONT, "src": "fonts/NotoSansSC-VF.ttf", "weight": "100 900"}],
        "root": {
            "id": "root", "type": "box", "style": style,
            "children": [
                {"id": "bar", "type": "box", "style": bar_style},
                {"id": "cap", "type": "text", "text": "bar", "style": {
                    "font-family": FONT, "font-size": "40px", "color": "#f2f5f7"}},
            ],
        },
        "tracks": tracks,
    }


def render(name: str, doc: dict) -> str:
    path = f"probe/{name}.json"
    engine.save_composition_reloading(path, doc)
    result = engine.render_composition(path)
    clip = result.get("clip") or {}
    return str(clip.get("frames_dir") or result)


cases = {
    "a_plain": document({}, None, None),
    "b_scale_track": document({}, "scale", [[0, 0], [30, 1]]),
    "c_reveal_track": document({}, "reveal", [[0, 0], [30, 1]]),
    "d_scale_no_style_transform": document({}, "scale", [[0, 0], [30, 1]], style_transform=None),
}
for name, doc in cases.items():
    try:
        where = render(name, doc)
        print(f"{name}: {where}")
    except Exception as error:  # noqa: BLE001
        print(f"{name}: FAILED {error}")
