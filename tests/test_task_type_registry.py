"""M2.0 T05：task_type 注册表的三处一致性（YAML / 代码 / PROMPT.md / 设计文档）。"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.app.core.config import get_agent_routing, get_settings
from backend.app.core.errors import DomainValidationError
from backend.app.execution.routing import (
    PROMPT_DECLARATION_PREFIX,
    assert_agent_allowed,
    assert_registry_valid,
    is_registry_error,
    prompt_task_types,
    resolve_agents,
    route_table,
    validate_registry,
)
from backend.app.execution.task_types import (
    HUMAN_GATE_TASK_TYPES,
    TASK_TYPES,
    TaskType,
    UnknownTaskTypeError,
    coerce,
    is_human_gate,
    is_registered,
)
from backend.app.services.agent_registry import load_roster

EXPECTED_TASK_TYPES = {
    "topic.discover",
    "research.plan",
    "research.collect",
    "research.extract",
    "research.analyze",
    "research.synthesize",
    "verify.claims",
    "content.script",
    "content.visual",
    "content.produce",
}

#: 只看以这四段开头的反引号字面量，能区分 task_type 与 ``表.列`` 引用
TASK_TYPE_ROOTS = ("topic", "research", "verify", "content")
BACKTICK_PATTERN = re.compile(r"`([a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*)`")


# --- 代码侧 ---------------------------------------------------------------


def test_task_types_are_the_ten_registered_ones():
    assert {member.value for member in TaskType} == EXPECTED_TASK_TYPES
    assert set(TASK_TYPES) == EXPECTED_TASK_TYPES


def test_topic_discover_is_registered():
    """M1.2 修正过的问题：topic.discover 必须真的在注册表里。"""
    assert is_registered("topic.discover") is True


def test_human_gate_task_types():
    assert set(HUMAN_GATE_TASK_TYPES) == {"verify.claims", "content.script", "content.produce"}
    assert is_human_gate("verify.claims") is True
    assert is_human_gate("research.plan") is False


def test_coerce_accepts_str_and_enum_and_rejects_unknown():
    assert coerce("research.plan") is TaskType.RESEARCH_PLAN
    assert coerce(TaskType.RESEARCH_PLAN) is TaskType.RESEARCH_PLAN
    with pytest.raises(UnknownTaskTypeError) as excinfo:
        coerce("research.nonexistent")
    assert excinfo.value.details["reason"] == "unknown_task_type"
    assert excinfo.value.status_code == 422


# --- YAML 与代码 ----------------------------------------------------------


def test_yaml_registry_matches_code_enum():
    routing = get_agent_routing()
    assert set(routing.task_types) == EXPECTED_TASK_TYPES


def test_yaml_human_gate_matches_code():
    routing = get_agent_routing()
    gates = {name for name, spec in routing.task_types.items() if spec.human_gate}
    assert gates == set(HUMAN_GATE_TASK_TYPES)


def test_yaml_task_types_are_allowed_for_registered_roles():
    routing = get_agent_routing()
    roster_keys = {spec.key for spec in load_roster()}
    for name, spec in routing.task_types.items():
        assert spec.allowed_agents, f"{name} 必须登记至少一个执行 Agent"
        unknown = set(spec.allowed_agents) - roster_keys
        assert not unknown, f"{name} 引用了不存在的角色: {sorted(unknown)}"


def test_routing_defaults_to_deny():
    routing = get_agent_routing()
    assert routing.default_deny is True


# --- 运行期路由 -----------------------------------------------------------


def test_resolve_agents_returns_frozenset_of_allowed_agents():
    allowed = resolve_agents("verify.claims")
    assert allowed == frozenset({"fact_checker"})


def test_resolve_agents_unknown_task_type_raises_unknown():
    with pytest.raises(UnknownTaskTypeError):
        resolve_agents("contnt.script")


def test_assert_agent_allowed_passes_for_allowed_agent():
    assert_agent_allowed("content.script", "scriptwriter")


def test_assert_agent_allowed_denies_other_agent():
    with pytest.raises(DomainValidationError) as excinfo:
        assert_agent_allowed("content.script", "fact_checker")
    details = excinfo.value.details
    assert details["reason"] == "agent_not_allowed"
    assert details["allowed_agents"] == ["scriptwriter"]


def test_route_table_exposes_tools_and_writes():
    table = route_table()
    assert set(table) == EXPECTED_TASK_TYPES
    collect = table["research.collect"]
    assert "web_search" in collect.tools
    assert collect.human_gate is False


# --- PROMPT.md 声明 -------------------------------------------------------


def _prompt_path(agent: str) -> pathlib.Path:
    return get_settings().agents_dir / agent / "PROMPT.md"


def test_every_prompt_declares_the_registry_line():
    for spec in load_roster():
        text = _prompt_path(spec.key).read_text(encoding="utf-8")
        assert PROMPT_DECLARATION_PREFIX in text, f"{spec.key}/PROMPT.md 缺少注册表声明行"
        assert prompt_task_types(_prompt_path(spec.key)), f"{spec.key}/PROMPT.md 声明行没有反引号 task_type"


def test_prompt_declarations_match_yaml_exactly():
    routing = get_agent_routing()
    for spec in load_roster():
        declared = prompt_task_types(_prompt_path(spec.key))
        expected = {
            name for name, task in routing.task_types.items() if spec.key in task.allowed_agents
        }
        assert declared == expected, f"{spec.key} 声明与 YAML 不一致"


def test_orchestrator_prompt_no_longer_mentions_old_subtask_names():
    text = _prompt_path("orchestrator").read_text(encoding="utf-8")
    for old in ("plan_research", "integrate_results", "escalate_to_human"):
        assert f"`{old}`" not in text


# --- 全量校验与设计文档 ---------------------------------------------------


def test_registry_is_consistent_everywhere():
    assert validate_registry() == []
    assert_registry_valid()  # 不抛异常


def test_is_registry_error_recognises_route_errors():
    try:
        resolve_agents("nope.nope")
    except DomainValidationError as exc:
        assert is_registry_error(exc) is True
    assert is_registry_error(ValueError("x")) is False


@pytest.mark.parametrize("doc", ["module_design.md", "api_design.md", "agent_architecture.md"])
def test_design_doc_task_type_literals_are_registered(doc: str):
    path = get_settings().root_dir / "docs" / "research_hub" / doc if hasattr(
        get_settings(), "root_dir"
    ) else pathlib.Path("docs/research_hub") / doc
    text = path.read_text(encoding="utf-8")
    literals = {
        token
        for token in BACKTICK_PATTERN.findall(text)
        if token.split(".", 1)[0] in TASK_TYPE_ROOTS
    }
    unknown = sorted(literals - EXPECTED_TASK_TYPES)
    assert not unknown, f"{doc} 引用了未登记的 task_type: {unknown}"
