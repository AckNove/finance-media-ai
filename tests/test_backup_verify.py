"""备份验证：先真造一份备份，再验它；坏掉的备份必须被判出来。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.services.backup_service import (
    latest_backup,
    read_verification,
    verify_backup,
)
from backend.scripts import backup_verify as cli


def _make_db(path: Path, *, rows: int = 3) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE reports (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
        INSERT INTO alembic_version (version_num) VALUES ('b6_v6_operations');
        """
    )
    for index in range(rows):
        connection.execute("INSERT INTO topics (title) VALUES (?)", (f"选题 {index}",))
        connection.execute("INSERT INTO reports (title) VALUES (?)", (f"报告 {index}",))
    connection.commit()
    connection.close()
    return path


def _backup(source: Path, target: Path) -> Path:
    """用 SQLite 自己的在线备份 API 造一份真备份，并写一份和 backup_db 同形状的说明。"""

    import hashlib

    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    source_connection.backup(target_connection)
    target_connection.close()
    source_connection.close()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".json").write_text(
        json.dumps({"backup_sha256": digest, "source_sha256": "x"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def test_a_real_backup_passes(tmp_path: Path) -> None:
    source = _make_db(tmp_path / "live.db")
    target = _backup(source, tmp_path / "backups" / "finance_media-20261114-101500-123.db")

    check = verify_backup(target, live_path=source)

    assert check.ok is True
    assert check.problems == []
    assert check.integrity == "ok"
    assert check.table_count == 3
    assert check.revision == "b6_v6_operations"
    assert check.sha256_matches is True
    assert check.foreign_key_violations == 0
    assert check.row_differences == []


def test_a_truncated_file_is_caught(tmp_path: Path) -> None:
    source = _make_db(tmp_path / "live.db")
    target = _backup(source, tmp_path / "backups" / "finance_media-20261114-101500-123.db")
    # 把文件截断：说明里记的 sha256 立刻对不上，integrity 也过不去
    raw = target.read_bytes()
    target.write_bytes(raw[: len(raw) // 2])

    check = verify_backup(target, write_report=False)

    assert check.ok is False
    assert check.sha256_matches is False
    assert any("sha256 对不上" in problem for problem in check.problems)


def test_an_empty_file_is_caught(tmp_path: Path) -> None:
    empty = tmp_path / "backups" / "finance_media-20261114-101500-000.db"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")

    check = verify_backup(empty, write_report=False)

    assert check.ok is False
    assert any("0 字节" in problem for problem in check.problems)


def test_missing_file_is_reported_not_raised(tmp_path: Path) -> None:
    check = verify_backup(tmp_path / "nope.db", write_report=False)
    assert check.ok is False
    assert check.problems == ["备份文件不存在"]


def test_a_backup_missing_a_table_now_in_the_live_db_fails(tmp_path: Path) -> None:
    source = _make_db(tmp_path / "live.db")
    target = _backup(source, tmp_path / "backups" / "finance_media-20261114-101500-123.db")
    # 现在的库后来多了一张表：拿旧备份恢复会丢掉它
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE finding_checks (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    check = verify_backup(target, live_path=source, write_report=False)

    assert check.ok is False
    assert check.missing_tables == ["finding_checks"]
    assert any("会丢表" in problem for problem in check.problems)


def test_row_count_differences_are_reported_but_do_not_fail(tmp_path: Path) -> None:
    source = _make_db(tmp_path / "live.db", rows=3)
    target = _backup(source, tmp_path / "backups" / "finance_media-20261114-101500-123.db")
    connection = sqlite3.connect(source)
    connection.execute("INSERT INTO topics (title) VALUES ('备份之后才写的')")
    connection.commit()
    connection.close()

    check = verify_backup(target, live_path=source, write_report=False)

    assert check.ok is True  # 备份旧于现在是正常的
    assert any(line.startswith("topics:") for line in check.row_differences)
    assert "行数差异" in check.summary()


def test_the_verdict_is_written_next_to_the_backup(tmp_path: Path) -> None:
    source = _make_db(tmp_path / "live.db")
    target = _backup(source, tmp_path / "backups" / "finance_media-20261114-101500-123.db")

    verify_backup(target, live_path=source)
    report = read_verification(target)

    assert report is not None
    assert report["ok"] is True
    assert report["checked_at"]
    assert read_verification(tmp_path / "nope.db") is None


def test_latest_backup_picks_the_newest(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    source = _make_db(tmp_path / "live.db")
    older = _backup(source, folder / "finance_media-20261113-090000-000.db")
    newer = _backup(source, folder / "finance_media-20261114-101500-123.db")
    import os

    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    assert latest_backup(folder) == newer
    assert latest_backup(tmp_path / "nothing") is None


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _make_db(tmp_path / "live.db")
    folder = tmp_path / "backups"
    target = _backup(source, folder / "finance_media-20261114-101500-123.db")

    # 说明文件里没有 source_path 时不要去和仓库真实库比表数（那会报出假问题）
    assert cli._compare_target(target, None) in (None, cli.DEFAULT_DB)
    assert cli.main(["--out", str(folder), "--json", "--no-compare"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    target.write_bytes(b"not a database")
    assert cli.main(["--out", str(folder), "--no-compare"]) == 1
    assert "问题" in capsys.readouterr().out

    empty_folder = tmp_path / "empty"
    empty_folder.mkdir()
    assert cli.main(["--out", str(empty_folder)]) == 2
