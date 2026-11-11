"""工具权限闸门（M2.0 T04）。

三层权限（见 docs/research_hub/tooling_architecture.md §1.3）：
1. **工具白名单**：agent 能调用哪些工具由 ``config/agent_routing.yaml`` 的
   ``agents.<key>.tools`` 决定；未登记即拒绝（fail-closed）。
2. **数据域**：``db_scopes`` 限定可写的表域；空 scopes 的 agent 连读都会被拒。
3. **实例权限**：human 才能做的动作（立项、审批、发布、暂停……）由
   端点 ``require_caller(CallerKind.HUMAN)`` 强制，工具层不再重复判断。

编排器纪律：本模块只做判定，不做 I/O；真正的强制执行点是
``backend/app/capabilities/tools.py`` 的 ``ToolRegistry.dispatch()``，
不允许各工具在自己内部各写一遍权限判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.core.caller import CallerContext, CallerKind
from backend.app.core.config import AgentRoutingConfig, get_agent_routing
from backend.app.core.errors import PermissionDeniedError


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个工具的权限元数据。"""

    name: str
    category: str
    # 写类工具需要 caller 拥有对应数据域
    required_scope: str | None = None
    # 是否只允许 agent 身份调用（human 不应代替 Agent 执行研究动作）
    agent_only: bool = False
    # 是否已真实实现（M2.0 全部为 False，只能是"契约已登记"）
    implemented: bool = False
    notes: str | None = None
    allowed_kinds: frozenset[CallerKind] = field(
        default_factory=lambda: frozenset({CallerKind.HUMAN, CallerKind.AGENT, CallerKind.DEV})
    )


def require(
    caller: CallerContext,
    spec: ToolSpec,
    *,
    routing: AgentRoutingConfig | None = None,
) -> None:
    """校验调用者是否可以使用该工具；不通过则抛 PermissionDeniedError。"""
    if caller.kind not in spec.allowed_kinds:
        raise PermissionDeniedError(
            f"{caller.kind.value} 身份不能调用工具 {spec.name}",
            required=sorted(kind.value for kind in spec.allowed_kinds),
            caller=caller.kind.value,
        )

    if spec.agent_only and caller.kind is not CallerKind.AGENT:
        raise PermissionDeniedError(
            f"工具 {spec.name} 只能由执行层内的 Agent 调用",
            required=[CallerKind.AGENT.value],
            caller=caller.kind.value,
            reason="agent_only",
        )

    # 工具白名单与数据域只约束 agent；human/dev 是操作者本人，不受白名单限制
    if caller.kind is CallerKind.AGENT:
        routing = routing or get_agent_routing()
        if spec.name not in routing.tools_for(caller.name):
            raise PermissionDeniedError(
                f"Agent {caller.name} 未获授权使用工具 {spec.name}",
                caller=caller.kind.value,
                reason="tool_not_in_whitelist",
            )
        if spec.required_scope and not caller.has_scope(spec.required_scope):
            raise PermissionDeniedError(
                f"Agent {caller.name} 缺少数据域 {spec.required_scope}",
                caller=caller.kind.value,
                reason="scope_denied",
            )


__all__ = ["ToolSpec", "require"]
