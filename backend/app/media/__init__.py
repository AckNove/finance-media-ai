"""Media production layer (V2.2 Phase 2).

Three modules, three jobs:

``storyboard``  the sheet — what the film says, shot by shot, as data;
``timeline``    the translation — storyboard to the engine's timeline contract;
``manifest``    the receipts — which asset, from where, under which licence.

The engine itself lives in :mod:`backend.app.capabilities.media`; nothing here
imports OpenKatana directly.
"""

from backend.app.media.manifest import (
    ASSET_CLASSES,
    MANIFEST_COLUMNS,
    ManifestError,
    ManifestRow,
    build_manifest,
    read_manifest,
    write_manifest,
)
from backend.app.media.storyboard import (
    ASSET_TYPES,
    CAMERA_MOTIONS,
    REQUIRED_COLUMNS,
    SOUND_EFFECT_NONE,
    TRANSITIONS,
    Shot,
    SoundEffectEvent,
    Storyboard,
    StoryboardError,
    load_storyboard,
    parse_storyboard,
    write_storyboard,
)
from backend.app.media.timeline import (
    SUBTITLE_SOURCES,
    TimelineError,
    audio_consistency,
    audio_mix,
    build_timeline,
    subtitle_events,
)

__all__ = [
    "ASSET_CLASSES",
    "ASSET_TYPES",
    "CAMERA_MOTIONS",
    "MANIFEST_COLUMNS",
    "REQUIRED_COLUMNS",
    "SOUND_EFFECT_NONE",
    "SUBTITLE_SOURCES",
    "TRANSITIONS",
    "ManifestError",
    "ManifestRow",
    "Shot",
    "SoundEffectEvent",
    "Storyboard",
    "StoryboardError",
    "TimelineError",
    "audio_consistency",
    "audio_mix",
    "build_manifest",
    "build_timeline",
    "load_storyboard",
    "parse_storyboard",
    "read_manifest",
    "subtitle_events",
    "write_manifest",
    "write_storyboard",
]
