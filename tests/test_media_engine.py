"""The engine adapter: process environment, error mapping and retry policy (V2.2 Phase 2).

These tests never start the real engine. They pin the contract of the adapter layer
itself — the part that decides *which* ffmpeg the engine sees, how a failure is
classified, and which failures are safe to retry.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.app.capabilities.media.engine import MediaEngineError
from backend.app.capabilities.media.openkatana import (
    PINNED_ENGINE_COMMIT,
    PINNED_ENGINE_VERSION,
    OpenKatanaEngine,
)


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _engine(tmp_path: Path, *, retries: int = 0, log_path: Path | None = None) -> OpenKatanaEngine:
    tools = tmp_path / "openkatana"
    (tools / ".venv" / "Scripts").mkdir(parents=True)
    python = tools / ".venv" / "Scripts" / "python.exe"
    python.write_bytes(b"stub")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ffmpeg_dir = tmp_path / "ffmpeg" / "bin"
    ffmpeg_dir.mkdir(parents=True)
    return OpenKatanaEngine(
        workspace,
        project_id="proj1",
        tools_dir=tools,
        python=python,
        ffmpeg_dir=ffmpeg_dir,
        retries=retries,
        log_path=log_path,
    )


def test_engine_is_pinned_to_a_version_and_commit() -> None:
    assert PINNED_ENGINE_VERSION == "0.1.3"
    assert PINNED_ENGINE_COMMIT == "f3b09107a9828ef770f5bf63116cd418197c15c2"


def test_environment_puts_the_project_ffmpeg_first_and_drops_pythonpath(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    os.environ["PYTHONPATH"] = "somewhere"
    try:
        env = engine.environment()
    finally:
        os.environ.pop("PYTHONPATH", None)
    first = env["PATH"].split(os.pathsep)[0]
    assert Path(first) == engine.ffmpeg_dir.resolve() or Path(first) == engine.ffmpeg_dir
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONPATH" not in env


def test_arguments_are_written_as_utf8_without_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout=json.dumps({"ok": True})))
    engine._call("save_composition", {"title": "中文标题"})
    payload = engine.workspace / ".openkatana_io" / "save_composition.json"
    raw = payload.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["title"] == "中文标题"


def test_contract_errors_are_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path, retries=3)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> FakeCompleted:
        calls.append(command)
        return FakeCompleted(returncode=1, stderr="ContractError: unsupported field")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(MediaEngineError) as error:
        engine._call("save_timeline", {})
    assert error.value.code == "contract"
    assert len(calls) == 1
    assert len(engine.calls) == 1
    assert engine.calls[0].ok is False


def test_tool_errors_are_retried_then_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path, retries=1)
    attempts: list[int] = []

    def fake_run(*_: Any, **__: Any) -> FakeCompleted:
        attempts.append(1)
        return FakeCompleted(returncode=2, stderr="Traceback: boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with pytest.raises(MediaEngineError) as error:
        engine._call("render_composition", {})
    assert error.value.code == "tool_error"
    assert len(attempts) == 2
    assert error.value.attempts == 2


def test_timeouts_are_reported_as_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)

    def fake_run(*_: Any, **__: Any):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(MediaEngineError) as error:
        engine._call("analyze_audio", {}, timeout=1)
    assert error.value.code == "timeout"


def test_non_json_output_is_a_bad_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout="not json"))
    with pytest.raises(MediaEngineError) as error:
        engine._call("get_project", {})
    assert error.value.code == "bad_output"


def test_call_log_is_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "logs" / "engine_calls.jsonl"
    engine = _engine(tmp_path, log_path=log_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(stdout=json.dumps({"ok": 1})))
    engine._call("get_capabilities", {})
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[0])
    assert record["tool"] == "get_capabilities"
    assert record["ok"] is True


def test_health_reports_a_foreign_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    payload = {
        "version": PINNED_ENGINE_VERSION,
        "ffmpeg": r"D:\elsewhere\ffmpeg.exe",
        "ffprobe": r"D:\elsewhere\ffprobe.exe",
        "tools": ["a", "b"],
    }
    monkeypatch.setattr(engine, "_call", lambda *a, **k: payload)
    monkeypatch.setattr(type(engine), "toolchain", property(lambda self: _FakeToolchain()))
    health = engine.health()
    assert health.ok is False
    assert any("expected" in problem for problem in health.problems)
    with pytest.raises(MediaEngineError) as error:
        engine.require_ready()
    assert error.value.code == "not_ready"


def test_health_accepts_the_project_ffmpeg_and_the_pinned_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    payload = {
        "version": PINNED_ENGINE_VERSION,
        "ffmpeg": str(engine.ffmpeg_dir / "ffmpeg.exe"),
        "ffprobe": str(engine.ffmpeg_dir / "ffprobe.exe"),
        "tools": ["a", "b"],
    }
    monkeypatch.setattr(engine, "_call", lambda *a, **k: payload)
    monkeypatch.setattr(type(engine), "toolchain", property(lambda self: _FakeToolchain()))
    health = engine.require_ready()
    assert health.ok is True
    assert health.version is not None and health.version.tool_count == 2
    assert health.warnings == ()


def test_a_different_engine_version_is_a_warning_not_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(tmp_path)
    payload = {
        "version": "0.1.4",
        "ffmpeg": str(engine.ffmpeg_dir / "ffmpeg.exe"),
        "ffprobe": str(engine.ffmpeg_dir / "ffprobe.exe"),
        "tools": [],
    }
    monkeypatch.setattr(engine, "_call", lambda *a, **k: payload)
    monkeypatch.setattr(type(engine), "toolchain", property(lambda self: _FakeToolchain()))
    health = engine.health()
    assert health.ok is True
    assert health.warnings and "0.1.4" in health.warnings[0]


class _FakeToolchain:
    version = (8, 1, 2)
    version_line = "ffmpeg version 8.1.2-full_build-www.gyan.dev"
    satisfies_minimum = True
    available = True

    @property
    def version_text(self) -> str:
        return "8.1.2"
