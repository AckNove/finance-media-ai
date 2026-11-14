"""验证一份备份到底能不能恢复（只读，不改任何库）。

    python -m backend.scripts.backup_verify                 # 验最近一份备份，并与默认库比行数
    python -m backend.scripts.backup_verify --list          # 列出有哪些备份
    python -m backend.scripts.backup_verify --backup data/backups/xxx.db --no-compare
    python -m backend.scripts.backup_verify --latest --out data/backups --json

退出码：0 可恢复 / 1 有问题 / 2 根本没有备份文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.services.backup_service import (
    DEFAULT_BACKUPS,
    latest_backup,
    sha256_of,
    verify_backup,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "finance_media.db"


def _resolve_live(value: str | None) -> Path | None:
    """``--compare-with`` 接受文件路径或 sqlite URL，两种都归一到绝对路径。"""

    if value in (None, "", "none"):
        return None
    text = value
    if "://" in text:
        from sqlalchemy.engine import make_url

        url = make_url(text)
        if url.get_backend_name() != "sqlite":
            raise SystemExit(f"只能和 SQLite 库比对行数，收到 {url.get_backend_name()}")
        text = url.database or ""
        if not text or text == ":memory:":
            return None
    path = Path(text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _compare_target(backup: Path, explicit: str | None) -> Path | None:
    """默认拿**备份说明里记的源库**做比对对象。

    不能默认拿 `data/finance_media.db`：验一份演示库的备份却去和真实库比表数，
    会把"备份里少了 12 张表"这种假问题报出来，反而教人忽略真问题。
    """

    if explicit is not None:
        return _resolve_live(explicit)
    manifest_path = backup.with_suffix(".json")
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        source = str(manifest.get("source_path") or "")
        if source:
            path = Path(source)
            return path if path.is_absolute() else PROJECT_ROOT / path
    return DEFAULT_DB if DEFAULT_DB.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证备份能否恢复（只读）")
    parser.add_argument("--backup", help="要验证的备份文件")
    parser.add_argument("--latest", action="store_true", help="验证最新一份备份（默认行为）")
    parser.add_argument("--out", default=str(DEFAULT_BACKUPS), help="备份目录")
    parser.add_argument("--compare-with", help="与哪个库逐表比行数（默认 data/finance_media.db）")
    parser.add_argument("--no-compare", action="store_true", help="不与当前库比对")
    parser.add_argument("--list", action="store_true", help="只列出备份")
    parser.add_argument("--json", action="store_true", help="用 JSON 输出")
    args = parser.parse_args(argv)

    folder = Path(args.out)
    if args.list:
        rows = []
        for path in sorted(folder.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
            rows.append(
                {
                    "name": path.name,
                    "byte_size": path.stat().st_size,
                    "sha256": sha256_of(path)[:16],
                    "verified": path.with_suffix(".verify.json").is_file(),
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if rows else 2

    target = Path(args.backup) if args.backup else latest_backup(folder)
    if target is None:
        message = {"ok": False, "reason": "no_backup", "message": f"{folder} 里没有任何 .db 备份"}
        print(json.dumps(message, ensure_ascii=False) if args.json else message["message"])
        return 2

    live = None if args.no_compare else _compare_target(target, args.compare_with)
    if live is not None and not live.is_file():
        live = None

    check = verify_backup(target, live_path=live)
    if args.json:
        print(json.dumps(check.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"备份：{check.path}")
        print(f"大小：{check.byte_size} B ｜ sha256：{check.sha256[:16]}…（与说明一致：{check.sha256_matches}）")
        if live is not None:
            print(f"比对：{live}")
        print(f"结论：{check.summary()}")
        for line in check.problems:
            print(f"  问题：{line}")
        for line in check.row_differences[:10]:
            print(f"  行数差异：{line}")
    return 0 if check.ok else 1


if __name__ == "__main__":
    sys.exit(main())
