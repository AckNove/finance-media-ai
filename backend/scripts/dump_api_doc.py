"""把 OpenAPI 文档导成一份可直接读的 Markdown。

为什么不用手写 API 文档：手写的一定会和代码漂移。这里每次从 ``create_app().openapi()``
现取，谁想知道"现在到底有哪些接口"，跑一遍就有答案。

用法::

    python -m backend.scripts.dump_api_doc                     # 打到标准输出
    python -m backend.scripts.dump_api_doc --out docs/x.md     # 写到文件

文档里会标出每个操作**允许谁调用**（human / dev / agent / 公开），这个信息来自
``backend/app/api/router.py`` 的 ``ROUTE_CALLERS`` —— 权限是这套系统最容易被写错的
地方，所以它必须出现在给人看的文档里。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.app.api.router import ROUTE_CALLERS
from backend.app.main import create_app

KIND_LABELS = {
    "human": "人",
    "dev": "开发调试",
    "agent": "Agent",
}


def _callers_label(method: str, path: str) -> str:
    kinds = ROUTE_CALLERS.get((method.upper(), path))
    if kinds is None:
        return "（未在清单里登记）"
    if not kinds:
        return "公开"
    return " / ".join(KIND_LABELS.get(str(kind.value if hasattr(kind, "value") else kind), str(kind)) for kind in kinds)


def _schema_ref(schema: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return "?"
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        return f"{_schema_ref(schema.get('items', {}))}[]"
    if "anyOf" in schema:
        return " | ".join(_schema_ref(item) for item in schema["anyOf"])
    return str(schema.get("type") or "object")


def _parameters(operation: dict[str, Any]) -> str:
    parts: list[str] = []
    for param in operation.get("parameters", []) or []:
        name = param.get("name", "?")
        where = param.get("in", "query")
        required = "必填" if param.get("required") else "可选"
        parts.append(f"`{name}`（{where}，{required}，{_schema_ref(param.get('schema', {}))}）")
    return "、".join(parts) or "—"


def _body(operation: dict[str, Any]) -> str:
    content = (operation.get("requestBody") or {}).get("content") or {}
    for media, spec in content.items():
        return f"{media} → `{_schema_ref(spec.get('schema', {}))}`"
    return "—"


def _responses(operation: dict[str, Any]) -> str:
    parts: list[str] = []
    for code, spec in sorted((operation.get("responses") or {}).items()):
        schema = ((spec.get("content") or {}).get("application/json") or {}).get("schema", {})
        parts.append(f"{code} `{_schema_ref(schema)}`")
    return "；".join(parts) or "—"


def build_markdown(*, title: str = "finance-media-ai API 参考") -> str:
    spec = create_app().openapi()
    info = spec.get("info", {})
    lines = [
        f"# {title}",
        "",
        f"- 版本：{info.get('version', '?')}",
        f"- 路径数：{len(spec.get('paths', {}))}",
        "",
        "> 这份文档由 `python -m backend.scripts.dump_api_doc --out <文件>` 从 `create_app().openapi()` 现取生成，",
        "> 不要手改——改了下次生成会被覆盖。权限列来自 `backend/app/api/router.py` 的 `ROUTE_CALLERS`。",
        "",
        "## 调用者身份",
        "",
        "- **人**：浏览器/脚本以人类身份调用（默认入口 `/app` 的前端就是这样调的）。",
        "- **开发调试**：只在非生产环境存在，生产环境一律 404（不暴露端点存在性）。",
        "- **Agent**：只能由服务端内部执行器构造，**外部请求无法靠请求头自报身份**。",
        "- **公开**：没有身份要求（目前只有健康检查）。",
        "",
        "## 认证与环境变量",
        "",
        "本机自用，没有账号体系：不发送任何 `Authorization` 头也能用。",
        "模型密钥只从环境变量读（`.env` 里的 `DEEPSEEK_API_KEY`），不会出现在任何响应里。",
        "",
        "## 操作清单",
        "",
        "| 方法 | 路径 | 摘要 | 允许调用者 | 参数 | 请求体 | 响应 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for path, methods in sorted(spec.get("paths", {}).items()):
        for method, operation in sorted(methods.items()):
            summary = str(operation.get("summary") or operation.get("operationId") or "").replace("|", "\\|")
            lines.append(
                f"| {method.upper()} | `{path}` | {summary} | {_callers_label(method, path)} | "
                f"{_parameters(operation)} | {_body(operation)} | {_responses(operation)} |"
            )
    lines += [
        "",
        "## 错误格式",
        "",
        "所有业务错误都是同一个信封（`backend/app/core/errors.py`）：",
        "",
        "```json",
        json.dumps(
            {
                "error": {
                    "code": "domain_validation_error",
                    "message": "人话说明",
                    "details": {"reason": "稳定原因码", "field": "出问题的字段"},
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "`details.reason` 是稳定码，脚本可以按它判断；`message` 会变，别拿来做逻辑。",
        "",
        "## 一份最小调用示例",
        "",
        "```bash",
        "# 建一个研究任务",
        'curl -s -X POST http://127.0.0.1:8000/api/research-tasks \\',
        '  -H "Content-Type: application/json" \\',
        '  -d \'{"title":"储能行业为什么价格战","objective":"说清供需与价格的关系","budget_max_calls":8}\'',
        "",
        "# 推进它（每个 worker 进程推进若干格）",
        "python -m backend.scripts.run_worker --task-id 1 --max-iterations 3",
        "",
        "# 看语义核查结果",
        "curl -s http://127.0.0.1:8000/api/research-reports/1/checks/summary",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 API 参考（Markdown）")
    parser.add_argument("--out", type=Path, default=None, help="写到文件；不给就打到标准输出")
    args = parser.parse_args(argv)

    text = build_markdown()
    if args.out is None:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"written: {args.out} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
