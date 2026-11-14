"""渲染前预检：能不能开渲，开渲之前就该知道。

这些用例全部只碰临时目录，不连引擎、不开 ffmpeg、不联网。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.media.manifest import MANIFEST_COLUMNS
from backend.app.media.storyboard import write_storyboard
from backend.app.video.preflight import (
    PreflightError,
    preflight_job,
    to_markdown,
)
from backend.app.video.render import RenderError, render_job

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ 造任务


def _row(**overrides):
    row = {
        "shot_id": "sh01",
        "start_time": "0.000000000",
        "end_time": "2.000000000",
        "narrative_purpose": "开场",
        "narration": "",
        "visual_description": "纯色底",
        "asset_id": "",
        "asset_type": "solid",
        "asset_source": "#1d2a44",
        "license": "原创",
        "animation": "fade_in",
        "camera_motion": "push",
        "transition": "none",
        "subtitle": "",
        "sound_effect": "none",
        "safety_zone": "bottom",
        "fps": "30",
        "canvas_width": "1080",
        "canvas_height": "1920",
    }
    row.update(overrides)
    return row


def _card_row(shot_id="sh02", *, asset_id="CARD_sh02", start="2.000000000", end="4.000000000"):
    return _row(
        shot_id=shot_id,
        start_time=start,
        end_time=end,
        narrative_purpose="引用数据点",
        visual_description="数据卡",
        asset_id=asset_id,
        asset_type="composition",
        asset_source=f"assets/compositions/{asset_id.lower()}.json",
        license="原创（程序生成）",
    )


def _write_storyboard(job: Path, rows) -> None:
    path = job / "storyboard" / "storyboard.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_storyboard(path, rows)


def _write_manifest(job: Path, rows) -> None:
    path = job / "assets_manifest.csv"
    lines = [",".join(MANIFEST_COLUMNS)]
    for row in rows:
        lines.append(",".join(str(row.get(name, "")) for name in MANIFEST_COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest_row(asset_id="CARD_sh02", **overrides):
    row = {
        "asset_id": asset_id,
        "asset_class": "B",
        "kind": "composition",
        "file": f"assets/compositions/{asset_id.lower()}.json",
        "license": "self-generated",
        "commercial_use": "allowed",
        "verification_status": "self_generated",
    }
    row.update(overrides)
    return row


def _write_composition(job: Path, asset_id="CARD_sh02", *, canvas=(1080, 1920), payload=None):
    path = job / "assets" / "compositions" / f"{asset_id.lower()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = payload if payload is not None else {
        "schema": 1,
        "canvas": {"w": canvas[0], "h": canvas[1], "fps": 30, "frames": 60},
        "root": {"id": "root", "type": "box", "children": []},
    }
    path.write_text(
        document if isinstance(document, str) else json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _clean_job(tmp_path: Path) -> Path:
    job = tmp_path / "clean_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    _write_composition(job)
    return job


# ------------------------------------------------------------------ 正常路径


def test_a_clean_job_passes_without_findings(tmp_path: Path) -> None:
    job = _clean_job(tmp_path)
    report = preflight_job(job)
    assert report.ok is True
    assert report.findings == ()
    assert report.storyboard_present is True
    assert report.shot_count == 2
    assert report.duration_seconds == pytest.approx(4.0)
    assert report.asset_count == 1
    assert report.facts["fps"] == 30
    assert report.facts["manifest_present"] is True
    assert "预检通过" in report.summary()


def test_the_report_is_json_ready(tmp_path: Path) -> None:
    job = _clean_job(tmp_path)
    payload = preflight_job(job).as_dict()
    assert payload["ok"] is True
    assert payload["job_id"] == "clean_job"
    assert json.dumps(payload, ensure_ascii=False)  # 能序列化，页面直接用
    markdown = to_markdown(preflight_job(job))
    assert "渲染前预检" in markdown
    assert "不判断内容好不好看" in markdown


# ------------------------------------------------------------------ 硬问题


def test_missing_storyboard_is_reported_first(tmp_path: Path) -> None:
    job = tmp_path / "empty_job"
    job.mkdir()
    report = preflight_job(job)
    assert report.ok is False
    assert report.storyboard_present is False
    assert [item.check for item in report.errors] == ["missing_storyboard"]
    assert "分镜文件" in report.summary()
    assert "先让人" in report.errors[0].detail


def test_invalid_storyboard_surfaces_the_validator_code(tmp_path: Path) -> None:
    job = tmp_path / "bad_time_job"
    job.mkdir()
    _write_storyboard(job, [_row(end_time="2.011111111")])  # 不落在整帧上
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["invalid_storyboard"]
    assert "bad_time" in report.errors[0].detail


def test_an_asset_the_manifest_never_registered_is_a_hard_error(tmp_path: Path) -> None:
    job = tmp_path / "unknown_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row(asset_id="CARD_missing")])
    _write_manifest(job, [_manifest_row()])
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["unknown_asset"]
    assert report.errors[0].shot_ids == ("sh02",)
    assert "CARD_missing" in report.errors[0].detail


def test_no_manifest_when_assets_are_needed(tmp_path: Path) -> None:
    job = tmp_path / "no_manifest_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["missing_manifest"]


def test_a_file_that_vanished_is_named_with_its_path(tmp_path: Path) -> None:
    job = tmp_path / "gone_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["missing_asset_file"]
    assert "assets/compositions/card_sh02.json" in report.errors[0].detail


def test_an_empty_file_is_not_a_file(tmp_path: Path) -> None:
    job = tmp_path / "empty_file_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    path = job / "assets" / "compositions" / "card_sh02.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["empty_asset_file"]


def test_a_path_that_escapes_the_job_is_refused(tmp_path: Path) -> None:
    job = tmp_path / "escape_job"
    job.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row(file="../outside.json")])
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["asset_outside_job"]
    assert "之外" in report.errors[0].detail


def test_a_broken_composition_is_caught_before_the_engine_sees_it(tmp_path: Path) -> None:
    job = tmp_path / "bad_json_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    _write_composition(job, payload="{not json")
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["bad_composition"]


def test_a_composition_without_a_canvas_is_caught(tmp_path: Path) -> None:
    job = tmp_path / "no_canvas_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    _write_composition(job, payload={"schema": 1, "root": {}})
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["bad_composition"]
    assert "canvas" in report.errors[0].detail


# ------------------------------------------------------------------ 授权


def test_a_licence_that_is_not_allowed_blocks_the_render(tmp_path: Path) -> None:
    job = tmp_path / "licence_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row(commercial_use="editorial_only")])
    _write_composition(job)
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["license_denied"]
    assert "editorial_only" in report.errors[0].detail


def test_real_material_without_a_licence_is_refused(tmp_path: Path) -> None:
    job = tmp_path / "real_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row(asset_class="A", commercial_use="")])
    _write_composition(job)
    report = preflight_job(job)
    assert [item.check for item in report.errors] == ["license_unclear"]
    assert "A 类" in report.errors[0].detail


def test_self_made_material_without_a_licence_is_only_a_warning(tmp_path: Path) -> None:
    job = tmp_path / "self_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row(asset_class="B", commercial_use="")])
    _write_composition(job)
    report = preflight_job(job)
    assert report.ok is True
    assert [item.check for item in report.warnings] == ["license_unclear"]


# ------------------------------------------------------------------ 提醒


def test_canvas_mismatch_is_a_warning_not_a_block(tmp_path: Path) -> None:
    job = tmp_path / "canvas_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    _write_composition(job, canvas=(720, 1280))
    report = preflight_job(job)
    assert report.ok is True
    assert [item.check for item in report.warnings] == ["composition_canvas_mismatch"]
    assert "720×1280" in report.warnings[0].detail


def test_a_colour_that_is_not_a_colour_is_flagged(tmp_path: Path) -> None:
    job = tmp_path / "colour_job"
    job.mkdir()
    _write_storyboard(job, [_row(asset_source="dark blue")])
    report = preflight_job(job)
    assert report.ok is True
    assert [item.check for item in report.warnings] == ["bad_solid_colour"]


def test_narration_without_any_text_cannot_be_synthesised(tmp_path: Path) -> None:
    job = _clean_job(tmp_path)
    report = preflight_job(job, narration=True)
    assert [item.check for item in report.errors] == ["no_narration_text"]


def test_shots_without_narration_text_are_listed(tmp_path: Path) -> None:
    job = tmp_path / "partial_narration_job"
    job.mkdir()
    rows = [
        _row(narration="这一句有词"),
        _card_row(),
    ]
    _write_storyboard(job, rows)
    _write_manifest(job, [_manifest_row()])
    _write_composition(job)
    report = preflight_job(job, narration=True)
    assert report.ok is True
    assert [item.check for item in report.warnings] == ["missing_narration_text"]
    assert report.warnings[0].shot_ids == ("sh02",)


def test_split_entries_fall_back_to_the_registered_base_asset(tmp_path: Path) -> None:
    job = tmp_path / "split_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row(asset_id="CARD_sh02A")])
    _write_manifest(job, [_manifest_row(asset_id="CARD_sh02")])
    _write_composition(job, asset_id="CARD_sh02")
    report = preflight_job(job)
    assert report.ok is True, report.summary()


def test_an_unknown_job_raises_instead_of_guessing(tmp_path: Path) -> None:
    with pytest.raises(PreflightError) as error:
        preflight_job(tmp_path / "not_here")
    assert error.value.reason == "unknown_job"


# ------------------------------------------------------------------ 接进渲染


def test_render_refuses_before_starting_an_engine(tmp_path: Path) -> None:
    job = tmp_path / "refuse_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])  # 文件没写：预检就该拦下
    with pytest.raises(RenderError) as error:
        render_job(job)
    assert error.value.reason == "missing_asset_file"
    # 没建工程目录、没起引擎，说明是在开渲之前就停了
    assert not (job / "project").exists()


def test_preflight_can_be_switched_off(tmp_path: Path) -> None:
    job = tmp_path / "skip_job"
    job.mkdir()
    _write_storyboard(job, [_row(), _card_row()])
    _write_manifest(job, [_manifest_row()])
    with pytest.raises(RenderError):
        render_job(job, preflight=False)
    # 关掉预检后仍会失败，但它已经走到起引擎那一步了（工程目录被建出来），
    # 这正是预检想省掉的那两三分钟。
    assert (job / "project").is_dir()


# ------------------------------------------------------------------ 命令行


def test_cli_check_exit_codes(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    clean = jobs / "clean_job"
    clean.mkdir(parents=True)
    _write_storyboard(clean, [_row()])
    broken = jobs / "broken_job"
    broken.mkdir(parents=True)
    _write_storyboard(broken, [_row(), _card_row()])  # 有素材但没清单

    ok = subprocess.run(
        [sys.executable, "-m", "backend.scripts.render_job", "--job", "clean_job",
         "--jobs-root", str(jobs), "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["ok"] is True

    failure = subprocess.run(
        [sys.executable, "-m", "backend.scripts.render_job", "--job", "broken_job",
         "--jobs-root", str(jobs), "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert failure.returncode == 1, failure.stderr
    assert json.loads(failure.stdout)["errors"][0]["check"] == "missing_manifest"

    missing = subprocess.run(
        [sys.executable, "-m", "backend.scripts.render_job", "--job", "nope",
         "--jobs-root", str(jobs), "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["error"] == "unknown_job"
