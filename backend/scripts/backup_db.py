"""SQLite 在线备份（不改被备份的库）。

为什么要有这个脚本
------------------
任务书 A 段要求"数据备份和恢复工具"。SQLite 不能靠复制文件备份——正在写的库旁边可能有
`-wal` / `-shm`，直接 copy 出来的文件可能自相矛盾。正确做法是用 SQLite 自己的在线备份 API
（`sqlite3.Connection.backup`），它会在读事务里拿一份一致快照。

安全边界
--------
* 备份**只读**源库（用 `mode=ro` URI 打开），绝不写它，也不改 WAL 状态；
* 受保护库 `data/finance_media.db` **允许**被备份（读它不会伤害它），但备份产物只落到
  `data/backups/`（`.gitignore` 已排除），不会被提交；
* 每次备份都写一份 `.json` 说明（源库 sha256、字节数、表数、`integrity_check`、创建时间），
  备份文件自己也再算一次 sha256——恢复前能对得上。

用法
----
    python -m backend.scripts.backup_db                       # 备份默认库
    python -m backend.scripts.backup_db --database-url sqlite:///./data/demo.db
    python -m backend.scripts.backup_db --out data/backups --keep 10
    python -m backend.scripts.backup_db --list                # 只列出已有备份
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "data" / "backups"
PROTECTED_DB_SUFFIX = "data/finance_media.db"
TOOL = "backend.scripts.backup_db"
# <库名>-<YYYYMMDD>-<HHMMSS>-<毫秒>[-序号].db
_BACKUP_NAME = re.compile(r"^(?P<stem>.+?)-\d{8}-\d{6}-\d{3}(?:-\d+)?$")


def _resolve_target(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise SystemExit(f"只支持 SQLite，收到 {url.get_backend_name()}")
    database = url.database or ""
    if not database or database == ":memory:":
        raise SystemExit("内存库没有可备份的文件")
    path = Path(database)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def is_protected(path: Path | str) -> bool:
    """受保护库判定（只用于**警告**：备份读它不会改它）。

    接受 ``Path`` 或 database_url：调用方有的手里是路径、有的手里是 URL，
    两种都按"末尾是不是 data/finance_media.db"判断，避免各处自己拼字符串。
    """

    if isinstance(path, str):
        if "://" in path:
            try:
                path = _resolve_target(path)
            except SystemExit:  # 内存库/非 SQLite：不可能是受保护库
                return False
        else:
            path = Path(path)
    try:
        return path.resolve().as_posix().endswith(PROTECTED_DB_SUFFIX)
    except OSError:  # pragma: no cover - 路径解析失败时按非保护库处理
        return False


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10.0)


def describe_source(path: Path) -> dict[str, Any]:
    """只读地描述一个 SQLite 文件：表数、完整性问题、journal 模式、revision。"""

    connection = _connect_readonly(path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        revision = None
        if "alembic_version" in tables:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
    finally:
        connection.close()
    return {
        "table_count": len(tables),
        "tables": tables,
        "integrity_check": integrity,
        "journal_mode": journal_mode,
        "migration_revision": revision,
    }


def _resolve_source(source: Path | str) -> Path:
    """接受路径或 database_url 两种写法。"""

    if isinstance(source, Path):
        return source
    if "://" not in source:
        candidate = Path(source)
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    return _resolve_target(source)


class BackupError(RuntimeError):
    """备份被拒绝或失败；``reason`` 是稳定原因码（``missing_source`` 等）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def backup_database(
    source: Path | str,
    out_dir: Path | str = DEFAULT_OUT,
    *,
    keep: int = 20,
    stamp: str | None = None,
    label: str = "",
) -> dict[str, Any]:
    """把一个 SQLite 库备份到 ``out_dir``，返回说明字典（``ok`` 恒为 True，失败抛异常）。"""

    out_dir = Path(out_dir)
    source_path = _resolve_source(source)
    if not source_path.exists():
        raise BackupError("missing_source", f"库不存在：{source_path}")
    if not source_path.is_file():
        raise BackupError("not_a_file", f"不是文件：{source_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    target = out_dir / f"{source_path.stem}-{stamp}.db"
    # 同一毫秒内连按两次也不许覆盖：撞名就加序号
    counter = 1
    while target.exists():
        counter += 1
        target = out_dir / f"{source_path.stem}-{stamp}-{counter}.db"

    source_info = describe_source(source_path)
    source_connection = _connect_readonly(source_path)
    try:
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()

    backup_info = describe_source(target)
    manifest = {
        "tool": TOOL,
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "source_path": str(source_path),
        "source_protected": is_protected(source_path),
        "source_size": source_path.stat().st_size,
        "source_sha256": sha256_of(source_path),
        "source_tables": source_info["table_count"],
        "source_migration_revision": source_info["migration_revision"],
        "backup_path": str(target),
        "backup_size": target.stat().st_size,
        "backup_sha256": sha256_of(target),
        "backup_tables": backup_info["table_count"],
        # 规范键名：读说明的人先看这个
        "table_count": backup_info["table_count"],
        "integrity_check": backup_info["integrity_check"],
        "backup_integrity_check": backup_info["integrity_check"],
        "migration_revision": source_info["migration_revision"],
        "backup_journal_mode": backup_info["journal_mode"],
    }
    if backup_info["integrity_check"] != "ok":
        raise BackupError("corrupt_backup", f"备份文件完整性检查失败：{backup_info['integrity_check']}")
    if backup_info["table_count"] != source_info["table_count"]:
        raise BackupError(
            "table_mismatch",
            f"备份表数与源库不一致：源 {source_info['table_count']} / 备份 {backup_info['table_count']}",
        )
    manifest_path = target.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    manifest["pruned"] = _prune(out_dir, source_path.stem, keep)
    return {
        "ok": True,
        "backup": str(target),
        "manifest_path": str(manifest_path),
        "source_sha256": manifest["source_sha256"],
        "backup_sha256": manifest["backup_sha256"],
        "table_count": manifest["backup_tables"],
        "migration_revision": manifest["source_migration_revision"],
        "source_protected": manifest["source_protected"],
        "label": label,
        "pruned": manifest["pruned"],
        "manifest": manifest,
    }


def _prune(out_dir: Path, stem: str, keep: int) -> list[str]:
    if keep <= 0:
        return []
    # 按 (mtime, 文件名) 排序：文件名里带零填充的时间戳，同秒内也不会乱
    backups = sorted(
        out_dir.glob(f"{stem}-*.db"),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    removed: list[str] = []
    for old in backups[keep:]:
        manifest = old.with_suffix(".json")
        for path in (old, manifest):
            if path.exists():
                path.unlink()
                removed.append(str(path))
    return removed


def prune_backups(out_dir: Path | str = DEFAULT_OUT, *, keep: int = 20, stem: str = "") -> dict[str, Any]:
    """按 ``keep`` 清理旧备份；``stem`` 为空则对目录下每个库分别清理。"""

    out_dir = Path(out_dir)
    if not out_dir.exists():
        return {"ok": True, "kept": 0, "removed": 0, "files": []}
    if stem:
        stems = [stem]
    else:
        stems = sorted(
            {match.group("stem") for item in out_dir.glob("*.db") if (match := _BACKUP_NAME.match(item.stem))}
        )
    removed: list[str] = []
    for name in stems:
        removed.extend(_prune(out_dir, name, keep))
    kept = len(list(out_dir.glob("*.db")))
    return {
        "ok": True,
        "kept": kept,
        "removed": sum(1 for item in removed if item.endswith(".db")),
        "removed_files": removed,
    }


def list_backups(out_dir: Path = DEFAULT_OUT) -> list[dict[str, Any]]:
    if not out_dir.exists():
        return []
    rows = []
    for manifest in sorted(out_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(manifest.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite 在线备份")
    parser.add_argument("--database-url", default="", help="默认取 settings.database_url")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--keep", type=int, default=20, help="同一库最多保留几份（0 表示不清理）")
    parser.add_argument("--list", action="store_true", help="只列出已有备份")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    if args.list:
        rows = list_backups(out_dir)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    database_url = args.database_url
    if not database_url:
        from backend.app.core.config import get_settings

        database_url = get_settings().database_url

    try:
        manifest = backup_database(database_url, out_dir=out_dir, keep=args.keep)
    except (FileNotFoundError, RuntimeError) as error:
        print(json.dumps({"ok": False, "reason": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
