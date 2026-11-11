"""Job-scoped source whitelist: what may be read, and what must be refused."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from backend.app.core.job_paths import (
    APPROVED_DIRNAME,
    REGISTER_COLUMNS,
    ApprovedSources,
    PathNotAllowedError,
)


@pytest.fixture()
def job(tmp_path: Path) -> Path:
    root = tmp_path / "jobs" / "20261111_demo"
    (root / "input" / APPROVED_DIRNAME).mkdir(parents=True)
    (root / "research").mkdir()
    return root


@pytest.fixture()
def sources(job: Path) -> ApprovedSources:
    return ApprovedSources(job)


def write(path: Path, text: str = "a,b\n1,2\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_register_inside_approved_directory_records_hash_and_size(job: Path, sources: ApprovedSources) -> None:
    target = write(job / "input" / APPROVED_DIRNAME / "table.csv")
    entry = sources.register("table.csv", source_id="S01", source_name="示例数据",
                             publisher="示例机构", unit="元")
    assert entry.path == target.resolve()
    assert entry.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert entry.byte_size == target.stat().st_size
    assert entry.verification_status == "unverified"


def test_resolve_returns_registered_entry(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / APPROVED_DIRNAME / "table.csv")
    sources.register("table.csv", source_id="S01", source_name="示例数据")
    entry = sources.resolve(job / "input" / APPROVED_DIRNAME / "table.csv")
    assert entry.source_id == "S01"


def test_unregistered_file_is_refused(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / APPROVED_DIRNAME / "secret.csv")
    with pytest.raises(PathNotAllowedError) as error:
        sources.resolve("secret.csv")
    assert error.value.reason == "not_registered"


def test_file_outside_approved_directory_is_refused(job: Path, sources: ApprovedSources) -> None:
    inside_job = write(job / "research" / "notes.csv")
    with pytest.raises(PathNotAllowedError) as error:
        sources.register(inside_job, source_id="S01", source_name="笔记")
    assert error.value.reason == "outside_job"


def test_file_outside_the_job_is_refused(job: Path, sources: ApprovedSources, tmp_path: Path) -> None:
    outside = write(tmp_path / "elsewhere.csv")
    with pytest.raises(PathNotAllowedError) as error:
        sources.register(outside, source_id="S01", source_name="外部文件")
    assert error.value.reason in {"outside_job", "traversal"}


def test_traversal_is_refused(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / "escape.csv")
    with pytest.raises(PathNotAllowedError) as error:
        sources.resolve("../escape.csv")
    assert error.value.reason == "traversal"


def test_symlink_is_refused(job: Path, sources: ApprovedSources) -> None:
    target = write(job / "input" / APPROVED_DIRNAME / "real.csv")
    link = job / "input" / APPROVED_DIRNAME / "link.csv"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - needs developer mode on Windows
        pytest.skip("symlink creation is not permitted on this machine")
    with pytest.raises(PathNotAllowedError) as error:
        sources.register("link.csv", source_id="S01", source_name="链接")
    assert error.value.reason == "symlink"


def test_missing_and_empty_files_are_refused(job: Path, sources: ApprovedSources) -> None:
    with pytest.raises(PathNotAllowedError) as missing:
        sources.register("nope.csv", source_id="S01", source_name="缺失")
    assert missing.value.reason == "missing"
    empty = job / "input" / APPROVED_DIRNAME / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(PathNotAllowedError) as empty_error:
        sources.register("empty.csv", source_id="S02", source_name="空文件")
    assert empty_error.value.reason == "empty_file"


def test_changed_bytes_after_registration_are_refused(job: Path, sources: ApprovedSources) -> None:
    target = write(job / "input" / APPROVED_DIRNAME / "table.csv")
    sources.register("table.csv", source_id="S01", source_name="示例数据")
    target.write_text("a,b\n9,9\n", encoding="utf-8")
    with pytest.raises(PathNotAllowedError) as error:
        sources.resolve("table.csv")
    assert error.value.reason == "not_registered"


def test_unknown_status_is_refused(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / APPROVED_DIRNAME / "table.csv")
    with pytest.raises(PathNotAllowedError) as error:
        sources.register("table.csv", source_id="S01", source_name="数据",
                         verification_status="probably_fine")
    assert error.value.reason == "invalid_status"


def test_register_csv_carries_the_provenance_columns(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / APPROVED_DIRNAME / "table.csv")
    sources.register("table.csv", source_id="S01", source_name="示例数据", publisher="示例机构",
                     source_url="https://example.invalid/data", data_as_of="2026-09-30",
                     unit="元", methodology="月度汇总", verification_status="verified")
    register = sources.write_register()
    with register.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert register.name == "source_register.csv"
    assert tuple(rows[0].keys()) == REGISTER_COLUMNS
    assert rows[0]["source_id"] == "S01"
    assert rows[0]["data_as_of"] == "2026-09-30"
    assert rows[0]["verification_status"] == "verified"
    assert len(rows[0]["original_file_hash"]) == 64
    assert rows[0]["retrieved_at"]


def test_two_jobs_do_not_share_registrations(tmp_path: Path) -> None:
    first = ApprovedSources(tmp_path / "jobs" / "a")
    second = ApprovedSources(tmp_path / "jobs" / "b")
    for job in (first, second):
        (job.job_root / "input" / APPROVED_DIRNAME).mkdir(parents=True, exist_ok=True)
        write(job.job_root / "input" / APPROVED_DIRNAME / "table.csv")
    first.register("table.csv", source_id="S01", source_name="A 的数据")
    with pytest.raises(PathNotAllowedError) as error:
        second.resolve("table.csv")
    assert error.value.reason == "not_registered"


def test_register_can_be_reloaded_in_another_processor_stage(job: Path, sources: ApprovedSources) -> None:
    write(job / "input" / APPROVED_DIRNAME / "table.csv")
    sources.register("table.csv", source_id="S01", source_name="示例数据",
                     license_name="CC0", commercial_use="allowed", credit="示例", 
                     verification_status="verified")
    sources.write_register()

    reloaded = ApprovedSources(job).load_register()
    entry = reloaded.get("S01")
    assert entry.source_name == "示例数据"
    assert entry.license_name == "CC0"
    assert entry.commercial_use == "allowed"
    assert reloaded.resolve("table.csv").sha256 == entry.sha256


def test_reloaded_register_refuses_a_file_that_changed(job: Path, sources: ApprovedSources) -> None:
    target = write(job / "input" / APPROVED_DIRNAME / "table.csv")
    sources.register("table.csv", source_id="S01", source_name="示例数据")
    sources.write_register()
    target.write_text("a,b\n7,7\n", encoding="utf-8")
    with pytest.raises(PathNotAllowedError) as error:
        ApprovedSources(job).load_register()
    assert error.value.reason == "not_registered"


def test_reload_without_a_register_is_refused(job: Path) -> None:
    with pytest.raises(PathNotAllowedError) as error:
        ApprovedSources(job).load_register()
    assert error.value.reason == "not_registered"
