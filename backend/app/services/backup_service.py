"""备份能不能恢复——这件事得真验一遍，不能只看"文件还在"。

为什么要有这个模块
------------------
`backend/scripts/backup_db.py` 备份时已经跑过一次 `integrity_check`，但那只是**备份那一刻**的
结论。真正的风险在后面：文件被半截复制、被同步盘改坏、备份的是个 0 字节文件、
或者库结构停在旧版本而你以为能直接顶上。

所以这里做三件事，都只读：
1. 把备份文件按 SQLite 只读 URI 打开，跑 `PRAGMA integrity_check` 与 `PRAGMA foreign_key_check`；
2. 数出表数、读出 `alembic_version`，并**按 manifest 里记的 sha256 核对文件有没有被动过**；
3. 如果给了当前库的 URL，就把两边逐表行数比一比——**差异如实报出来**（备份比现在旧是正常的，
   但"备份里少了现在有的表"就是需要人看一眼的事）。

写盘的地方只有一处：验证结论落到备份旁边的 `<备份名>.verify.json`，好让每日体检能引用
"上次验证时间与结论"，而不是每天重新验一遍。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BACKUPS = PROJECT_ROOT / "data" / "backups"


@dataclass(frozen=True)
class BackupCheck:
    """一次备份验证的结论。``ok`` 为假时 ``problems`` 一定有内容。"""

    path: str
    ok: bool
    byte_size: int = 0
    sha256: str = ""
    manifest_sha256: str = ""
    sha256_matches: bool = True
    integrity: str = ""
    foreign_key_violations: int = 0
    table_count: int = 0
    revision: str = ""
    compared_with: str = ""
    missing_tables: list[str] = field(default_factory=list)
    row_differences: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    checked_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "manifest_sha256": self.manifest_sha256,
            "sha256_matches": self.sha256_matches,
            "integrity": self.integrity,
            "foreign_key_violations": self.foreign_key_violations,
            "table_count": self.table_count,
            "revision": self.revision,
            "compared_with": self.compared_with,
            "missing_tables": list(self.missing_tables),
            "row_differences": list(self.row_differences),
            "problems": list(self.problems),
            "checked_at": self.checked_at,
        }

    def summary(self) -> str:
        if self.ok:
            head = f"可恢复：{self.table_count} 张表、revision {self.revision or '（无）'}"
            if self.row_differences:
                return f"{head}；与当前库有 {len(self.row_differences)} 处行数差异（备份旧于现在属正常）"
            return head
        return "不能当作可恢复的备份，原因：" + "；".join(self.problems)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _revision_of(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def compare_with_live(backup: Path, live: Path) -> tuple[list[str], list[str]]:
    """逐表比行数。返回 ``(备份缺的表, 行数差异说明)``，两个都只是"要看一眼"，不是错。"""

    # `with sqlite3.Connection` 只管事务、**不会关连接**；Windows 上没关的句柄会让
    # 备份文件删不掉（清理/裁剪时最容易撞上），所以这里显式 closing。
    with closing(_connect_readonly(backup)) as left, closing(_connect_readonly(live)) as right:
        backup_tables = set(_table_names(left))
        live_tables = set(_table_names(right))
        missing = sorted(live_tables - backup_tables)
        differences: list[str] = []
        for table in sorted(backup_tables & live_tables):
            backup_rows = _row_count(left, table)
            live_rows = _row_count(right, table)
            if backup_rows != live_rows:
                differences.append(f"{table}: 备份 {backup_rows} 行 / 当前 {live_rows} 行")
    return missing, differences


def verify_backup(
    path: Path | str,
    *,
    live_path: Path | str | None = None,
    now: datetime | None = None,
    write_report: bool = True,
) -> BackupCheck:
    """只读验证一份备份到底能不能恢复。

    ``live_path`` 给了就逐表比行数；比不上不影响 ``ok``（备份旧于现在是正常的），
    但"备份里没有当前库有的表"会计入 ``problems``——那意味着拿它恢复会丢掉一张表。
    """

    target = Path(path)
    problems: list[str] = []
    checked_at = (now or datetime.now(UTC)).isoformat()

    if not target.is_file():
        return BackupCheck(
            path=str(target),
            ok=False,
            problems=["备份文件不存在"],
            checked_at=checked_at,
        )

    size = target.stat().st_size
    digest = sha256_of(target)
    if size == 0:
        problems.append("备份文件是 0 字节")

    manifest_path = target.with_suffix(".json")
    manifest_sha256 = ""
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_sha256 = str(manifest.get("backup_sha256") or "")
        except (OSError, json.JSONDecodeError):
            problems.append("备份说明文件读不出来（.json 损坏）")

    matches = True
    if manifest_sha256:
        matches = manifest_sha256 == digest
        if not matches:
            problems.append("备份文件与说明里记的 sha256 对不上：这个文件被改过或复制不全")

    integrity = ""
    violations = 0
    table_count = 0
    revision = ""
    missing: list[str] = []
    differences: list[str] = []

    if size:
        try:
            with closing(_connect_readonly(target)) as connection:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
                table_count = len(_table_names(connection))
                revision = _revision_of(connection)
        except sqlite3.DatabaseError as error:
            problems.append(f"这个文件打不开成 SQLite 库：{error}")

    if integrity and integrity != "ok":
        problems.append(f"integrity_check 不通过：{integrity}")
    if violations:
        problems.append(f"有 {violations} 条外键违规（PRAGMA foreign_key_check）")
    if size and not integrity:
        problems.append("读不出 integrity_check 结果")

    if live_path is not None and not problems:
        try:
            missing, differences = compare_with_live(target, Path(live_path))
        except (OSError, sqlite3.DatabaseError) as error:
            differences = [f"与当前库比对失败：{error}"]
        if missing:
            problems.append(f"备份里没有当前库有的表：{', '.join(missing)}（拿它恢复会丢表）")

    check = BackupCheck(
        path=str(target),
        ok=not problems,
        byte_size=size,
        sha256=digest,
        manifest_sha256=manifest_sha256,
        sha256_matches=matches,
        integrity=integrity,
        foreign_key_violations=violations,
        table_count=table_count,
        revision=revision,
        compared_with=str(live_path or ""),
        missing_tables=missing,
        row_differences=differences,
        problems=problems,
        checked_at=checked_at,
    )

    if write_report:
        report_path = target.with_suffix(".verify.json")
        try:
            report_path.write_text(
                json.dumps(check.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except OSError:  # 写不了报告不影响结论本身
            pass
    return check


def latest_backup(directory: Path | str = DEFAULT_BACKUPS) -> Path | None:
    folder = Path(directory)
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_verification(backup: Path | str) -> dict[str, Any] | None:
    """读回上次的验证结论（每日体检用；没有就返回 None）。"""

    path = Path(backup).with_suffix(".verify.json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


__all__ = [
    "BackupCheck",
    "DEFAULT_BACKUPS",
    "compare_with_live",
    "latest_backup",
    "read_verification",
    "sha256_of",
    "verify_backup",
]
