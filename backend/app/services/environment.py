"""本机环境体检：把"这台机器够不够用"一次说清。

与另外两处体检的分工
--------------------
* `backend/app/core/startup_checks.py`：**启动时**跑，错了就不许起服务（配置/路由/台账一致性）；
* `preflight.py`：**渲染前**跑，回答"这个任务能不能渲"；
* 本模块：**排障用**，覆盖面最广——解释器与依赖、FFmpeg/字体/浏览器、库是否可读、
  迁移版本、磁盘空间、工具目录、配置段、服务商状态，缺什么给出**可直接执行的补救命令**。

同一份实现两个出口：`python -m backend.scripts.doctor`（命令行）与
`GET /api/system/environment`（系统设置页）。**判断写两遍迟早会互相矛盾**，
所以 doctor 现在只是本模块的薄 CLI。

全部只读：不建库、不迁移、不改配置、不联网（除非显式 `probe_network=True`，
而它也只是读服务商配置，不会真的发请求）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.app.core.config import PROJECT_ROOT

OK = "ok"
WARN = "warn"
BAD = "bad"

STATUS_TEXT = {OK: "正常", WARN: "注意", BAD: "有问题"}

NOTE = (
    "只读体检：不建库、不迁移、不改配置、不发外部请求。"
    "它回答「这台机器缺什么」，不回答「内容做得好不好」。"
)


def check(name: str, status: str, detail: str, *, fix: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


# 兼容旧名字（doctor.py 与老测试里用的是 _check）
_check = check


def _python_check() -> dict[str, Any]:
    version = ".".join(str(item) for item in sys.version_info[:3])
    status = OK if sys.version_info >= (3, 11) else BAD
    return check(
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
            rows.append(check(f"dep:{module}", OK, f"{version}（{label}）"))
        except ModuleNotFoundError:
            optional = "可选" in label
            rows.append(
                check(
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
        return check(
            f"bin:{name}",
            BAD if required else WARN,
            "PATH 上找不到",
            fix="把 ffmpeg 的 bin 目录加进 PATH，或用 -FfmpegDir 指定（见 README）",
        )
    try:
        output = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10).stdout
        first = output.splitlines()[0] if output else ""
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover
        return check(f"bin:{name}", WARN, f"无法执行：{error}")
    return check(f"bin:{name}", OK, f"{first.strip()}（{path}）")


def _project_ffmpeg_check(root: Path | None = None) -> dict[str, Any]:
    base = root or PROJECT_ROOT
    binary = base / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not binary.exists():
        return check(
            "ffmpeg:project",
            WARN,
            "项目内没有 tools/ffmpeg/bin/ffmpeg.exe",
            fix="python -m backend.scripts.install_ffmpeg --download",
        )
    version_file = base / "tools" / "ffmpeg" / "VERSION.json"
    version = ""
    if version_file.exists():
        try:
            version = json.loads(version_file.read_text(encoding="utf-8")).get("version_line", "")
        except (OSError, json.JSONDecodeError):  # pragma: no cover
            version = ""
    return check("ffmpeg:project", OK, version or str(binary))


def _font_check() -> dict[str, Any]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [windir / "Fonts" / "msyh.ttc", windir / "Fonts" / "NotoSansSC-VF.ttf"]
    found = [str(item) for item in candidates if item.exists()]
    if found:
        return check("fonts:chinese", OK, "、".join(found))
    return check("fonts:chinese", WARN, "没找到中文字体（字幕/图表中文会变成方框）", fix="安装微软雅黑或思源黑体")


def _browser_check() -> dict[str, Any]:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    found = [str(item) for item in candidates if item.exists()]
    if found:
        return check("browser", OK, found[0])
    return check("browser", WARN, "没找到 Chrome/Edge（工作台无需浏览器，但组合渲染需要）")


def _port_check(host: str, port: int) -> dict[str, Any]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        busy = sock.connect_ex((host, port)) == 0
    return check(
        f"port:{port}",
        WARN if busy else OK,
        "已被占用（可能已有服务在跑）" if busy else "空闲",
        fix=f"换个端口：scripts\\start.ps1 -Port {port + 1}" if busy else "",
    )


def _database_check(database_url: str, root: Path | None = None) -> dict[str, Any]:
    from sqlalchemy.engine import make_url

    from backend.scripts.backup_db import describe_source, is_protected

    base = root or PROJECT_ROOT
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return check("database", WARN, f"非 SQLite：{url.get_backend_name()}（这里只详查 SQLite）")
    database = url.database or ""
    if not database or database == ":memory:":
        return check("database", WARN, "内存库")
    path = Path(database)
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        return check(
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
    return check("database", status, detail)


def _disk_check(root: Path | None = None) -> dict[str, Any]:
    usage = shutil.disk_usage(root or PROJECT_ROOT)
    free_gb = usage.free / (1024**3)
    status = OK if free_gb >= 5 else WARN
    return check(
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
        return [check("config", BAD, f"读取配置失败：{error}")]

    rows = []
    try:
        models = get_models_config()
        enabled = [item for item in models.models if item.enabled]
        rows.append(
            check("config:models", OK if enabled else WARN, f"{len(enabled)} 个启用模型 / 共 {len(models.models)}")
        )
        features = get_features_settings()
        rows.append(
            check(
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
        rows.append(check("config:cost", OK, f"当日上限 {cost.daily_cap_cny} 元"))
        routing = get_agent_routing()
        rows.append(check("config:agents", OK, f"{len(routing.agents)} 个角色"))
        pipelines = get_pipelines_config()
        rows.append(check("config:pipelines", OK, f"{len(pipelines.pipelines)} 条流水线"))
    except Exception as error:  # pragma: no cover
        rows.append(check("config", BAD, f"配置校验失败：{error}"))
    return rows


def _provider_check(probe_network: bool) -> dict[str, Any]:
    try:
        from backend.app.capabilities.model.providers import provider_status

        statuses = provider_status()
    except Exception as error:  # pragma: no cover
        return check("providers", WARN, f"读不到服务商状态：{error}")
    ready = [name for name, item in statuses.items() if item.get("state") == "ready"]
    detail = "、".join(f"{name}={item.get('state')}" for name, item in statuses.items()) or "无"
    row = check(
        "providers",
        OK if ready else WARN,
        detail,
        fix="" if ready else "在 .env 里配置对应 *_API_KEY（不要把密钥贴到任何地方）",
    )
    if probe_network and ready:
        row["network_probe"] = "跳过（这里不代你花钱；真要验证用 model_smoke）"
    return row


def _tool_check(root: Path | None = None) -> dict[str, Any]:
    tools_dir = (root or PROJECT_ROOT) / "tools"
    if not tools_dir.exists():
        return check("tools", WARN, "没有 tools/ 目录（FFmpeg/OpenKatana 都在这里）")
    items = [f"{item.name}{'/' if item.is_dir() else ''}" for item in sorted(tools_dir.iterdir())]
    return check("tools", OK, "、".join(items))


def collect_environment(
    *,
    database_url: str = "",
    probe_network: bool = False,
    port: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """跑全部检查，返回 `{ok, counts, database_url, project_root, checks, note}`。

    `port=None` 表示不查端口占用：接口服务自己就占着端口，查出来只会得到
    "已被占用"这种必然结论，反而像故障。
    """

    base = root or PROJECT_ROOT
    checks: list[dict[str, Any]] = []
    checks.append(_python_check())
    checks.extend(_package_check())
    checks.append(_binary_check("ffmpeg"))
    checks.append(_binary_check("ffprobe"))
    checks.append(_project_ffmpeg_check(base))
    checks.append(_font_check())
    checks.append(_browser_check())
    if port is not None:
        checks.append(_port_check("127.0.0.1", port))
    checks.append(_disk_check(base))
    checks.append(_tool_check(base))
    if not database_url:
        try:
            from backend.app.core.config import get_settings

            database_url = get_settings().database_url
        except Exception as error:  # pragma: no cover
            checks.append(check("database", WARN, f"读不到 database_url：{error}"))
            database_url = ""
    if database_url:
        checks.append(_database_check(database_url, base))
    checks.extend(_config_check())
    checks.append(_provider_check(probe_network))

    counts = {OK: 0, WARN: 0, BAD: 0}
    for item in checks:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "ok": counts[BAD] == 0,
        "counts": counts,
        "database_url": database_url,
        "project_root": str(base),
        "checks": checks,
        "note": NOTE,
    }


__all__ = [
    "BAD",
    "NOTE",
    "OK",
    "STATUS_TEXT",
    "WARN",
    "check",
    "collect_environment",
]
