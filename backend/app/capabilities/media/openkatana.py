"""OpenKatana CLI adapter (V2.2 Phase 2).

OpenKatana is vendored at ``tools/openkatana`` (v0.1.3, commit
``f3b09107a9828ef770f5bf63116cd418197c15c2``, Apache-2.0) and run from its own
virtual environment. This module is the only place in the repository that knows
how to talk to it.

Three decisions worth the reader's time:

**PATH, not configuration.** OpenKatana shells out to a bare ``ffmpeg`` / ``ffprobe``
and resolves them through ``shutil.which``. The machine's own ``ffmpeg`` on PATH is
4.2.2, which cannot do ``-fps_mode`` and is therefore unusable for video sources.
The project ships 8.1.2 under ``tools/ffmpeg/bin``. So every call made here runs
with that directory prepended to ``PATH``: the engine follows its own resolution
rules, and we hand it a PATH that starts with the binary we have verified. Nothing
is installed system-wide and nothing on PATH is edited for other processes.

**Loud failures.** A refused contract, a timeout and a missing toolchain are three
different problems and get three different codes. Retries are applied only to
transient I/O and timeouts — never to a contract error, because retrying bad input
just burns time and hides the real reason.

**No silent field dropping.** Timeline and composition payloads are forwarded as
authored. If the engine does not support a field it says so; this layer never
strips a key to make a render go through.

Every call is appended to a JSONL run log so a failed render can be reconstructed
after the fact.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.app.capabilities.media.engine import (
    EngineHealth,
    EngineVersion,
    ImportResult,
    MediaEngineError,
)
from backend.app.capabilities.media.ffmpeg import (
    PROJECT_BIN_DIR,
    MediaToolchain,
    read_version,
    resolve_toolchain,
)
from backend.app.core.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_TOOLS_DIR = PROJECT_ROOT / "tools" / "openkatana"
PINNED_ENGINE_VERSION = "0.1.3"
PINNED_ENGINE_COMMIT = "f3b09107a9828ef770f5bf63116cd418197c15c2"

#: Transient failure codes that are worth one more attempt.
RETRYABLE_CODES = frozenset({"timeout", "tool_error"})

#: Tools whose payload is large enough that the log keeps only its shape.
_LOG_SUMMARY_KEYS = frozenset({"timeline", "document", "notes", "tracks", "shots"})


@dataclass(frozen=True)
class CallRecord:
    """One engine invocation, as written to the run log."""

    tool: str
    ok: bool
    duration_ms: int
    attempt: int
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "tool": self.tool,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "attempt": self.attempt,
            "error": self.error,
        }


def _summarise_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep the log readable: large payloads become a key/count summary."""
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in _LOG_SUMMARY_KEYS and isinstance(value, dict):
            summary[key] = {"keys": sorted(value.keys()), "size": len(value)}
        elif isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = {"list_len": len(value)}
        else:
            summary[key] = {"type": type(value).__name__}
    return summary


class OpenKatanaEngine:
    """Drive the vendored OpenKatana CLI for one project workspace."""

    name = "openkatana"

    def __init__(
        self,
        workspace: Path | str,
        *,
        project_id: str | None = None,
        tools_dir: Path | str | None = None,
        python: Path | str | None = None,
        ffmpeg_dir: Path | str | None = None,
        timeout: float = 240.0,
        retries: int = 1,
        log_path: Path | str | None = None,
    ) -> None:
        self.tools_dir = Path(tools_dir or DEFAULT_TOOLS_DIR).expanduser().absolute()
        self.workspace = Path(workspace).expanduser().absolute()
        self.project_id = project_id
        self.python = Path(python) if python else self._default_python()
        self.ffmpeg_dir = Path(ffmpeg_dir).expanduser().absolute() if ffmpeg_dir else PROJECT_BIN_DIR
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.log_path = Path(log_path).expanduser().absolute() if log_path else None
        self._toolchain: MediaToolchain | None = None
        self.calls: list[CallRecord] = []

    # ------------------------------------------------------------------ set-up
    def _default_python(self) -> Path:
        candidate = self.tools_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if candidate.exists():
            return candidate
        raise MediaEngineError(
            "unavailable",
            f"OpenKatana virtual environment not found at {candidate}; run scripts/setup.py first",
            tool="python",
        )

    @property
    def toolchain(self) -> MediaToolchain:
        if self._toolchain is None:
            self._toolchain = resolve_toolchain(self.ffmpeg_dir)
        return self._toolchain

    def environment(self) -> dict[str, str]:
        """Process environment with the project's ffmpeg first on PATH."""
        env = dict(os.environ)
        binary_dir = str(self.ffmpeg_dir)
        env["PATH"] = binary_dir + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONPATH", None)
        return env

    # ------------------------------------------------------------------- calls
    def _call(self, tool: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if not self.python.exists():
            raise MediaEngineError(
                "unavailable", f"engine python missing: {self.python}", tool=tool)
        payload_dir = self.workspace / ".openkatana_io"
        payload_dir.mkdir(parents=True, exist_ok=True)
        payload = payload_dir / f"{tool}.json"
        payload.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")

        command = [
            str(self.python), "-m", "openkatana", "call", tool,
            "--workspace", str(self.workspace),
            "--arguments", str(payload),
        ]
        last_error: MediaEngineError | None = None
        for attempt in range(1, self.retries + 2):
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(self.tools_dir),
                    env=self.environment(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout or self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                elapsed = int((time.monotonic() - started) * 1000)
                last_error = MediaEngineError(
                    "timeout",
                    f"{tool} timed out after {timeout or self.timeout:.0f}s (attempt {attempt})",
                    tool=tool,
                    detail=str(error),
                    attempts=attempt,
                )
                self._record(CallRecord(tool, False, elapsed, attempt, "timeout"))
                if attempt <= self.retries:
                    continue
                raise last_error
            elapsed = int((time.monotonic() - started) * 1000)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                code = "contract" if "ContractError" in detail else "tool_error"
                last_error = MediaEngineError(
                    code,
                    f"{tool} failed with exit code {completed.returncode}",
                    tool=tool,
                    detail=detail,
                    attempts=attempt,
                )
                self._record(CallRecord(tool, False, elapsed, attempt, detail.splitlines()[0] if detail else code))
                if code in RETRYABLE_CODES and attempt <= self.retries:
                    time.sleep(min(2.0, 0.5 * attempt))
                    continue
                raise last_error
            text = (completed.stdout or "").strip()
            if not text:
                self._record(CallRecord(tool, True, elapsed, attempt))
                return {}
            try:
                result = json.loads(text)
            except json.JSONDecodeError as error:
                self._record(CallRecord(tool, False, elapsed, attempt, "bad_output"))
                raise MediaEngineError(
                    "bad_output",
                    f"{tool} returned non-JSON output",
                    tool=tool,
                    detail=text[:2000],
                    attempts=attempt,
                ) from error
            self._record(CallRecord(tool, True, elapsed, attempt))
            return result if isinstance(result, dict) else {"result": result}
        raise last_error or MediaEngineError("tool_error", f"{tool} failed", tool=tool)

    def _record(self, record: CallRecord) -> None:
        self.calls.append(record)
        logger.debug("openkatana %s ok=%s %sms", record.tool, record.ok, record.duration_ms)
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- health
    def health(self) -> EngineHealth:
        problems: list[str] = []
        warnings: list[str] = []
        version: EngineVersion | None = None
        try:
            toolchain = self.toolchain
        except Exception as error:  # noqa: BLE001 - health never raises
            return EngineHealth(self.name, False, None, (f"ffmpeg unavailable: {error}",))
        if not toolchain.satisfies_minimum:
            problems.append(
                f"ffmpeg {toolchain.version_text} is below the required "
                f"{self.toolchain_minimum()}"
            )
        try:
            payload = self._call("get_capabilities", {}, timeout=120)
        except MediaEngineError as error:
            return EngineHealth(self.name, False, None, (f"get_capabilities failed: {error}",) + tuple(problems))
        ffmpeg = str(payload.get("ffmpeg") or "")
        ffprobe = str(payload.get("ffprobe") or "")
        engine_version = str(payload.get("version") or "")
        tools = payload.get("tools") or []
        version = EngineVersion(
            name=self.name,
            version=engine_version,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            tool_count=len(tools) if isinstance(tools, list) else 0,
        )
        expected = str(self.ffmpeg_dir)
        if not ffmpeg or Path(ffmpeg).parent != Path(expected):
            problems.append(
                f"engine resolved ffmpeg at {ffmpeg or '<none>'}, expected {expected}; "
                "video import needs the project's build on PATH"
            )
        if engine_version != PINNED_ENGINE_VERSION:
            warnings.append(
                f"engine version {engine_version or '<unknown>'} differs from the pinned "
                f"{PINNED_ENGINE_VERSION}"
            )
        if not self.workspace.exists():
            problems.append(f"workspace missing: {self.workspace}")
        return EngineHealth(self.name, not problems, version, tuple(problems), tuple(warnings))

    def toolchain_minimum(self) -> str:
        from backend.app.capabilities.media.ffmpeg import MIN_FFMPEG_VERSION_TEXT

        return MIN_FFMPEG_VERSION_TEXT

    def require_ready(self) -> EngineHealth:
        health = self.health()
        if not health.ok:
            raise MediaEngineError(
                "not_ready",
                "video engine is not ready: " + "; ".join(health.problems),
                tool="health",
                detail=json.dumps(health.describe(), ensure_ascii=False),
            )
        return health

    def version(self) -> EngineVersion:
        health = self.require_ready()
        assert health.version is not None
        return health.version

    def ffmpeg_version(self) -> str:
        text, _ = read_version(self.ffmpeg_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
        return text or "unknown"

    # --------------------------------------------------------------- project
    def create_project(self, title: str, *, project_id: str | None = None) -> dict[str, Any]:
        """Create a project inside the workspace. The name is what the engine calls ``name``."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        arguments: dict[str, Any] = {"name": title}
        if project_id:
            arguments["project_id"] = project_id
        result = self._call("create_project", arguments)
        self.project_id = str(result.get("project_id") or result.get("id") or "")
        return result

    def get_project(self) -> dict[str, Any]:
        return self._call("get_project", {"project_id": self._project()})

    def _project(self) -> str:
        if not self.project_id:
            raise MediaEngineError(
                "usage", "no project selected; call create_project or pass project_id", tool="project")
        return self.project_id

    # ----------------------------------------------------------------- media
    def inspect_media(self, source: Path) -> dict[str, Any]:
        return self._call("inspect_media", {"source": str(source)})

    def import_media(
        self,
        source: Path,
        *,
        rights: str | None = None,
        start: float | None = None,
        duration: float | None = None,
        max_width: int | None = None,
        audio_only: bool = False,
    ) -> ImportResult:
        arguments: dict[str, Any] = {"project_id": self._project(), "source": str(source)}
        if rights:
            arguments["rights"] = rights
        if start is not None:
            arguments["start"] = float(start)
        if duration is not None:
            arguments["duration"] = float(duration)
        if max_width is not None:
            arguments["max_width"] = int(max_width)
        if audio_only:
            arguments["audio_only"] = True
        payload = self._call("import_media", arguments, timeout=max(self.timeout, 600))
        return _import_result(payload, source)

    # -------------------------------------------------------------- timeline
    def validate_timeline(self, timeline: dict[str, Any]) -> dict[str, Any]:
        return self._call("validate_timeline", {"project_id": self._project(), "timeline": timeline})

    def save_timeline(self, timeline: dict[str, Any], *, revision: str | None = None) -> dict[str, Any]:
        return self._call(
            "save_timeline",
            {"project_id": self._project(), "timeline": timeline, "revision": revision},
        )

    def save_timeline_with_retry(self, timeline: dict[str, Any], *, attempts: int = 4) -> dict[str, Any]:
        """Save, reloading the project revision when the engine reports a stale view."""
        revision: str | None = None
        last: MediaEngineError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.save_timeline(timeline, revision=revision)
            except MediaEngineError as error:
                last = error
                if "Project changed" not in error.detail and "reload" not in error.detail.lower():
                    raise
                project = self.get_project()
                revision = str(
                    project.get("timeline_revision") or project.get("revision") or ""
                ) or None
                logger.info("timeline revision stale, retrying with %s", revision)
                time.sleep(min(2.0, 0.5 * attempt))
        raise last or MediaEngineError("tool_error", "save_timeline failed", tool="save_timeline")

    def preview_timeline(self, *, frames: int = 3) -> dict[str, Any]:
        return self._call("preview_timeline", {"project_id": self._project(), "frames": frames})

    # -------------------------------------------------------------- rendering
    def start_render(self, *, request_id: str) -> dict[str, Any]:
        return self._call(
            "start_render", {"project_id": self._project(), "request_id": request_id})

    def get_render(self, job_id: str) -> dict[str, Any]:
        return self._call("get_render", {"project_id": self._project(), "job_id": job_id})

    def render(self, *, request_id: str, timeout: float = 1800.0, poll: float = 2.0) -> dict[str, Any]:
        """Start a render and poll until it settles."""
        started = self.start_render(request_id=request_id)
        job_id = str(started.get("job_id") or started.get("id") or "")
        if not job_id:
            raise MediaEngineError("bad_output", "start_render returned no job id", tool="start_render",
                                   detail=json.dumps(started, ensure_ascii=False)[:1000])
        deadline = time.monotonic() + timeout
        intervals = (poll, max(poll, 5.0), max(poll, 10.0))
        interval_index = 0
        while True:
            state = self.get_render(job_id)
            status = str(state.get("status") or state.get("state") or "").lower()
            if status in {"done", "complete", "completed", "succeeded", "ok", "failed", "error", "cancelled"}:
                return {"job_id": job_id, **state}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MediaEngineError(
                    "timeout", f"render {job_id} did not finish within {timeout:.0f}s",
                    tool="get_render", detail=json.dumps(state, ensure_ascii=False)[:1000])
            time.sleep(min(intervals[interval_index], remaining))
            interval_index = min(interval_index + 1, len(intervals) - 1)

    # ----------------------------------------------------------- compositions
    def save_composition(self, path: str, document: dict[str, Any], *, revision: str | None = None) -> dict[str, Any]:
        return self._call(
            "save_composition",
            {"project_id": self._project(), "path": path, "document": document, "revision": revision},
        )

    def inspect_composition(self, path: str) -> dict[str, Any]:
        return self._call("inspect_composition", {"project_id": self._project(), "path": path})

    def render_composition(self, path: str, *, width: int | None = None, height: int | None = None,
                           video: bool = True) -> dict[str, Any]:
        arguments: dict[str, Any] = {"project_id": self._project(), "path": path}
        if width:
            arguments["width"] = int(width)
        if height:
            arguments["height"] = int(height)
        if video:
            arguments["video"] = True
        return self._call("render_composition", arguments, timeout=max(self.timeout, 900))

    def save_composition_reloading(self, path: str, document: dict[str, Any]) -> dict[str, Any]:
        """Create the composition, or update it with the revision the engine holds."""
        try:
            return self.save_composition(path, document, revision=None)
        except MediaEngineError as error:
            if error.code != "contract" or "reload" not in error.detail.lower():
                raise
            current = self.inspect_composition(path)
            revision = str(current.get("revision") or "")
            if not revision:
                raise
            return self.save_composition(path, document, revision=revision)

    # ----------------------------------------------------------------- audio
    def synthesize_score(self, notes: Sequence[dict[str, Any]], *, duration: float,
                         sample_rate: int = 48000, seed: int = 1) -> dict[str, Any]:
        """Local oscillator/noise score. The engine wants one ``document`` argument."""
        document = {
            "duration": float(duration),
            "sample_rate": int(sample_rate),
            "seed": int(seed),
            "notes": list(notes),
        }
        return self._call("synthesize_score", {"project_id": self._project(), "document": document})

    def analyze_audio(self, source: Path) -> dict[str, Any]:
        return self._call("analyze_audio", {"source": str(source)}, timeout=max(self.timeout, 300))

    def make_captions(self, words: Sequence[dict[str, Any]], **options: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {"project_id": self._project(), "words": list(words)}
        arguments.update(options)
        return self._call("make_captions", arguments)


def _import_result(payload: dict[str, Any], source: Path) -> ImportResult:
    asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else payload
    clip = payload.get("clip") if isinstance(payload.get("clip"), dict) else {}
    path = asset.get("path") or asset.get("file") or clip.get("path") or str(source)
    kind = str(asset.get("kind") or asset.get("type") or "").lower()
    if not kind:
        suffix = Path(str(path)).suffix.lower()
        kind = {
            ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
            ".mp4": "video", ".mov": "video", ".webm": "video",
            ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".aac": "audio",
        }.get(suffix, "unknown")
    duration = asset.get("duration", clip.get("duration"))
    fps = asset.get("fps", clip.get("fps"))
    has_audio = bool(asset.get("has_audio", clip.get("audio", False)))
    return ImportResult(
        kind=kind,
        path=Path(str(path)),
        sha256=str(asset.get("sha256") or ""),
        width=_as_int(asset.get("width") or clip.get("w")),
        height=_as_int(asset.get("height") or clip.get("h")),
        fps=float(fps) if isinstance(fps, (int, float)) else None,
        duration=float(duration) if isinstance(duration, (int, float)) else None,
        has_audio=has_audio,
        raw=payload,
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


__all__ = [
    "DEFAULT_TOOLS_DIR",
    "PINNED_ENGINE_COMMIT",
    "PINNED_ENGINE_VERSION",
    "CallRecord",
    "OpenKatanaEngine",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke helper
    logging.basicConfig(level=logging.INFO)
    engine = OpenKatanaEngine(sys.argv[1] if len(sys.argv) > 1 else PROJECT_ROOT / "content" / "jobs")
    print(json.dumps(engine.health().describe(), ensure_ascii=False, indent=2))
