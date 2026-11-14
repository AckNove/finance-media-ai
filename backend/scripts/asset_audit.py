"""素材清单审计 CLI。

用法::

    python -m backend.scripts.asset_audit --job v6_accept_company_v2
    python -m backend.scripts.asset_audit --root content/jobs --json

退出码：0 = 没有硬问题（可能有提醒）；1 = 有硬问题；2 = 任务目录不存在。
只读本地文件，不联网、不改任何东西。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.video.asset_audit import audit_job, to_markdown

DEFAULT_ROOT = PROJECT_ROOT / "content" / "jobs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="核对素材清单与成片实际使用是否一致")
    parser.add_argument("--job", help="任务目录名（在 --root 下）")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="任务根目录，默认 content/jobs")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.job:
        print("要么给 --job，要么先用 --root 看清目录；这个命令一次只看一个任务", file=sys.stderr)
        return 2
    job_dir = Path(args.root) / args.job
    if not job_dir.is_dir():
        print(f"没找到任务目录：{job_dir}", file=sys.stderr)
        return 2

    audit = audit_job(job_dir)
    if args.json:
        print(json.dumps(audit.as_dict(), ensure_ascii=False, indent=1))
    else:
        print(to_markdown(audit))
    return 0 if audit.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
