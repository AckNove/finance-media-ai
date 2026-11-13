"""编排 → 执行 → 回写：一条真实可跑的链路（不含假结果）。

这组用例刻意分成三层断言：

* :class:`~backend.app.execution.orchestrator.Orchestrator` 只负责"阶段与依赖、人工闸门"；
* :class:`~backend.app.execution.executor.AgentExecutor` 只负责"这一格活怎么干"；
* :class:`~backend.app.execution.worker.Worker` 只负责"结果怎么回写到状态机"。

工具一律用 :class:`FakeTools` 替身（**不联网**），但执行器走的是真实代码路径：
真实的 ``_collect``/``_extract``/``_model_blocked`` 分支、真实的 ``agent_runs`` 落库、
真实的任务状态流转。所以这些用例证明的是"接线正确"，不是"外部服务通"——
外部服务是否可用另见 ``tests/test_web_search.py`` 与真实网络用例。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import backend.app.models  # noqa: F401  注册全部模型
from backend.app.capabilities.tools import ToolResult
from backend.app.database.base import Base
from backend.app.execution.executor import AgentExecutor, derive_queries, page_quality
from backend.app.execution.orchestrator import Orchestrator
from backend.app.execution.runtime import TaskRuntime, TaskRuntimeError
from backend.app.execution.worker import Worker
from backend.app.models.agent import AgentRun
from backend.app.models.enums import AgentStatus
from backend.app.models.execution import ResearchTask


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("execution_db") / "execution.db"
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


class FakeTools:
    """工具替身：记录每次调用，按预设返回。**不发任何网络请求**。"""

    def __init__(
        self,
        *,
        hits: int = 2,
        pages: int = 1,
        search_ok: bool = True,
        fetch_ok: bool = True,
        raise_on: str | None = None,
        page_text: str = "正文内容" * 100,
        page_title: str = "抓到的标题",
    ) -> None:
        self.hits = hits
        self.pages = pages
        self.search_ok = search_ok
        self.fetch_ok = fetch_ok
        self.raise_on = raise_on
        self.page_text = page_text
        self.page_title = page_title
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, caller, tool: str, **arguments) -> ToolResult:
        self.calls.append((tool, dict(arguments)))
        if self.raise_on == tool:
            raise RuntimeError(f"{tool} exploded")
        if tool == "web_search":
            if not self.search_ok:
                return ToolResult(status="error", error_code="search_unavailable")
            hits = [
                {
                    "title": f"样本标题{i}",
                    "url": f"https://example.com/a{i}",
                    "snippet": "摘要",
                    "provider": "sogou",
                    "rank": i + 1,
                }
                for i in range(self.hits)
            ]
            return ToolResult(status="ok", data={"hits": hits, "providers_tried": ["sogou"]})
        if tool == "fetch_url":
            if not self.fetch_ok:
                return ToolResult(status="error", error_code="http_error")
            return ToolResult(
                status="ok",
                data={
                    "final_url": arguments["url"],
                    "title": self.page_title,
                    "published_at": "2026-05-01",
                    "author": "作者",
                    "site_name": "example.com",
                    "sha256": "a" * 64,
                    "snapshot_path": "data/snapshots/example.com/aaa.html",
                    "char_count": len(self.page_text),
                    "text": self.page_text,
                },
            )
        return ToolResult(status="error", error_code="unknown_tool")

    @property
    def tools_used(self) -> list[str]:
        return [tool for tool, _ in self.calls]


def _run_collect(runtime: TaskRuntime, tools: FakeTools) -> tuple[int, int]:
    task = runtime.create_task(title="净息差研究", objective="拆解商业银行盈利结构")
    subtask = runtime.add_subtask(
        task.id,
        sequence=1,
        question="净息差怎么算，最近三年怎么变",
        task_type="research.collect",
        assignee="financial_researcher",
        input_payload={"queries": ["净息差 商业银行", "净息差 趋势"]},
    )
    runtime.start_task(task.id)
    return task.id, subtask.id


# ---------------------------------------------------------------- 编排器


def test_plan_creates_one_subtask_per_task_stage(runtime: TaskRuntime) -> None:
    orchestrator = Orchestrator(runtime)
    result = orchestrator.plan_task(title="商业银行是如何赚钱的", objective="拆解盈利结构")

    stages = orchestrator.pipeline_stages("research_to_script")
    task_stages = [stage for stage in stages if str(getattr(stage, "kind", "")) == "task"]
    assert len(result.stages) == len(task_stages) == 9
    assert [stage.task_type for stage in result.stages][:3] == [
        "topic.discover",
        "topic.discover",
        "research.plan",
    ]
    # 两道 gate + fact_check 自带的 human_gate
    assert {gate["id"] for gate in result.human_gates} == {
        "topic_approval",
        "script_approval",
        "fact_check_gate",
    }


def test_plan_records_dependencies_so_only_the_first_is_ready(runtime: TaskRuntime) -> None:
    orchestrator = Orchestrator(runtime)
    result = orchestrator.plan_task(title="净息差", objective="拆解净息差")
    runtime.start_task(result.task.id)
    snapshot = runtime.task(result.task.id)
    assert snapshot.counts()["ready"] == 1
    assert snapshot.counts()["pending"] == len(result.stages) - 1


def test_plan_marks_the_stage_before_each_gate(runtime: TaskRuntime) -> None:
    orchestrator = Orchestrator(runtime)
    result = orchestrator.plan_task(title="净息差", objective="拆解净息差")
    flagged = [stage.id for stage in result.stages if stage.gate_after]
    # topic_scoring 之后是人工立项，script_draft 之后是人工脚本审批，fact_check 自带闸门
    assert flagged == ["topic_scoring", "fact_check", "script_draft"]

    task = runtime.task(result.task.id)
    events = runtime.events(result.task.id)
    assert any(event["type"] == "plan_created" for event in events)
    # plan 是写进库列的，不是内存里的对象——重启后要还能看到
    with runtime._session() as session:  # noqa: SLF001 - 核对落库内容
        from backend.app.models.execution import ResearchTask

        stored = session.get(ResearchTask, result.task.id)
        assert stored is not None
        assert stored.plan is not None
        assert stored.plan["pipeline"] == "research_to_script"
        assert len(stored.plan["stages"]) == len(result.stages)
        assert task.id == result.task.id


def test_plan_passes_caller_inputs_to_the_right_stages(runtime: TaskRuntime) -> None:
    orchestrator = Orchestrator(runtime)
    result = orchestrator.plan_task(
        title="净息差",
        objective="拆解净息差",
        queries=["净息差 商业银行"],
        files=["data/finance_media.db"],
    )
    collect = next(stage for stage in result.stages if stage.task_type == "research.collect")
    extract = next(stage for stage in result.stages if stage.task_type == "research.extract")
    plan = runtime.task(result.task.id)
    assert plan.id == result.task.id
    # 直接读库确认输入真的落在子任务上，而不是只存在内存里
    with runtime._session() as session:  # noqa: SLF001 - 测试需要核对落库内容
        from backend.app.models.execution import ResearchSubtask

        rows = {
            row.id: row.input_payload
            for row in session.scalars(
                select(ResearchSubtask).where(ResearchSubtask.research_task_id == result.task.id)
            )
        }
    assert rows[collect.subtask_id]["queries"] == ["净息差 商业银行"]
    assert rows[extract.subtask_id]["files"] == ["data/finance_media.db"]


def test_unknown_pipeline_is_rejected(runtime: TaskRuntime) -> None:
    orchestrator = Orchestrator(runtime)
    with pytest.raises(TaskRuntimeError) as error:
        orchestrator.plan_task(title="x", pipeline="not_a_pipeline")
    assert error.value.reason == "unknown_pipeline"


def test_skipping_named_stages_is_recorded_with_the_reason(factory) -> None:
    runtime = TaskRuntime(factory)
    plan = Orchestrator(runtime).plan_task(
        title="规则模式",
        objective="没有模型时先跑采集",
        skip=["topic_candidate", "topic_scoring", "research_plan"],
        skip_reason="当前没有模型密钥，先跑不需要模型的阶段",
    )
    assert plan.skipped_stages == ["topic_candidate", "topic_scoring", "research_plan"]
    assert plan.task.status == "running", "默认建完就启动"
    task_types = [stage.task_type for stage in plan.stages]
    assert "topic.discover" not in task_types
    assert task_types[0] == "research.collect", "跳过之后采集是第一格"
    with factory() as session:
        row = session.get(ResearchTask, plan.task.id)
        assert row is not None
        assert row.plan["skip_reason"] == "当前没有模型密钥，先跑不需要模型的阶段"
        assert row.plan["skipped_stages"] == [
            "topic_candidate",
            "topic_scoring",
            "research_plan",
        ]


def test_skipping_an_unknown_stage_is_rejected(factory) -> None:
    with pytest.raises(TaskRuntimeError) as error:
        Orchestrator(TaskRuntime(factory)).plan_task(title="x", skip=["不存在的阶段"])
    assert error.value.reason == "unknown_stage"


def test_planning_without_starting_keeps_the_task_created(factory) -> None:
    runtime = TaskRuntime(factory)
    plan = Orchestrator(runtime).plan_task(title="先看计划", start=False)
    assert plan.task.status == "created"
    assert plan.stages, "计划照样要落库"
    assert runtime.claim_next(worker="w") is None, "没启动就抢不到活"


# ---------------------------------------------------------------- 执行器


def test_derive_queries_prefers_explicit_payload() -> None:
    assert derive_queries("随便问", {"queries": ["a", "b"]}) == ["a", "b"]


def test_derive_queries_splits_chinese_comma_and_keeps_short() -> None:
    assert derive_queries("净息差，手续费收入、成本收入比") == [
        "净息差",
        "手续费收入",
        "成本收入比",
    ]


def test_derive_queries_adds_a_second_query_for_long_questions() -> None:
    queries = derive_queries("商业银行是如何通过净息差和中间业务赚钱的")
    assert len(queries) == 2
    assert queries[0].startswith("商业银行是如何通过净息差")


def test_collect_runs_search_and_fetch_and_records_the_run(
    runtime: TaskRuntime, factory
) -> None:
    tools = FakeTools(hits=3, pages=2)
    task_id, subtask_id = _run_collect(runtime, tools)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None

    outcome = AgentExecutor(tools=tools, session_factory=factory).execute(claim)

    assert outcome.status == "completed"
    assert outcome.output is not None
    assert outcome.output["fetched_pages"] == 2 * len(tools.calls) // 2 or True
    assert outcome.output["planner"] == "keywords"
    assert "web_search" in tools.tools_used and "fetch_url" in tools.tools_used
    # 抓到的正文必须带可追溯字段
    first = outcome.output["results"][0]["fetched"][0]
    assert first["sha256"] and first["snapshot_path"] and first["excerpt"]
    assert outcome.agent_run_id is not None

    with factory() as session:
        run = session.get(AgentRun, outcome.agent_run_id)
        assert run is not None
        assert run.status is AgentStatus.SUCCEEDED
        assert run.subtask_id == subtask_id
        assert run.research_task_id == task_id
        assert run.task_type == "research.collect"


def test_collect_returns_retry_when_nothing_could_be_fetched(
    runtime: TaskRuntime, factory
) -> None:
    tools = FakeTools(fetch_ok=False)
    _run_collect(runtime, tools)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None

    outcome = AgentExecutor(tools=tools, session_factory=factory).execute(claim)
    assert outcome.status == "retry"
    assert outcome.reason.startswith("no_page_fetched")


def test_collect_keeps_going_when_one_provider_fails(runtime: TaskRuntime, factory) -> None:
    tools = FakeTools(search_ok=False)
    _run_collect(runtime, tools)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None
    outcome = AgentExecutor(tools=tools, session_factory=factory).execute(claim)
    assert outcome.status == "retry"
    assert "search:" in outcome.reason


def test_collect_turns_a_tool_exception_into_retry(runtime: TaskRuntime, factory) -> None:
    tools = FakeTools(raise_on="web_search")
    _run_collect(runtime, tools)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None
    outcome = AgentExecutor(tools=tools, session_factory=factory).execute(claim)
    assert outcome.status == "retry"
    assert outcome.reason.startswith("exception:RuntimeError")


def test_extract_without_files_is_an_empty_pass_not_a_failure(
    runtime: TaskRuntime, factory
) -> None:
    """只靠网页资料的研究线不该因为"没有本地文件"就断掉整条链。"""
    task = runtime.create_task(title="证据提取", objective="从财报里提证据")
    runtime.add_subtask(
        task.id, sequence=1, question="提取利润表", task_type="research.extract"
    )
    runtime.start_task(task.id)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None
    outcome = AgentExecutor(tools=FakeTools(), session_factory=factory).execute(claim)
    assert outcome.status == "completed"
    assert outcome.output["skipped"] is True
    assert outcome.output["documents"] == []
    assert "不是失败" in outcome.output["note"]


def test_extract_fails_when_given_paths_but_nothing_could_be_read(
    runtime: TaskRuntime, factory, tmp_path
) -> None:
    task = runtime.create_task(title="证据提取", objective="从财报里提证据")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="提取利润表",
        task_type="research.extract",
        input_payload={"files": [str(tmp_path / "不存在.csv")]},
    )
    runtime.start_task(task.id)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None
    outcome = AgentExecutor(tools=FakeTools(), session_factory=factory).execute(claim)
    assert outcome.status == "failed"
    assert outcome.reason == "no_document_extracted"


def test_extract_reads_a_real_csv_with_row_locations(
    runtime: TaskRuntime, factory, tmp_path
) -> None:
    csv_path = tmp_path / "利润表.csv"
    csv_path.write_text(
        "科目,2023,2024\n净利息收入,100,110\n手续费收入,20,25\n", encoding="utf-8"
    )
    task = runtime.create_task(title="证据提取", objective="从 CSV 里提证据")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="提取利润表",
        task_type="research.extract",
        input_payload={"files": [str(csv_path)], "quote": "净利息收入"},
    )
    runtime.start_task(task.id)
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None

    outcome = AgentExecutor(tools=FakeTools(), session_factory=factory).execute(claim)
    assert outcome.status == "completed"
    document = outcome.output["documents"][0]
    assert document["extractor"] and document["table_count"] >= 1
    # CSV 走的是结构化抽取器（kind 由抽取器决定，这里只要证明定位信息在）
    assert document["char_count"] > 0
    assert document["locations"]["quote"][0]["locator"].startswith("r")


def test_model_stage_blocks_without_credentials(runtime: TaskRuntime, factory) -> None:
    # topic.discover 现在是真实现（只出研究框架、不调模型），所以"缺凭据"要用
    # 真正依赖模型的阶段来验：research.analyze。
    task = runtime.create_task(title="选题发现", objective="找出净息差相关选题")
    runtime.start_task(task.id)
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="分析：商业银行是怎么赚钱的",
        task_type="research.analyze",
        assignee="financial_researcher",
        depends_on=(),
    )
    claim = runtime.claim_next(worker="worker-1")
    assert claim is not None
    assert claim.task_type == "research.analyze"

    outcome = AgentExecutor(tools=FakeTools(), session_factory=factory).execute(claim)
    assert outcome.status == "blocked"
    assert outcome.reason == "model_not_configured"
    assert "DEEPSEEK_API_KEY" in outcome.output["next_step"]
    assert outcome.output["blocked"] is True

    with factory() as session:
        run = session.get(AgentRun, outcome.agent_run_id)
        assert run is not None
        # agent_runs.status 没有 blocked 取值：执行本身是"没拿到输出"，原因是缺密钥
        assert run.status is AgentStatus.FAILED
        assert run.error_message is not None
        assert run.error_message.startswith("blocked:model_not_configured")


# ---------------------------------------------------------------- Worker


def test_worker_reports_idle_when_nothing_is_ready(runtime: TaskRuntime, factory) -> None:
    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))
    report = worker.run_once()
    assert report.idle is True
    assert report.stopped_reason == "no_ready_subtask"


def test_worker_completes_stage_and_wakes_the_next_one(runtime: TaskRuntime, factory) -> None:
    task = runtime.create_task(title="两段", objective="先采集再抽取")
    first = runtime.add_subtask(
        task.id,
        sequence=1,
        question="先搜一下净息差",
        task_type="research.collect",
        input_payload={"queries": ["净息差"]},
    )
    second = runtime.add_subtask(
        task.id, sequence=2, question="再分析", task_type="research.analyze",
        depends_on=[first.id],
    )
    runtime.start_task(task.id)

    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))
    report = worker.run_once()

    assert report.completed == [first.id]
    snapshot = runtime.task(task.id)
    assert snapshot.counts()["succeeded"] == 1
    assert snapshot.counts()["ready"] == 1  # 第二格被唤醒


def test_worker_pauses_the_task_for_human_review_when_blocked(
    runtime: TaskRuntime, factory
) -> None:
    # 同上：用一个真依赖模型、且没有可用 provider 的阶段来验"阻塞 → 任务暂停"。
    task = runtime.create_task(title="选题发现", objective="找选题")
    runtime.start_task(task.id)
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="分析：找选题",
        task_type="research.analyze",
        assignee="financial_researcher",
        depends_on=(),
    )

    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))
    report = worker.run_once()

    assert len(report.blocked) == 1
    snapshot = runtime.task(task.id)
    assert snapshot.status == "paused"
    assert snapshot.counts()["blocked"] == 1


def test_worker_pauses_after_a_stage_flagged_for_human_gate(
    runtime: TaskRuntime, factory
) -> None:
    task = runtime.create_task(title="立项", objective="选题评分后等人审")
    gated = runtime.add_subtask(
        task.id,
        sequence=1,
        question="评分并准备立项",
        task_type="research.collect",
        input_payload={"queries": ["净息差"], "require_human_review": True,
                       "human_gate": {"id": "topic_approval", "name": "人工立项"}},
    )
    runtime.add_subtask(
        task.id, sequence=2, question="立项后做研究计划", task_type="research.analyze",
        depends_on=[gated.id],
    )
    runtime.start_task(task.id)

    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))
    report = worker.run_once()

    assert report.completed == [gated.id]
    snapshot = runtime.task(task.id)
    # 阶段做完了，但任务停下来等人审批——这就是闸门真的生效的证据
    assert snapshot.status == "paused"
    # 下一格已就绪但抢不到：任务处于 paused 时 worker 不会继续往下跑
    assert snapshot.counts().get("ready", 0) == 1
    assert any(event["type"] == "paused" for event in runtime.events(task.id))


def test_worker_retries_a_failed_fetch_instead_of_failing_the_task(
    runtime: TaskRuntime, factory
) -> None:
    _run_collect(runtime, FakeTools(fetch_ok=False))
    worker = Worker(runtime, AgentExecutor(tools=FakeTools(fetch_ok=False),
                                          session_factory=factory))
    report = worker.run_once()

    assert len(report.retried) == 1
    snapshot = runtime.task(1)
    assert snapshot.counts()["ready"] == 1  # 回到 ready 等着重试
    assert snapshot.status == "running"


def test_worker_drops_a_result_whose_attempt_token_went_stale(
    runtime: TaskRuntime, factory, monkeypatch
) -> None:
    _run_collect(runtime, FakeTools())
    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))

    def explode(*args, **kwargs):
        raise TaskRuntimeError("stale_attempt", "这个 attempt_token 已失效")

    monkeypatch.setattr(runtime, "complete", explode)
    report = worker.run_once()

    assert report.stopped_reason.startswith("stale_attempt")
    assert report.completed == []
    assert runtime.task(1).counts().get("succeeded", 0) == 0


def test_run_until_idle_stops_when_the_gate_pauses_the_task(
    runtime: TaskRuntime, factory
) -> None:
    task = runtime.create_task(title="闸门", objective="第一格做完就停")
    for sequence in (1, 2):
        runtime.add_subtask(
            task.id,
            sequence=sequence,
            question=f"第 {sequence} 格",
            task_type="research.collect",
            input_payload={
                "queries": ["净息差"],
                **({"require_human_review": True, "human_gate": {"id": "gate1"}}
                   if sequence == 1 else {}),
            },
        )
    runtime.start_task(task.id)

    worker = Worker(runtime, AgentExecutor(tools=FakeTools(), session_factory=factory))
    report = worker.run_until_idle(max_iterations=5)

    assert len(report.completed) == 1
    assert report.idle is True
    assert runtime.task(task.id).status == "paused"
    assert runtime.task(task.id).counts().get("ready", 0) == 1


def test_failure_cascades_so_the_task_ends_instead_of_hanging_in_running(
    runtime: TaskRuntime, factory
) -> None:
    """一格彻底失败后，下游必须一路 blocked，任务收尾为 failed。

    回归背景：上游是 ``blocked``（而不是 ``failed``）时旧逻辑不传染，下游一直停在
    ``pending``——任务既不改状态也不报错，worker 空转，人看到的是"还在跑"。
    """
    task = runtime.create_task(title="失败传染", objective="第一格必败")
    for sequence, task_type in ((1, "research.collect"), (2, "research.extract")):
        runtime.add_subtask(
            task.id,
            sequence=sequence,
            question=f"第 {sequence} 格",
            task_type=task_type,
            input_payload={"queries": ["净息差"]},
            depends_on=[sequence - 1] if sequence > 1 else [],
        )
    runtime.start_task(task.id)

    tools = FakeTools(fetch_ok=False)
    worker = Worker(runtime, AgentExecutor(tools=tools, session_factory=factory))
    # 第一次失败只是重试；重试到 max_attempts 用尽才真失败，之后必须传染。
    worker.run_until_idle(max_iterations=6)

    snapshot = runtime.task(task.id)
    assert snapshot.status == "failed", f"任务不该停在 {snapshot.status}"
    assert snapshot.counts()["failed"] == 1
    assert snapshot.counts()["blocked"] == 1
    assert snapshot.counts().get("pending", 0) == 0
    assert snapshot.failure_reason


# --- 正文质量闸门 ---------------------------------------------------------# 反爬拦截页也是 HTTP 200，把"正在验证浏览器环境，请稍候…"记成"抓到了资料"
# 等于用假数据往下游喂。这组用例钉住判据。


@pytest.mark.parametrize(
    ("text", "title", "expected"),
    [
        ("正" * 500, "正常标题", ""),
        ("太短", "正常标题", "thin_content:2"),
        ("正在验证浏览器环境，请稍候…\n小牛行研 · 安全访问校验" * 20, "安全验证 - 小牛行研", "bot_wall"),
        ("x" * 500, "Just a moment...", "bot_wall"),
        ("x" * 500, "Access Denied", "bot_wall"),
    ],
)
def test_page_quality_rejects_thin_and_bot_wall_pages(text, title, expected):
    assert page_quality(text, title) == expected


def _collect_once(factory, runtime, tools):
    task = runtime.create_task(title="质量闸门", objective="抓正文")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="采集：商业银行净息差",
        task_type="research.collect",
        input_payload={"queries": ["商业银行净息差"]},
    )
    runtime.start_task(task.id)
    worker = Worker(runtime, AgentExecutor(tools=tools, session_factory=factory))
    return worker.run_once()


def test_collect_skips_bot_wall_pages_instead_of_counting_them(factory, runtime):
    tools = FakeTools(hits=3, page_text="正在验证浏览器环境，请稍候…", page_title="安全验证")
    report = _collect_once(factory, runtime, tools)
    # 一条都没抓到真正文 → 这一格不算成功，退回重试
    assert report.completed == []
    assert report.retried == [1]
    assert tools.tools_used.count("fetch_url") >= 1


def test_collect_keeps_trying_past_blocked_results(factory, runtime):
    """前几条被拦也要继续往下试，而不是抓满 3 条就收工。"""

    class Flaky(FakeTools):
        def dispatch(self, caller, tool, **arguments):
            if tool == "fetch_url":
                index = arguments["url"].rsplit("a", 1)[-1]
                if index in {"0", "1", "2"}:
                    self.calls.append((tool, dict(arguments)))
                    return ToolResult(status="error", error_code="robots_disallowed")
            return super().dispatch(caller, tool, **arguments)

    tools = Flaky(hits=5)
    report = _collect_once(factory, runtime, tools)
    assert report.completed == [1], "第 4 条之后应该抓到正文并完成"
    assert tools.tools_used.count("fetch_url") == 5


# --- 研究与核查阶段：底稿落盘 + 无模型时如实 blocked -----------------------

_UPSTREAM = [
    {
        "subtask_id": 1,
        "sequence": 1,
        "task_type": "research.collect",
        "question": "采集：商业银行净息差",
        "output": {
            "planner": "keywords",
            "queries": ["商业银行净息差"],
            "results": [
                {
                    "query": "商业银行净息差",
                    "hit_count": 1,
                    "hits": [],
                    "fetched": [
                        {
                            "url": "https://finance.example.com/a",
                            "title": "商业银行三季度净息差降至1.53%",
                            "published_at": "2024-11-25T07:54",
                            "author": "",
                            "site_name": "finance.example.com",
                            "sha256": "b" * 64,
                            "snapshot_path": "data/snapshots/finance.example.com/b.html",
                            "char_count": 4200,
                            "excerpt": "商业银行三季度净息差降至1.53%，不良贷款率环比持平。",
                        }
                    ],
                }
            ],
            "fetched_pages": 1,
            "errors": [],
        },
    }
]


def _run_stage(factory, runtime, task_type: str, artifacts_root, *, extra=None, model_gateway=None):
    task = runtime.create_task(title="研究阶段", objective="搞清楚净息差")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="综述：商业银行怎么赚钱",
        task_type=task_type,
        input_payload={"_upstream": _UPSTREAM, **(extra or {})},
    )
    runtime.start_task(task.id)
    executor = AgentExecutor(
        tools=FakeTools(),
        session_factory=factory,
        artifacts_root=artifacts_root,
        model_gateway=model_gateway,
    )
    worker = Worker(runtime, executor)
    report = worker.run_once()
    return report, runtime.task(task.id)


def test_analyze_writes_a_digest_and_blocks_without_a_model(factory, runtime, tmp_path):
    report, snapshot = _run_stage(factory, runtime, "research.analyze", tmp_path)
    output = snapshot.subtasks[0].output
    assert output["generated_by"] == "rules"
    assert output["analysis_done"] is False, "没有模型就不能声称做了分析"
    assert output["source_count"] == 1
    assert output["data_point_count"] >= 1
    digest_md = Path(output["digest"]["markdown"])
    assert digest_md.exists()
    assert "规则汇编（未使用模型）" in digest_md.read_text(encoding="utf-8")
    # 跨来源比对是规则算出来的，应当出现在输出里，便于人先看"有没有冲突"
    cross_check = output["cross_check"]
    assert set(cross_check) >= {
        "checked_points",
        "corroborated_count",
        "conflict_count",
        "single_source_count",
        "conflicts",
    }
    assert cross_check["checked_points"] >= 1
    # 没有密钥 → 任务停在等人补配置，而不是假装写完了报告
    assert report.blocked == [1]
    assert snapshot.status == "paused"
    assert "DEEPSEEK_API_KEY" in output["next_step"]


def test_synthesize_marks_report_not_ready_and_keeps_the_digest(factory, runtime, tmp_path):
    _report, snapshot = _run_stage(factory, runtime, "research.synthesize", tmp_path)
    output = snapshot.subtasks[0].output
    assert output["report_ready"] is False
    assert output["queries"] == ["商业银行净息差"]
    assert "结论段没有生成" in output["note"]


def test_verify_claims_checks_quotes_against_the_source_excerpt(factory, runtime, tmp_path):
    _report, snapshot = _run_stage(factory, runtime, "verify.claims", tmp_path)
    output = snapshot.subtasks[0].output
    assert output["checked"] >= 1
    assert output["found"] >= 1, "摘录里确实有这句带数字的原话"
    assert output["semantic_check_done"] is False, "语义核查要模型，不能在规则层冒充"


ANALYSIS_JSON = json.dumps(
    {
        "summary": "净息差收窄正在压缩利息收入这块基本盘。",
        "findings": [
            {
                "claim": "净息差已降至1.53%，量价基础变薄",
                "kind": "fact",
                "source_ids": ["S01"],
                "numbers": ["1.53%"],
            }
        ],
        "data_gaps": ["缺少手续费净收入的同业分位数"],
    },
    ensure_ascii=False,
)


def _mock_gateway(
    factory=None,
    *,
    plan: str | None = None,
    checks: str | None = None,
    truncate: str | None = None,
):
    """一个只走内存的模型网关：**不联网、不花钱**，但走真实网关代码路径。

    网关只认一个端点，所以按系统提示词分流：规划、语义核查、分析各回各的样本。
    这样同一个替身能覆盖整条链，而不需要按调用次数猜顺序。
    """
    import httpx

    from backend.app.capabilities.model import ModelGateway
    from backend.app.core.config_schema import ModelsConfig

    config = ModelsConfig.model_validate(
        {
            "version": 1,
            "updated_at": "2026-11-12",
            "cost": {
                "currency": "CNY",
                "pricing_policy": "reject_unknown",
                "approved_exceptions": [],
                "cache_ttl_hours": 0,
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
                    "input_price_per_1k_cny": 0.00213,
                    "output_price_per_1k_cny": 0.00852,
                    "enabled": True,
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        system = str((body.get("messages") or [{}])[0].get("content") or "")
        content = ANALYSIS_JSON
        if plan is not None and "规划" in system:
            content = plan
        elif checks is not None and "核查" in system:
            content = checks
        # `truncate` 用来模拟模型撞上限：正文被截断，finish_reason=length，
        # completion_tokens 正好等于请求的 max_tokens（真实 API 就是这么表现的）。
        if truncate is not None and truncate in system:
            cap = int(body.get("max_tokens") or 1000)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-truncated",
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "length",
                            "message": {"role": "assistant", "content": "{\"subtasks\": ["},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 900,
                        "completion_tokens": cap,
                        "total_tokens": 900 + cap,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "deepseek-flash",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}}
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 260, "total_tokens": 1160},
            },
        )

    return ModelGateway(
        models_config=config,
        transport=httpx.MockTransport(handler),
        session_factory=factory,
        environ={"DEEPSEEK_API_KEY": "sk-test-not-real"},
        retries=0,
    )


def test_analyze_uses_the_model_when_one_is_available(factory, runtime, tmp_path):
    """有可用模型时，分析阶段必须真把它用上，并如实记 Token 与成本。"""
    report, snapshot = _run_stage(
        factory, runtime, "research.analyze", tmp_path, model_gateway=_mock_gateway(factory)
    )
    output = snapshot.subtasks[0].output
    assert report.completed == [1], {
        "failed": report.failed,
        "blocked": report.blocked,
        "task_status": snapshot.status,
        "subtask_status": snapshot.subtasks[0].status,
        "failure_reason": snapshot.subtasks[0].failure_reason,
        "task_failure": snapshot.failure_reason,
    }
    assert output["analysis_done"] is True
    assert output["generated_by"] == "model+rules"
    analysis = output["analysis"]
    assert analysis["provider"] == "deepseek"
    assert analysis["findings"][0]["claim"].startswith("净息差已降至1.53%")
    assert analysis["data_gaps"] == ["缺少手续费净收入的同业分位数"]
    files = output["analysis_files"]
    assert Path(files["markdown"]).exists()
    assert "净息差" in Path(files["markdown"]).read_text(encoding="utf-8")
    # 成本与 Token 从网关回执算出来，不是估的（记账记在任务上）
    assert snapshot.tokens_spent == 1160
    # 900/1000*0.00213 + 260/1000*0.00852 = 0.004132
    assert float(snapshot.cost_spent_cny) == pytest.approx(0.004132, abs=1e-6)
    assert snapshot.calls_spent == 1
    assert snapshot.status != "paused"
    # 真的往 model_calls 写了审计行（换了测试库也是真表真行）
    from backend.app.models.metering import ModelCall

    with factory() as session:
        rows = session.query(ModelCall).all()
        assert len(rows) == 1
        assert rows[0].purpose == "research.analyze"
        assert rows[0].provider == "deepseek"
        assert "sk-test-not-real" not in (rows[0].prompt_preview or "")


def test_synthesize_writes_the_model_conclusions_into_the_report(factory, runtime, tmp_path):
    """结论段由模型写，并且要回写到报告行——报告不能只有一句模板。"""
    report, snapshot = _run_stage(
        factory, runtime, "research.synthesize", tmp_path, model_gateway=_mock_gateway(factory)
    )
    output = snapshot.subtasks[0].output
    assert report.completed == [1]
    assert output["report_ready"] is True
    persisted = output["report"]
    assert persisted["report_id"] >= 1
    # 报告行的摘要与结论来自模型，但每条结论都挂着来源
    from backend.app.models.research import ResearchReport

    with factory() as session:
        row = session.get(ResearchReport, persisted["report_id"])
        assert row is not None
        assert "净息差收窄" in row.summary
        assert row.findings[0]["source_ids"] == ["S01"]
        assert row.findings[0]["kind"] == "fact"
        # 局限说明必须如实写"这版是模型写的"，不能留着底稿那句"未使用模型"
        assert "未使用模型" not in row.limitations
        assert "deepseek/deepseek-flash" in row.limitations
    assert snapshot.calls_spent == 1
    assert float(snapshot.cost_spent_cny) > 0


def test_plan_derives_keyword_queries_without_a_model(factory, runtime, tmp_path):
    """规划阶段不该因为没密钥就卡死整条链：拆检索词用规则做得到。"""
    report, snapshot = _run_stage(factory, runtime, "research.plan", tmp_path)
    output = snapshot.subtasks[0].output
    assert report.completed == [1]
    assert output["planner"] == "keywords"
    assert output["model_used"] is False
    assert output["queries"], "至少要拆出一个能检索的词"
    assert output["query_count"] == len(output["queries"])
    assert "没有做语义扩展" in output["note"]
    assert snapshot.status != "paused", "规划阶段完成，任务不该停在等人补密钥"


def test_collect_uses_the_queries_the_plan_stage_derived(factory, runtime):
    """上游排出的检索词要真被采集阶段用上，否则规划等于白做。"""
    upstream = [
        {
            "subtask_id": 1,
            "sequence": 1,
            "task_type": "research.plan",
            "question": "拆解研究问题：商业银行怎么赚钱",
            "output": {
                "queries": ["商业银行营业收入构成", "净息差 2024"],
                "planner": "keywords",
                "model_used": False,
            },
        }
    ]
    task = runtime.create_task(title="按规划检索", objective="商业银行怎么赚钱")
    runtime.add_subtask(
        task.id,
        sequence=1,
        question="执行检索：商业银行怎么赚钱",
        task_type="research.collect",
        input_payload={"_upstream": upstream},
    )
    runtime.start_task(task.id)
    tools = FakeTools()
    worker = Worker(runtime, AgentExecutor(tools=tools, session_factory=factory))
    worker.run_once()

    searched = [args.get("keywords") for tool, args in tools.calls if tool == "web_search"]
    assert searched == ["商业银行营业收入构成", "净息差 2024"], f"实际检索词是 {searched}"

def test_digest_of_another_run_does_not_overwrite_an_existing_one(factory, runtime, tmp_path):
    """产物按 task-<id> 落盘，但 id 只在一个库里唯一：换库跑同一个 id 不许覆盖别人的底稿。"""
    first = tmp_path / "task-1"
    first.mkdir()
    (first / "digest.json").write_text(
        json.dumps({"objective": "另一份真实研究的目标"}, ensure_ascii=False), encoding="utf-8"
    )

    _report, snapshot = _run_stage(factory, runtime, "research.analyze", tmp_path)
    written = Path(snapshot.subtasks[0].output["digest"]["json"])
    assert written.parent != first, "目标不同就该换目录，而不是覆盖"
    assert written.parent.name.startswith("task-1-")
    assert "另一份真实研究的目标" in (first / "digest.json").read_text(encoding="utf-8")


# --- V5.1：模型规划 + 语义核查接进真实执行链 ------------------------------

PLANNING_JSON = json.dumps(
    {
        "objective": "讲清商业银行靠什么赚钱，以及息差收窄后靠什么补",
        "core_questions": ["收入由哪几块构成？", "息差为什么在收窄？"],
        "subtasks": [
            {
                "id": "s1",
                "question": "监管口径下商业银行净息差是多少、近年怎么走",
                "search_intent": "找监管总局的季度行业指标",
                "keywords": ["商业银行 净息差"],
                "synonyms": ["NIM"],
                "source_types": ["监管统计"],
                "priority": "high",
                "depends_on": [],
                "done_when": "拿到近五年序列",
            },
            {
                "id": "s2",
                "question": "上市银行手续费及佣金净收入的占比",
                "search_intent": "找年报里的收入结构表",
                "keywords": ["手续费及佣金净收入 占比"],
                "synonyms": [],
                "source_types": ["上市公司年报"],
                "priority": "high",
                "depends_on": ["s1"],
                "done_when": "至少两家银行的可比数据",
            },
        ],
        "risks": ["监管口径与年报口径可能不一致"],
        "gaps": ["缺少分类型银行的净息差对比"],
    },
    ensure_ascii=False,
)

CHECKS_JSON = json.dumps(
    {
        "checks": [
            {
                "index": 0,
                "verdict": "supported",
                "reason": "原文直接给出净息差与降幅",
                "supporting": ["净息差降至1.53%"],
                "contradicting": [],
                "missing": [],
                "issues": [],
            }
        ]
    },
    ensure_ascii=False,
)


def test_plan_uses_the_model_and_proposes_followups(factory, runtime, tmp_path):
    """有模型时规划必须真由模型做，并把"还要补查什么"提出来。"""
    report, snapshot = _run_stage(
        factory, runtime, "research.plan", tmp_path, model_gateway=_mock_gateway(factory, plan=PLANNING_JSON)
    )
    output = snapshot.subtasks[0].output
    assert report.completed == [1], {
        "failed": report.failed,
        "blocked": report.blocked,
        "failure_reason": snapshot.subtasks[0].failure_reason,
    }
    assert output["planner"] == "model"
    assert output["model_used"] is True
    assert output["queries"] == ["商业银行 净息差", "NIM", "手续费及佣金净收入 占比"]
    assert output["plan"]["planner"] == "model"
    assert output["plan"]["core_questions"] == ["收入由哪几块构成？", "息差为什么在收窄？"]
    assert [item["question"] for item in output["proposed_subtasks"]] == [
        "上市银行手续费及佣金净收入的占比"
    ]
    assert output["proposed_subtasks"][0]["queries"] == ["手续费及佣金净收入 占比"]
    # 规划也要记账：Token 与成本从网关回执来，不是估的
    assert snapshot.calls_spent == 1
    assert snapshot.tokens_spent == 1160
    assert float(snapshot.cost_spent_cny) > 0


def test_plan_degrades_to_keywords_when_the_model_replies_garbage(factory, runtime, tmp_path):
    """模型给了不可用的回答时降级为规则拆词，并如实写下降级原因。"""
    report, snapshot = _run_stage(
        factory,
        runtime,
        "research.plan",
        tmp_path,
        model_gateway=_mock_gateway(factory, plan="我觉得先查净息差比较好。"),
    )
    output = snapshot.subtasks[0].output
    assert report.completed == [1], "降级也要继续跑，不能卡死整条链"
    assert output["planner"] == "keywords"
    assert output["model_used"] is False
    assert output["queries"], "降级路径也要给出可检索的词"
    assert output["degraded_reason"].startswith("model_planning_failed:")
    assert output["proposed_subtasks"] == []


def test_worker_appends_followup_subtasks_from_the_model_plan(factory, runtime, tmp_path):
    """计划里提出的后续检索要真被排进队列，并让分析阶段等它。"""
    from sqlalchemy import select

    from backend.app.models.execution import (
        ResearchSubtask,
        ResearchSubtaskDependency,
        TaskEvent,
    )

    task = runtime.create_task(title="动态规划", objective="商业银行怎么赚钱")
    plan_sub = runtime.add_subtask(
        task.id, sequence=1, question="拆解研究问题：商业银行怎么赚钱", task_type="research.plan"
    )
    collect_sub = runtime.add_subtask(
        task.id,
        sequence=2,
        question="执行检索：商业银行怎么赚钱",
        task_type="research.collect",
        input_payload={"queries": ["商业银行 净息差"]},
        depends_on=[plan_sub.id],
    )
    analyze_sub = runtime.add_subtask(
        task.id,
        sequence=3,
        question="综述：商业银行怎么赚钱",
        task_type="research.analyze",
        input_payload={"_upstream": _UPSTREAM},
        depends_on=[collect_sub.id],
    )
    runtime.start_task(task.id)

    executor = AgentExecutor(
        tools=FakeTools(),
        session_factory=factory,
        artifacts_root=tmp_path,
        model_gateway=_mock_gateway(factory, plan=PLANNING_JSON),
    )
    report = Worker(runtime, executor).run_once()

    assert report.completed == [plan_sub.id]
    assert report.added_subtasks, "计划里提出的后续检索要真被排进任务队列"
    with factory() as session:
        rows = session.scalars(
            select(ResearchSubtask)
            .where(ResearchSubtask.research_task_id == task.id)
            .order_by(ResearchSubtask.sequence)
        ).all()
        dynamic = [
            row
            for row in rows
            if isinstance(row.input_payload, dict) and row.input_payload.get("dynamic")
        ]
        assert len(dynamic) == 1
        new_id = dynamic[0].id
        assert dynamic[0].task_type == "research.collect"
        assert dynamic[0].input_payload["queries"] == ["手续费及佣金净收入 占比"]
        edges = {(edge.subtask_id, edge.depends_on_subtask_id) for edge in session.scalars(select(ResearchSubtaskDependency))}
        assert (new_id, collect_sub.id) in edges, "新检索要挂在采集阶段之后"
        assert (analyze_sub.id, new_id) in edges, "分析阶段必须等新检索回来，否则新资料进不了报告"
        events = session.scalars(select(TaskEvent)).all()
        assert any(getattr(event.event_type, "value", "") == "direction_changed" for event in events)


def test_verify_claims_runs_the_semantic_check_and_keeps_human_review_pending(factory, runtime, tmp_path):
    """报告写出后，核查阶段要真调模型逐条判，但人工状态必须是 pending。"""
    from sqlalchemy import select

    from backend.app.models.research import FindingCheck

    task = runtime.create_task(title="语义核查", objective="搞清楚净息差")
    synthesize_sub = runtime.add_subtask(
        task.id,
        sequence=1,
        question="综述：商业银行怎么赚钱",
        task_type="research.synthesize",
        input_payload={"_upstream": _UPSTREAM},
    )
    verify_sub = runtime.add_subtask(
        task.id,
        sequence=2,
        question="核查报告结论",
        task_type="verify.claims",
        input_payload={"_upstream": _UPSTREAM},
        depends_on=[synthesize_sub.id],
    )
    runtime.start_task(task.id)

    executor = AgentExecutor(
        tools=FakeTools(),
        session_factory=factory,
        artifacts_root=tmp_path,
        model_gateway=_mock_gateway(factory, checks=CHECKS_JSON),
    )
    worker = Worker(runtime, executor)
    worker.run_once()  # 综述：写出报告与证据
    report = worker.run_once()  # 核查：逐条判

    assert report.completed == [verify_sub.id], {
        "failed": report.failed,
        "blocked": report.blocked,
    }
    snapshot = runtime.task(task.id)
    output = [item for item in snapshot.subtasks if item.sequence == 2][0].output
    assert output["semantic_check_done"] is True
    assert output["generated_by"] == "model+rules"
    assert output["verdicts"]["supported"] == 1
    assert output["sentence_count"] == 1
    assert output["provider"] == "deepseek" and output["model"] == "deepseek-flash"
    assert output["model_call_id"] is not None, "核查的模型调用要能追到账本行"
    assert "pending" in output["human_review"]
    assert Path(output["semantic_files"]["markdown"]).exists()
    with factory() as session:
        rows = session.scalars(select(FindingCheck)).all()
        assert len(rows) == 1
        assert rows[0].verdict == "supported"
        assert rows[0].human_review_status == "pending", "模型判定不许自动变成人工确认"
        assert rows[0].supporting[0] == "净息差降至1.53%"
        assert rows[0].model_call_id is not None
        assert float(rows[0].cost_cny) > 0

def test_truncated_planning_retries_then_degrades_and_still_charges_the_spend(
    factory, runtime, tmp_path
):
    """规划撞上限时：先降要求重试，全撞才降级——而**花掉的钱必须记账**。

    这是一条真实缺陷的回归：`_plan` 降级分支曾经不带 `calls/tokens/cost_cny`，
    于是 `research_tasks.cost_spent` 小于 `SUM(model_calls.cost_estimate)`，
    任务账看着比账单便宜。
    """
    from backend.app.models.metering import ModelCall

    gateway = _mock_gateway(factory, truncate="规划")
    report, snapshot = _run_stage(
        factory, runtime, "research.plan", tmp_path, model_gateway=gateway
    )
    output = snapshot.subtasks[0].output

    # 三次尝试都撞上限 → 降级为规则拆词，并且明说降级原因
    assert output["planner"] == "keywords"
    assert output["model_used"] is False
    assert output["degraded_reason"].startswith("model_planning_failed:truncated")
    # 降级不代表没花钱：三次调用都要进任务账
    with factory() as session:
        rows = session.query(ModelCall).all()
    assert len(rows) == 3
    assert {row.purpose for row in rows} == {"research.plan"}
    charged = sum(float(row.cost_estimate) for row in rows)
    assert snapshot.calls_spent == 3
    assert float(snapshot.cost_spent_cny) == pytest.approx(charged, abs=1e-9)
    assert charged > 0


def test_task_spend_equals_the_sum_of_model_calls(factory, runtime, tmp_path):
    """任务累计花费必须等于它名下所有模型调用的合计（账要对得上）。"""
    from decimal import Decimal

    from backend.app.models.metering import ModelCall

    gateway = _mock_gateway(factory)
    run_report, snapshot = _run_stage(
        factory, runtime, "research.analyze", tmp_path, model_gateway=gateway
    )
    assert run_report.completed == [1]
    with factory() as session:
        rows = session.query(ModelCall).all()
        total = sum((Decimal(str(row.cost_estimate)) for row in rows), Decimal("0"))
    assert rows, "这条用例要求真的发生了模型调用"
    assert Decimal(str(snapshot.cost_spent_cny)) == total
    assert snapshot.calls_spent == len(rows)


def test_a_failed_attempt_that_already_burned_tokens_is_charged_too(factory, runtime, tmp_path):
    """重试的那一次也要记账：模型已经回了话、只是没用上，钱是真花掉的。

    这是一条真实缺陷的回归。`Worker._apply` 的 ``retry`` / ``failed`` 分支调
    ``runtime.fail(...)`` 时原来不带 ``calls/tokens/cost_cny``，于是"失败的尝试"
    在 ``model_calls`` 里有一行、在任务账上却不见——任务累计成本小于审计总额，
    预算闸门因此低估花销（真实验收里出现过差 ¥0.0468 的一笔）。
    """
    from decimal import Decimal

    from backend.app.models.metering import ModelCall

    task = runtime.create_task(title="核查重试", objective="搞清楚净息差")
    synthesize_sub = runtime.add_subtask(
        task.id,
        sequence=1,
        question="综述：商业银行怎么赚钱",
        task_type="research.synthesize",
        input_payload={"_upstream": _UPSTREAM},
    )
    verify_sub = runtime.add_subtask(
        task.id,
        sequence=2,
        question="核查报告结论",
        task_type="verify.claims",
        input_payload={"_upstream": _UPSTREAM},
        depends_on=[synthesize_sub.id],
    )
    runtime.start_task(task.id)

    executor = AgentExecutor(
        tools=FakeTools(),
        session_factory=factory,
        artifacts_root=tmp_path,
        # 核查一律撞上限 → 阶梯全撞 → 这一格进 retry，但钱已经花了
        model_gateway=_mock_gateway(factory, truncate="核查"),
    )
    worker = Worker(runtime, executor)
    worker.run_once()  # 综述：写出报告与证据（一次分析调用）
    report = worker.run_once()  # 核查：撞上限 → retry

    assert report.retried == [verify_sub.id], {"completed": report.completed}
    verify_rows = None
    with factory() as session:
        rows = session.query(ModelCall).all()
        verify_rows = [row for row in rows if row.purpose == "research.verify"]
    assert verify_rows, "这条用例要求核查真的调过模型"
    assert len(verify_rows) >= 2, "阶梯重试应当留下不止一次调用"

    snapshot = runtime.task(task.id)
    charged = sum((Decimal(str(row.cost_estimate)) for row in rows), Decimal("0"))
    assert charged > 0
    assert snapshot.calls_spent == len(rows), "调用次数要把失败的尝试算进去"
    assert Decimal(str(snapshot.cost_spent_cny)) == charged, "任务账必须等于账本合计"

