"""启动自检：宁可启动失败，也不要带着不一致的配置运行。

设计依据：``docs/research_hub/agent_architecture.md`` §4.0（三处校验）与
``docs/research_hub/m2_0_implementation_plan.md`` T09。

检查分两类：

- **error**：发现即拒绝启动（配置互相矛盾、注册表不一致、端点漏声明权限）；
- **warning**：只记录（比如数据库当前 revision 落后——开发中很常见）。

刻意不做的事：不在这里迁移数据库、不在这里写任何数据、不在这里发起网络请求。
启动自检必须是**只读且无副作用**的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI

from backend.app.api.router import ROUTE_CALLERS
from backend.app.capabilities.tools import TOOL_NAMES, TOOL_SPECS
from backend.app.core.caller import CallerKind
from backend.app.core.config import (
    Settings,
    get_agent_routing,
    get_cost_settings,
    get_execution_settings,
    get_features_settings,
    get_models_config,
    get_pipelines_config,
)
from backend.app.core.logging import get_logger
from backend.app.execution.routing import validate_registry
from backend.app.execution.task_types import TASK_TYPES
from backend.app.services.agent_registry import load_roster, roster_keys_missing_prompts

logger = get_logger(__name__)

Severity = Literal["error", "warning"]


class StartupCheckError(RuntimeError):
    """自检出现 error 级问题时抛出，交由进程启动失败处理。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("启动自检失败：" + "；".join(self.problems))


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    severity: Severity
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StartupReport:
    results: tuple[CheckResult, ...]

    @property
    def errors(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if not r.ok and r.severity == "error")

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if not r.ok and r.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.ok)
        return (
            f"{passed}/{len(self.results)} 项通过"
            f"，error={len(self.errors)}，warning={len(self.warnings)}"
        )


def _check_config_sections() -> CheckResult:
    """六份结构化配置必须能按 schema 解析（缺段/类型错会直接抛异常）。"""
    try:
        cost = get_cost_settings()
        execution = get_execution_settings()
        features = get_features_settings()
        models = get_models_config()
        routing = get_agent_routing()
        pipelines = get_pipelines_config()
    except Exception as exc:  # noqa: BLE001 - 自检要报告任意配置异常
        return CheckResult("config_sections", False, "error", f"{type(exc).__name__}: {exc}")
    detail = (
        f"daily_cap={cost.daily_cap_cny} 并发={execution.max_concurrency} "
        f"features={features.model}/{features.search}/{features.execution} "
        f"models={len(models.models)} tasks={len(routing.task_types)} "
        f"pipelines={len(pipelines.pipelines)}"
    )
    return CheckResult("config_sections", True, "error", detail)


def _check_task_type_registry() -> CheckResult:
    """task_type 三处注册表必须一致（YAML / 代码 / 九个 PROMPT.md）。"""
    problems = validate_registry()
    if problems:
        return CheckResult("task_type_registry", False, "error", " | ".join(problems))
    return CheckResult(
        "task_type_registry", True, "error", f"{len(TASK_TYPES)} 个 task_type 三处一致"
    )


def _check_agent_roster() -> CheckResult:
    """九个角色的提示词文件必须存在（不存在的角色无法开工）。"""
    try:
        roster = load_roster()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("agent_roster", False, "error", f"{type(exc).__name__}: {exc}")
    missing = roster_keys_missing_prompts()
    if missing:
        return CheckResult("agent_roster", False, "error", f"缺少提示词文件：{sorted(missing)}")
    return CheckResult("agent_roster", True, "error", f"{len(roster)} 个角色，提示词齐全")


def _check_tool_specs() -> CheckResult:
    """工具名清单与工具规格必须一一对应（多一个少一个都是漏登记）。"""
    if set(TOOL_NAMES) != set(TOOL_SPECS):
        diff = sorted(set(TOOL_NAMES) ^ set(TOOL_SPECS))
        return CheckResult("tool_specs", False, "error", f"工具名与规格不一致：{diff}")
    return CheckResult("tool_specs", True, "error", f"{len(TOOL_NAMES)} 个工具已登记")


@dataclass(frozen=True)
class RouteInfo:
    """一个真实存在的 HTTP 路由，以及它**实际挂载**的调用者要求。"""

    methods: tuple[str, ...]
    path: str
    allowed: frozenset[CallerKind] | None
    conflicts: tuple[str, ...] = ()


def iter_api_routes(app: FastAPI) -> list[RouteInfo]:
    """遍历真实的 ``APIRoute``（穿透 ``_IncludedRouter`` 包装），带上前缀。

    为什么不用 ``app.routes`` 直接过滤：Starlette 1.10 把 ``include_router``
    的结果存成 ``_IncludedRouter``，真正的 ``APIRoute`` 藏在
    ``original_router.routes`` 里，且前缀需要自己累加。
    这里走的是运行时真正被匹配的对象，因此能验证"依赖到底挂没挂上"。
    """
    from fastapi.routing import APIRoute, APIRouter

    from backend.app.core.caller import allowed_kinds_of

    def walk(routes, prefix: str, acc: list[RouteInfo]) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                found: set[frozenset[CallerKind]] = set()
                names: list[str] = []

                def collect(dependant) -> None:
                    for dependency in dependant.dependencies:
                        call = getattr(dependency, "call", None)
                        declared = allowed_kinds_of(call)
                        if declared is not None:
                            found.add(declared)
                            names.append(getattr(call, "__name__", "require_caller"))
                        collect(dependency)

                collect(route.dependant)
                conflicts = tuple(sorted(names)) if len(found) > 1 else ()
                acc.append(
                    RouteInfo(
                        methods=tuple(sorted(route.methods or ())),
                        path=f"{prefix}{route.path}",
                        allowed=next(iter(found)) if len(found) == 1 else None,
                        conflicts=conflicts,
                    )
                )
            elif type(route).__name__ == "_IncludedRouter":
                context = getattr(route, "include_context", None)
                piece = getattr(context, "prefix", "") or ""
                inner = getattr(route, "original_router", None)
                if inner is not None:
                    walk(inner.routes, f"{prefix}{piece}", acc)
            elif isinstance(route, APIRouter):
                walk(route.routes, f"{prefix}{route.prefix or ''}", acc)

    collected: list[RouteInfo] = []
    walk(app.routes, "", collected)
    return collected


def _check_route_callers(app: FastAPI) -> CheckResult:
    """三件事必须同时成立，少一件就是"看起来有权限、实际没有"。

    1. 每个真实路由都能在 ``ROUTE_CALLERS`` 里找到归属（或显式登记为豁免）；
    2. **端点上真的挂了** ``require_caller(...)``，且允许集合与清单完全一致
       ——只对清单不对挂载，会漏掉"清单写了但忘了挂依赖"这种最危险的错；
    3. OpenAPI 里出现的操作与真实路由一致（防止绕过路由走查）。

    顺便解释为什么不用 ``app.openapi()`` 单独做判定：OpenAPI 看不到
    ``include_in_schema=False`` 的端点（例如根路径身份横幅），
    也看不到端点上挂了什么依赖。
    """
    from backend.app.api.router import NON_API_ROUTES, PUBLIC

    problems: list[str] = []
    routes = iter_api_routes(app)
    seen: dict[tuple[str, str], RouteInfo] = {}

    for route in routes:
        for method in route.methods:
            key = (method, route.path)
            seen[key] = route

    declared = dict(ROUTE_CALLERS)
    for key, route in seen.items():
        expected = declared.get(key)
        if route.conflicts:
            problems.append(f"{key[0]} {key[1]} 挂了多个 require_caller：{list(route.conflicts)}")
            continue
        if key in NON_API_ROUTES:
            if route.allowed is not None:
                problems.append(f"{key[0]} {key[1]} 是豁免端点，不应挂 require_caller")
            continue
        if expected is None:
            problems.append(f"{key[0]} {key[1]} 未声明调用者（新增端点必须登记 ROUTE_CALLERS）")
            continue
        if expected == PUBLIC:
            if route.allowed is not None:
                problems.append(
                    f"{key[0]} {key[1]} 清单标为公开，却挂了 require_caller"
                    f"({sorted(k.value for k in route.allowed)})"
                )
            continue
        if route.allowed is None:
            problems.append(
                f"{key[0]} {key[1]} 清单要求"
                f"{sorted(k.value for k in expected)}，但端点上没有挂 require_caller（漏声明权限）"
            )
            continue
        if route.allowed != expected:
            problems.append(
                f"{key[0]} {key[1]} 清单要求"
                f"{sorted(k.value for k in expected)}，端点实际"
                f"{sorted(k.value for k in route.allowed)}"
            )

    stale = sorted(set(declared) - set(seen))
    if stale:
        problems.append(f"声明了不存在的端点：{stale}")
    missing_from_walk = sorted(set(declared) - set(seen))

    schema_ops = {
        (method.upper(), path)
        for path, operations in app.openapi().get("paths", {}).items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }
    unseen_schema = sorted(schema_ops - set(seen))
    if unseen_schema:
        problems.append(f"OpenAPI 里的操作未出现在路由走查中：{unseen_schema}")

    if problems:
        return CheckResult("route_callers", False, "error", "；".join(problems))

    if missing_from_walk:  # pragma: no cover - 仅在极端不一致时触发
        return CheckResult("route_callers", False, "error", f"清单未覆盖：{missing_from_walk}")
    protected = sum(1 for key, value in declared.items() if value)
    return CheckResult(
        "route_callers",
        True,
        "error",
        f"{len(declared)} 个 API 操作均已挂载并声明调用者（受保护 {protected} 个，"
        f"公开 {len(declared) - protected} 个，另 {len(NON_API_ROUTES)} 个豁免端点）",
    )



def _check_pricing(settings: Settings) -> CheckResult:
    """价目缺失不是启动阻塞项（Q8=B 已经决定在调用时拒绝），但必须被看见。"""
    models = get_models_config()
    missing = [
        f"{m.provider}/{m.model}"
        for m in models.models
        if m.pricing_status != "known"
    ]
    if not missing:
        return CheckResult("pricing", True, "warning", "全部模型价目已知")
    policy = get_cost_settings().pricing_policy
    return CheckResult(
        "pricing",
        False,
        "warning",
        f"价目缺失={missing}，pricing_policy={policy}（调用会被直接拒绝，不会按 0 元记账）",
    )


def _check_database(settings: Settings) -> CheckResult:
    """数据库只读探测：能不能连、有多少张表。连不上只是 warning。

    刻意不比较 alembic revision：开发库可能本来就落后于 head，
    那属于操作问题，不属于启动阻塞项（迁移由 ``alembic upgrade head`` 负责）。
    """
    from sqlalchemy import inspect

    from backend.app.database.session import build_engine

    try:
        engine = build_engine(settings)
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
                tables = inspect(conn).get_table_names()
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("database", False, "warning", f"无法连接：{type(exc).__name__}: {exc}")
    return CheckResult("database", True, "warning", f"可连接，{len(tables)} 张表")


def run_startup_checks(app: FastAPI, settings: Settings | None = None) -> StartupReport:
    """跑完整自检并返回报告（不抛异常，便于测试逐项断言）。"""
    settings = settings or app.state.settings
    results = (
        _check_config_sections(),
        _check_task_type_registry(),
        _check_agent_roster(),
        _check_tool_specs(),
        _check_route_callers(app),
        _check_pricing(settings),
        _check_database(settings),
    )
    report = StartupReport(results)
    for result in results:
        if result.ok:
            logger.debug("启动自检通过 %s：%s", result.name, result.detail)
        elif result.severity == "error":
            logger.error("启动自检失败 %s：%s", result.name, result.detail)
        else:
            logger.warning("启动自检告警 %s：%s", result.name, result.detail)
    return report


def assert_startup_ok(report: StartupReport) -> None:
    """error 级问题直接让进程起不来——带病运行比启动失败更危险。"""
    if not report.ok:
        raise StartupCheckError([f"{r.name}: {r.detail}" for r in report.errors])


__all__ = [
    "CheckResult",
    "RouteInfo",
    "StartupCheckError",
    "StartupReport",
    "assert_startup_ok",
    "iter_api_routes",
    "run_startup_checks",
]
