"""Agent 团队：名册、提示词、统一协议与调度接口契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.core.errors import DependencyUnavailableError
from backend.app.models import AgentRun
from backend.app.services.agent_registry import (
    AgentTaskEnvelope,
    RecordingDispatcher,
    UnavailableDispatcher,
    get_dispatcher,
    load_roster,
    roster_keys_missing_prompts,
)

EXPECTED_AGENTS = {
    "orchestrator",
    "financial_researcher",
    "football_researcher",
    "industry_researcher",
    "case_analyst",
    "fact_checker",
    "scriptwriter",
    "visual_designer",
    "video_producer",
}

PROTOCOL_FIELDS = (
    "task_id",
    "agent_name",
    "task_type",
    "input",
    "context",
    "sources",
    "evidence",
    "output",
    "status",
    "errors",
)


def test_roster_lists_all_nine_agents():
    specs = load_roster()
    assert {spec.key for spec in specs} == EXPECTED_AGENTS
    assert len(specs) == 9
    assert all(spec.display_name for spec in specs)
    assert all(spec.responsibilities for spec in specs)


def test_every_agent_has_an_existing_prompt_file():
    assert roster_keys_missing_prompts() == []

    for spec in load_roster():
        prompt_path = Path(spec.prompt_file)
        assert prompt_path.is_file(), f"{spec.key} 缺少提示词文件: {prompt_path}"
        text = prompt_path.read_text(encoding="utf-8")
        assert len(text) > 800, f"{spec.key} 提示词过短"
        assert "task_type" in text
        assert "禁止" in text
        assert "质量检查" in text


def test_prompt_files_live_under_agents_directory():
    agents_dir = get_settings().agents_dir.resolve()
    for spec in load_roster():
        resolved = Path(spec.prompt_file).resolve()
        assert str(resolved).startswith(str(agents_dir)), spec.key
        assert resolved.name == "PROMPT.md"


def test_protocol_document_describes_all_fields():
    protocol = (PROJECT_ROOT / "agents" / "PROTOCOL.md").read_text(encoding="utf-8")
    for field in PROTOCOL_FIELDS:
        assert field in protocol, f"协议缺少字段说明: {field}"
    assert "不编造" in protocol
    assert "足球类比" in protocol


def test_task_envelope_serializes_protocol_keys():
    envelope = AgentTaskEnvelope(
        task_id="task-0123456789abcdef",
        agent_name="financial_researcher",
        task_type="research_instrument",
    )
    payload = envelope.to_dict()

    assert set(payload) == set(PROTOCOL_FIELDS)
    assert payload["status"] == "pending"
    assert payload["sources"] == []
    assert payload["evidence"] == []
    assert payload["errors"] == []
    assert payload["output"] is None


def test_envelope_requires_core_identity_fields():
    with pytest.raises(TypeError):
        AgentTaskEnvelope()  # type: ignore[call-arg]


def test_recording_dispatcher_persists_a_pending_run(db_session):
    dispatcher = RecordingDispatcher(db_session)
    task = AgentTaskEnvelope(
        task_id="task-pending-record",
        agent_name="fact_checker",
        task_type="review_facts",
        input={"claim": "可转债溢价率长期为正"},
        context={"topic_id": None},
    )

    returned = dispatcher.dispatch(task)
    assert returned.task_id == "task-pending-record"

    stored = db_session.execute(
        select(AgentRun).where(AgentRun.task_id == "task-pending-record")
    ).scalar_one()
    assert stored.agent_name == "fact_checker"
    assert stored.task_type == "review_facts"
    assert stored.status.value == "pending"
    assert stored.input_payload["claim"] == "可转债溢价率长期为正"
    # 没有真实执行，绝不能编造输出
    assert stored.output_payload is None
    assert stored.requested_at is not None


def test_dispatcher_generates_task_id_when_absent(db_session):
    empty = AgentTaskEnvelope(task_id="", agent_name="orchestrator", task_type="plan")
    returned = RecordingDispatcher(db_session).dispatch(empty)
    assert returned.task_id.startswith("task-")


def test_unavailable_dispatcher_reports_503(db_session):
    dispatcher = UnavailableDispatcher("第一阶段未接入模型 API")
    with pytest.raises(DependencyUnavailableError) as excinfo:
        dispatcher.dispatch(
            AgentTaskEnvelope(task_id="task-x", agent_name="scriptwriter", task_type="write")
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "dependency_unavailable"


def test_get_dispatcher_returns_recording_dispatcher_by_default(db_session):
    assert isinstance(get_dispatcher(db_session), RecordingDispatcher)
