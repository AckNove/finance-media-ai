"""能力层（capabilities，M2.0 T04）。

职责边界：Agent 想"做事"，只能通过这里的工具；工具自己不做权限判断，
统一由 ``guard.require()`` 在 ``ToolRegistry.dispatch()`` 入口处强制。

模块：
- ``guard``：权限元数据与判定；
- ``tools``：工具契约登记、统一结果信封与调度；
- ``budget``：成本闸门的纯函数判定（含 Q8=B 价目缺失即拒绝）。

M2.0 状态：**契约与闸门就绪，工具实现为零**。
"""

from __future__ import annotations

from backend.app.capabilities.budget import BudgetDecision, evaluate, resolve_price
from backend.app.capabilities.guard import ToolSpec, require
from backend.app.capabilities.tools import (
    TOOL_NAMES,
    TOOL_SPECS,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
)

__all__ = [
    "BudgetDecision",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "evaluate",
    "get_tool_registry",
    "require",
    "resolve_price",
]
