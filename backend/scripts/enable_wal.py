"""显式把某个 SQLite 库切到 WAL 模式（M2.0 专项整改后的唯一入口）。

为什么需要这个脚本：``journal_mode=WAL`` 会持久化到库文件，属于"改文件"的动作，
不能挂在连接事件上。要切就必须有人明确说"我要切哪个库"，并且这个库是已知的。

安全约定（见 :mod:`backend.app.database.wal`）：

- 只接受**已存在的文件型** SQLite 库；不新建文件、不猜路径；
- 默认拒绝真实开发库 ``data/finance_media.db``，必须显式 ``--allow-development-db``；
- ``--dry-run`` 完全不连接数据库，只回报将要操作的目标；
- 重复执行是安全的：已是 WAL 时返回 ``changed=False already_enabled=True``。

用法::

    # 演练：只看目标，不连接、不修改
    python -m backend.scripts.enable_wal --database-url sqlite:///C:/tmp/t.db --dry-run

    # 真正切换（临时库）
    python -m backend.scripts.enable_wal --database-url sqlite:///C:/tmp/t.db

    # 真实开发库（M2.0 阶段禁止执行；需要显式放行）
    python -m backend.scripts.enable_wal \\
        --database-url sqlite:///C:/Users/25803/finance-media-ai/data/finance_media.db \\
        --allow-development-db

退出码：``0`` 成功（含 dry-run 与"已经是 WAL"）；``2`` 被安全阈值拒绝；``1`` 其他错误。
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.app.core.config import get_settings
from backend.app.database.wal import WalInitializationError, enable_wal


def resolve_database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    return get_settings().resolved_database_url()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显式启用 SQLite WAL 模式（幂等）")
    parser.add_argument("--database-url", default=None, help="目标库 URL，缺省用 settings")
    parser.add_argument("--dry-run", action="store_true", help="只回报目标，不连接、不修改")
    parser.add_argument(
        "--allow-development-db",
        action="store_true",
        help="显式允许对真实开发库 data/finance_media.db 操作（M2.0 阶段不要使用）",
    )
    args = parser.parse_args(argv)

    url = resolve_database_url(args.database_url)
    try:
        result = enable_wal(
            url,
            allow_development_db=args.allow_development_db,
            dry_run=args.dry_run,
        )
    except WalInitializationError as exc:
        print(f"拒绝执行[{exc.reason}]：{exc.message}")
        return 2 if exc.reason == "protected_database" else 1

    print(
        json.dumps(
            {
                "database_url": result.database_url,
                "target_path": result.target_path,
                "before": result.before,
                "after": result.after,
                "changed": result.changed,
                "already_enabled": result.already_enabled,
                "dry_run": result.dry_run,
                "protected": result.protected,
            },
            ensure_ascii=False,
        )
    )
    print(f"enable_wal: {result.describe()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
