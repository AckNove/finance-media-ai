"""M2.0 T02：分层配置读取与校验。

覆盖：
- 四个新 YAML（models / agent_routing / pipelines + settings 新增段）能被类型化读取；
- 非法配置在**加载期**就被拒绝（extra 字段、越界值、悬空引用）；
- 价目缺失必须表现为 ``missing``，绝不退化成 0。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.core.config import (
    get_agent_routing,
    get_cost_settings,
    get_execution_settings,
    get_features_settings,
    get_models_config,
    get_pipelines_config,
    get_settings_section,
)
from backend.app.core.config_schema import (
    AgentRoutingConfig,
    CostSettings,
    ExecutionSettings,
    ModelsConfig,
    PipelineSpec,
    PipelineStage,
)
from backend.app.execution.task_types import HUMAN_GATE_TASK_TYPES, TASK_TYPES

# --- cost -----------------------------------------------------------------


def test_cost_settings_loaded_from_yaml():
    cost = get_cost_settings()
    assert cost.currency == "CNY"
    assert cost.pricing_policy == "reject_unknown"
    assert cost.daily_cap_cny == pytest.approx(5.0)
    assert cost.default_task_calls == 60
    assert cost.default_task_cost_cny <= cost.daily_cap_cny


def test_cost_settings_rejects_task_budget_above_daily_cap():
    with pytest.raises(ValidationError):
        CostSettings(
            currency="CNY",
            pricing_policy="reject_unknown",
            daily_cap_cny=1.0,
            default_task_calls=10,
            default_task_cost_cny=2.0,
            unknown_price_upper_bound_cny=0.05,
        )


def test_cost_settings_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        CostSettings(
            currency="CNY",
            pricing_policy="reject_unknown",
            daily_cap_cny=5.0,
            default_task_calls=10,
            default_task_cost_cny=1.0,
            unknown_price_upper_bound_cny=0.05,
            surprise_field=1,
        )


# --- execution ------------------------------------------------------------


def test_execution_settings_loaded_and_bounded():
    execution = get_execution_settings()
    assert execution.enabled is False, "M2.0 不得默认开启执行引擎"
    assert execution.max_concurrency == 1
    assert execution.lease_seconds == 300
    assert execution.heartbeat_seconds * 2 <= execution.lease_seconds


def test_execution_settings_rejects_concurrency_above_one():
    with pytest.raises(ValidationError):
        ExecutionSettings(
            enabled=False,
            worker_poll_seconds=1.0,
            max_concurrency=4,
            lease_seconds=300,
            heartbeat_seconds=100,
            dev_tick_enabled=False,
        )


def test_execution_settings_rejects_heartbeat_longer_than_half_lease():
    with pytest.raises(ValidationError):
        ExecutionSettings(
            enabled=False,
            worker_poll_seconds=1.0,
            max_concurrency=1,
            lease_seconds=100,
            heartbeat_seconds=80,
            dev_tick_enabled=False,
        )


# --- features -------------------------------------------------------------


def test_features_default_off():
    features = get_features_settings()
    assert features.model is False
    assert features.search is False
    assert features.execution is False


# --- models ---------------------------------------------------------------


def test_models_config_price_is_missing_not_zero():
    models = get_models_config()
    assert models.providers, "至少登记一个 provider"
    assert models.models, "至少登记一个模型条目"
    for entry in models.models:
        assert entry.pricing_status == "missing", f"{entry.model} 的价目未填，必须是 missing"
        assert entry.input_price_per_1k_cny is None
        assert entry.output_price_per_1k_cny is None
    assert models.find_model("deepseek", "deepseek-chat") is not None
    assert models.find_model("deepseek", "no-such-model") is None


def test_models_config_rejects_unknown_provider_reference():
    with pytest.raises(ValidationError):
        ModelsConfig(
            version=1,
            updated_at="2026-11-10",
            cost={"currency": "CNY", "pricing_policy": "reject_unknown"},
            providers={"deepseek": {"display_name": "DeepSeek", "enabled": False}},
            models=[
                {
                    "provider": "nowhere",
                    "model": "ghost",
                    "enabled": False,
                }
            ],
        )


# --- routing --------------------------------------------------------------


def test_routing_keys_match_task_type_registry():
    routing = get_agent_routing()
    assert set(routing.task_types) == set(TASK_TYPES)
    gates = {name for name, spec in routing.task_types.items() if spec.human_gate}
    assert gates == set(HUMAN_GATE_TASK_TYPES)


def test_routing_helpers_return_declared_values():
    routing = get_agent_routing()
    assert routing.allowed_agents_for("verify.claims") == frozenset({"fact_checker"})
    assert "llm_complete" in routing.tools_for("scriptwriter")
    assert routing.db_scopes_for("video_producer") == frozenset({"media_assets", "publications"})
    assert routing.allowed_agents_for("no.such.type") == frozenset()
    assert routing.default_deny is True


def test_routing_rejects_empty_allowed_agents():
    with pytest.raises(ValidationError):
        AgentRoutingConfig(
            version=1,
            updated_at="2026-11-10",
            default_deny=True,
            task_types={
                "topic.discover": {
                    "display_name": "x",
                    "allowed_agents": [],
                    "tools": ["db_query"],
                    "writes": [],
                    "human_gate": False,
                }
            },
            agents={"orchestrator": {"tools": ["db_query"], "db_scopes": []}},
        )


# --- pipelines ------------------------------------------------------------


def test_pipelines_have_expected_stages():
    pipelines = get_pipelines_config()
    research = pipelines.pipelines["research_to_script"]
    video = pipelines.pipelines["script_to_video"]
    assert len(research.stages) == 11
    assert len(video.stages) == 5
    gates = [stage.id for stage in research.stages if stage.kind == "gate"]
    assert "topic_approval" in gates and "script_approval" in gates


def test_pipeline_stage_requires_task_type_for_task_stages():
    with pytest.raises(ValidationError):
        PipelineStage(id="orphan", name="缺 task_type", kind="task")


def test_pipeline_stage_rejects_task_type_on_gate():
    with pytest.raises(ValidationError):
        PipelineStage(
            id="gate_with_task",
            name="闸门不应绑定 task_type",
            kind="gate",
            task_type="research.plan",
            human_gate=True,
        )


def test_pipeline_rejects_duplicate_stage_ids():
    with pytest.raises(ValidationError):
        PipelineSpec(
            display_name="dup",
            stages=[
                {"id": "a", "name": "a", "kind": "task", "task_type": "research.plan"},
                {"id": "a", "name": "a2", "kind": "task", "task_type": "research.plan"},
            ],
        )


# --- 通用读取器 ------------------------------------------------------------


def test_get_settings_section_raises_on_unknown_section():
    with pytest.raises(ValueError):
        get_settings_section("definitely_not_a_section")
