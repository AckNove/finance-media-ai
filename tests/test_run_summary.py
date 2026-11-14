"""一次推进的人话总结：做完哪几格、停在哪、下一步干什么。

为什么要有这一层：``POST /run`` 原来只回 ``completed=[2,3]`` 这类子任务号，
界面上显示"这一轮做完了 N 格"，人看不出推进了哪些阶段、是不是又卡在另一道门上。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.services.run_summary import summarise_run
from backend.app.services.task_gate import GateInfo


@dataclass
class _Subtask:
    id: int
    sequence: int
    task_type: str
    status: str = "pending"
    failure_reason: str | None = None


@dataclass
class _Report:
    completed: list[int] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)
    retried: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    iterations: int = 0
    idle: bool = False
    stopped_reason: str = ""


def _rows() -> list[_Subtask]:
    return [
        _Subtask(id=1, sequence=1, task_type="topic.discover", status="succeeded"),
        _Subtask(id=2, sequence=2, task_type="research.plan", status="succeeded"),
        _Subtask(id=3, sequence=3, task_type="research.collect", status="failed",
                 failure_reason="no_page_fetched:search:feature_disabled"),
    ]


def test_stopping_at_a_gate_says_which_gate_and_what_next() -> None:
    """停在闸门时，必须点名是哪道门、下一步看哪里——只说"N 格"等于没说。"""

    gate = GateInfo(
        gate_id="topic_approval",
        label="人工立项",
        subtask_id=2,
        after_sequence=2,
        next_step="看完选题拆解再决定放不放",
    )
    report = _Report(completed=[1, 2], iterations=2, stopped_reason="no_ready_subtask")

    summary = summarise_run(
        report, subtasks=_rows(), task_status="paused", gate=gate
    )

    assert summary.stopped_at == "gate"
    assert "人工立项" in summary.headline
    assert summary.next_step == "看完选题拆解再决定放不放"
    assert any("第 2 格 research.plan" in line for line in summary.lines)


def test_no_progress_while_already_at_the_gate_does_not_claim_it_advanced() -> None:
    """已经在门上时再点一次推进，不能写成"又停在…"——那会让人以为推进过。"""

    gate = GateInfo(
        gate_id="topic_approval",
        label="人工立项",
        subtask_id=2,
        after_sequence=2,
        next_step="先放行",
    )
    report = _Report(iterations=0, idle=True, stopped_reason="no_ready_subtask")

    summary = summarise_run(report, subtasks=_rows(), task_status="paused", gate=gate)

    assert summary.stopped_at == "gate"
    assert "推进 0 格" not in summary.headline
    assert "没有可推进的格子" in summary.headline


def test_each_completed_stage_is_named_with_its_status() -> None:
    report = _Report(completed=[1, 2], iterations=2, stopped_reason="max_iterations")

    summary = summarise_run(report, subtasks=_rows(), task_status="running")

    assert summary.stopped_at == "max_iterations"
    assert "还没做完" in summary.headline
    assert any("topic.discover" in line for line in summary.lines)
    assert any("（succeeded）" in line for line in summary.lines)


def test_blocked_stage_reason_is_shown_verbatim() -> None:
    """阻塞原因原样带出来，不翻译成"失败"——阻塞不是失败，处理方式不一样。"""

    report = _Report(blocked=[3], iterations=1, idle=True, stopped_reason="no_ready_subtask")

    summary = summarise_run(report, subtasks=_rows(), task_status="paused")

    blocked_line = next(line for line in summary.lines if line.startswith("卡住了"))
    assert "research.collect" in blocked_line
    assert "feature_disabled" in blocked_line


def test_failed_stage_is_not_reported_as_blocked() -> None:
    report = _Report(failed=[3], iterations=1)

    summary = summarise_run(report, subtasks=_rows(), task_status="failed")

    assert summary.stopped_at == "failed"
    assert any(line.startswith("失败了") for line in summary.lines)
    assert not any(line.startswith("卡住了") for line in summary.lines)


def test_retried_stage_is_listed_separately() -> None:
    report = _Report(retried=[3], iterations=1, stopped_reason="max_iterations")

    summary = summarise_run(report, subtasks=_rows(), task_status="running")

    assert any(line.startswith("重试了") for line in summary.lines)


def test_finished_task_points_at_the_report() -> None:
    report = _Report(completed=[1, 2], iterations=2, idle=True)

    summary = summarise_run(report, subtasks=_rows(), task_status="completed")

    assert summary.stopped_at == "completed"
    assert "研究报告" in summary.next_step


def test_unknown_subtask_id_does_not_crash() -> None:
    """回执里出现库里查不到的 id（并发/删过）时，只能退化成编号，不能抛异常。"""

    report = _Report(completed=[99], iterations=1)

    summary = summarise_run(report, subtasks=_rows(), task_status="running")

    assert any("#99" in line for line in summary.lines)


def test_as_dict_is_json_ready() -> None:
    report = _Report(completed=[1], iterations=1)
    payload = summarise_run(report, subtasks=_rows(), task_status="running").as_dict()

    assert set(payload) == {"headline", "lines", "stopped_at", "next_step"}
    assert isinstance(payload["lines"], list)


def _make_task(client) -> int:
    columns = client.get("/api/topics/columns").json()
    column_id = columns[0]["id"] if columns else None
    topic = client.post(
        "/api/topics",
        json={
            "title": "推进之后发生什么",
            "description": "验证一次推进的人话总结",
            "column_id": column_id,
        },
    )
    response = client.post(
        "/api/research-tasks",
        json={
            "title": "推进总结验收",
            "objective": "商业银行净息差为什么一路往下",
            "topic_id": topic.json().get("id"),
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_run_endpoint_returns_the_summary(client) -> None:
    """推进两格：前两格都是规则实现的 topic.discover，第二格做完就停在人工立项门。"""

    task_id = _make_task(client)

    response = client.post(f"/api/research-tasks/{task_id}/run", json={"max_iterations": 2})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["iterations"] >= 1
    summary = body["summary"]
    assert summary is not None
    assert summary["stopped_at"] == "gate"
    assert "人工立项" in summary["headline"]
    assert summary["next_step"]
    assert any("topic.discover" in line for line in summary["lines"])
