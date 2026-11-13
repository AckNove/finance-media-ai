"""`python -m backend.scripts.daily_check` —— 今天的系统体检（一条命令看完）。

判断逻辑在 `backend/app/services/daily_service.py`，这里只负责三件事：
拿到一个只读会话、（必要时）把"库文件不存在"说清楚、把结果打印出来并按需给退出码。

退出码：`0` 一切正常；`1` 有需要处理的事项（见输出的"需要处理"一节）；`3` 连不上库。
参数错误（`argparse`）照旧返回 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.database.session import build_engine
from backend.app.services.daily_service import (
    DEFAULT_BACKUPS,
    DEFAULT_JOBS_ROOT,
    collect_daily,
    render_text,
)


class DailyCheckError(RuntimeError):
    """连不上库属于"体检做不了"，不是"体检结果不好"。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _ensure_sqlite_exists(url: str) -> None:
    """SQLite 会"顺手"新建一个空库，把路径写错就永远查不出东西——先自己判一下。"""

    if not url.startswith("sqlite"):
        return
    parsed = make_url(url)
    path = parsed.database or ""
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if not target.exists():
        raise DailyCheckError(
            "database_missing", f"库文件不存在：{target}（先确认 --database-url 写对了）"
        )


def collect(
    *,
    database_url: str = "",
    jobs_root: Path | str = DEFAULT_JOBS_ROOT,
    backups: Path | str = DEFAULT_BACKUPS,
    days: int = 1,
    now=None,
) -> dict:
    """开一个自己的引擎做一次体检（CLI 用；接口侧直接用请求会话调 `collect_daily`）。"""

    url = database_url or get_settings().database_url
    _ensure_sqlite_exists(url)
    engine = build_engine(get_settings().model_copy(update={"database_url": url}))
    try:
        factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        with factory() as session:
            return collect_daily(
                session,
                jobs_root=jobs_root,
                backups=backups,
                days=days,
                now=now,
                database_url=url,
            )
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="今天的系统体检（只读，不改任何东西）")
    parser.add_argument("--database-url", default="", help="默认用 config/settings.yaml 里的库")
    parser.add_argument("--jobs-root", default=str(DEFAULT_JOBS_ROOT))
    parser.add_argument("--backups", default=str(DEFAULT_BACKUPS))
    parser.add_argument("--days", type=int, default=1, help="窗口天数（默认 1 天）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    try:
        report = collect(
            database_url=args.database_url,
            jobs_root=args.jobs_root,
            backups=args.backups,
            days=args.days,
        )
    except Exception as error:  # noqa: BLE001 - 连不上库就直接说清楚
        reason = getattr(error, "reason", "database_unavailable")
        payload = {"ok": False, "blocked": True, "reason": reason, "message": str(error)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
        return 3

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    else:
        print(render_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
