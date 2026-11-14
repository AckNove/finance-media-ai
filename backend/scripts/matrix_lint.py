"""把需求矩阵当账本查一遍：状态合法吗？引用的东西还在吗？表头数字对得上代码吗？

用法：

    python -m backend.scripts.matrix_lint                # 查仓库里那份矩阵
    python -m backend.scripts.matrix_lint --json          # 机器可读
    python -m backend.scripts.matrix_lint --matrix <path> # 查另一份

退出码：0 没有硬问题（可能有提示）/ 1 有硬问题 / 2 文件读不到。

它只验证"存在性"：引用的文件、函数名、接口路径在仓库里确实找得到。**它证明不了
功能真能用**——那是测试和真机验收的事。所以别把这个脚本的绿灯当成验收通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.services.matrix_lint import lint, render_text

DEFAULT_MATRIX = PROJECT_ROOT / "docs" / "research_hub" / "v6_0_requirements_matrix.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.matrix_lint",
        description="核对需求追踪矩阵：状态取值、引用是否还存在、表头数字是否与代码一致",
    )
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="矩阵文件路径")
    parser.add_argument("--root", default=str(PROJECT_ROOT), help="仓库根（默认当前仓库）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    matrix = Path(args.matrix)
    if not matrix.exists():
        print(f"读不到矩阵文件：{matrix}", file=sys.stderr)
        return 2
    report = lint(Path(args.root), matrix)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print(render_text(report))
        for item in report.findings:
            if item.severity == "error":
                print(f"  [错误] {item.check}：{item.detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
