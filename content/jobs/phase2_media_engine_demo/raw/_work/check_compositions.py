"""Static pre-flight for the phase-2 compositions: catch contract violations before rendering."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\25803\finance-media-ai")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "openkatana"))

from openkatana.composition import NAME, STYLES, TRACKS  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "build_phase2", ROOT / "content/jobs/phase2_media_engine_demo/script/build_phase2.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

problems: list[str] = []


def walk(node: dict, where: str) -> None:
    if not isinstance(node, dict):
        problems.append(f"{where}: not an object")
        return
    ident = node.get("id")
    if not isinstance(ident, str) or not NAME.fullmatch(ident):
        problems.append(f"{where}.id: {ident!r} is not a legal node name")
    kind = node.get("type")
    if kind not in ("box", "text", "image", "component"):
        problems.append(f"{where}.type: {kind!r}")
    style = node.get("style") or {}
    for key in style:
        if key not in STYLES:
            problems.append(f"{where}({ident}).style: unsupported {key!r}")
        value = style[key]
        if not isinstance(value, str):
            problems.append(f"{where}({ident}).style.{key}: value must be a string, got {type(value).__name__}")
    for index, child in enumerate(node.get("children") or []):
        walk(child, f"{where}.children[{index}]")
    if kind == "image" and isinstance(node.get("src"), str):
        src = node["src"]
        if src.startswith("/") or ":" in src or "\\" in src:
            problems.append(f"{where}({ident}).src: must be a relative forward-slash path, got {src!r}")


from backend.app.media.storyboard import load_storyboard  # noqa: E402

counts = load_storyboard(ROOT / "content/jobs/phase2_media_engine_demo/storyboard/storyboard.csv").counts()

FACTORIES = {
    "chart_composition": lambda: module.chart_composition(counts),
    "diagram_composition": module.diagram_composition,
    "outro_composition": lambda: module.outro_composition(counts),
}

for factory_name, factory in FACTORIES.items():
    document = factory()
    if set(document) != {"schema", "canvas", "fonts", "root", "tracks"}:
        problems.append(f"{factory_name}: top-level keys {sorted(document)}")
    walk(document["root"], factory_name)
    for index, track in enumerate(document["tracks"]):
        where = f"{factory_name}.tracks[{index}]"
        if track.get("property") not in TRACKS:
            problems.append(f"{where}.property: {track.get('property')!r}")
        if track.get("easing") not in ("linear", "smooth", "step"):
            problems.append(f"{where}.easing: {track.get('easing')!r}")
        frames = document["canvas"]["frames"]
        for frame, _ in track.get("keys") or []:
            if not 0 <= frame <= frames - 1:
                problems.append(f"{where}: key frame {frame} outside 0..{frames - 1}")
    for font in document["fonts"]:
        if set(font) - {"family", "src", "weight", "style"}:
            problems.append(f"{factory_name}.fonts: unsupported {set(font)}")
        if "url" in font:
            problems.append(f"{factory_name}.fonts: compositions use 'src', not 'url'")

if problems:
    print(f"PROBLEMS {len(problems)}")
    for problem in problems:
        print(" -", problem)
    raise SystemExit(1)
print("COMPOSITION CONTRACT: PASS (3 documents)")
