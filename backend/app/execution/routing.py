"""任务路由（M2.0 T05）。

把 ``config/agent_routing.yaml`` 变成可查询的路由表，并提供"三处一致"校验。
运行期只有 ``resolve_agents()`` / ``assert_agent_allowed()`` 会被调用；
校验函数由启动自检（T09）与测试调用。

设计要点（docs/research_hub/agent_architecture.md §4.0）：
- 路由表**只从 YAML 派生**，代码里不再维护第二份"哪个 Agent 能干什么"；
- 未登记的 task_type 一律拒绝（fail-closed），错误码 ``unknown_task_type``；
- PROMPT.md 的声明必须 ⊆ 注册表，防止"文档先行、代码没跟上"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from backend.app.capabilities.tools import TOOL_NAMES
from backend.app.core.config import AgentRoutingConfig, get_agent_routing, get_settings
from backend.app.core.errors import AppError, DomainValidationError
from backend.app.execution.task_types import (
    HUMAN_GATE_TASK_TYPES,
    TASK_TYPES,
    UnknownTaskTypeError,
)

#: PROMPT.md 中声明注册表 task_type 的固定行首
PROMPT_DECLARATION_PREFIX = "本角色在注册表中接受的 task_type"
_BACKTICK_PATTERN = re.compile(r"`([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """一条路由的只读视图。"""

    task_type: str
    display_name: str
    allowed_agents: frozenset[str]
    tools: frozenset[str]
    writes: tuple[str, ...]
    human_gate: bool


def load_routing() -> AgentRoutingConfig:
    return get_agent_routing()


def route_table(routing: AgentRoutingConfig | None = None) -> dict[str, RouteInfo]:
    config = routing or load_routing()
    return {
        name: RouteInfo(
            task_type=name,
            display_name=spec.display_name,
            allowed_agents=frozenset(spec.allowed_agents),
            tools=frozenset(spec.tools),
            writes=tuple(spec.writes),
            human_gate=bool(spec.human_gate),
        )
        for name, spec in config.task_types.items()
    }


def resolve_agents(task_type: str, *, routing: AgentRoutingConfig | None = None) -> frozenset[str]:
    """返回该 task_type 允许的 Agent 集合；未登记或空集合则抛错。"""
    if task_type not in TASK_TYPES:
        raise UnknownTaskTypeError(task_type)
    allowed = (routing or load_routing()).allowed_agents_for(task_type)
    if not allowed:
        raise DomainValidationError(
            f"task_type={task_type!r} 没有登记任何执行 Agent",
            details={"reason": "no_allowed_agent", "task_type": task_type},
        )
    return allowed


def assert_agent_allowed(
    task_type: str,
    agent: str,
    *,
    routing: AgentRoutingConfig | None = None,
) -> None:
    """运行期白名单：Agent 与 task_type 不匹配时抛 422 unknown/denied。"""
    allowed = resolve_agents(task_type, routing=routing)
    if agent not in allowed:
        raise DomainValidationError(
            f"Agent {agent!r} 无权执行 task_type={task_type!r}",
            details={
                "reason": "agent_not_allowed",
                "task_type": task_type,
                "agent": agent,
                "allowed_agents": sorted(allowed),
            },
        )


def prompt_task_types(prompt_path: Path) -> frozenset[str]:
    """从 PROMPT.md 里解析出该角色声明的注册表 task_type。

    约定：文件里有一行以 ``本角色在注册表中接受的 task_type`` 开头，
    其后用反引号列出 task_type。缺这一行视为"未声明"，由校验报错。
    """
    if not prompt_path.is_file():
        return frozenset()
    declared: set[str] = set()
    for line in prompt_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(PROMPT_DECLARATION_PREFIX):
            declared.update(_BACKTICK_PATTERN.findall(line))
    return frozenset(declared)


def validate_registry(routing: AgentRoutingConfig | None = None) -> list[str]:
    """校验三处一致；返回问题描述列表（空列表 = 通过）。"""
    problems: list[str] = []
    config = routing or load_routing()

    yaml_keys = set(config.task_types)
    if yaml_keys != set(TASK_TYPES):
        missing = sorted(set(TASK_TYPES) - yaml_keys)
        extra = sorted(yaml_keys - set(TASK_TYPES))
        if missing:
            problems.append(f"agent_routing.yaml 缺少 task_type: {missing}")
        if extra:
            problems.append(f"task_types.py 缺少 YAML 中登记的 task_type: {extra}")

    gate_in_yaml = {name for name, spec in config.task_types.items() if spec.human_gate}
    if gate_in_yaml != set(HUMAN_GATE_TASK_TYPES):
        problems.append(
            "human_gate 口径不一致：YAML="
            f"{sorted(gate_in_yaml)} 代码={sorted(HUMAN_GATE_TASK_TYPES)}"
        )

    # allowed_agents 必须是真实存在的角色
    from backend.app.services.agent_registry import load_roster

    roster_keys = {spec.key for spec in load_roster()}
    for name, spec in config.task_types.items():
        unknown = sorted(set(spec.allowed_agents) - roster_keys)
        if unknown:
            problems.append(f"{name}.allowed_agents 含未登记角色: {unknown}")
        bad_tools = sorted(set(spec.tools) - TOOL_NAMES)
        if bad_tools:
            problems.append(f"{name}.tools 含未登记工具: {bad_tools}")

    # PROMPT.md 声明必须与 YAML 双向一致
    agents_dir = get_settings().agents_dir
    for agent in sorted(roster_keys):
        prompt = agents_dir / agent / "PROMPT.md"
        declared = prompt_task_types(prompt)
        expected = {name for name, spec in config.task_types.items() if agent in spec.allowed_agents}
        if not declared:
            problems.append(f"{agent}/PROMPT.md 缺少注册表 task_type 声明行")
            continue
        undeclared = sorted(declared - set(TASK_TYPES))
        if undeclared:
            problems.append(f"{agent}/PROMPT.md 声明了未登记的 task_type: {undeclared}")
        mismatch = sorted(declared ^ expected)
        if mismatch:
            problems.append(
                f"{agent}/PROMPT.md 声明与 agent_routing.yaml 不一致: {mismatch}"
            )

    # agent 工具白名单也必须是已登记的工具
    for agent, spec in config.agents.items():
        bad = sorted(set(spec.tools) - TOOL_NAMES)
        if bad:
            problems.append(f"agents.{agent}.tools 含未登记工具: {bad}")

    return problems


def assert_registry_valid(routing: AgentRoutingConfig | None = None) -> None:
    """启动自检用：任一问题即拒绝启动。"""
    problems = validate_registry(routing)
    if problems:
        raise DomainValidationError(
            "task_type 注册表校验失败：" + "；".join(problems),
            details={"reason": "task_type_registry_invalid", "problems": problems},
        )


def is_registry_error(exc: BaseException) -> bool:
    """判断异常是否属于路由/注册表类错误（供上层区分 500 与 422）。"""
    if isinstance(exc, AppError) and isinstance(exc.details, dict):
        return exc.details.get("reason") in {
            "unknown_task_type",
            "agent_not_allowed",
            "no_allowed_agent",
            "task_type_registry_invalid",
        }
    return False


__all__ = [
    "PROMPT_DECLARATION_PREFIX",
    "RouteInfo",
    "assert_agent_allowed",
    "assert_registry_valid",
    "is_registry_error",
    "load_routing",
    "prompt_task_types",
    "resolve_agents",
    "route_table",
    "validate_registry",
]
