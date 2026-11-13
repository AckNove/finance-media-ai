"""`python -m backend.scripts.content_qc --job <job_id>`

对一份视频任务做内容质检（数字出处 / 来源覆盖 / 字幕来源 / 素材授权），
把结果写进 `qc/content_qc.{json,md}`。不联网、不调模型、不做判断好坏。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.video.content_qc import ContentQcError, review_and_write


def _print_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="视频任务内容质检（只核对，不评判好坏）")
    parser.add_argument("--job", required=True, help="content/jobs 下的任务目录名")
    parser.add_argument(
        "--root",
        default=str(PROJECT_ROOT / "content" / "jobs"),
        help="任务根目录（默认 content/jobs，测试时可指向临时目录）",
    )
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    try:
        report, files = review_and_write(args.job, root=Path(args.root))
    except ContentQcError as error:
        _print_json({"ok": False, "blocked": True, "reason": error.reason, "message": str(error)})
        return 3

    payload = report.as_dict()
    payload["files"] = files
    if args.json:
        _print_json(payload)
    else:
        print(report.to_markdown())
        print(f"结果已写入：{files['markdown']}")
        print(f"机器可读：{files['json']}")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
