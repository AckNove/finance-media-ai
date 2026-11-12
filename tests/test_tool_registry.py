"""M2.0 T04：工具契约登记、权限闸门与"未实现就说不"的诚实性。

最重要的两个断言：
- ``test_only_parser_tools_are_claimed_as_implemented``：声明已实现的工具（解析器 +
  ``llm_complete``）必须真的能在默认注册表里派发，其余七个不许注册；
- ``test_dispatch_returns_not_implemented_for_registered_tool``：没有实现时返回
  ``not_implemented``，绝不返回伪造的成功结果。
"""

from __future__ import annotations

import pytest

from backend.app.capabilities.guard import ToolSpec, require
from backend.app.capabilities.tools import (
    TOOL_NAMES,
    TOOL_SPECS,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
)
from backend.app.core.caller import CallerContext, CallerKind
from backend.app.core.errors import PermissionDeniedError


def agent_caller(agent: str = "financial_researcher", *, scopes: set[str] | None = None) -> CallerContext:
    return CallerContext.for_executor(
        agent=agent,
        task_id="task-1",
        attempt_token="tok-1",
        db_scopes=scopes if scopes is not None else {"sources", "evidence"},
    )


# --- 契约登记 -------------------------------------------------------------


def test_tool_specs_cover_all_tool_names():
    assert set(TOOL_SPECS) == TOOL_NAMES
    assert len(TOOL_NAMES) == 10


def test_only_parser_tools_are_claimed_as_implemented():
    """诚实边界：声明"已实现"的工具必须真的能在默认注册表里派发。

    M2.0 交付时十个工具全部 ``implemented=False``；随后 MarkItDown 适配器让
    ``parse_pdf`` / ``parse_xlsx`` 变成真实现，V5.0 M2 让 ``llm_complete``
    接到统一模型网关，V5.0 M3 又让 ``web_search`` / ``fetch_url`` 接到真实
    搜索与抓取，其余五个仍然没有。
    这条断言的两半都必须成立，防止有人只改标记不改实现（或反之）。
    """
    claimed = sorted(name for name, spec in TOOL_SPECS.items() if spec.implemented)
    assert claimed == [
        "db_query",
        "db_write",
        "fact_check",
        "fetch_url",
        "handoff",
        "llm_complete",
        "parse_pdf",
        "parse_xlsx",
        "task_update",
        "web_search",
    ], f"声称已实现的工具集合变了: {claimed}"

    registry = get_tool_registry()
    assert sorted(name for name in TOOL_NAMES if registry.is_implemented(name)) == claimed
    for name, spec in TOOL_SPECS.items():
        if name not in claimed:
            assert registry.is_implemented(name) is False, f"{name} 没有实现却被注册了"


def test_every_tool_declares_category_and_notes():
    for name, spec in TOOL_SPECS.items():
        assert spec.name == name
        assert spec.category, f"{name} 缺少分类"
        assert spec.notes, f"{name} 缺少说明（需求/边界）"


def test_write_tools_declare_required_scope():
    for name in ("fetch_url", "db_write", "parse_pdf", "parse_xlsx", "task_update", "fact_check"):
        assert TOOL_SPECS[name].required_scope, f"{name} 必须声明 required_scope"


# --- guard ----------------------------------------------------------------


def test_guard_allows_human_to_use_read_tool():
    spec = ToolSpec(name="db_query", category="storage")
    require(CallerContext.human(), spec)  # 不抛异常即通过


def test_guard_denies_agent_only_tool_for_human():
    spec = TOOL_SPECS["web_search"]
    with pytest.raises(PermissionDeniedError) as excinfo:
        require(CallerContext.human(), spec)
    assert excinfo.value.details["reason"] == "agent_only"


def test_guard_denies_tool_outside_agent_whitelist():
    # fact_checker 的白名单里没有 web_search
    with pytest.raises(PermissionDeniedError) as excinfo:
        require(agent_caller("fact_checker", scopes={"evidence"}), TOOL_SPECS["web_search"])
    assert excinfo.value.details["reason"] == "tool_not_in_whitelist"


def test_guard_denies_missing_db_scope():
    with pytest.raises(PermissionDeniedError) as excinfo:
        require(agent_caller("financial_researcher", scopes={"sources"}), TOOL_SPECS["db_write"])
    assert excinfo.value.details["reason"] == "scope_denied"


def test_guard_allows_whitelisted_tool_with_scope():
    require(agent_caller("financial_researcher", scopes={"evidence"}), TOOL_SPECS["db_write"])


# --- registry -------------------------------------------------------------


def test_dispatch_unknown_tool_is_error():
    result = get_tool_registry().dispatch(CallerContext.human(), "no_such_tool")
    assert result.status == "error"
    assert result.error_code == "unknown_tool"
    assert result.ok is False


def test_dispatch_returns_denied_for_out_of_whitelist_tool():
    result = get_tool_registry().dispatch(agent_caller("scriptwriter", scopes={"scripts"}), "web_search")
    assert result.status == "denied"
    assert result.error_code == "tool_not_in_whitelist"


def test_dispatch_returns_not_implemented_for_tool_without_provider():
    """登记了契约但注册表里没挂实现 → not_implemented（不是 ok、不是 error）。

    默认注册表现在把十个工具都挂上了，所以这里用一个空注册表来验证"没实现就是
    没实现"，而不是靠某个具体工具还欠着。
    """
    result = ToolRegistry().dispatch(CallerContext.human(), "db_query")
    assert result.status == "not_implemented"
    assert result.error_code == "not_implemented"
    assert result.data is None


def test_registry_rejects_registering_unknown_tool_name():
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register("made_up_tool", lambda **_: ToolResult(status="ok"))


def test_registry_dispatches_to_registered_provider():
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    def fake_db_query(*, caller: CallerContext, **kwargs: object) -> ToolResult:
        calls.append({"caller": caller.name, **kwargs})
        return ToolResult(status="ok", data={"rows": []})

    registry.register("db_query", fake_db_query)
    assert registry.is_implemented("db_query") is True
    result = registry.dispatch(CallerContext.human(), "db_query", table="topics")
    assert result.ok is True
    assert result.data == {"rows": []}
    assert calls == [{"caller": "local-operator", "table": "topics"}]


def test_default_registry_matches_declared_implementations():
    """默认注册表里能派发的工具，必须恰好等于 ``implemented=True`` 的那些。"""
    registry = get_tool_registry()
    implemented = sorted(name for name in TOOL_NAMES if registry.is_implemented(name))
    assert implemented == [
        "db_query",
        "db_write",
        "fact_check",
        "fetch_url",
        "handoff",
        "llm_complete",
        "parse_pdf",
        "parse_xlsx",
        "task_update",
        "web_search",
    ]


def test_parser_tool_reaches_real_adapter_through_default_registry():
    """经默认注册表真派发一次解析（权限用 agent + source_extractions 域）。"""
    from pathlib import Path

    sample = Path(__file__).parent / "fixtures"
    target = sample / "sample_table.csv"
    if not target.is_file():  # 夹具缺失时不让测试假装通过
        pytest.skip(f"缺少测试夹具: {target}")
    result = get_tool_registry().dispatch(
        agent_caller("financial_researcher", scopes={"source_extractions"}),
        "parse_xlsx",
        path=str(target),
    )
    assert result.status == "ok", result.error_message
    assert result.data["parser"] == "markitdown"
    assert result.data["kind"] == "spreadsheet_rows"
