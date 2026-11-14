"""本机环境诊断（doctor）——命令行外壳。

真正的检查在 `backend/app/services/environment.py`：同一份实现同时供
`python -m backend.scripts.doctor` 与 `GET /api/system/environment` 使用。
判断写两遍迟早会互相矛盾（这个仓库已经吃过一次亏：文档说"技术 QC 十项"、
代码里其实是七项），所以这里不再复制实现，只保留参数解析与文本渲染。

用法
----
    python -m backend.scripts.doctor            # 文本报告
    python -m backend.scripts.doctor --json     # 机器可读
    python -m backend.scripts.doctor --probe-network   # 额外读一次服务商可达配置
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from backend.app.services.environment import (  # noqa: F401  (re-export 供老调用方使用)
    BAD,
    NOTE,
    OK,
    STATUS_TEXT,
    WARN,
    _browser_check,
    _check,
    _config_check,
    _database_check,
    _disk_check,
    _font_check,
    _package_check,
    _port_check,
    _project_ffmpeg_check,
    _provider_check,
    _python_check,
    _tool_check,
    check,
    collect_environment,
)


def collect_report(*, database_url: str = "", probe_network: bool = False, port: int = 8000) -> dict[str, Any]:
    """命令行口径：默认查端口占用（`--port` 默认 8000）。"""

    return collect_environment(database_url=database_url, probe_network=probe_network, port=port)


def render_text(report: dict[str, Any]) -> str:
    badge = {OK: "[ok]  ", WARN: "[warn]", BAD: "[BAD] "}
    lines = [
        f"finance-media-ai 环境诊断  （{report['project_root']}）",
        "",
    ]
    for item in report["checks"]:
        lines.append(f"{badge.get(item['status'], '[?]   ')} {item['name']:18s} {item['detail']}")
        if item.get("fix"):
            lines.append(f"         → {item['fix']}")
    lines.append("")
    counts = report["counts"]
    lines.append(f"合计：ok {counts['ok']} / warn {counts['warn']} / bad {counts['bad']}")
    if not report["ok"]:
        lines.append("有 BAD 项，先按上面的修复建议处理；warn 项不影响启动，但相关能力会降级。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机环境诊断")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--probe-network", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    report = collect_report(
        database_url=args.database_url,
        probe_network=args.probe_network,
        port=args.port,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
