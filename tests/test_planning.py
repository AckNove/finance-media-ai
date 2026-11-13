"""模型驱动研究规划（P1）的离线测试：不联网、不花钱。

盯四件事：

1. 提示词把"只规划要查什么、不许编事实"说清楚，并把上下文带进去；
2. 模型输出不可用时（无子任务、缺关键词、词重复）**丢弃**而不是猜；
3. 降级路径（``keyword_plan``）如实标 ``planner="keywords"``，不冒充模型；
4. 截断 / 无模型必须报错，让执行器去降级，而不是返回一个假计划。
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.capabilities.model import ModelGateway
from backend.app.core.config_schema import ModelsConfig
from backend.app.research.planning import (
    MAX_SUBTASKS,
    PlanningError,
    build_messages,
    keyword_plan,
    parse_plan,
    plan_research,
)


def build_config(*, enabled: bool = True) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "version": 1,
            "updated_at": "2026-11-13",
            "cost": {
                "currency": "CNY",
                "pricing_policy": "reject_unknown",
                "approved_exceptions": [],
                "cache_ttl_hours": 24,
            },
            "providers": {
                "deepseek": {
                    "display_name": "DeepSeek",
                    "base_url_env": "DEEPSEEK_BASE_URL",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "enabled": True,
                }
            },
            "models": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "display_name": "flash",
                    "context_window": 1_000_000,
                    "input_price_per_1k_cny": 0.00213,
                    "output_price_per_1k_cny": 0.00852,
                    "enabled": enabled,
                }
            ],
        }
    )


def ok_response(text: str, *, prompt: int = 800, completion: int = 500) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-plan",
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


def build_gateway(handler, *, enabled: bool = True) -> ModelGateway:
    return ModelGateway(
        models_config=build_config(enabled=enabled),
        transport=httpx.MockTransport(handler),
        environ={"DEEPSEEK_API_KEY": "sk-test-key-0003"},
        retries=0,
    )


GOOD_PLAN = json.dumps(
    {
        "objective": "讲清商业银行靠什么赚钱，以及息差收窄后靠什么补",
        "core_questions": [
            "商业银行的收入由哪几块构成？",
            "净息差为什么在持续收窄？",
            "中间业务收入能不能补上缺口？",
        ],
        "subtasks": [
            {
                "id": "s1",
                "question": "监管口径下商业银行净息差是多少、近年怎么走",
                "search_intent": "找监管总局发布的季度行业指标",
                "keywords": ["商业银行 净息差", "银行业 主要监管指标"],
                "synonyms": ["NIM", "净利息收益率"],
                "source_types": ["监管统计", "行业报告"],
                "priority": "high",
                "depends_on": [],
                "done_when": "拿到近五年净息差序列并标明口径",
            },
            {
                "id": "s2",
                "question": "上市银行手续费及佣金净收入的占比",
                "search_intent": "找年报或行业研究里的收入结构表",
                "keywords": ["手续费及佣金净收入 占比", "中间业务收入 银行"],
                "synonyms": [],
                "source_types": ["上市公司年报"],
                "priority": "medium",
                "depends_on": ["s1"],
                "done_when": "至少两家银行的可比数据",
            },
            {
                "id": "s3",
                "question": "利率市场化对银行息差的影响机制",
                "search_intent": "找学术或央行研究",
                "keywords": ["利率市场化 息差", "LPR 改革 银行"],
                "synonyms": [],
                "source_types": ["研究报告"],
                "priority": "low",
                "depends_on": [],
                "done_when": "能说清传导链条",
            },
        ],
        "risks": ["监管口径与年报口径可能不一致"],
        "gaps": ["缺少分类型银行的净息差对比"],
    },
    ensure_ascii=False,
)


# --- 提示词 ---------------------------------------------------------------


def test_prompt_states_the_job_and_carries_context() -> None:
    system, user = build_messages(
        "商业银行是如何赚钱的？",
        {"pipeline": "research_to_script", "explicit_queries": ["银行 收入结构"]},
    )
    assert "规划" in system["content"]
    assert "不要编造" in system["content"]
    body = user["content"]
    assert "商业银行是如何赚钱的？" in body
    assert "research_to_script" in body
    assert "银行 收入结构" in body


# --- 解析与校验 -----------------------------------------------------------


def test_plan_keeps_every_field_the_spec_requires() -> None:
    plan = parse_plan(GOOD_PLAN, objective="商业银行是如何赚钱的？")
    as_dict = plan.as_dict()
    for key in (
        "objective",
        "core_questions",
        "subtasks",
        "risks",
        "gaps",
        "planner",
    ):
        assert key in as_dict
    first = as_dict["subtasks"][0]
    for key in (
        "question",
        "search_intent",
        "keywords",
        "synonyms",
        "source_types",
        "priority",
        "depends_on",
        "done_when",
    ):
        assert key in first
    assert plan.planner == "model"
    assert [item.priority for item in plan.subtasks] == ["high", "medium", "low"]


def test_subtasks_without_keywords_or_questions_are_dropped_not_guessed() -> None:
    raw = json.dumps(
        {
            "subtasks": [
                {"question": "这条没有关键词", "keywords": []},
                {"question": "短", "keywords": ["净息差"]},
                {"question": "有效的子任务问题", "keywords": ["净息差", "净息差"]},
            ]
        },
        ensure_ascii=False,
    )
    plan = parse_plan(raw, objective="商业银行是如何赚钱的？")
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].keywords == ("净息差",)  # 重复词只留一个
    assert any(item.startswith("drop_subtask:") for item in plan.notes)


def test_duplicate_queries_across_subtasks_are_dropped() -> None:
    raw = json.dumps(
        {
            "subtasks": [
                {"question": "第一个子任务问题", "keywords": ["商业银行 净息差"]},
                {"question": "第二个子任务问题", "keywords": ["商业银行 净息差"]},
            ]
        },
        ensure_ascii=False,
    )
    plan = parse_plan(raw, objective="商业银行是如何赚钱的？")
    assert len(plan.subtasks) == 1
    assert any("关键词与前面的子任务重复" in item for item in plan.notes)


def test_too_many_subtasks_are_truncated_with_a_note() -> None:
    subtasks = [
        {"question": f"第{i}个子任务的问题描述", "keywords": [f"关键词{i}"], "priority": "high"}
        for i in range(MAX_SUBTASKS + 3)
    ]
    plan = parse_plan(
        json.dumps({"subtasks": subtasks}, ensure_ascii=False), objective="商业银行是如何赚钱的？"
    )
    assert len(plan.subtasks) == MAX_SUBTASKS
    assert any(item.startswith("truncated_subtasks:") for item in plan.notes)


def test_plan_without_any_subtask_raises() -> None:
    with pytest.raises(PlanningError) as error:
        parse_plan(json.dumps({"core_questions": ["凑数"]}), objective="商业银行是如何赚钱的？")
    assert error.value.reason == "no_subtasks"


def test_not_json_at_all_raises_instead_of_inventing_a_plan() -> None:
    with pytest.raises(PlanningError):
        parse_plan("我觉得应该先查净息差。", objective="商业银行是如何赚钱的？")


# --- 调用 -----------------------------------------------------------------


def test_plan_research_records_model_tokens_and_cost() -> None:
    gateway = build_gateway(lambda request: ok_response(GOOD_PLAN))
    plan = plan_research("商业银行是如何赚钱的？", gateway=gateway)
    assert plan.planner == "model"
    assert plan.provider == "deepseek" and plan.model == "deepseek-flash"
    assert plan.prompt_tokens == 800 and plan.completion_tokens == 500
    assert plan.cost_cny == pytest.approx(0.005964, abs=1e-9)  # 0.8*0.00213 + 0.5*0.00852
    assert plan.model_call_id is not None
    assert len(plan.queries()) >= 3
    assert plan.high_priority()[0].id == "s1"


def test_truncated_plan_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-cut",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{\"subtasks\": [{"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 800,
                    "completion_tokens": body["max_tokens"],
                    "total_tokens": 800 + body["max_tokens"],
                },
            },
        )

    with pytest.raises(PlanningError) as error:
        plan_research("商业银行是如何赚钱的？", gateway=build_gateway(handler))
    assert error.value.reason == "truncated"


def test_no_model_raises_so_the_executor_can_degrade() -> None:
    gateway = build_gateway(lambda request: ok_response(GOOD_PLAN), enabled=False)
    with pytest.raises(PlanningError) as error:
        plan_research("商业银行是如何赚钱的？", gateway=gateway)
    assert error.value.reason == "no_model"


# --- 规则降级 -------------------------------------------------------------


def test_keyword_plan_is_honest_about_being_rules() -> None:
    plan = keyword_plan("商业银行是如何赚钱的？", ["商业银行 净息差", "银行 收入结构"])
    assert plan.planner == "keywords"
    assert plan.model_used is False
    assert plan.queries() == ["商业银行 净息差", "银行 收入结构"]
    assert any("规则拆词" in item for item in plan.notes)


def test_keyword_plan_without_queries_has_no_subtask() -> None:
    plan = keyword_plan("商业银行是如何赚钱的？", [])
    assert plan.subtasks == ()
    assert plan.planner == "keywords"


def test_a_retried_plan_charges_every_attempt_not_just_the_last_one() -> None:
    """撞上限重试的第一次也是真花了钱：规划账单必须累加。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append(int(body["max_tokens"]))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-cut",
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "{\"subtasks\": [{"},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 800,
                        "completion_tokens": body["max_tokens"],
                        "total_tokens": 800 + body["max_tokens"],
                    },
                },
            )
        return ok_response(GOOD_PLAN, prompt=600, completion=300)

    plan = plan_research("商业银行是如何赚钱的？", gateway=build_gateway(handler), max_tokens=3000)
    assert len(calls) == 2  # 第一次撞上限，第二次才成功
    assert plan.prompt_tokens == 800 + 600
    assert plan.completion_tokens == 3000 + 300
    # 0.8*0.00213 + 3.0*0.00852 + 0.6*0.00213 + 0.3*0.00852
    assert plan.cost_cny == pytest.approx(0.031098, abs=1e-6)
    assert any("retried_with_fewer_subtasks" in note for note in plan.notes)
