"""素材审计的接口侧用例：素材页要能看到"清单说的和片子里有的对不对得上"。

用临时目录造任务，不碰仓库里真实的 content/jobs。
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.media.manifest import MANIFEST_COLUMNS, MANIFEST_FILENAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_job(root: Path, *, with_plate_row: bool) -> Path:
    job = root / "audit_job"
    (job / "assets" / "cards").mkdir(parents=True)
    (job / "assets" / "plates").mkdir(parents=True)
    (job / "project").mkdir(parents=True)
    (job / "storyboard").mkdir(parents=True)

    card = job / "assets" / "cards" / "card_sh02.mp4"
    card.write_bytes(b"card")
    plate = job / "assets" / "plates" / "solid_1d2a44.mp4"
    plate.write_bytes(b"plate")

    rows = [
        {
            "asset_id": "CARD_sh02",
            "asset_class": "B",
            "kind": "composition",
            "file": "assets/cards/card_sh02.mp4",
            "sha256": _sha256(card),
            "license": "self-generated",
            "commercial_use": "allowed",
            "credit": "本仓库程序生成",
        }
    ]
    if with_plate_row:
        rows.append(
            {
                "asset_id": "solid_1d2a44",
                "asset_class": "B",
                "kind": "plate",
                "file": "assets/plates/solid_1d2a44.mp4",
                "sha256": _sha256(plate),
                "license": "self-generated",
                "commercial_use": "allowed",
                "credit": "本仓库程序生成的纯色底板",
            }
        )
    with (job / MANIFEST_FILENAME).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(MANIFEST_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in MANIFEST_COLUMNS})

    with (job / "storyboard" / "storyboard.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["shot_id", "asset_id"])
        writer.writeheader()
        writer.writerow({"shot_id": "sh01", "asset_id": "solid_1d2a44"})
        writer.writerow({"shot_id": "sh02", "asset_id": "CARD_sh02"})

    (job / "project" / "timeline.json").write_text(
        json.dumps({"clips": {"solid_1d2a44": {}, "CARD_sh02": {}}}), encoding="utf-8"
    )
    return job


@pytest.fixture()
def jobs_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from backend.app.api.routes import workspace as workspace_routes

    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(workspace_routes, "JOBS_ROOT", root)
    return root


def test_job_detail_carries_the_asset_audit(client: Any, jobs_root: Path) -> None:
    _write_job(jobs_root, with_plate_row=False)

    body = client.get("/api/workspace/jobs/audit_job").json()

    audit = body["asset_audit"]
    assert audit["ok"] is False
    assert audit["errors"] == 1
    finding = next(
        item for item in audit["findings"] if item["check"] == "asset_not_registered"
    )
    assert finding["asset_id"] == "solid_1d2a44"
    assert "素材清单里没有这一条" in finding["detail"]


def test_audit_passes_once_the_plate_is_registered(client: Any, jobs_root: Path) -> None:
    _write_job(jobs_root, with_plate_row=True)

    body = client.get("/api/workspace/jobs/audit_job").json()

    audit = body["asset_audit"]
    assert audit["ok"] is True
    assert audit["errors"] == 0
    assert audit["manifest_assets"] == 2
    assert audit["timeline_assets"] == 2
    assert "素材审计通过" in audit["summary"]


def test_audit_is_computed_now_not_read_from_a_stale_file(
    client: Any, jobs_root: Path
) -> None:
    job = _write_job(jobs_root, with_plate_row=True)
    # 事后有人换掉了素材文件：现算的审计必须查得出来，读旧快照就查不出来
    (job / "assets" / "cards" / "card_sh02.mp4").write_bytes("换过了".encode("utf-8"))

    audit = client.get("/api/workspace/jobs/audit_job").json()["asset_audit"]

    assert audit["ok"] is False
    assert any(item["check"] == "asset_hash_mismatch" for item in audit["findings"])
