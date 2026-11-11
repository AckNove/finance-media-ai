"""Assets manifest: provenance and licence for everything in a render (V2.2 Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.job_paths import ApprovedSources, PathNotAllowedError
from backend.app.media.manifest import (
    ASSET_CLASSES,
    MANIFEST_COLUMNS,
    ManifestError,
    build_manifest,
    read_manifest,
    write_manifest,
)


def _sources(tmp_path: Path, **overrides) -> tuple[ApprovedSources, Path]:
    job = tmp_path / "job"
    approved = job / "input" / "approved_sources"
    approved.mkdir(parents=True)
    payload = approved / "earth.jpg"
    payload.write_bytes(b"not really a jpeg")
    registry = ApprovedSources(job)
    fields = {
        "source_id": "S01",
        "source_name": "Earth",
        "publisher": "NASA SVS",
        "source_url": "https://example.invalid/earth",
        "retrieved_at": "2026-11-11T00:00:00+00:00",
        "license_name": "NASA public-domain media",
        "commercial_use": "allowed",
        "credit": "NASA SVS",
        "verification_status": "verified",
    }
    fields.update(overrides)
    registry.register(payload, **fields)
    return registry, payload


def test_manifest_columns_are_the_documented_fifteen() -> None:
    assert len(MANIFEST_COLUMNS) == 15
    assert MANIFEST_COLUMNS[0] == "asset_id"
    assert MANIFEST_COLUMNS[-1] == "purpose"
    assert ASSET_CLASSES == ("A", "B", "C")


def test_build_manifest_joins_registry_and_usage(tmp_path: Path) -> None:
    registry, _ = _sources(tmp_path)
    rows = build_manifest(registry, {"S01": ["sh02", "sh01", "sh02"]},
                          classes={"S01": "A"}, kinds={"S01": "image"},
                          purposes={"S01": "开场地球"})
    assert len(rows) == 1
    row = rows[0]
    assert row.asset_id == "S01"
    assert row.asset_class == "A"
    assert row.kind == "image"
    assert row.file == "earth.jpg"
    assert row.license == "NASA public-domain media"
    assert row.commercial_use == "allowed"
    assert row.verification_status == "verified"
    assert row.used_in_shots == "sh01, sh02"
    assert row.purpose == "开场地球"
    assert len(row.sha256) == 64


def test_unregistered_source_is_refused(tmp_path: Path) -> None:
    registry, _ = _sources(tmp_path)
    with pytest.raises(ManifestError) as error:
        build_manifest(registry, {"S99": ["sh01"]}, classes={"S99": "A"}, kinds={})
    assert error.value.code == "unregistered"


def test_bad_asset_class_is_refused(tmp_path: Path) -> None:
    registry, _ = _sources(tmp_path)
    with pytest.raises(ManifestError) as error:
        build_manifest(registry, {"S01": ["sh01"]}, classes={"S01": "D"}, kinds={})
    assert error.value.code == "bad_class"


@pytest.mark.parametrize("commercial_use", ["unclear", "unknown", "not_allowed"])
def test_rights_unclear_blocks_a_rendered_asset(tmp_path: Path, commercial_use: str) -> None:
    registry, _ = _sources(tmp_path, commercial_use=commercial_use)
    with pytest.raises(PathNotAllowedError) as error:
        build_manifest(registry, {"S01": ["sh01"]}, classes={"S01": "A"}, kinds={})
    assert error.value.reason == "rights_unclear"


def test_auxiliary_class_can_skip_the_commercial_gate(tmp_path: Path) -> None:
    registry, _ = _sources(tmp_path, commercial_use="unclear")
    rows = build_manifest(registry, {"S01": ["sh01"]}, classes={"S01": "C"}, kinds={},
                          require_commercial=False)
    assert rows[0].commercial_use == "unclear"


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    registry, _ = _sources(tmp_path)
    rows = build_manifest(registry, {"S01": ["sh01"]}, classes={"S01": "A"}, kinds={"S01": "image"})
    target = write_manifest(tmp_path / "out" / "assets_manifest.csv", rows)
    back = read_manifest(target)
    assert back[0]["asset_id"] == "S01"
    assert back[0]["sha256"] == rows[0].sha256
    assert target.read_text(encoding="utf-8").splitlines()[0] == ",".join(MANIFEST_COLUMNS)


def test_read_missing_manifest_reports_missing(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as error:
        read_manifest(tmp_path / "absent.csv")
    assert error.value.code == "missing"
