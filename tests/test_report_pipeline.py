"""`GET /api/research-reports/{id}/pipeline` 的回归测试（全离线，不联网、不调模型）。

这条接口存在的理由：人在「语义核查」页逐条确认完，页面上必须能立刻看到
"现在能不能点继续跑"。以前这一步要自己去「研究任务」页翻是哪条任务、卡在哪个阶段。
口径：**确认本身不会触发花钱**，跑不跑由人按键决定。
"""

from __future__ import annotations

from backend.app.models.enums import ResearchTaskStatus, SubtaskStatus
from backend.app.models.execution import ResearchSubtask, ResearchTask
from backend.app.models.research import FindingCheck, ResearchReport


def _task(db, *, status: ResearchTaskStatus = ResearchTaskStatus.PAUSED, reason: str = "") -> ResearchTask:
    task = ResearchTask(
        title="商业银行是怎么赚钱的",
        objective="查清商业银行的收入结构",
        status=status,
        failure_reason=reason or None,
        requested_by="human",
    )
    db.add(task)
    db.flush()
    return task


def _blocked_subtask(db, task: ResearchTask, *, reason: str, order: int = 9) -> ResearchSubtask:
    row = ResearchSubtask(
        research_task_id=task.id,
        sequence=order,
        question="写脚本",
        task_type="content.script",
        assignee="scriptwriter",
        status=SubtaskStatus.BLOCKED,
        failure_reason=reason,
    )
    db.add(row)
    db.flush()
    return row


def _report(db, task: ResearchTask | None, *, findings: list[dict] | None = None) -> ResearchReport:
    report = ResearchReport(
        title="商业银行是怎么赚钱的",
        summary="净息差在收窄。",
        findings=findings or [{"text": "净息差降至1.52%", "kind": "fact", "source_ids": ["S01"]}],
        limitations="只用公开资料。",
        research_task_id=task.id if task is not None else None,
    )
    db.add(report)
    db.flush()
    return report


def _check(db, report: ResearchReport, *, status: str, index: int = 0) -> FindingCheck:
    row = FindingCheck(
        research_report_id=report.id,
        finding_index=index,
        claim="净息差降至1.52%",
        verdict="supported",
        reason="原文逐字命中",
        human_review_status=status,
    )
    db.add(row)
    db.flush()
    return row


def test_blocked_on_confirmation_tells_the_human_what_to_do_first(client, db_session) -> None:
    task = _task(db_session)
    _blocked_subtask(db_session, task, reason="no_confirmed_findings")
    report = _report(db_session, task)
    _check(db_session, report, status="pending")
    db_session.commit()

    response = client.get(f"/api/research-reports/{report.id}/pipeline")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["research_task_id"] == task.id
    assert payload["task_status"] == "paused"
    assert payload["paused"] is True
    assert payload["pending_checks"] == 1
    assert payload["confirmed_checks"] == 0
    # 没有确认过，就不能点继续跑 —— 这条闸门就是"人工确认"，模型判定不算
    assert payload["can_resume"] is False
    assert "先" in payload["next_step"] and "确认" in payload["next_step"]
    assert payload["blocked"]["failure_reason"] == "no_confirmed_findings"
    assert payload["blocked"]["task_type"] == "content.script"


def test_after_one_confirmation_the_same_endpoint_allows_resuming(client, db_session) -> None:
    task = _task(db_session)
    _blocked_subtask(db_session, task, reason="no_confirmed_findings")
    report = _report(db_session, task)
    _check(db_session, report, status="confirmed", index=0)
    _check(db_session, report, status="pending", index=1)
    db_session.commit()

    payload = client.get(f"/api/research-reports/{report.id}/pipeline").json()

    assert payload["confirmed_checks"] == 1
    assert payload["pending_checks"] == 1
    assert payload["can_resume"] is True
    assert "继续跑" in payload["next_step"]


def test_a_running_task_is_not_something_the_human_must_push(client, db_session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.RUNNING)
    report = _report(db_session, task)
    db_session.commit()

    payload = client.get(f"/api/research-reports/{report.id}/pipeline").json()

    assert payload["task_status"] == "running"
    assert payload["can_resume"] is False
    assert payload["blocked"] is None
    assert "正在跑" in payload["next_step"]


def test_a_failed_task_shows_the_reason_instead_of_pretending_to_resume(client, db_session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.FAILED, reason="抓取连续失败")
    report = _report(db_session, task)
    db_session.commit()

    payload = client.get(f"/api/research-reports/{report.id}/pipeline").json()

    assert payload["task_status"] == "failed"
    assert payload["can_resume"] is False
    assert payload["note"] == "抓取连续失败"


def test_a_report_without_a_task_says_so_plainly(client, db_session) -> None:
    report = _report(db_session, None)
    db_session.commit()

    payload = client.get(f"/api/research-reports/{report.id}/pipeline").json()

    assert payload["research_task_id"] is None
    assert payload["can_resume"] is False
    assert "没有关联研究任务" in payload["next_step"]


def test_unknown_report_is_a_404(client) -> None:
    response = client.get("/api/research-reports/999999/pipeline")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
