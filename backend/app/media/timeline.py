"""Storyboard → engine timeline (V2.2 Phase 2).

The builder is intentionally dumb: it maps each storyboard row to one shot, uses the
shot's declared camera and transition, and refuses to invent anything. Everything
it emits is checked twice — once here against :data:`SUPPORTED_*` sets, and again
by the engine, whose own contract validation rejects unknown fields. The local
check exists so a typo fails with a sentence that names the column, instead of a
render that silently ignores it.

Frame math: the canvas is ``fps`` × storyboard duration. Shots are contiguous by
construction (the storyboard validates that), so ``f0``/``f1`` never leave a hole.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from backend.app.media.storyboard import Shot, Storyboard

#: Keys we emit on a shot. Anything else is a bug in this module, not a feature.
SUPPORTED_SHOT_KEYS = frozenset(
    {"f0", "f1", "clip", "src", "speed", "loop", "cam", "transition", "in", "out", "bg"}
)
#: A clip is exactly what ``import_media`` hands back — nothing may be added to it.
SUPPORTED_CLIP_KEYS = frozenset({"frames_dir", "pattern", "n", "fps", "w", "h"})
SUPPORTED_AUDIO_KEYS = frozenset({"mode", "tracks", "gain", "loudness", "duck", "limiter"})
SUPPORTED_TRACK_KEYS = frozenset({"src", "start", "at", "duration", "volume", "fade_in", "fade_out", "speed"})
SUPPORTED_TOP_KEYS = frozenset({"canvas", "clips", "shots", "audio", "fonts", "text", "palette", "beat"})


class TimelineError(ValueError):
    """The storyboard cannot be expressed as a timeline the engine accepts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_timeline(
    storyboard: Storyboard,
    clips: Mapping[str, Mapping[str, Any]],
    *,
    audio: Mapping[str, Any] | None = None,
    fonts: Iterable[Mapping[str, Any]] | None = None,
    text: Iterable[Mapping[str, Any]] | None = None,
    bg: str = "#0b1220",
    transition_seconds: float = 0.4,
) -> dict[str, Any]:
    """Turn a validated storyboard into the engine's timeline document.

    ``clips`` maps a storyboard ``asset_id`` to the clip descriptor the engine
    prepared for it (``frames_dir``/``pattern``/``n``/``fps``/``w``/``h``).
    """
    if not storyboard.shots:
        raise TimelineError("empty", "storyboard has no shots")
    frames = storyboard.total_frames
    fps = storyboard.fps

    used: dict[str, dict[str, Any]] = {}
    shots: list[dict[str, Any]] = []
    for shot in storyboard.shots:
        f0, f1 = shot.frame_range(fps)
        entry: dict[str, Any] = {"f0": f0, "f1": f1}
        if shot.asset_type == "solid":
            entry["bg"] = shot.asset_source or bg
        else:
            clip = clips.get(shot.asset_id)
            if clip is None:
                raise TimelineError(
                    "missing_clip",
                    f"{shot.shot_id}: asset {shot.asset_id!r} has no prepared clip",
                )
            _require_keys(clip, SUPPORTED_CLIP_KEYS, f"clip {shot.asset_id}")
            used[shot.asset_id] = dict(clip)
            entry["clip"] = shot.asset_id
            entry["src"] = 0
            entry["speed"] = 1
            if int(clip.get("n") or 0) <= 1:
                # A still plate is one frame: say "loop" so the hold is declared, not clamped.
                entry["loop"] = "loop"
        camera = camera_block(shot)
        if camera:
            entry["cam"] = camera
        transition = transition_block(shot, transition_seconds)
        if transition:
            entry["transition"] = transition
        fade = fade_blocks(shot, fps)
        entry.update(fade)
        _require_keys(entry, SUPPORTED_SHOT_KEYS, f"shot {shot.shot_id}")
        shots.append(entry)

    if shots[0]["f0"] != 0 or shots[-1]["f1"] != frames:
        raise TimelineError(
            "coverage",
            f"shots cover {shots[0]['f0']}–{shots[-1]['f1']} but the canvas has {frames} frames",
        )
    document: dict[str, Any] = {
        "canvas": {"w": storyboard.width, "h": storyboard.height, "fps": fps, "frames": frames, "bg": bg},
        "clips": used,
        "shots": shots,
    }
    if fonts:
        document["fonts"] = [dict(font) for font in fonts]
    if text:
        document["text"] = [dict(item) for item in text]
    if audio:
        _require_keys(audio, SUPPORTED_AUDIO_KEYS, "audio")
        for index, track in enumerate(audio.get("tracks", [])):
            _require_keys(track, SUPPORTED_TRACK_KEYS, f"audio track {index}")
        document["audio"] = dict(audio)
    _require_keys(document, SUPPORTED_TOP_KEYS, "timeline")
    return document


def camera_block(shot: Shot) -> dict[str, Any] | None:
    """Translate the storyboard's camera word into the engine's ``cam`` object.

    Real media rarely has the canvas aspect ratio, so every non-solid shot asks for
    ``fit: cover`` — the frame is filled by cropping, never letterboxed — and
    ``cover: true`` so movement cannot expose an edge.
    """
    motion = shot.camera_motion
    if motion in {"", "static"}:
        return {"fit": "cover", "cover": True} if shot.asset_type != "solid" else None
    if motion == "push":
        block: dict[str, Any] = {"push": {"v": 0.03}}
    elif motion == "pull":
        block = {"push": {"v": -0.03}}
    elif motion == "pan_left":
        block = {"pan": {"dx": 0.05}}
    elif motion == "pan_right":
        block = {"pan": {"dx": -0.05}}
    elif motion == "pan_up":
        block = {"pan": {"dy": 0.05}}
    elif motion == "pan_down":
        block = {"pan": {"dy": -0.05}}
    elif motion == "punch":
        block = {"punch": {"v": 0.06, "d": 0.25}}
    elif motion == "handheld":
        block = {"handheld": {"amp": 5, "hz": 0.6}}
    elif motion == "shake":
        block = {"shake": {"amp": [12, 9], "d": 0.25}}
    elif motion == "roll":
        block = {"roll": {"v": 0.03}}
    else:
        raise TimelineError("unknown_camera", f"{shot.shot_id}: unsupported camera_motion {motion!r}")
    if shot.asset_type != "solid":
        block["fit"] = "cover"
        block["cover"] = True
    return block


#: Transition dir vocabularies differ per type; sending the wrong word is a contract error.
_TRANSITION_DIRS = {
    "push": "left",
    "slide": "up",
    "whip": "left",
    "wipe": "ltr",
}


def transition_block(shot: Shot, seconds: float) -> dict[str, Any] | None:
    """Transition lives on the *incoming* shot, so the first shot never has one."""
    if shot.transition in {"", "none"}:
        return None
    block: dict[str, Any] = {"type": shot.transition, "d": round(seconds, 3)}
    direction = _TRANSITION_DIRS.get(shot.transition)
    if direction:
        block["dir"] = direction
    if shot.transition in {"spin", "zoom", "lens", "whip"}:
        block.pop("dir", None)
    return block


def fade_blocks(shot: Shot, fps: int) -> dict[str, Any]:
    """Short picture fades so cuts do not read as dropped frames."""
    del fps
    blocks: dict[str, Any] = {}
    if shot.asset_type in {"solid", "composition"}:
        blocks["in"] = {"type": "dip", "d": 0.22, "color": "#0b1220"}
    return blocks


def subtitle_events(
    storyboard: Storyboard,
    *,
    family: str,
    size: int,
    color: str = "#f6f8fa",
    stroke: str = "#0b1220",
    pos: tuple[float, float] = (0.5, 0.84),
) -> list[dict[str, Any]]:
    """One caption event per shot that declares a subtitle.

    ``safety_zone`` moves the baseline: ``top`` 0.16, ``center`` 0.5, ``bottom`` 0.84.
    """
    zones = {"top": 0.16, "center": 0.5, "bottom": 0.84}
    events: list[dict[str, Any]] = []
    for shot in storyboard.shots:
        text = shot.subtitle.strip()
        if not text:
            continue
        f0, f1 = shot.frame_range(storyboard.fps)
        x, y = pos
        events.append(
            {
                "type": "caption",
                "family": family,
                "text": text,
                "f0": f0,
                "f1": f1,
                "pos": [x, zones.get(shot.safety_zone, y)],
                "size": size,
                "fill": color,
                "stroke": stroke,
                "lw": 3,
                "shadow": "dark",
            }
        )
    return events


def audio_mix(tracks: Iterable[Mapping[str, Any]], *, gain: float = 1.0) -> dict[str, Any]:
    """Assemble the ``audio`` block; ``duck`` is added by callers that have narration."""
    clean: list[dict[str, Any]] = []
    for index, track in enumerate(tracks):
        _require_keys(track, SUPPORTED_TRACK_KEYS, f"audio track {index}")
        if not track.get("src"):
            raise TimelineError("bad_track", f"audio track {index} has no src")
        clean.append(dict(track))
    if not clean:
        raise TimelineError("empty_audio", "audio_mix needs at least one track")
    return {"mode": "mix", "gain": gain, "tracks": clean}


def _require_keys(payload: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise TimelineError(
            "unsupported_field",
            f"{where}: unsupported field(s) {unknown}; supported: {sorted(allowed)}",
        )


__all__ = [
    "SUPPORTED_AUDIO_KEYS",
    "SUPPORTED_CLIP_KEYS",
    "SUPPORTED_SHOT_KEYS",
    "SUPPORTED_TOP_KEYS",
    "SUPPORTED_TRACK_KEYS",
    "TimelineError",
    "audio_mix",
    "build_timeline",
    "camera_block",
    "subtitle_events",
    "transition_block",
]
