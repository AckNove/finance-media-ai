"""工具契约登记与统一调度（M2.0 T04）。

**诚实边界**：这里登记 10 个工具的契约、权限元数据与统一信封。
其中 ``parse_pdf`` / ``parse_xlsx`` 已由 ``capabilities.adapters.markitdown_adapter``
提供**真实实现**（本地 MarkItDown，无网络、无模型）；其余 8 个仍只有契约
（``implemented=False``），``ToolRegistry.dispatch()`` 对它们返回
``status="not_implemented"``，绝不返回伪造的成功结果。

未实现 ≠ 不存在：契约（输入/输出/错误码/权限/离线测试方法）已在
``docs/research_hub/tooling_architecture.md`` 中定义，2.1–2.4 阶段按契约实现。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from backend.app.capabilities.guard import ToolSpec, require
from backend.app.core.caller import CallerContext, CallerKind
from backend.app.core.errors import AppError

# 十个工具的规范名（与 config/agent_routing.yaml 中 tools 取值一致）
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "web_search",
        "fetch_url",
        "parse_pdf",
        "parse_xlsx",
        "db_query",
        "db_write",
        "llm_complete",
        "task_update",
        "handoff",
        "fact_check",
    }
)

# 统一结果信封的状态取值
ToolStatus = Literal["ok", "denied", "error", "not_implemented", "not_configured"]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """统一的工具返回信封（所有工具必须返回这个结构）。"""

    status: ToolStatus
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    billable: bool = False
    cost_cny: float | None = None
    model_call_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# 工具能力清单：M2.0 只登记，不实现（implemented=False）
TOOL_SPECS: dict[str, ToolSpec] = {
    "web_search": ToolSpec(
        name="web_search",
        category="retrieval",
        agent_only=True,
        implemented=False,
        notes="需要搜索服务商；默认离线，未配置时返回 not_configured",
    ),
    "fetch_url": ToolSpec(
        name="fetch_url",
        category="retrieval",
        required_scope="sources",
        agent_only=True,
        implemented=False,
        notes="抓取网页并落 source_snapshots；禁止抓取 robots 禁止的页面",
    ),
    "parse_pdf": ToolSpec(
        name="parse_pdf",
        category="parsing",
        required_scope="source_extractions",
        agent_only=True,
        implemented=True,
        notes="已由 capabilities/adapters/markitdown_adapter.py 实现（本地 MarkItDown，"
        "无网络）；产出 source_extractions 的 pdf_text，带字符偏移基准与字节数",
    ),
    "parse_xlsx": ToolSpec(
        name="parse_xlsx",
        category="parsing",
        required_scope="source_extractions",
        agent_only=True,
        implemented=True,
        notes="已由 capabilities/adapters/markitdown_adapter.py 实现（xlsx/xls/csv/tsv → "
        "spreadsheet_rows）；不允许把摘要当证据",
    ),
    "db_query": ToolSpec(
        name="db_query",
        category="storage",
        implemented=False,
        notes="只读查询；等价能力目前散在服务层，尚无统一的工具实现",
    ),
    "db_write": ToolSpec(
        name="db_write",
        category="storage",
        required_scope="evidence",
        agent_only=True,
        implemented=False,
        notes="写操作必须过 CallerContext 的 db_scopes，禁止写 model_calls 金额字段",
    ),
    "llm_complete": ToolSpec(
        name="llm_complete",
        category="model",
        agent_only=True,
        implemented=False,
        notes="P0-2：必须先 reserve_call() 再发请求；价目缺失按 Q8=B 直接拒绝",
    ),
    "task_update": ToolSpec(
        name="task_update",
        category="orchestration",
        required_scope="research_tasks",
        agent_only=True,
        implemented=False,
        notes="推进子任务状态；非法迁移返回 422 并带 details.allowed",
    ),
    "handoff": ToolSpec(
        name="handoff",
        category="orchestration",
        implemented=False,
        notes="五型交接：request_subtask/request_revision/need_human/"
        "insufficient_evidence/request_asset",
    ),
    "fact_check": ToolSpec(
        name="fact_check",
        category="verification",
        required_scope="evidence",
        agent_only=True,
        implemented=False,
        notes="核对引文与原文快照一致性（quote_hash），不可搜索新资料",
    ),
}

assert set(TOOL_SPECS) == TOOL_NAMES, "TOOL_SPECS 与 TOOL_NAMES 必须一一对应"


Provider = Callable[..., ToolResult]


class ToolRegistry:
    """工具的统一入口：先过权限闸门，再分发给已注册的实现。"""

    def __init__(self, providers: Mapping[str, Provider] | None = None) -> None:
        self._providers: dict[str, Provider] = dict(providers or {})

    def register(self, name: str, provider: Provider) -> None:
        if name not in TOOL_SPECS:
            raise ValueError(f"未登记的工具名: {name}")
        self._providers[name] = provider

    def is_implemented(self, name: str) -> bool:
        return name in self._providers

    def dispatch(self, caller: CallerContext, tool: str, **arguments: Any) -> ToolResult:
        """统一调度：未知工具 → error；越权 → denied；未实现 → not_implemented。

        注意：M2.0 **不写** ``tool_calls`` 表（写空行等于伪造审计记录）。
        真正的落库在 2.4 阶段与执行引擎一起上线。
        """
        spec = TOOL_SPECS.get(tool)
        if spec is None:
            return ToolResult(
                status="error",
                error_code="unknown_tool",
                error_message=f"未登记的工具: {tool}",
            )
        try:
            require(caller, spec)
        except AppError as exc:
            reason = None
            if isinstance(exc.details, dict):
                reason = exc.details.get("reason")
            return ToolResult(
                status="denied",
                error_code=str(reason or "permission_denied"),
                error_message=exc.message,
            )
        provider = self._providers.get(tool)
        if provider is None:
            return ToolResult(
                status="not_implemented",
                error_code="not_implemented",
                error_message=f"工具 {tool} 只有契约，M2.0 未实现",
            )
        return provider(caller=caller, **arguments)


_default_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """进程内默认注册表。

    首次创建时挂上**已真实实现**的适配器（当前只有 MarkItDown 解析器）；
    其余工具保持"未实现"，dispatch 返回 ``not_implemented``。
    适配器的导入放在函数内部，避免 ``adapters`` ↔ ``tools`` 的循环导入。
    """
    global _default_registry
    if _default_registry is None:
        registry = ToolRegistry()
        from backend.app.capabilities.adapters import register_parser_adapters

        register_parser_adapters(registry)
        _default_registry = registry
    return _default_registry


__all__ = [
    "TOOL_NAMES",
    "TOOL_SPECS",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "get_tool_registry",
]
