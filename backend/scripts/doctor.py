"""本机环境诊断（doctor）：把"为什么跑不起来"一次说清楚。

与 `preflight.py` 的分工
------------------------
* `preflight.py`：**启动前**体检（依赖、配置、能力开关），回答"现在能不能提供某项能力"；
* `doctor.py`：**排障**用，覆盖面更广——解释器与依赖版本、FFmpeg/字体/浏览器、
  端口占用、库是否可读、迁移版本、磁盘空间、工具目录，缺什么给出**可直接执行的补救命令**。

全部只读：不建库、不迁移、不改配置、不联网（除非显式 `--probe-network`）。

用法
----
    python -m backend.scripts.doctor            # 文本报告
    python -m backend.scripts.doctor --json     # 机器可读
    python -m backend.scripts.doctor --probe-network   # 额外探一次模型服务商可达性
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OK = "ok"
WARN = "warn"
BAD = "bad"


def _check(name: str, status: str, detail: str, *, fix: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _python_check() -> dict[str, Any]:
    version = ".".join(str(item) for item in sys.version_info[:3])
    status = OK if sys.version_info >= (3, 11) else BAD
    return _check(
        "python",
        status,
        f"{version}（{sys.executable}）",
        fix="" if status == OK else "装 Python 3.11+ 再建 .venv",
    )


def _package_check() -> list[dict[str, Any]]:
    rows = []
    for module, label in (
        ("fastapi", "Web 框架"),
        ("sqlalchemy", "ORM"),
        ("alembic", "迁移"),
        ("httpx", "HTTP 客户端"),
        ("pydantic", "配置校验"),
        ("markitdown", "文档解析（可选）"),
        ("edge_tts", "在线 TTS（可选）"),
        ("matplotlib", "图表（可选）"),
    ):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "?")
            rows.append(_check(f"dep:{module}", OK, f"{version}（{label}）"))
        except ModuleNotFoundError:
            optional = "可选" in label
            rows.append(
                _check(
                    f"dep:{module}",
                    WARN if optional else BAD,
                    "未安装" + ("（缺它相关能力会返回 not_configured）" if optional else ""),
                    fix=f".venv\\Scripts\\python.exe -m pip install {module}",
                )
            )
    return rows


def _binary_check(name: str, *, required: bool = False) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return _check(
            f"bin:{name}",
            BAD if required else WARN,
            "PATH 上找不到",
            fix=f"把 ffmpeg 的 bin 目录加进 PATH，或用 -FfmpegDir 指定（见 README）",
        )
    try:
        output = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
        first = output.splitlines()[0] if output else ""
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover
        return _check(f"bin:{name}", WARN, f"无法执行：{error}")
    return _check(f"bin:{name}", OK, f"{first.strip()}（{path}）")


def _project_ffmpeg_check() -> dict[str, Any]:
    binary = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not binary.exists():
        return _check(
            "ffmpeg:project",
            WARN,
            "项目内没有 tools/ffmpeg/bin/ffmpeg.exe",
            fix="python -m backend.scripts.install_ffmpeg --download",
        )
    version_file = PROJECT_ROOT / "tools" / "ffmpeg" / "VERSION.json"
    version = ""
    if version_file.exists():
        try:
            version = json.loads(version_file.read_text(encoding="utf-8")).get("version_line", "")
        except (OSError, json.JSONDecodeError):  # pragma: no cover
            version = ""
    return _check("ffmpeg:project", OK, version or str(binary))


def _font_check() -> dict[str, Any]:
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "NotoSansSC-VF.ttf",
    ]
    found = [str(item) for item in candidates if item.exists()]
    if found:
        return _check("fonts:chinese", OK, "、".join(found))
    return _check("fonts:chinese", WARN, "没找到中文字体（字幕/图表中文会变成方框）", fix="安装微软雅黑或思源黑体")


def _browser_check() -> dict[str, Any]:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    found = [str(item) for item in candidates if item.exists()]
    if found:
        return _check("browser", OK, found[0])
    return _check("browser", WARN, "没找到 Chrome/Edge（工作台无需浏览器，但组合渲染需要）")


def _port_check(host: str, port: int) -> dict[str, Any]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        busy = sock.connect_ex((host, port)) == 0
    return _check(
        f"port:{port}",
        WARN if busy else OK,
        "已被占用（可能已有服务在跑）" if busy else "空闲",
        fix=f"换个端口：scripts\\start.ps1 -Port {port + 1}" if busy else "",
    )


def _database_check(database_url: str) -> dict[str, Any]:
    from backend.scripts.backup_db import describe_source, is_protected
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return _check("database", WARN, f"非 SQLite：{url.get_backend_name()}（本脚本只详查 SQLite）")
    database = url.database or ""
    if not database or database == ":memory:":
        return _check("database", WARN, "内存库")
    path = Path(database)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return _check(
            "database",
            WARN,
            f"库文件还不存在：{path}（首次启动会自动建表）",
            fix="python -m alembic upgrade head",
        )
    info = describe_source(path)
    detail = (
        f"{path.name}：{info['table_count']} 张表、revision={info['migration_revision']}、"
        f"journal_mode={info['journal_mode']}、integrity={info['integrity_check']}"
        + ("、**受保护库（只读）**" if is_protected(path) else "")
    )
    status = OK if info["integrity_check"] == "ok" else BAD
    return _check("database", status, detail)


def _disk_check() -> dict[str, Any]:
    usage = shutil.disk_usage(PROJECT_ROOT)
    free_gb = usage.free / (1024**3)
    status = OK if free_gb >= 5 else WARN
    return _check(
        "disk",
        status,
        f"剩余 {free_gb:.1f} GB",
        fix="" if status == OK else "清理视频渲染中间产物：content/jobs/*/project/**/frames",
    )


def _config_check() -> list[dict[str, Any]]:
    os.environ.setdefault("FM_SKIP_DOTENV", os.environ.get("FM_SKIP_DOTENV", ""))
    try:
        from backend.app.core.config import (
            get_agent_routing,
            get_cost_settings,
            get_features_settings,
            get_models_config,
            get_pipelines_config,
        )
    except Exception as error:  # pragma: no cover - 配置坏到无法导入
        return [_check("config", BAD, f"读取配置失败：{error}")]

    rows = []
    try:
        models = get_models_config()
        enabled = [item for item in models.models if item.enabled]
        rows.append(_check("config:models", OK if enabled else WARN, f"{len(enabled)} 个启用模型 / 共 {len(models.models)}"))
        features = get_features_settings()
        rows.append(
            _check(
                "config:features",
                OK,
                ", ".join(
                    f"{name}={'on' if value else 'off'}"
                    for name, value in (
                        ("model", features.model),
                        ("search", features.search),
                        ("execution", features.execution),
                    )
                ),
            )
        )
        cost = get_cost_settings()
        rows.append(_check("config:cost", OK, f"当日上限 {cost.daily_cap_cny} 元"))
        routing = get_agent_routing()
        rows.append(_check("config:agents", OK, f"{len(routing.agents)} 个角色"))
        pipelines = get_pipelines_config()
        rows.append(_check("config:pipelines", OK, f"{len(pipelines.pipelines)} 条流水线"))
    except Exception as error:  # pragma: no cover
        rows.append(_check("config", BAD, f"配置校验失败：{error}"))
    return rows


def _provider_check(probe_network: bool) -> dict[str, Any]:
    try:
        from backend.app.capabilities.model.providers import provider_status

        statuses = provider_status()
    except Exception as error:  # pragma: no cover
        return _check("providers", WARN, f"读不到服务商状态：{error}")
    ready = [name for name, item in statuses.items() if item.get("state") == "ready"]
    detail = "、".join(f"{name}={item.get('state')}" for name, item in statuses.items()) or "无"
    row = _check(
        "providers",
        OK if ready else WARN,
        detail,
        fix="" if ready else "在 .env 里配置对应 *_API_KEY（不要把密钥贴到任何地方）",
    )
    if probe_network and ready:
        row["network_probe"] = "跳过（doctor 不代你花钱；真要验证用 model_smoke）"
    return row


def _tool_check() -> dict[str, Any]:
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.exists():
        return _check("tools", WARN, "没有 tools/ 目录（FFmpeg/OpenKatana 都在这里）")
    items = [f"{item.name}{'/' if item.is_dir() else ''}" for item in sorted(tools_dir.iterdir())]
    return _check("tools", OK, "、".join(items))


def collect_report(*, database_url: str = "", probe_network: bool = False, port: int = 8000) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_python_check())
    checks.extend(_package_check())
    checks.append(_binary_check("ffmpeg"))
    checks.append(_binary_check("ffprobe"))
    checks.append(_project_ffmpeg_check())
    checks.append(_font_check())
    checks.append(_browser_check())
    checks.append(_port_check("127.0.0.1", port))
    checks.append(_disk_check())
    checks.append(_tool_check())
    if not database_url:
        try:
            from backend.app.core.config import get_settings

            database_url = get_settings().database_url
        except Exception as error:  # pragma: no cover
            checks.append(_check("database", WARN, f"读不到 database_url：{error}"))
            database_url = ""
    if database_url:
        checks.append(_database_check(database_url))
    checks.extend(_config_check())
    checks.append(_provider_check(probe_network))

    counts = {"ok": 0, "warn": 0, "bad": 0}
    for item in checks:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "ok": counts["bad"] == 0,
        "counts": counts,
        "database_url": database_url,
        "project_root": str(PROJECT_ROOT),
        "checks": checks,
    }


def render_text(report: dict[str, Any]) -> str:
    badge = {"ok": "[ok]  ", "warn": "[warn]", "bad": "[BAD] "}
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
