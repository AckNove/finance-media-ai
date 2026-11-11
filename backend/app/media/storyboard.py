"""Storyboard-first video authoring (V2.2 Phase 2).

A storyboard is the contract between whoever decides *what the film says* and the
engine that renders it. This module owns the file format, the validation and
nothing else: it turns ``storyboard.csv`` into :class:`Shot` objects and refuses a
sheet that cannot be rendered.

Why CSV and not Python: a storyboard authored by a future research agent is data,
not code. Keeping it data means the visual content stops leaking into scripts —
the earlier demo hard-coded six plates in Python, which is exactly the coupling
this module removes.

Rules enforced here (each one is a failure we would otherwise ship):

* every required column exists, and an unknown column is an error rather than a
  silent typo (``camera_motionn`` must not be ignored);
* ``shot_id`` is unique; shots are contiguous and start at 0, because a timeline
  with a hole renders a freeze nobody asked for;
* ``asset_type``, ``camera_motion``, ``transition`` and ``safety_zone`` come from
  fixed vocabularies that match what the engine actually supports;
* ``start_time``/``end_time`` are seconds and must match ``fps`` frame math, so a
  shot cannot end mid-frame.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REQUIRED_COLUMNS = (
    "shot_id",
    "start_time",
    "end_time",
    "narrative_purpose",
    "narration",
    "visual_description",
    "asset_id",
    "asset_type",
    "asset_source",
    "license",
    "animation",
    "camera_motion",
    "transition",
    "subtitle",
    "sound_effect",
    "safety_zone",
)

#: Optional columns; when present they must be identical on every row.
OPTIONAL_COLUMNS = ("fps", "canvas_width", "canvas_height", "notes")

#: What a shot's picture comes from. A and B are documented in the V2.1 report:
#: ``real_*`` is licensed real media, ``generated_*`` is authored by us, and a
#: ``composition`` is a rendered OpenKatana composition. ``solid`` exists for
#: deliberate typographic beats, not as a way to dodge asset work.
ASSET_TYPES = (
    "real_video",
    "real_image",
    "generated_image",
    "composition",
    "solid",
)

#: Camera vocabulary — the subset of the engine's ``cam`` block this pipeline drives.
CAMERA_MOTIONS = ("static", "push", "pull", "pan_left", "pan_right", "pan_up", "pan_down",
                  "punch", "handheld", "shake", "roll")

#: Transition vocabulary — mirrors the engine's transition registry.
TRANSITIONS = ("none", "dissolve", "fade", "dip", "flash", "glitch", "push", "slide",
               "wipe", "zoom", "lens", "light_leak", "spin", "whip", "pixel_sort",
               "luma", "liquid", "datamosh")

SAFETY_ZONES = ("top", "center", "bottom")

DEFAULT_FPS = 30

DEFAULT_CANVAS = (1080, 1920)


class StoryboardError(ValueError):
    """The sheet cannot be rendered. ``code`` is stable for tests and callers.

    Codes: ``missing_columns``, ``unknown_columns``, ``empty``, ``duplicate_shot``,
    ``bad_time``, ``non_contiguous``, ``unknown_vocabulary``, ``bad_number``,
    ``inconsistent_column``, ``missing_value``.
    """

    def __init__(self, code: str, message: str, *, shot_id: str = "", column: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.shot_id = shot_id
        self.column = column


@dataclass(frozen=True)
class Shot:
    """One shot of the film, with the provenance its picture needs."""

    shot_id: str
    start_time: float
    end_time: float
    narrative_purpose: str
    narration: str
    visual_description: str
    asset_id: str
    asset_type: str
    asset_source: str
    license: str
    animation: str
    camera_motion: str
    transition: str
    subtitle: str
    sound_effect: str
    safety_zone: str
    notes: str = ""

    @property
    def duration(self) -> float:
        return round(self.end_time - self.start_time, 6)

    def frame_range(self, fps: int = DEFAULT_FPS) -> tuple[int, int]:
        """Inclusive-exclusive frame range, rounded to whole frames."""
        start = int(round(self.start_time * fps))
        end = int(round(self.end_time * fps))
        return start, end

    def as_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "narrative_purpose": self.narrative_purpose,
            "narration": self.narration,
            "visual_description": self.visual_description,
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "asset_source": self.asset_source,
            "license": self.license,
            "animation": self.animation,
            "camera_motion": self.camera_motion,
            "transition": self.transition,
            "subtitle": self.subtitle,
            "sound_effect": self.sound_effect,
            "safety_zone": self.safety_zone,
        }


@dataclass(frozen=True)
class Storyboard:
    """A validated sheet plus the canvas it renders onto."""

    shots: tuple[Shot, ...]
    fps: int = DEFAULT_FPS
    width: int = DEFAULT_CANVAS[0]
    height: int = DEFAULT_CANVAS[1]

    @property
    def duration(self) -> float:
        return round(self.shots[-1].end_time - self.shots[0].start_time, 6) if self.shots else 0.0

    @property
    def total_frames(self) -> int:
        return int(round(self.duration * self.fps))

    def shot(self, shot_id: str) -> Shot:
        for shot in self.shots:
            if shot.shot_id == shot_id:
                return shot
        raise KeyError(shot_id)

    def asset_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for shot in self.shots:
            if shot.asset_id and shot.asset_id not in seen:
                seen.append(shot.asset_id)
        return tuple(seen)

    def counts(self) -> dict[str, int]:
        """Tally the V2.2 acceptance facts straight from the sheet."""
        counts = {
            "shots": len(self.shots),
            "real_video": 0,
            "real_image": 0,
            "composition": 0,
            "generated_image": 0,
            "subtitles": 0,
            "transitions": 0,
            "camera_motions": 0,
            "sound_effects": 0,
        }
        motions: set[str] = set()
        transitions: set[str] = set()
        for shot in self.shots:
            if shot.asset_type in counts:
                counts[shot.asset_type] += 1
            if shot.subtitle.strip():
                counts["subtitles"] += 1
            if shot.transition not in {"", "none"}:
                transitions.add(shot.transition)
            if shot.camera_motion not in {"", "static"}:
                motions.add(shot.camera_motion)
            if shot.sound_effect.strip():
                counts["sound_effects"] += 1
        counts["transitions"] = len(transitions)
        counts["camera_motions"] = len(motions)
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "total_frames": self.total_frames,
            "counts": self.counts(),
            "shots": [shot.as_dict() for shot in self.shots],
        }


def load_storyboard(path: Path | str, *, fps: int | None = None) -> Storyboard:
    """Read and validate ``storyboard.csv``."""
    source = Path(path)
    if not source.exists():
        raise StoryboardError("missing_file", f"storyboard not found: {source}")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = tuple(name.strip() for name in (reader.fieldnames or ()))
        rows = [{key.strip(): (value or "").strip() for key, value in row.items() if key} for row in reader]
    return parse_storyboard(rows, columns=columns, fps=fps)


def parse_storyboard(
    rows: Iterable[dict[str, str]],
    *,
    columns: Iterable[str] | None = None,
    fps: int | None = None,
) -> Storyboard:
    """Validate already-parsed rows. ``columns`` enables the header checks."""
    row_list = list(rows)
    if columns is not None:
        column_list = list(columns)
        missing = [name for name in REQUIRED_COLUMNS if name not in column_list]
        if missing:
            raise StoryboardError(
                "missing_columns", f"storyboard is missing required columns: {', '.join(missing)}")
        unknown = [name for name in column_list if name not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
        if unknown:
            raise StoryboardError(
                "unknown_columns",
                f"storyboard has unknown columns: {', '.join(unknown)}; "
                f"allowed extras are {', '.join(OPTIONAL_COLUMNS)}",
            )
    if not row_list:
        raise StoryboardError("empty", "storyboard has no shots")

    fps_value = _constant_int(row_list, "fps", fps or DEFAULT_FPS)
    width = _constant_int(row_list, "canvas_width", DEFAULT_CANVAS[0])
    height = _constant_int(row_list, "canvas_height", DEFAULT_CANVAS[1])

    shots: list[Shot] = []
    seen_ids: set[str] = set()
    previous_end: float | None = None
    for index, row in enumerate(row_list, start=2):  # row 1 is the header
        shot_id = row.get("shot_id", "")
        if not shot_id:
            raise StoryboardError("missing_value", f"row {index}: shot_id is empty", column="shot_id")
        if shot_id in seen_ids:
            raise StoryboardError(
                "duplicate_shot", f"duplicate shot_id {shot_id!r}", shot_id=shot_id)
        seen_ids.add(shot_id)
        start = _seconds(row, "start_time", shot_id)
        end = _seconds(row, "end_time", shot_id)
        if end <= start:
            raise StoryboardError(
                "bad_time", f"{shot_id}: end_time {end} is not after start_time {start}", shot_id=shot_id)
        if abs(start * fps_value - round(start * fps_value)) > 1e-6 or abs(end * fps_value - round(end * fps_value)) > 1e-6:
            raise StoryboardError(
                "bad_time",
                f"{shot_id}: times must land on whole frames at {fps_value}fps "
                f"(got {start}–{end})",
                shot_id=shot_id,
            )
        if previous_end is not None and abs(start - previous_end) > 1e-6:
            raise StoryboardError(
                "non_contiguous",
                f"{shot_id}: starts at {start} but the previous shot ends at {previous_end}",
                shot_id=shot_id,
            )
        if previous_end is None and abs(start) > 1e-6:
            raise StoryboardError(
                "non_contiguous", f"{shot_id}: the first shot must start at 0 (got {start})", shot_id=shot_id)
        previous_end = end

        asset_type = row.get("asset_type", "")
        if asset_type not in ASSET_TYPES:
            raise StoryboardError(
                "unknown_vocabulary",
                f"{shot_id}: asset_type {asset_type!r} is not one of {ASSET_TYPES}",
                shot_id=shot_id, column="asset_type")
        camera = row.get("camera_motion", "") or "static"
        if camera not in CAMERA_MOTIONS:
            raise StoryboardError(
                "unknown_vocabulary",
                f"{shot_id}: camera_motion {camera!r} is not one of {CAMERA_MOTIONS}",
                shot_id=shot_id, column="camera_motion")
        transition = row.get("transition", "") or "none"
        if transition not in TRANSITIONS:
            raise StoryboardError(
                "unknown_vocabulary",
                f"{shot_id}: transition {transition!r} is not one of {TRANSITIONS}",
                shot_id=shot_id, column="transition")
        safety = row.get("safety_zone", "") or "center"
        if safety not in SAFETY_ZONES:
            raise StoryboardError(
                "unknown_vocabulary",
                f"{shot_id}: safety_zone {safety!r} is not one of {SAFETY_ZONES}",
                shot_id=shot_id, column="safety_zone")
        for column in ("narrative_purpose", "visual_description", "asset_source", "license"):
            if not row.get(column, "").strip():
                raise StoryboardError(
                    "missing_value", f"{shot_id}: {column} is empty", shot_id=shot_id, column=column)

        shots.append(
            Shot(
                shot_id=shot_id,
                start_time=start,
                end_time=end,
                narrative_purpose=row.get("narrative_purpose", ""),
                narration=row.get("narration", ""),
                visual_description=row.get("visual_description", ""),
                asset_id=row.get("asset_id", ""),
                asset_type=asset_type,
                asset_source=row.get("asset_source", ""),
                license=row.get("license", ""),
                animation=row.get("animation", ""),
                camera_motion=camera,
                transition=transition,
                subtitle=row.get("subtitle", ""),
                sound_effect=row.get("sound_effect", ""),
                safety_zone=safety,
                notes=row.get("notes", ""),
            )
        )
    return Storyboard(shots=tuple(shots), fps=fps_value, width=width, height=height)


def write_storyboard(path: Path | str, shots: Iterable[dict[str, Any]], *,
                     fps: int = DEFAULT_FPS, width: int = DEFAULT_CANVAS[0],
                     height: int = DEFAULT_CANVAS[1]) -> Path:
    """Write a storyboard CSV (used by tests and by the demo builder)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for shot in shots:
            row = {key: shot.get(key, "") for key in REQUIRED_COLUMNS}
            row["fps"] = fps
            row["canvas_width"] = width
            row["canvas_height"] = height
            row["notes"] = shot.get("notes", "")
            writer.writerow(row)
    return target


def _seconds(row: dict[str, str], column: str, shot_id: str) -> float:
    raw = row.get(column, "")
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise StoryboardError(
            "bad_number", f"{shot_id}: {column} {raw!r} is not a number", shot_id=shot_id, column=column) from error
    if value < 0:
        raise StoryboardError(
            "bad_number", f"{shot_id}: {column} must be >= 0", shot_id=shot_id, column=column)
    return round(value, 6)


def _constant_int(rows: list[dict[str, str]], column: str, default: int) -> int:
    seen: set[int] = set()
    for row in rows:
        raw = row.get(column, "")
        if raw in ("", None):
            continue
        try:
            seen.add(int(float(raw)))
        except (TypeError, ValueError) as error:
            raise StoryboardError(
                "bad_number", f"{column} {raw!r} is not a number", column=column) from error
    if not seen:
        return default
    if len(seen) > 1:
        raise StoryboardError(
            "inconsistent_column", f"{column} differs between rows: {sorted(seen)}", column=column)
    return seen.pop()


__all__ = [
    "ASSET_TYPES",
    "CAMERA_MOTIONS",
    "DEFAULT_CANVAS",
    "DEFAULT_FPS",
    "OPTIONAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "SAFETY_ZONES",
    "TRANSITIONS",
    "Shot",
    "Storyboard",
    "StoryboardError",
    "load_storyboard",
    "parse_storyboard",
    "write_storyboard",
]
