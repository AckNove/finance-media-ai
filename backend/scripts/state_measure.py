"""一轮一次的项目现状实测：只量，不猜，不扫全仓。

为什么要有这个脚本
------------------
`PROJECT_STATE.md` 是"这个项目现在是什么样"的唯一台账。手写台账必然飘——
上一轮写的 84 个接口，这一轮可能就变成 86 个。所以状态数字不能靠回忆，
必须每次由命令现算一遍。

用法::

    python -m backend.scripts.state_measure           # JSON
    python -m backend.scripts.state_measure --md      # 直接贴进 PROJECT_STATE.md 的表格

它只做三件事：读代码结构、读受保护库的**只读**元信息、数目录里的文件。
不建表、不迁移、不开 WAL、不联网、不花一分钱。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys


def measure() -> dict[str, object]:
    from backend.app.api.router import ROUTE_CALLERS
    from backend.app.capabilities.tools import TOOL_NAMES
    from backend.app.main import create_app
    from backend.app.models import Base

    spec = create_app().openapi()
    operations = sum(
        1
        for path in spec["paths"].values()
        for method in path
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )
    app_js = pathlib.Path("web/app.js").read_text(encoding="utf-8")
    pages = re.findall(r"\{ path: '(/[^']*)', title:", app_js)

    payload: dict[str, object] = {
        "tables_orm": len(Base.metadata.tables),
        "api_paths": len(spec["paths"]),
        "api_operations": operations,
        "route_callers": len(ROUTE_CALLERS),
        "pages": len(pages),
        "tools": len(TOOL_NAMES),
        "test_files": len(list(pathlib.Path("tests").glob("test_*.py"))),
        "docs_root": len(list(pathlib.Path("docs").glob("*.md"))),
        "docs_research_hub": len(list(pathlib.Path("docs/research_hub").glob("*.md"))),
        "scripts_py": len(list(pathlib.Path("backend/scripts").glob("*.py"))),
        "scripts_ps1": len(list(pathlib.Path("scripts").glob("*.ps1"))),
        "jobs_with_mp4": len(list(pathlib.Path("content/jobs").glob("*/output/*.mp4"))),
        "backend_py_lines": sum(
            len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
            for p in pathlib.Path("backend/app").rglob("*.py")
        ),
        "web_app_js_lines": len(app_js.splitlines()),
    }

    db = pathlib.Path("data/finance_media.db")
    if db.is_file():
        raw = db.read_bytes()
        con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            tables = [
                row[0]
                for row in con.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
                )
            ]
            revision = con.execute("select version_num from alembic_version").fetchone()[0]
            journal = con.execute("pragma journal_mode").fetchone()[0]
        finally:
            con.close()
        payload.update(
            {
                "dev_db_sha256": hashlib.sha256(raw).hexdigest(),
                "dev_db_bytes": len(raw),
                "dev_db_tables": len(tables),
                "dev_db_revision": revision,
                "dev_db_journal_mode": journal,
                "dev_db_has_model_calls": "model_calls" in tables,
            }
        )
    return payload


LABELS = {
    "tables_orm": "ORM 表（`Base.metadata.tables`）",
    "api_paths": "API 路径",
    "api_operations": "API 操作",
    "route_callers": "权限清单条目（`ROUTE_CALLERS`）",
    "pages": "工作台页面（`web/app.js`）",
    "tools": "已注册工具（`TOOL_NAMES`）",
    "test_files": "测试文件",
    "docs_root": "`docs/*.md`",
    "docs_research_hub": "`docs/research_hub/*.md`",
    "scripts_py": "`backend/scripts/*.py`",
    "scripts_ps1": "`scripts/*.ps1`",
    "jobs_with_mp4": "已出片的视频任务",
    "backend_py_lines": "`backend/app` 行数",
    "web_app_js_lines": "`web/app.js` 行数",
}


def as_markdown(payload: dict[str, object]) -> str:
    lines = ["| 项 | 实测 |", "| --- | --- |"]
    for key, label in LABELS.items():
        lines.append(f"| {label} | {payload.get(key)} |")
    for key in ("dev_db_bytes", "dev_db_tables", "dev_db_revision", "dev_db_journal_mode"):
        if key in payload:
            lines.append(f"| 真实库 {key.removeprefix('dev_db_')} | {payload[key]} |")
    if "dev_db_sha256" in payload:
        lines.append(f"| 真实库 sha256 | `{payload['dev_db_sha256']}` |")
    if "dev_db_has_model_calls" in payload:
        lines.append(f"| 真实库有 `model_calls` 表 | {payload['dev_db_has_model_calls']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实测项目现状（只读）")
    parser.add_argument("--md", action="store_true", help="输出可直接粘进 PROJECT_STATE.md 的表格")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    payload = measure()
    print(as_markdown(payload) if args.md else json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
