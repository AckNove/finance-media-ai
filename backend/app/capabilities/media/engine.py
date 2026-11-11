"""Video engine contract (V2.2 Phase 2).

Business code must never import a specific engine. It asks this module for the
engine object, and the engine object hides *how* frames get rendered. That is the
whole point of the layer: today the engine is OpenKatana's CLI; tomorrow it could
be a different toolchain, and the storyboard/render pipeline above it must not
change.

What lives here, deliberately:

* :class:`MediaEngineError` — one error type with a stable ``code`` so callers and
  tests never parse prose;
* :class:`EngineHealth` / :class:`EngineVersion` — the facts a health check reports;
* :class:`ImportResult` — one imported asset, as the engine names it;
* :class:`VideoEngine` — the protocol every engine implements.

What does *not* live here: any timeline, composition or ffmpeg detail. Those are
the engine's business.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

#: Stable error codes. ``usage`` is our own bad call; ``contract`` means the
#: engine refused the payload; ``unavailable`` means the toolchain is missing;
#: ``timeout`` / ``tool_error`` come from the engine process itself.
ERROR_CODES = (
    "usage",
    "unavailable",
    "contract",
    "timeout",
    "tool_error",
    "bad_output",
    "not_ready",
)


class MediaEngineError(RuntimeError):
    """A video-engine call failed.

    ``code`` is one of :data:`ERROR_CODES`. ``detail`` keeps the engine's own
    message verbatim: the caller decides what to do, but nobody truncates the
    reason a render was refused.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        tool: str = "",
        detail: str = "",
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}; use one of {ERROR_CODES}")
        self.code = code
        self.tool = tool
        self.detail = detail
        self.attempts = attempts

    def describe(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "tool": self.tool,
            "message": str(self),
            "detail": self.detail,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class EngineVersion:
    """What the engine says it is, and which ffmpeg it will use."""

    name: str
    version: str
    ffmpeg: str
    ffprobe: str
    tool_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineHealth:
    """Result of a health check; ``problems`` is empty when the engine is usable."""

    engine: str
    ok: bool
    version: EngineVersion | None = None
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "ok": self.ok,
            "version": None
            if self.version is None
            else {
                "name": self.version.name,
                "version": self.version.version,
                "ffmpeg": self.version.ffmpeg,
                "ffprobe": self.version.ffprobe,
                "tool_count": self.version.tool_count,
            },
            "problems": list(self.problems),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ImportResult:
    """One asset the engine imported (an image, a video excerpt or an audio file)."""

    kind: str
    path: Path
    sha256: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration: float | None = None
    has_audio: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VideoEngine(Protocol):
    """The only surface the pipeline above is allowed to depend on."""

    name: str

    def health(self) -> EngineHealth: ...

    def create_project(self, title: str) -> dict[str, Any]: ...

    def get_project(self) -> dict[str, Any]: ...

    def import_media(
        self,
        source: Path,
        *,
        rights: str | None = None,
        start: float | None = None,
        duration: float | None = None,
        max_width: int | None = None,
        audio_only: bool = False,
    ) -> ImportResult: ...

    def validate_timeline(self, timeline: dict[str, Any]) -> dict[str, Any]: ...

    def save_timeline(self, timeline: dict[str, Any], *, revision: str | None = None) -> dict[str, Any]: ...

    def render(self, *, request_id: str, timeout: float | None = None) -> dict[str, Any]: ...


__all__ = [
    "ERROR_CODES",
    "EngineHealth",
    "EngineVersion",
    "ImportResult",
    "MediaEngineError",
    "VideoEngine",
]
