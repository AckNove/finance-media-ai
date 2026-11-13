"""从备份恢复一个 SQLite 库（受保护库默认拒绝）。

设计取舍
--------
* 恢复是**破坏性**操作，所以默认拒绝目标为受保护库 `data/finance_media.db`；
  确实要覆盖它必须显式 `--allow-development-db`（V6.0 边界：真实库迁移/覆盖要单独授权）。
* 覆盖前先把现有目标库整份复制成 `<名字>.pre-restore-<时间戳>.db`，恢复失败可以退回去。
* 恢复前校验备份文件的 sha256 与 `integrity_check`，与 `.json` 说明对不上就不动目标库。

用法
----
    python -m backend.scripts.restore_db --backup data/backups/demo-20261113-101500.db
    python -m backend.scripts.restore_db --backup ... --target sqlite:///./data/demo.db
    python -m backend.scripts.restore_db --backup ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from backend.scripts.backup_db import PROJECT_ROOT, describe_source, is_protected, sha256_of

PROTECTED_DB_SUFFIX = "data/finance_media.db"


class RestoreError(RuntimeError):
    """恢复被拒绝或校验失败；``reason`` 是稳定原因码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _resolve_target(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise RestoreError("not_sqlite", f"只支持 SQLite，收到 {url.get_backend_name()}")
    database = url.database or ""
    if not database or database == ":memory:":
        raise RestoreError("in_memory", "内存库不能作为恢复目标")
    path = Path(database)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _target_is_protected(path: Path) -> bool:
    try:
        return path.resolve().as_posix().endswith(PROTECTED_DB_SUFFIX)
    except OSError:  # pragma: no cover
        return False


def _manifest_of(backup: Path) -> dict[str, Any] | None:
    manifest = backup.with_suffix(".json")
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover
        return None


def verify_backup(backup: Path) -> dict[str, Any]:
    """校验备份文件：存在、是 SQLite、完整性 ok、与说明里的 sha256 一致。"""

    if not backup.exists():
        raise RestoreError("missing_backup", f"备份文件不存在：{backup}")
    if not backup.is_file():
        raise RestoreError("not_a_file", f"不是文件：{backup}")
    info = describe_source(backup)
    if info["integrity_check"] != "ok":
        raise RestoreError("corrupt_backup", f"备份文件完整性检查失败：{info['integrity_check']}")
    digest = sha256_of(backup)
    manifest = _manifest_of(backup)
    if manifest and manifest.get("backup_sha256") and manifest["backup_sha256"] != digest:
        raise RestoreError(
            "checksum_mismatch",
            f"备份文件 sha256 与说明不一致：说明 {manifest['backup_sha256'][:12]}… / 实际 {digest[:12]}…",
        )
    return {"path": str(backup), "sha256": digest, **info, "manifest": manifest}


def restore_backup(
    backup: Path,
    *,
    target_url: str,
    allow_development_db: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    verified = verify_backup(backup)
    target = _resolve_target(target_url)
    protected = _target_is_protected(target)
    if protected and not allow_development_db:
        raise RestoreError(
            "protected_database",
            f"拒绝覆盖受保护库 {target}：真实开发库只读。确需覆盖请显式加 --allow-development-db",
        )

    result: dict[str, Any] = {
        "ok": True,
        "backup": verified["path"],
        "backup_sha256": verified["sha256"],
        "backup_tables": verified["table_count"],
        "target": str(target),
        "target_protected": protected,
        "dry_run": dry_run,
    }
    if dry_run:
        result["note"] = "dry-run：只校验备份，没有写目标库"
        return result

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        snapshot = target.with_name(
            f"{target.stem}.pre-restore-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}{target.suffix}"
        )
        shutil.copy2(target, snapshot)
        result["pre_restore_copy"] = str(snapshot)

    # 用 SQLite 备份 API 把备份内容写进目标（而不是文件复制）：目标上已有的连接会被正确处理。
    source = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    restored = describe_source(target)
    if restored["integrity_check"] != "ok":
        raise RestoreError("restore_corrupt", f"恢复后完整性检查失败：{restored['integrity_check']}")
    result["restored_tables"] = restored["table_count"]
    result["restored_migration_revision"] = restored["migration_revision"]
    if restored["table_count"] != verified["table_count"]:
        raise RestoreError(
            "table_mismatch",
            f"恢复后表数与备份不一致：备份 {verified['table_count']} / 目标 {restored['table_count']}",
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从备份恢复 SQLite 库")
    parser.add_argument("--backup", required=True, help="备份 .db 文件路径")
    parser.add_argument("--target", default="", help="目标 database_url，默认取 settings.database_url")
    parser.add_argument("--allow-development-db", action="store_true", help="允许覆盖受保护库")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    target_url = args.target
    if not target_url:
        from backend.app.core.config import get_settings

        target_url = get_settings().database_url

    try:
        result = restore_backup(
            Path(args.backup),
            target_url=target_url,
            allow_development_db=args.allow_development_db,
            dry_run=args.dry_run,
        )
    except RestoreError as error:
        print(json.dumps({"ok": False, "reason": error.reason, "message": error.message}, ensure_ascii=False))
        return 2 if error.reason == "protected_database" else 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
