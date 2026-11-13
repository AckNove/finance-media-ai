"""模型改写脚本（``content.script`` 的模型分支）的测试。

钉住三条硬规矩：

1. 提示词里只能出现**人工确认过**的结论，且带编号；
2. 模型写的句子必须挂到某条结论上，数字必须在它引的那条结论里出现过，
   否则整句丢进 ``dropped``，不进稿；
3. 模型不可用/写歪了 → 退回规则版并写明原因，**不许假装是模型写的**。

全部离线：用一个假的网关对象返回固定文本，不联网、不花钱。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.capabilities.model import ModelReply, ModelRequest
from backend.app.capabilities.model.gateway import CallRecord
from backend.app.content.stages import (
    ScriptDraft,
    StageError,
    build_script_messages,
    draft_script,
    parse_script_draft,
)
from backend.app.database.base import Base
from backend.app.execution.executor import AgentExecutor
from backend.app.execution.runtime import TaskRuntime
from backend.app.models.execution import ResearchTask
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.models.topic import Topic

FINDINGS = [
    {
        "text": "2024 年第四季度商业银行净息差降至 1.52%，环比下降 4 个基点。",
        "kind": "fact",
        "source_ids": ["S01"],
        "numbers": ["1.52%"],
    },
    {
        "text": "净利息收入约占商业银行营业收入的七成，是主要收入来源。",
        "kind": "inference",
        "source_ids": ["S02"],
        "numbers": [],
    },
]

GOOD_REPLY = """
```json
{"title": "息差降到 1.52% 之后", "hook": "息差又降了。", "lines": [
  {"text": "2024 年四季度净息差降到 1.52%，比上季少了 4 个基点。", "source_index": 1, "role": "evidence"},
  {"text": "七成收入来自净利息，息差一降，收入就跟着薄。", "source_index": 2, "role": "mechanism"}
]}
```
"""


class _Report:
    def __init__(self, *, title: str, findings: list[dict]) -> None:
        self.id = 1
        self.title = title
        self.findings = findings


def _confirmed(indexes: tuple[int, ...] = (0, 1)) -> list[dict[str, Any]]:
    return [
        {
            "finding_index": index,
            "text": FINDINGS[index]["text"],
            "source_ids": FINDINGS[index]["source_ids"],
            "evidence_ids": [index + 1],
            "verdict": "supported",
            "check_id": index + 10,
        }
        for index in indexes
    ]


def _rule_draft() -> ScriptDraft:
    report = _Report(title="商业银行怎么赚钱", findings=FINDINGS)
    return draft_script(report=report, confirmed=_confirmed())


def test_prompt_only_carries_confirmed_findings_with_numbers() -> None:
    messages = build_script_messages(draft=_rule_draft(), confirmed=_confirmed())
    system, user = messages[0]["content"], messages[1]["content"]
    assert "只能改写给定的编号结论" in system
    assert "不许出现结论里没有的数字" in system
    assert "[1] 2024 年第四季度商业银行净息差" in user
    assert "1.52%" in user
    assert "数字白名单" in user


def test_model_reply_is_accepted_when_every_line_maps_to_a_finding() -> None:
    draft, dropped = parse_script_draft(GOOD_REPLY, draft=_rule_draft(), confirmed=_confirmed())
    assert draft.generated_by == "model"
    assert draft.title == "息差降到 1.52% 之后"
    assert len(draft.lines) == 2
    assert draft.lines[0].source_ids == ("S01",)
    assert draft.lines[1].role == "mechanism"
    assert dropped == ()


def test_a_sentence_without_a_finding_is_dropped() -> None:
    reply = '{"lines": [{"text": "银行躺着赚钱的日子结束了。", "source_index": 99}]}'
    with pytest.raises(StageError) as caught:
        parse_script_draft(reply, draft=_rule_draft(), confirmed=_confirmed())
    assert caught.value.reason == "empty_model_draft"


def test_a_number_that_is_not_in_the_cited_finding_is_dropped() -> None:
    reply = (
        '{"lines": ['
        '{"text": "净息差降到 1.20%，创十年新低。", "source_index": 1},'
        '{"text": "七成收入来自净利息。", "source_index": 2}]}'
    )
    draft, _ = parse_script_draft(reply, draft=_rule_draft(), confirmed=_confirmed())
    assert [line.text for line in draft.lines] == ["七成收入来自净利息。"]
    assert any("1.20%" in item for item in draft.dropped)


def test_unknown_role_falls_back_and_bad_json_raises() -> None:
    reply = '{"lines": [{"text": "七成收入来自净利息。", "source_index": 2, "role": "卖点"}]}'
    draft, _ = parse_script_draft(reply, draft=_rule_draft(), confirmed=_confirmed())
    assert draft.lines[0].role == "evidence"
    with pytest.raises(StageError) as caught:
        parse_script_draft("我觉得挺好的", draft=_rule_draft(), confirmed=_confirmed())
    assert caught.value.reason == "bad_model_script"


def test_a_range_cannot_be_rewritten_into_a_single_number() -> None:
    """结论说「预亏 70-75 亿元」，模型不能写成「预亏 70 亿元」。"""
    findings = [
        {
            "text": "通威 2024 年预亏 70-75 亿元。",
            "kind": "fact",
            "source_ids": ["S09"],
            "numbers": [],
        }
    ]
    report = _Report(title="光伏", findings=findings)
    confirmed = [
        {
            "finding_index": 0,
            "text": findings[0]["text"],
            "source_ids": ["S09"],
            "evidence_ids": [1],
            "verdict": "supported",
            "check_id": 1,
        }
    ]
    base = draft_script(report=report, confirmed=confirmed, requested_title="光伏")
    good = '{"lines": [{"text": "通威 2024 年预亏 70-75 亿元。", "source_index": 1}]}'
    draft, _ = parse_script_draft(good, draft=base, confirmed=confirmed)
    assert len(draft.lines) == 1

    bad = '{"lines": [{"text": "通威 2024 年预亏 70 亿元。", "source_index": 1}]}'
    with pytest.raises(StageError) as caught:
        parse_script_draft(bad, draft=base, confirmed=confirmed)
    assert caught.value.reason == "empty_model_draft"


class _RecordingGateway:
    """记录被调用的请求，按预设返回一条回复。"""

    def __init__(self, reply: ModelReply) -> None:
        self.reply = reply
        self.requests: list[ModelRequest] = []

    def complete(self, caller, request: ModelRequest, *, budget=None) -> ModelReply:
        self.requests.append(request)
        return self.reply


def _reply(text: str, *, status: str = "ok") -> ModelReply:
    record = CallRecord(
        id=77,
        provider="deepseek",
        model="deepseek-flash",
        prompt_tokens=800,
        completion_tokens=200,
        total_tokens=1000,
        cost_cny=0.003412,
        cache_hit=False,
        latency_ms=1200,
        status="ok",
    )
    return ModelReply(
        status=status,
        text=text,
        error_code=None,
        error_message=None,
        record=record,
        raw_usage={},
    )


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'script_model.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(factory) -> int:
    with factory() as session:
        topic = Topic(title="商业银行怎么赚钱", slug="bank-profit", description="")
        session.add(topic)
        session.commit()
        task = ResearchTask(
            topic_id=topic.id,
            title="商业银行怎么赚钱",
            objective="商业银行怎么赚钱",
            status="running",
            plan={},
            requested_by="human",
        )
        session.add(task)
        session.commit()
        report = ResearchReport(
            topic_id=topic.id,
            research_task_id=task.id,
            title="商业银行怎么赚钱",
            summary="净息差收窄。",
            findings=[
                {"text": item["text"], "kind": item["kind"], "source_ids": item["source_ids"], "numbers": item["numbers"]}
                for item in FINDINGS
            ],
            limitations="模型结论不等于事实核查结论",
        )
        session.add(report)
        session.commit()
        for index in range(len(FINDINGS)):
            session.add(
                FindingCheck(
                    research_report_id=report.id,
                    finding_index=index,
                    claim=FINDINGS[index]["text"],
                    claim_kind=FINDINGS[index]["kind"],
                    evidence_ids=[index + 1],
                    source_ids=FINDINGS[index]["source_ids"],
                    verdict="supported",
                    reason="原文有据",
                    supporting=[FINDINGS[index]["text"]],
                    contradicting=[],
                    missing=[],
                    issues=[],
                    provider="deepseek",
                    model="deepseek-flash",
                    human_review_status="confirmed",
                )
            )
        session.commit()
        return task.id


def _claim(runtime: TaskRuntime, task_id: int, task_type: str):
    from datetime import UTC, datetime

    from backend.app.execution.runtime import Claim

    return Claim(
        task_id=task_id,
        subtask_id=0,
        task_type=task_type,
        question="阶段测试",
        assignee="scriptwriter",
        sequence=1,
        max_attempts=2,
        attempt=1,
        attempt_token="token-1",
        lease_until=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
        input_payload={},
    )


def test_executor_uses_the_model_and_charges_the_call(factory, tmp_path) -> None:
    task_id = _seed(factory)
    runtime = TaskRuntime(factory)
    gateway = _RecordingGateway(_reply(GOOD_REPLY))
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path, model_gateway=gateway)

    outcome = executor.execute(_claim(runtime, task_id, "content.script"))

    assert outcome.status == "completed"
    assert outcome.output["generated_by"] == "model"
    assert outcome.calls == 1
    assert outcome.tokens == 1000
    assert float(outcome.cost_cny) == pytest.approx(0.003412, abs=1e-6)
    assert gateway.requests and gateway.requests[0].purpose == "content.script"


def test_executor_falls_back_to_rules_and_says_so(factory, tmp_path) -> None:
    task_id = _seed(factory)
    runtime = TaskRuntime(factory)
    gateway = _RecordingGateway(_reply("我觉得这条挺有意思"))
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path, model_gateway=gateway)

    outcome = executor.execute(_claim(runtime, task_id, "content.script"))

    assert outcome.status == "completed"
    assert outcome.output["generated_by"] == "rules"
    assert outcome.calls == 0
    draft = ScriptDraft(
        title=outcome.output["title"],
        hook="",
        lines=(),
        disclaimer="",
        notes=tuple(outcome.output.get("notes") or ()),
    )
    assert draft.title  # 规则版仍然是可用的草稿


def test_a_worker_style_executor_still_reaches_the_global_gateway(factory, tmp_path, monkeypatch) -> None:
    """worker 里构造的是 `AgentExecutor()`（没注入网关），脚本阶段也得能用模型。

    这是一个真缺陷的回归：只看 `self._model_gateway` 的话，生产链路上的
    `content.script` 永远走规则版，模型改写等于没接上。
    """
    task_id = _seed(factory)
    runtime = TaskRuntime(factory)
    gateway = _RecordingGateway(_reply(GOOD_REPLY))
    monkeypatch.setattr("backend.app.capabilities.model.get_model_gateway", lambda: gateway)
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path)

    outcome = executor.execute(_claim(runtime, task_id, "content.script"))

    assert outcome.status == "completed"
    assert outcome.output["generated_by"] == "model"
    assert outcome.calls == 1
    assert gateway.requests and gateway.requests[0].purpose == "content.script"
