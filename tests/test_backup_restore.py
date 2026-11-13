"""备份/恢复/诊断三个运维脚本的回归测试。

只用临时目录里的 SQLite 库：不碰 `data/finance_media.db`，也不联网。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.scripts import backup_db, doctor, restore_db


def _make_db(path: Path, *, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
        connection.executemany("INSERT INTO demo (name) VALUES (?)", [(f"r{i}",) for i in range(rows)])
        connection.commit()
    finally:
        connection.close()
    return path


def _rows(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM demo").fetchone()[0])
    finally:
        connection.close()


# --- backup ---------------------------------------------------------------


def test_backup_writes_db_and_manifest(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=5)
    result = backup_db.backup_database(source, tmp_path / "backups", label="unit")
    assert result["ok"] is True
    target = Path(result["backup"])
    assert target.exists() and _rows(target) == 5
    manifest = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == result["source_sha256"]
    assert manifest["backup_sha256"] == result["backup_sha256"]
    assert manifest["table_count"] == result["table_count"] >= 1
    assert manifest["label"] == "unit"


def test_backup_refuses_missing_source(tmp_path):
    with pytest.raises(backup_db.BackupError) as error:
        backup_db.backup_database(tmp_path / "nope.db", tmp_path / "backups")
    assert error.value.reason == "missing_source"


def test_backup_keep_prunes_old_files(tmp_path):
    source = _make_db(tmp_path / "work.db")
    backups = tmp_path / "backups"
    made = [backup_db.backup_database(source, backups, label=f"b{i}")["backup"] for i in range(3)]
    pruned = backup_db.prune_backups(backups, keep=1)
    assert pruned["kept"] == 1
    assert pruned["removed"] == 2
    assert Path(made[-1]).exists()
    assert not Path(made[0]).exists()
    assert not Path(made[0]).with_suffix(".json").exists()


# --- restore --------------------------------------------------------------


def test_restore_replaces_target_and_keeps_pre_restore_copy(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=4)
    result = backup_db.backup_database(source, tmp_path / "backups")
    target = _make_db(tmp_path / "target.db", rows=1)

    restored = restore_db.restore_backup(
        Path(result["backup"]), target_url=f"sqlite:///{target.as_posix()}"
    )
    assert restored["restored_tables"] == restored["backup_tables"]
    assert _rows(target) == 4
    assert Path(restored["pre_restore_copy"]).exists()
    assert _rows(Path(restored["pre_restore_copy"])) == 1


def test_restore_dry_run_does_not_touch_target(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=4)
    result = backup_db.backup_database(source, tmp_path / "backups")
    target = _make_db(tmp_path / "target.db", rows=1)

    dry = restore_db.restore_backup(
        Path(result["backup"]), target_url=f"sqlite:///{target.as_posix()}", dry_run=True
    )
    assert dry["dry_run"] is True
    assert _rows(target) == 1


def test_restore_catches_a_tampered_backup(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=2)
    result = backup_db.backup_database(source, tmp_path / "backups")
    backup = Path(result["backup"])
    backup.write_bytes(backup.read_bytes() + b"tampered")

    with pytest.raises(restore_db.RestoreError) as error:
        restore_db.restore_backup(backup, target_url=f"sqlite:///{(tmp_path / 'target.db').as_posix()}")
    assert error.value.reason == "checksum_mismatch"


def test_restore_refuses_the_protected_database(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=2)
    result = backup_db.backup_database(source, tmp_path / "backups")
    with pytest.raises(restore_db.RestoreError) as error:
        restore_db.restore_backup(
            Path(result["backup"]),
            target_url="sqlite:///C:/Users/25803/finance-media-ai/data/finance_media.db",
        )
    assert error.value.reason == "protected_database"


def test_restore_refuses_in_memory_target(tmp_path):
    source = _make_db(tmp_path / "work.db", rows=2)
    result = backup_db.backup_database(source, tmp_path / "backups")
    with pytest.raises(restore_db.RestoreError) as error:
        restore_db.restore_backup(Path(result["backup"]), target_url="sqlite://")
    assert error.value.reason == "in_memory"


def test_restore_reports_missing_backup(tmp_path):
    with pytest.raises(restore_db.RestoreError) as error:
        restore_db.restore_backup(tmp_path / "ghost.db", target_url="sqlite:///" + (tmp_path / "t.db").as_posix())
    assert error.value.reason == "missing_backup"


def test_protected_suffix_matches_only_the_development_database():
    assert backup_db.is_protected("sqlite:///C:/x/data/finance_media.db") is True
    assert backup_db.is_protected("sqlite:///C:/x/data/finance_media.work.db") is False


# --- doctor ---------------------------------------------------------------


def test_doctor_reports_every_check_and_a_verdict(tmp_path):
    database = _make_db(tmp_path / "doctor.db", rows=1)
    report = doctor.collect_report(database_url=f"sqlite:///{database.as_posix()}", port=65531)
    names = [item["name"] for item in report["checks"]]
    for required in ("python", "database", "config:models", "providers", "disk", "fonts:chinese"):
        assert required in names
    assert set(report["counts"]) == {"ok", "warn", "bad"}
    assert sum(report["counts"].values()) == len(report["checks"])
    assert isinstance(report["ok"], bool)
    # 每一条都要有 detail；warn/bad 的必须给出可执行建议，否则报障没用
    for item in report["checks"]:
        assert item["detail"]
        if item["status"] in {"warn", "bad"}:
            assert item["fix"], f"{item['name']} 需要修复建议"


def test_doctor_database_check_flags_missing_file(tmp_path):
    missing = tmp_path / "not-yet.db"
    report = doctor.collect_report(database_url=f"sqlite:///{missing.as_posix()}", port=65532)
    database = next(item for item in report["checks"] if item["name"] == "database")
    assert database["status"] == "warn"
    assert "还不存在" in database["detail"]
    assert "alembic" in database["fix"]


def test_doctor_text_report_mentions_counts(tmp_path):
    database = _make_db(tmp_path / "doctor2.db", rows=1)
    report = doctor.collect_report(database_url=f"sqlite:///{database.as_posix()}", port=65533)
    text = doctor.render_text(report)
    assert "合计：ok" in text
    assert "环境诊断" in text
