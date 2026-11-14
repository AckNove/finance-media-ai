"""素材审计用例：清单、分镜、时间轴、磁盘四份来源能不能对上。

这些用例都只碰临时目录，不联网、不渲染、不碰真实任务目录。
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.media.manifest import MANIFEST_COLUMNS, MANIFEST_FILENAME
from backend.app.video.asset_audit import audit_job, to_markdown
from backend.scripts.asset_audit import main as cli_main


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file(root: Path, relative: str, text: str = "x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _manifest_row(root: Path, asset_id: str, relative: str, **overrides: str) -> dict[str, str]:
    row = {column: "" for column in MANIFEST_COLUMNS}
    row.update(
        {
            "asset_id": asset_id,
            "asset_class": "B",
            "kind": "composition",
            "file": relative,
            "sha256": _sha256(root / relative),
            "license": "self-generated",
            "commercial_use": "allowed",
            "credit": "本仓库程序生成",
        }
    )
    row.update(overrides)
    return row


def _write_manifest(root: Path, rows: list[dict[str, str]]) -> None:
    path = root / MANIFEST_FILENAME
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in MANIFEST_COLUMNS})


def _write_timeline(root: Path, clip_ids: list[str], *, name: str = "project/timeline.json") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"clips": {clip: {"src": f"{clip}.mp4"} for clip in clip_ids}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_storyboard(root: Path, asset_ids: list[str]) -> None:
    path = root / "storyboard" / "storyboard.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["shot_id", "asset_id"])
        writer.writeheader()
        for index, asset_id in enumerate(asset_ids, start=1):
            writer.writerow({"shot_id": f"sh{index:02d}", "asset_id": asset_id})


def _checks(audit) -> set[str]:
    return {finding.check for finding in audit.findings}


@pytest.fixture()
def clean_job(tmp_path: Path) -> Path:
    """清单、分镜、时间轴、磁盘四份都对得上的一个任务目录。"""

    root = tmp_path / "job_clean"
    _write_file(root, "assets/cards/card_sh02.mp4")
    _write_file(root, "assets/plates/solid_1d2a44.mp4")
    _write_manifest(
        root,
        [
            _manifest_row(root, "CARD_sh02", "assets/cards/card_sh02.mp4"),
            _manifest_row(root, "solid_1d2a44", "assets/plates/solid_1d2a44.mp4", kind="plate"),
        ],
    )
    _write_storyboard(root, ["CARD_sh02", "solid_1d2a44"])
    _write_timeline(root, ["CARD_sh02", "solid_1d2a44"])
    return root


def test_clean_job_passes_with_a_visible_info_line(clean_job: Path) -> None:
    audit = audit_job(clean_job)

    assert audit.ok is True
    assert audit.errors == []
    assert audit.warnings == []
    assert _checks(audit) == {"all_registered"}
    assert audit.manifest_assets == 2
    assert audit.timeline_assets == 2
    assert "素材审计通过" in audit.summary()


def test_asset_used_in_the_film_but_missing_from_the_manifest_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "job_missing_row"
    _write_file(root, "assets/cards/card_sh02.mp4")
    _write_manifest(root, [_manifest_row(root, "CARD_sh02", "assets/cards/card_sh02.mp4")])
    _write_storyboard(root, ["CARD_sh02", "solid_1d2a44"])
    _write_timeline(root, ["CARD_sh02", "solid_1d2a44"])

    audit = audit_job(root)

    assert audit.ok is False
    assert "asset_not_registered" in _checks(audit)
    detail = next(item.detail for item in audit.errors if item.check == "asset_not_registered")
    assert "solid_1d2a44" in detail


def test_manifest_absent_while_the_film_uses_assets_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "job_no_manifest"
    _write_storyboard(root, ["CARD_sh02"])
    _write_timeline(root, ["CARD_sh02"])

    audit = audit_job(root)

    assert audit.ok is False
    assert "manifest_missing" in _checks(audit)
    assert audit.manifest_present is False


def test_manifest_absent_and_nothing_used_is_only_an_info_line(tmp_path: Path) -> None:
    root = tmp_path / "job_empty"
    root.mkdir()

    audit = audit_job(root)

    assert audit.ok is True
    assert "manifest_missing" in _checks(audit)
    assert audit.errors == []
    # 没有任何素材时，清单缺失只是说明；时间轴找不到仍然提醒一句（否则等于假装核对过）
    assert {item.severity for item in audit.findings} <= {"info", "warning"}
    assert "timeline_missing" in _checks(audit)


def test_registered_but_unused_asset_is_a_warning(clean_job: Path) -> None:
    _write_file(clean_job, "assets/cards/card_sh09.mp4")
    rows = [
        _manifest_row(clean_job, "CARD_sh02", "assets/cards/card_sh02.mp4"),
        _manifest_row(clean_job, "solid_1d2a44", "assets/plates/solid_1d2a44.mp4"),
        _manifest_row(clean_job, "CARD_sh09", "assets/cards/card_sh09.mp4"),
    ]
    _write_manifest(clean_job, rows)

    audit = audit_job(clean_job)

    assert audit.ok is True
    assert audit.warnings, "清单多登记了一条，应该给提醒"
    assert any(item.check == "asset_unused" and item.asset_id == "CARD_sh09" for item in audit.warnings)


def test_missing_file_and_changed_file_are_both_errors(clean_job: Path) -> None:
    (clean_job / "assets" / "cards" / "card_sh02.mp4").unlink()
    (clean_job / "assets" / "plates" / "solid_1d2a44.mp4").write_text("被改过了", encoding="utf-8")

    audit = audit_job(clean_job)

    checks = _checks(audit)
    assert "asset_file_missing" in checks
    assert "asset_hash_mismatch" in checks
    detail = next(item.detail for item in audit.errors if item.check == "asset_hash_mismatch")
    assert "登记之后被改过" in detail


def test_license_not_allowed_blocks_the_film(clean_job: Path) -> None:
    rows = [
        _manifest_row(clean_job, "CARD_sh02", "assets/cards/card_sh02.mp4", commercial_use="unknown"),
        _manifest_row(clean_job, "solid_1d2a44", "assets/plates/solid_1d2a44.mp4"),
    ]
    _write_manifest(clean_job, rows)

    audit = audit_job(clean_job)

    assert audit.ok is False
    assert "license_not_allowed" in _checks(audit)
    detail = next(item.detail for item in audit.errors if item.check == "license_not_allowed")
    assert "unknown" in detail


def test_storyboard_referencing_an_unregistered_asset_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "job_storyboard_only"
    _write_file(root, "assets/cards/card_sh02.mp4")
    _write_manifest(root, [_manifest_row(root, "CARD_sh02", "assets/cards/card_sh02.mp4")])
    _write_storyboard(root, ["CARD_sh02", "CARD_sh07"])
    _write_timeline(root, ["CARD_sh02"])

    audit = audit_job(root)

    assert audit.ok is False
    assert any(
        item.check == "storyboard_asset_not_registered" and item.asset_id == "CARD_sh07"
        for item in audit.errors
    )


def test_missing_timeline_is_a_warning_not_a_pass(tmp_path: Path) -> None:
    root = tmp_path / "job_no_timeline"
    _write_file(root, "assets/cards/card_sh02.mp4")
    _write_manifest(root, [_manifest_row(root, "CARD_sh02", "assets/cards/card_sh02.mp4")])
    _write_storyboard(root, ["CARD_sh02"])

    audit = audit_job(root)

    assert "timeline_missing" in _checks(audit)
    assert any(item.severity == "warning" for item in audit.findings)
    assert "核不了成片实际用了什么" in audit.as_dict()["summary"] or audit.warnings


def test_render_receipt_alone_is_enough_to_spot_an_unregistered_asset(tmp_path: Path) -> None:
    root = tmp_path / "job_receipt_only"
    _write_file(root, "assets/cards/card_sh02.mp4")
    _write_manifest(root, [_manifest_row(root, "CARD_sh02", "assets/cards/card_sh02.mp4")])
    _write_storyboard(root, ["CARD_sh02"])
    receipt = root / "qc" / "render_run.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "status": "done",
                "report": {
                    "assets": [
                        {"asset_id": "CARD_sh02"},
                        {"asset_id": "solid_1d2a44", "registered": False},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    audit = audit_job(root)

    assert audit.ok is False
    detail = next(item.detail for item in audit.errors if item.asset_id == "solid_1d2a44")
    assert "登记失败" in detail


def test_markdown_lists_the_findings_by_severity(clean_job: Path) -> None:
    (clean_job / "assets" / "cards" / "card_sh02.mp4").unlink()

    text = to_markdown(audit_job(clean_job))

    assert "# 素材审计" in text
    assert "## 硬问题" in text
    assert "不判断素材好不好看" in text


def test_cli_exit_codes(clean_job: Path, tmp_path: Path) -> None:
    assert cli_main(["--job", clean_job.name, "--root", str(clean_job.parent), "--json"]) == 0
    assert cli_main(["--job", "nope", "--root", str(tmp_path)]) == 2

    (clean_job / "assets" / "cards" / "card_sh02.mp4").unlink()
    assert cli_main(["--job", clean_job.name, "--root", str(clean_job.parent)]) == 1
