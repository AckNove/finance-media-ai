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

from backend.app.media.storyboard import Shot, SoundEffectEvent, Storyboard

#: Keys we emit on a shot. Anything else is a bug in this module, not a feature.
SUPPORTED_SHOT_KEYS = frozenset(
    {"f0", "f1", "clip", "src", "speed", "loop", "cam", "transition", "in", "out", "bg"}
)
#: A clip is exactly what ``import_media`` hands back — nothing may be added to it.
SUPPORTED_CLIP_KEYS = frozenset({"frames_dir", "pattern", "n", "fps", "w", "h"})
SUPPORTED_AUDIO_KEYS = frozenset({"mode", "tracks", "gain", "loudness", "duck", "limiter"})
SUPPORTED_TRACK_KEYS = frozenset({"src", "start", "at", "duration", "volume", "fade_in", "fade_out", "speed"})
SUPPORTED_TOP_KEYS = frozenset({"canvas", "clips", "shots", "audio", "fonts", "text", "palette", "beat"})

#: Which storyboard column may become an on-screen caption.
#:
#: ``subtitle`` is the default and the only source this pipeline uses: the
#: storyboard's ``narration`` column is a *spoken script* for a voice that does not
#: exist yet, so turning it into captions would silently invent a second, unreviewed
#: text track. Generating captions from narration is therefore an explicit request
#: (``source="narration"``), never a fallback.
SUBTITLE_SOURCES = ("subtitle", "narration", "none")


class TimelineError(ValueError):
    """The storyboard cannot be expressed as a timeline the engine accepts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def solid_clip_id(colour: str) -> str:
    """纯色底镜头在 ``clips`` 里的键，如 ``solid_0b1220``。

    引擎的 clip id 只允许字母、数字、下划线和连字符（1..64 位），所以颜色里的
    ``#`` 必须去掉。
    """
    safe = (colour or "").strip().lstrip("#").lower() or "0b1220"
    return f"solid_{safe}"


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
            colour = shot.asset_source or bg
            entry["bg"] = colour
            # 引擎要求每个镜头都指向一个真实 clip；纯色底镜头由渲染层生成一张
            # 同色单帧图并登记成 ``solid:<color>``。没有这张图时退回只写 bg
            # （旧行为，供不渲染的纯时间轴检查使用）。
            plate_key = solid_clip_id(colour)
            plate = clips.get(plate_key)
            if plate is not None:
                _require_keys(plate, SUPPORTED_CLIP_KEYS, f"clip {plate_key}")
                used[plate_key] = dict(plate)
                entry["clip"] = plate_key
                entry["src"] = 0
                entry["speed"] = 1
                entry["loop"] = "loop"
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
    source: str = "subtitle",
) -> list[dict[str, Any]]:
    """One caption event per shot, read from ``source`` (``subtitle`` by default).

    ``safety_zone`` moves the baseline: ``top`` 0.16, ``center`` 0.5, ``bottom`` 0.84.

    ``source="none"`` draws nothing. ``source="narration"`` is opt-in and exists
    only for a future build that has a reviewed voice-over track; it is never
    chosen implicitly, and this pipeline does not use it.
    """
    if source not in SUBTITLE_SOURCES:
        raise TimelineError(
            "unsupported_source",
            f"subtitle source {source!r} is not one of {SUBTITLE_SOURCES}",
        )
    if source == "none":
        return []
    zones = {"top": 0.16, "center": 0.5, "bottom": 0.84}
    events: list[dict[str, Any]] = []
    for shot in storyboard.shots:
        text = (shot.narration if source == "narration" else shot.subtitle).strip()
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


def audio_consistency(
    *,
    events: Iterable[SoundEffectEvent],
    generated: Mapping[str, str],
    music_srcs: Iterable[str],
    audio: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove that a sound-effect count means one thing everywhere.

    Four numbers are easy to confuse, so they are reported separately and then
    checked against each other:

    * ``events`` — cues the storyboard asks for;
    * ``distinct_effects`` — different effect names among those cues;
    * ``generated_files`` — audio files the builder actually produced;
    * ``music_tracks`` — beds that run for the whole film;
    * ``effect_tracks`` + ``total_tracks`` — what the timeline really mixes.

    ``generated`` maps an effect name to the timeline-relative ``src`` written for
    it. Every cue must have a generated file, every generated file must have a cue,
    and every cue must appear exactly once at the right second. ``ok`` is false and
    ``problems`` is non-empty whenever those statements do not hold — the point is
    that a report cannot claim a count the timeline does not support.
    """
    event_list = list(events)
    music = list(music_srcs)
    tracks = list(audio.get("tracks", []))
    by_src: dict[str, list[Mapping[str, Any]]] = {}
    for track in tracks:
        by_src.setdefault(str(track.get("src", "")), []).append(track)

    problems: list[dict[str, str]] = []

    def problem(code: str, detail: str) -> None:
        problems.append({"code": code, "detail": detail})

    used_names: set[str] = set()
    effect_srcs: set[str] = set()
    for event in event_list:
        src = generated.get(event.name)
        if not src:
            problem("missing_generated", f"{event.shot_id}: no audio was generated for {event.name!r}")
            continue
        used_names.add(event.name)
        effect_srcs.add(src)
        matches = by_src.get(src, [])
        if not matches:
            problem("missing_track", f"{event.shot_id}: {src} is not in the timeline")
            continue
        if len(matches) > 1:
            problem("duplicate_track", f"{event.shot_id}: {src} appears {len(matches)} times")
            continue
        try:
            at = float(matches[0].get("at", 0.0))
        except (TypeError, ValueError):
            at = -1.0
        if abs(at - event.at) > 1e-6:
            problem("wrong_time", f"{event.shot_id}: {event.name} sits at {at}s, cue says {event.at}s")

    for name, src in generated.items():
        if name not in used_names:
            problem("unused_generated", f"{name} was generated but no shot asks for it ({src})")

    for src in music:
        if src not in by_src:
            problem("missing_music", f"music bed {src} is not in the timeline")

    for src in by_src:
        if src in music or src in effect_srcs:
            continue
        problem("unexpected_track", f"{src} is in the timeline but neither music nor a cue")

    if len(tracks) != len(music) + len(event_list):
        problem(
            "count_mismatch",
            f"timeline mixes {len(tracks)} tracks, expected {len(music)} music + "
            f"{len(event_list)} effects",
        )

    return {
        "events": len(event_list),
        "distinct_effects": len(used_names),
        "generated_files": len(generated),
        "music_tracks": len(music),
        "effect_tracks": sum(len(by_src.get(src, [])) for src in effect_srcs),
        "total_tracks": len(tracks),
        "event_detail": [event.as_dict() for event in event_list],
        "generated": dict(generated),
        "music_srcs": music,
        "ok": not problems,
        "problems": problems,
    }


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
    "SUBTITLE_SOURCES",
    "SUPPORTED_AUDIO_KEYS",
    "SUPPORTED_CLIP_KEYS",
    "SUPPORTED_SHOT_KEYS",
    "SUPPORTED_TOP_KEYS",
    "SUPPORTED_TRACK_KEYS",
    "TimelineError",
    "audio_consistency",
    "audio_mix",
    "build_timeline",
    "camera_block",
    "solid_clip_id",
    "subtitle_events",
    "transition_block",
]
