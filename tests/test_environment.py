"""环境体检（doctor 与 /api/system/environment 同一份实现）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.services import environment as env


def _make_db(path: Path, *, tables: int = 2) -> Path:
    connection = sqlite3.connect(path)
    try:
        for index in range(tables):
            connection.execute(f"CREATE TABLE t{index} (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        connection.execute("INSERT INTO alembic_version VALUES ('b8_calendar_tags')")
        connection.commit()
    finally:
        connection.close()
    return path


def test_report_shape_and_note(tmp_path: Path) -> None:
    report = env.collect_environment(database_url="", port=None, root=tmp_path)
    assert set(report) == {"ok", "counts", "database_url", "project_root", "checks", "note"}
    assert report["project_root"] == str(tmp_path)
    # 「只读」这句话必须在，页面也照抄它——它是这份体检的边界
    assert "不发外部请求" in report["note"]
    names = [item["name"] for item in report["checks"]]
    # 这些是任何机器上都该有的检查项
    assert "python" in names
    assert "disk" in names
    assert "tools" in names
    assert any(name.startswith("dep:") for name in names)
    # counts 必须与实际条目一致，否则页面上的「N 项有问题」会对不上
    counted = {"ok": 0, "warn": 0, "bad": 0}
    for item in report["checks"]:
        counted[item["status"]] += 1
    assert report["counts"] == counted
    assert report["ok"] == (counted["bad"] == 0)


def test_port_check_is_skipped_when_no_port_is_given(tmp_path: Path) -> None:
    # 接口服务自己就占着端口，查出来必然「已占用」，所以接口一律不查
    report = env.collect_environment(port=None, root=tmp_path)
    assert not any(item["name"].startswith("port:") for item in report["checks"])


def test_port_check_reports_busy_and_free(tmp_path: Path) -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        busy = env._port_check("127.0.0.1", busy_port)
    assert busy["status"] == env.WARN
    assert "已被占用" in busy["detail"]
    assert "start.ps1" in busy["fix"]

    free = env._port_check("127.0.0.1", 1)
    assert free["status"] == env.OK


def test_project_ffmpeg_is_reported_missing_under_an_empty_root(tmp_path: Path) -> None:
    row = env._project_ffmpeg_check(tmp_path)
    assert row["status"] == env.WARN
    assert "install_ffmpeg" in row["fix"]


def test_tools_dir_is_reported_missing_under_an_empty_root(tmp_path: Path) -> None:
    row = env._tool_check(tmp_path)
    assert row["status"] == env.WARN
    assert "tools/" in row["detail"]


def test_database_check_reads_tables_and_revision(tmp_path: Path) -> None:
    database = _make_db(tmp_path / "probe.db", tables=3)
    row = env._database_check(f"sqlite:///{database.as_posix()}", tmp_path)
    assert row["status"] == env.OK, row["detail"]
    assert "4 张表" in row["detail"]  # 3 张业务表 + alembic_version
    assert "b8_calendar_tags" in row["detail"]
    assert "integrity=ok" in row["detail"]


def test_database_check_says_the_file_does_not_exist_yet(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    row = env._database_check(f"sqlite:///{missing.as_posix()}", tmp_path)
    assert row["status"] == env.WARN
    assert "还不存在" in row["detail"]
    assert "alembic upgrade head" in row["fix"]


def test_disk_check_accepts_a_root(tmp_path: Path) -> None:
    row = env._disk_check(tmp_path)
    assert row["status"] in {env.OK, env.WARN}
    assert "剩余" in row["detail"]


def test_database_url_is_read_from_settings_when_omitted(tmp_path: Path) -> None:
    report = env.collect_environment(port=None, root=tmp_path)
    assert report["database_url"]
    assert any(item["name"] == "database" for item in report["checks"])


@pytest.mark.parametrize("status", [env.OK, env.WARN, env.BAD])
def test_check_helper_keeps_the_fix_field(status: str) -> None:
    row = env.check("x", status, "detail", fix="do something")
    assert row == {"name": "x", "status": status, "detail": "detail", "fix": "do something"}


def test_api_returns_the_same_report(client: TestClient) -> None:
    response = client.get("/api/system/environment")
    assert response.status_code == 200
    body = response.json()
    assert body["note"]
    assert body["checks"], "体检不能是空的"
    counts = {"ok": 0, "warn": 0, "bad": 0}
    for item in body["checks"]:
        assert item["status"] in counts
        counts[item["status"]] += 1
    assert body["counts"] == counts
    assert body["ok"] == (counts["bad"] == 0)
    # 接口口径不查端口（自己就占着）
    assert not any(item["name"].startswith("port:") for item in body["checks"])


def test_api_rejects_a_bad_query(client: TestClient) -> None:
    response = client.get("/api/system/environment?probe_network=maybe")
    assert response.status_code == 422
