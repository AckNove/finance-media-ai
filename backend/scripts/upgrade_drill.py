"""跨版本升级演练的命令行入口。

用法::

    # 拿当前配置的库（或指定库）只读复制一份来演练，源库一个字节都不会被写
    python -m backend.scripts.upgrade_drill
    python -m backend.scripts.upgrade_drill --source-db data/finance_media.work.db

    # 不带数据、只验结构能不能升（跑得快）
    python -m backend.scripts.upgrade_drill --no-seed

    # 想留副本自己翻
    python -m backend.scripts.upgrade_drill --work-dir %TEMP%\\drill

    python -m backend.scripts.upgrade_drill --json

退出码：0 全过 / 1 有失败步骤 / 2 参数或环境问题（例如源库不存在）。

为什么要有这个命令
------------------
`alembic check` 只比结构：加了新列、忘了给默认值、回填漏了，它一句都不说。
本项目已经从 V5 的老库升上来过一次（M2.0 B1–B3），而当时只有"空库逐批升级"的测试——
**带数据整条链升上来**这件事，直到这次演练才第一次真跑通，并当场查出
`topics.origin_type` 在老行上是 NULL（设计与 ORM 都写着 NOT NULL default manual），
于是补了 B7 迁移。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from alembic.util.exc import CommandError

from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.services.upgrade_drill import DrillError, DrillReport, is_protected, run_drill

DRILL_NAME = "drill_copy.db"


def _resolve(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _source_of(args: argparse.Namespace) -> Path:
    if args.source_db:
        path = _resolve(args.source_db)
        if not path.is_file():
            raise DrillError("source_missing", f"源库不存在：{path}")
        return path
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        raise DrillError("unsupported_source", f"只支持 SQLite，当前是 {url}")
    path = _resolve(url.split("sqlite:///")[-1])
    if not path.is_file():
        raise DrillError("source_missing", f"配置里的库不存在：{path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.upgrade_drill",
        description="把一个老库复制一份，带数据整库升级到当前版本，逐步检查数据与回填",
    )
    parser.add_argument(
        "--source-db",
        default="",
        help="要演练的库文件；不给就用配置里的库（只读复制，源库不会被写）",
    )
    parser.add_argument(
        "--work-dir",
        default="",
        help="演练副本放哪；不给就用临时目录，跑完删掉",
    )
    parser.add_argument("--from-revision", default="de8148733a9b", help="从哪个版本开始（默认初版）")
    parser.add_argument("--to-revision", default="head", help="升到哪个版本（默认 head）")
    parser.add_argument("--no-seed", action="store_true", help="不塞旧数据，只验结构")
    parser.add_argument("--no-roundtrip", action="store_true", help="不验降级再升级")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = _source_of(args)
    except DrillError as error:
        print(f"演练没开始：{error.message}（{error.reason}）")
        return 2

    keep = bool(args.work_dir)
    if keep:
        work_dir = _resolve(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="fmai_drill_"))
    target = work_dir / DRILL_NAME

    try:
        report: DrillReport = run_drill(
            target,
            source_db=source,
            from_revision=args.from_revision,
            to_revision=args.to_revision,
            seed=not args.no_seed,
            roundtrip=not args.no_roundtrip,
        )
    except (DrillError, CommandError) as error:
        message = getattr(error, "message", None) or str(error)
        reason = getattr(error, "reason", "drill_failed")
        print(f"演练没跑完：{message}（{reason}）")
        return 2
    finally:
        pass

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print(report.summary())
        for step in report.steps:
            mark = "OK  " if step.ok else "FAIL"
            print(f"[{mark}] {step.name}: {step.detail}")
        guard = "受保护开发库，只读复制" if is_protected(source) else "只读复制"
        print(f"源库：{source}（{guard}，源库 sha256 前后一致）")
        print(f"演练副本：{target}")
        if not keep:
            print("（临时副本已在退出前删除；想留着看就加 --work-dir）")

    if not keep:
        shutil.rmtree(work_dir, ignore_errors=True)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - 手工运行
    sys.exit(main())
