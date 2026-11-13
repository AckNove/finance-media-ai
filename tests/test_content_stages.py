"""内容三阶段（脚本 / 分镜 / 制作计划）的测试。

这里刻意**不调模型**：三阶段目前都是规则实现，规则就该被钉住。
真正调模型的是 ``research.analyze / synthesize / verify``，那几个的测试在
``tests/test_execution_pipeline.py`` 与 ``tests/test_semantic_check.py``。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.content.stages import (
    DISCLAIMER,
    MAX_FINDINGS,
    ScriptDraft,
    ScriptLine,
    StageError,
    draft_script,
    plan_shots,
    production_plan,
    write_stage_files,
)
from backend.app.database.base import Base
from backend.app.execution.executor import AgentExecutor
from backend.app.execution.runtime import TaskRuntime
from backend.app.models.execution import ResearchTask
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.models.topic import Topic


class _Report:
    """轻量报告替身：只要 ``findings`` 与 ``title``，不必进库。"""

    def __init__(self, *, title: str, findings: list[dict]) -> None:
        self.id = 1
        self.title = title
        self.findings = findings


def _confirmed(*indexes: int, source_ids: tuple[str, ...] = ("S01",)) -> list[dict]:
    return [
        {
            "finding_index": index,
            "text": f"第 {index} 条结论：2024 年净息差 1.52%。",
            "verdict": "supported",
            "check_id": 100 + index,
            "source_ids": list(source_ids),
        }
        for index in indexes
    ]


# ------------------------------------------------------------------ 纯函数


def test_draft_refuses_when_nothing_is_confirmed() -> None:
    report = _Report(title="测试报告", findings=[])
    with pytest.raises(StageError) as caught:
        draft_script(report=report, confirmed=[])
    assert caught.value.reason == "no_confirmed_findings"
    assert "确认" in caught.value.message


def test_draft_only_uses_the_confirmed_findings() -> None:
    report = _Report(
        title="商业银行研究",
        findings=[
            {"text": "未确认的结论：招商银行 ROE 17%。", "source_ids": ["S09"], "numbers": ["17%"]},
            {"text": "已确认的结论：净息差 1.52%。", "source_ids": ["S01"], "numbers": ["1.52%"]},
        ],
    )
    draft = draft_script(report=report, confirmed=_confirmed(1))

    texts = [line.text for line in draft.lines]
    assert texts == ["已确认的结论：净息差 1.52%。"]
    assert all("未确认" not in text for text in texts)
    # 来源与数字要从报告里那一条带过来，不能凭空产生
    assert draft.lines[0].source_ids == ("S01",)
    assert draft.lines[0].numbers == ("1.52%",)
    assert draft.disclaimer == DISCLAIMER
    assert draft.generated_by == "rules"


def test_draft_caps_findings_and_dedupes() -> None:
    findings = [
        {"text": f"结论 {index}：某个指标 {index}%。", "source_ids": ["S01"], "numbers": ["1%"]}
        for index in range(12)
    ]
    report = _Report(title="测试", findings=findings)
    draft = draft_script(report=report, confirmed=_confirmed(*range(12)))

    assert len(draft.lines) <= MAX_FINDINGS
    assert len({line.text for line in draft.lines}) == len(draft.lines)
    assert any("只用了前" in note for note in draft.notes)


def test_draft_markdown_lists_sources_and_disclaimer() -> None:
    report = _Report(title="测试", findings=[{"text": "净息差 1.52%。", "source_ids": ["S01"]}])
    markdown = draft_script(report=report, confirmed=_confirmed(0)).to_markdown()

    assert "# 测试" in markdown
    assert "来源 S01" in markdown
    assert DISCLAIMER in markdown


def test_plan_shots_opens_with_a_title_card_and_ends_with_disclaimer() -> None:
    draft = ScriptDraft(
        title="测试片",
        hook="开场一句",
        lines=(
            ScriptLine(text="净息差 1.52%。", role="evidence", source_ids=("S01",), numbers=("1.52%",)),
            ScriptLine(text="机制是资产端重定价。", role="mechanism", source_ids=("S02",)),
        ),
        disclaimer=DISCLAIMER,
    )
    shots = plan_shots(draft)

    assert shots[0]["asset_type"] == "title"
    assert shots[-1]["asset_type"] == "outro"
    body = shots[1:-1]
    assert len(body) == 2
    # 带单位的数字走数据卡，纯文字走文字卡
    assert body[0]["asset_type"] == "data_card"
    assert body[1]["asset_type"] == "text_card"
    for shot in shots:
        assert shot["shot_id"].startswith("sh")
        assert shot["narration"].strip()
        assert shot["source_ids"] is not None


def test_production_plan_says_what_is_not_done() -> None:
    draft = ScriptDraft(
        title="测试片",
        hook="开场",
        lines=(ScriptLine(text="一句话。", role="evidence"),),
        disclaimer=DISCLAIMER,
    )
    plan = production_plan(draft=draft, shots=plan_shots(draft), job_id="task-9")

    assert plan["job_id"] == "task-9"
    assert plan["canvas"] == {"width": 1080, "height": 1920, "fps": 30}
    assert "渲染" in plan["not_done"]
    assert "发布" in plan["not_done"]
    # 计划里不能出现"已渲染/已发布"这类谎话
    text = str(plan)
    assert "已渲染" not in text and "已发布" not in text


def test_write_stage_files_writes_three_products(tmp_path) -> None:
    draft = ScriptDraft(
        title="测试片",
        hook="开场",
        lines=(ScriptLine(text="一句话。", role="evidence"),),
        disclaimer=DISCLAIMER,
    )
    shots = plan_shots(draft)
    plan = production_plan(draft=draft, shots=shots, job_id="task-1")
    files = write_stage_files(out_dir=tmp_path / "content", draft=draft, shots=shots, plan=plan)

    assert set(files) == {"script_markdown", "script_json", "visual_plan", "production_plan"}
    for path in files.values():
        assert path.endswith((".md", ".json"))
        assert (tmp_path / "content") == __import__("pathlib").Path(path).parent


# ------------------------------------------------------------------ 执行器链路


@pytest.fixture()
def engine(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("content_stages") / "stage.db"
    created = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(created)
    yield created
    created.dispose()


@pytest.fixture()
def factory(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        for table in Base.metadata.tables.values():
            connection.exec_driver_sql(f'DELETE FROM "{table.name}"')
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def runtime(factory) -> TaskRuntime:
    return TaskRuntime(factory, clock=lambda: datetime(2026, 5, 20, 9, 0, tzinfo=UTC))


def _seed_task(factory, *, with_report: bool, confirmed: bool, topic: bool):
    with factory() as session:
        topic_id = None
        if topic:
            row = Topic(slug="stage-topic", title="阶段测试选题", description="测试用")
            session.add(row)
            session.flush()
            topic_id = row.id
        task = ResearchTask(
            topic_id=topic_id,
            title="阶段测试",
            objective="商业银行是怎么赚钱的",
            status="running",
        )
        session.add(task)
        session.flush()
        task_id = task.id
        report_id = None
        if with_report:
            report = ResearchReport(
                research_task_id=task_id,
                title="阶段测试报告",
                summary="摘要",
                limitations="测试用局限说明",
                findings=[
                    {
                        "text": "净息差已降至 1.52%。",
                        "kind": "fact",
                        "source_ids": ["S01"],
                        "numbers": ["1.52%"],
                    }
                ],
                verification_status="unverified",
            )
            session.add(report)
            session.flush()
            report_id = report.id
            if confirmed:
                session.add(
                    FindingCheck(
                        research_report_id=report_id,
                        finding_index=0,
                        claim="净息差已降至 1.52%。",
                        verdict="supported",
                        human_review_status="confirmed",
                    )
                )
        session.commit()
        return task_id, report_id


def _claim(runtime: TaskRuntime, task_id: int, task_type: str):
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


def test_script_stage_is_blocked_until_someone_confirms(runtime: TaskRuntime, factory, tmp_path) -> None:
    task_id, _ = _seed_task(factory, with_report=True, confirmed=False, topic=True)
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path)

    outcome = executor.execute(_claim(runtime, task_id, "content.script"))

    assert outcome.status == "blocked"
    assert outcome.reason == "no_confirmed_findings"
    assert "确认" in str(outcome.output)


def test_script_stage_writes_a_draft_and_stores_the_script(runtime: TaskRuntime, factory, tmp_path) -> None:
    task_id, report_id = _seed_task(factory, with_report=True, confirmed=True, topic=True)
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path)

    outcome = executor.execute(_claim(runtime, task_id, "content.script"))

    assert outcome.status == "completed"
    assert outcome.output["script_id"] is not None
    assert outcome.output["confirmed_used"] == 1
    assert (tmp_path / f"task-{task_id}" / "content" / "script_draft.md").exists()

    from backend.app.models.script import Script

    with factory() as session:
        script = session.get(Script, outcome.output["script_id"])
        assert script is not None
        assert script.research_report_id == report_id
        assert "净息差" in script.body
        assert script.status.value == "draft"


def test_visual_and_produce_only_plan(runtime: TaskRuntime, factory, tmp_path) -> None:
    task_id, _ = _seed_task(factory, with_report=True, confirmed=True, topic=True)
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path)

    early = executor.execute(_claim(runtime, task_id, "content.visual"))
    assert early.status == "blocked"
    assert early.reason == "no_script_draft"

    assert executor.execute(_claim(runtime, task_id, "content.script")).status == "completed"

    visual = executor.execute(_claim(runtime, task_id, "content.visual"))
    assert visual.status == "completed"
    assert visual.output["shot_count"] >= 3
    assert "title" in visual.output["asset_types"]
    assert "text_card" in visual.output["asset_types"] or "data_card" in visual.output["asset_types"]

    produce = executor.execute(_claim(runtime, task_id, "content.produce"))
    assert produce.status == "completed"
    assert produce.output["job_id"] == f"task-{task_id}"
    assert "渲染" in produce.output["not_done"]
    assert "render_job" in produce.output["next_step"]


def test_stage_dir_does_not_overwrite_another_run_with_the_same_task_id(
    runtime: TaskRuntime, factory, tmp_path
) -> None:
    """`task-<id>` 只在一个库里唯一：换库跑同一个 id 不能盖掉上一份脚本草稿。"""
    import json

    task_id, _ = _seed_task(factory, with_report=True, confirmed=True, topic=True)
    executor = AgentExecutor(session_factory=factory, artifacts_root=tmp_path)
    claim = _claim(runtime, task_id, "content.script")

    # 先放一份"别人的"底稿：目标与当前任务不同
    digest_dir = tmp_path / f"task-{task_id}"
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / "digest.json").write_text(
        json.dumps({"objective": "另一次研究的题目"}, ensure_ascii=False), encoding="utf-8"
    )

    stage = executor._stage_dir(claim)
    assert stage.parent.name.startswith(f"task-{task_id}-")
    assert stage.name == "content"

    # 目标一致时仍落在原目录，不因为指纹到处建新文件夹
    (digest_dir / "digest.json").write_text(
        json.dumps({"objective": executor._task_objective(task_id)}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert executor._stage_dir(claim).parent.name == f"task-{task_id}"
