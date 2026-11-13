"""Skill 注入提示词：已登记的 Skill 真的能影响写出来的东西。

这一组盯三件事：

1. **只有 Agent 身份能加载**——人/开发身份调 ``load_for`` 要被拒（工具权限不能靠文字提权）；
2. **Skill 正文确实进了提示词**，而且带的是"怎么写"的要求，不是"能调什么工具"；
3. **加载失败不许把研究链打断**：没 Skill 就照常跑，只是 output 里的 ``skills_used`` 是空的。

全部离线，不打网络、不花钱。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.caller import CallerContext
from backend.app.research.analysis import MAX_SKILL_CHARS, build_messages
from backend.app.research.digest import Digest, SourceRef
from backend.app.skills.registry import SkillRegistry, SkillRegistryError

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _digest() -> Digest:
    return Digest(
        title="商业银行怎么赚钱",
        objective="说清净息差与中间业务的关系",
        sources=(SourceRef(source_id="S01", title="年报", url="https://www.pbc.gov.cn/a"),),
        data_points=(),
        meta={"excerpts": {"S01": "净息差 1.53%"}},
    )


def _agent(name: str = "scriptwriter") -> CallerContext:
    return CallerContext.for_executor(agent=name, task_id="t-1", attempt_token="tok-1")


def _claim(task_type: str = "research.analyze", assignee: str = "financial_analyst") -> "Claim":
    """按 runtime 里真实字段造一条 claim（``Claim`` 没有 ``attempts``，是 ``attempt``）。"""

    from datetime import UTC, datetime, timedelta

    from backend.app.execution.runtime import Claim

    return Claim(
        task_id=1,
        subtask_id=1,
        sequence=1,
        task_type=task_type,
        assignee=assignee,
        question="商业银行怎么赚钱",
        attempt=1,
        max_attempts=2,
        attempt_token="tok-1",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        input_payload={},
    )


# --- 提示词一侧 -------------------------------------------------------------


def test_skill_text_lands_in_the_user_message() -> None:
    messages = build_messages(
        _digest(),
        skill_notes=[("ak-signature", "一句话说清，不铺垫；先给结论再给理由。")],
    )
    user = messages[1]["content"]
    assert "写作与研究方法要求" in user
    assert "### ak-signature" in user
    assert "先给结论再给理由" in user


def test_no_skills_leaves_no_empty_heading() -> None:
    user = build_messages(_digest())[1]["content"]
    assert "写作与研究方法要求" not in user


def test_overlong_skill_body_is_truncated_with_a_visible_marker() -> None:
    long_body = "要求" * (MAX_SKILL_CHARS + 200)
    user = build_messages(_digest(), skill_notes=[("big", long_body)])[1]["content"]
    assert "Skill 正文过长，已截断" in user
    assert len(user) < MAX_SKILL_CHARS * 3, "截断后不该把整段都塞进去"


def test_empty_skill_body_is_skipped() -> None:
    user = build_messages(_digest(), skill_notes=[("blank", "   ")])[1]["content"]
    assert "### blank" not in user


# --- 注册表一侧 -------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path) -> SkillRegistry:
    """每次实例化都把审计写到临时目录（否则仓库里会多出一个日志文件）。"""

    return SkillRegistry(SKILLS_ROOT, audit_path=tmp_path / "skill_events.jsonl")


def test_a_human_caller_cannot_load_skills(registry: SkillRegistry) -> None:
    with pytest.raises(SkillRegistryError):
        registry.load_for(CallerContext.human(), "research.analyze")


def test_the_declared_skills_load_for_the_declared_task_type(registry: SkillRegistry) -> None:
    loaded = registry.load_for(_agent("scriptwriter"), "content.script")
    names = {item.name for item in loaded}
    assert "ak-signature" in names
    assert all(item.instructions.strip() for item in loaded)


def test_a_role_that_is_not_declared_for_a_task_type_gets_nothing(registry: SkillRegistry) -> None:
    with pytest.raises(SkillRegistryError):
        registry.load_for(_agent("video_producer"), "content.script")


# --- 执行器一侧（失败不打断） -----------------------------------------------


def test_executor_returns_no_skill_notes_for_a_human_caller() -> None:
    """人身份（测试里也用人的身份跑执行器）不该把 Skill 塞进提示词，但也不许报错。"""

    from backend.app.execution.executor import AgentExecutor

    executor = AgentExecutor(session_factory=None, tools=None)  # type: ignore[arg-type]
    assert executor._skill_notes(CallerContext.human(), _claim()) == ()


def test_executor_hands_the_registered_skill_to_the_prompt(monkeypatch, tmp_path) -> None:
    """真身份 + 真登记的 Skill：提示词里能读到它的正文，output 里记得住名字。"""

    from backend.app.core import config as config_module
    from backend.app.execution.executor import AgentExecutor

    monkeypatch.setattr(config_module, "PROJECT_ROOT", Path(__file__).resolve().parents[1])
    executor = AgentExecutor(session_factory=None, tools=None)  # type: ignore[arg-type]
    notes = executor._skill_notes(_agent("financial_researcher"), _claim("research.analyze"))
    assert notes, "research.analyze 上登记了 accessible-explainer，应该拿到正文"
    name, body = notes[0]
    assert name and body.strip()
    user = build_messages(_digest(), skill_notes=notes)[1]["content"]
    assert f"### {name}" in user


def test_executor_survives_a_broken_skills_directory(monkeypatch, tmp_path) -> None:
    """skills 目录没了也不许把研究跑挂：返回空、记 warning、链路继续。"""

    from backend.app.core import config as config_module
    from backend.app.execution.executor import AgentExecutor

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    executor = AgentExecutor(session_factory=None, tools=None)  # type: ignore[arg-type]
    assert executor._skill_notes(_agent("financial_analyst"), _claim()) == ()
