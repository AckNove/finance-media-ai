"""启动预检：把「能不能跑」说清楚，别让它在运行到一半时才失败。

分三档输出：

* ``ok``      —— 现在就是好的；
* ``blocked`` —— 缺外部条件（密钥、二进制），相关能力会明确标 BLOCKED，不会伪造；
* ``error``   —— 配置或代码层面真的很糟糕，需要修。

退出码：0 表示没有 error（blocked 不算失败，因为它是如实告知）；1 表示有 error。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

Status = str


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "hint": self.hint}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: Status, detail: str, hint: str = "") -> None:
        self.checks.append(Check(name=name, status=status, detail=detail, hint=hint))

    @property
    def errors(self) -> list[Check]:
        return [item for item in self.checks if item.status == "error"]

    @property
    def blocked(self) -> list[Check]:
        return [item for item in self.checks if item.status == "blocked"]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": not self.errors,
            "checks": [item.as_dict() for item in self.checks],
            "blocked": [item.name for item in self.blocked],
            "errors": [item.name for item in self.errors],
        }


def check_config(report: Report) -> None:
    try:
        from backend.app.core.config import (
            get_agent_routing,
            get_cost_settings,
            get_execution_settings,
            get_features_settings,
            get_models_config,
            get_pipelines_config,
        )

        features = get_features_settings()
        cost = get_cost_settings()
        execution = get_execution_settings()
        models = get_models_config()
        routing = get_agent_routing()
        pipelines = get_pipelines_config()
    except Exception as error:  # noqa: BLE001 - 预检要能报出任何配置炸法
        report.add("config", "error", f"配置解析失败：{error}", "检查 config/*.yaml 的字段拼写")
        return

    report.add(
        "config",
        "ok",
        f"features(model={features.model}, search={features.search}, execution={execution.enabled})；"
        f"cost.daily_cap_cny={cost.daily_cap_cny}；models={len(models.models)}；pipelines={len(pipelines.pipelines)}",
    )
    if not any(entry.enabled for entry in models.models):
        report.add(
            "models",
            "blocked",
            "config/models.yaml 里没有启用任何模型，模型阶段会标 BLOCKED",
            "把要用的模型 enabled 设为 true 并填上价目（未知价格必须阻止请求）",
        )
    missing_price = [f"{item.provider}/{item.model}" for item in models.models if item.enabled and item.pricing_status != "known"]
    if missing_price:
        report.add(
            "pricing",
            "blocked",
            "以下模型缺价目，调用会被拒绝：" + ", ".join(missing_price),
            "在 config/models.yaml 补 input/output_price_per_1k_cny",
        )
    if not routing.task_types:
        report.add("routing", "error", "config/agent_routing.yaml 没有 task_types", "恢复该文件")


def check_providers(report: Report) -> None:
    try:
        from backend.app.capabilities.model.providers import provider_status

        statuses = provider_status()
    except Exception as error:  # noqa: BLE001
        report.add("providers", "error", f"服务商状态读取失败：{error}")
        return
    ready = [name for name, entry in statuses.items() if entry.get("status") == "ready"]
    detail = ", ".join(f"{name}={entry.get('status')}" for name, entry in statuses.items())
    if ready:
        report.add("providers", "ok", f"可用服务商：{', '.join(ready)}；{detail}")
    else:
        report.add(
            "providers",
            "blocked",
            f"没有可用服务商（{detail}）",
            "在 .env 里配 DEEPSEEK_API_KEY / OPENAI_API_KEY，或在 config/models.yaml 启用 local 指向本机模型",
        )


def check_toolchain(report: Report) -> None:
    try:
        from backend.app.capabilities.media.ffmpeg import probe_capabilities, resolve_toolchain

        toolchain = resolve_toolchain()
        if not toolchain.available:
            report.add("ffmpeg", "blocked", "没有找到 ffmpeg", "运行 python -m backend.scripts.install_ffmpeg --verify")
            return
        capabilities = probe_capabilities(toolchain)
        if capabilities.ok:
            report.add("ffmpeg", "ok", f"{toolchain.source} {toolchain.version_text}")
        else:
            missing = ", ".join(capabilities.missing_required)
            report.add(
                "ffmpeg",
                "error",
                f"ffmpeg {toolchain.version_text} 缺少必需能力：{missing}",
                "固定使用 tools/ffmpeg 的 8.1.2 构建",
            )
    except Exception as error:  # noqa: BLE001
        report.add("ffmpeg", "blocked", f"ffmpeg 检查失败：{error}")


def check_tts(report: Report) -> None:
    try:
        from backend.app.capabilities.tts import available_providers

        providers = available_providers()
    except Exception as error:  # noqa: BLE001
        report.add("tts", "blocked", f"语音后端检查失败：{error}")
        return
    if any(providers.values()):
        report.add("tts", "ok", ", ".join(f"{name}={value}" for name, value in providers.items()))
    else:
        report.add("tts", "blocked", "没有可用的语音后端", "pip install edge-tts==7.3.1 或使用 Windows SAPI")


def check_database(report: Report) -> None:
    try:
        from sqlalchemy import inspect, text

        from backend.app.core.config import get_settings
        from backend.app.database.session import get_engine

        settings = get_settings()
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            names = set(inspect(connection).get_table_names())
        expected = {"research_tasks", "research_subtasks", "task_events", "sources", "research_reports", "evidence", "model_calls"}
        missing = sorted(expected - names)
        if missing:
            report.add(
                "database",
                "error",
                f"{settings.database_url} 缺表：{', '.join(missing)}",
                "对工作库跑 alembic upgrade head（不要对 data/finance_media.db 跑）",
            )
        else:
            report.add("database", "ok", f"{settings.database_url}（{len(names)} 张表）")
    except Exception as error:  # noqa: BLE001
        report.add("database", "error", f"数据库不可用：{error}")


def check_startup_checks(report: Report) -> None:
    try:
        from backend.app.core.startup_checks import run_startup_checks
        from backend.app.main import create_app

        result = run_startup_checks(create_app())
    except Exception as error:  # noqa: BLE001
        report.add("startup", "error", f"启动自检本身失败：{error}")
        return
    if result.ok:
        report.add("startup", "ok", result.summary())
    else:
        report.add("startup", "error", result.summary())


def run_preflight() -> Report:
    report = Report()
    check_config(report)
    check_database(report)
    check_providers(report)
    check_toolchain(report)
    check_tts(report)
    check_startup_checks(report)
    return report


def _print_text(report: Report) -> None:
    marks = {"ok": "OK     ", "blocked": "BLOCKED", "error": "ERROR  "}
    for check in report.checks:
        print(f"[{marks.get(check.status, check.status)}] {check.name}: {check.detail}")
        if check.hint:
            print(f"           → {check.hint}")
    if report.blocked:
        print("\n阻塞项（服务会照常启动，但对应能力会如实标 BLOCKED，不会伪造结果）：")
        for check in report.blocked:
            print(f"  - {check.name}: {check.detail}")
    if report.errors:
        print("\n错误项（需要修）：")
        for check in report.errors:
            print(f"  - {check.name}: {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="finance-media-ai 启动预检")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    arguments = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 老终端不支持就算了
        pass

    report = run_preflight()
    if arguments.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
