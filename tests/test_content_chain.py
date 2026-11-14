"""整条链路（研究 → 报告 → 核查 → 脚本 → 成片 → 排期 → 导出）的判定。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.models.enums import (
    Platform,
    PublishState,
    ResearchTaskStatus,
    ScriptStatus,
    SubtaskStatus,
    VerificationStatus,
)
from backend.app.models.execution import ResearchSubtask, ResearchTask
from backend.app.models.operations import ContentCalendar
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.models.script import Script
from backend.app.models.topic import Topic
from backend.app.services import content_chain
from backend.app.services.content_chain import ChainError, chain_of

STAGE_ORDER = ["research", "report", "checks", "script", "film", "schedule", "export"]


def _topic(session: Session) -> int:
    topic = Topic(title=f"链路 {uuid4().hex[:6]}", slug=f"chain-{uuid4().hex[:8]}")
    session.add(topic)
    session.commit()
    return topic.id


def _report(session: Session, *, task_id: int | None = None) -> ResearchReport:
    report = ResearchReport(
        title=f"链路报告 {uuid4().hex[:6]}",
        summary="一句话结论。",
        findings=[],
        limitations="探针数据。",
        verification_status=VerificationStatus.UNVERIFIED,
        jurisdiction="cn",
        version=1,
        research_task_id=task_id,
    )
    session.add(report)
    session.commit()
    return report


def _task(session: Session, *, status: ResearchTaskStatus = ResearchTaskStatus.PAUSED) -> ResearchTask:
    task = ResearchTask(
        title=f"链路任务 {uuid4().hex[:6]}",
        objective="研究点什么",
        status=status,
        planner="pipeline:research_only",
        requested_by="human",
        failure_reason="抓取连续失败" if status is ResearchTaskStatus.FAILED else None,
        subtasks=[
            ResearchSubtask(
                sequence=1,
                question="第一格",
                task_type="topic.discover",
                status=SubtaskStatus.SUCCEEDED,
                max_attempts=2,
            )
        ],
    )
    session.add(task)
    session.commit()
    return task


def _check(session: Session, report: ResearchReport, *, human: str) -> FindingCheck:
    row = FindingCheck(
        research_report_id=report.id,
        finding_index=0,
        claim="净息差在下降",
        verdict="supported",
        human_review_status=human,
        cost_cny=Decimal("0"),
    )
    session.add(row)
    session.commit()
    return row


def _script(session: Session, report: ResearchReport, *, status: ScriptStatus) -> Script:
    row = Script(
        topic_id=_topic(session),
        research_report_id=report.id,
        title="脚本",
        body="第一句。\n第二句。",
        version=1,
        status=status,
        segments=[{"text": "第一句。", "role": "evidence", "source_ids": ["S01"], "numbers": []}],
    )
    session.add(row)
    session.commit()
    return row


def _job(root: Path, job_id: str, *, report_id: int, usable: bool, title: str = "链路报告") -> Path:
    directory = root / job_id
    (directory / "output").mkdir(parents=True, exist_ok=True)
    (directory / "output" / "final.mp4").write_bytes(b"film")
    job = {
        "job_id": job_id,
        "report": {"report_id": report_id, "title": title},
        "draft_gate": {"usable": usable, "invalidated_reason": "" if usable else "人工把确认撤回了"},
    }
    (directory / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    return directory


def _entry(session: Session, *, report_id: int, publish: bool = False, export_path: str = "") -> ContentCalendar:
    entry = ContentCalendar(
        research_report_id=report_id,
        title="排期",
        platform=Platform.DOUYIN,
        scheduled_for=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        publish_state=PublishState.PUBLISHED if publish else PublishState.NOT_PUBLISHED,
        published_url="https://www.douyin.com/video/1" if publish else None,
        export_path=export_path or None,
    )
    session.add(entry)
    session.commit()
    return entry


# --------------------------------------------------------------------------- 判定


def test_stage_order_is_fixed_and_unknown_report_is_refused(db_session: Session) -> None:
    report = _report(db_session)
    chain = chain_of(db_session, report.id, jobs_root=Path("/nonexistent-jobs"))
    assert [stage.key for stage in chain.stages] == STAGE_ORDER
    assert [stage.label for stage in chain.stages] == [content_chain.STAGE_LABELS[key] for key in STAGE_ORDER]

    with pytest.raises(ChainError) as error:
        chain_of(db_session, 999999)
    assert error.value.reason == "unknown_report"


def test_a_report_without_a_research_task_is_not_counted_as_blocked(db_session: Session) -> None:
    """没有研究任务的报告不该把 current 指到研究那一段（那一段根本没有前置要求）。"""

    report = _report(db_session)
    chain = chain_of(db_session, report.id, jobs_root=Path("/nonexistent-jobs"))
    research = next(stage for stage in chain.stages if stage.key == "research")
    assert research.status == "done"
    assert "不是研究流水线产出" in research.detail
    # 没有核查记录 → 第一段没做完的是核查
    assert chain.current == "checks"


def test_a_paused_task_says_which_gate_it_waits_for(db_session: Session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.PAUSED)
    report = _report(db_session, task_id=task.id)
    chain = chain_of(db_session, report.id, jobs_root=Path("/nonexistent-jobs"))
    research = next(stage for stage in chain.stages if stage.key == "research")
    assert research.status == "blocked"
    assert "人工闸门" in research.detail
    assert research.next_step and research.link == f"#/tasks/{task.id}"


def test_a_failed_task_carries_its_reason(db_session: Session) -> None:
    task = _task(db_session, status=ResearchTaskStatus.FAILED)
    report = _report(db_session, task_id=task.id)
    chain = chain_of(db_session, report.id, jobs_root=Path("/nonexistent-jobs"))
    research = next(stage for stage in chain.stages if stage.key == "research")
    assert research.status == "blocked"
    assert "抓取连续失败" in research.detail


def test_checks_stage_moves_from_missing_to_blocked_to_current_to_done(db_session: Session) -> None:
    report = _report(db_session)
    jobs_root = Path("/nonexistent-jobs")

    missing = chain_of(db_session, report.id, jobs_root=jobs_root)
    assert next(s.key for s in missing.stages if s.detail.startswith("这条报告还没有")) == "checks"

    _check(db_session, report, human="pending")
    blocked = chain_of(db_session, report.id, jobs_root=jobs_root)
    checks = next(stage for stage in blocked.stages if stage.key == "checks")
    assert checks.status == "blocked"
    assert "一条都没人工确认" in checks.detail

    _check(db_session, report, human="confirmed")
    current = chain_of(db_session, report.id, jobs_root=jobs_root)
    checks = next(stage for stage in current.stages if stage.key == "checks")
    assert checks.status == "current"
    assert "已人工确认 1 条" in checks.detail

    db_session.query(FindingCheck).filter(FindingCheck.research_report_id == report.id).update(
        {"human_review_status": "confirmed"}
    )
    db_session.commit()
    done = chain_of(db_session, report.id, jobs_root=jobs_root)
    assert next(stage for stage in done.stages if stage.key == "checks").status == "done"


def test_script_stage_reads_the_latest_version_and_its_status(db_session: Session) -> None:
    report = _report(db_session)
    jobs_root = Path("/nonexistent-jobs")
    _script(db_session, report, status=ScriptStatus.DRAFT)
    chain = chain_of(db_session, report.id, jobs_root=jobs_root)
    script = next(stage for stage in chain.stages if stage.key == "script")
    assert script.status == "current"
    assert "draft" in script.detail

    db_session.query(Script).filter(Script.research_report_id == report.id).update(
        {"status": ScriptStatus.APPROVED.value}
    )
    db_session.commit()
    approved = chain_of(db_session, report.id, jobs_root=jobs_root)
    assert next(stage for stage in approved.stages if stage.key == "script").status == "done"


def test_film_stage_separates_usable_films_from_invalidated_ones(db_session: Session, tmp_path: Path) -> None:
    report = _report(db_session)
    _job(tmp_path, "job_bad", report_id=report.id, usable=False, title=report.title)
    blocked = chain_of(db_session, report.id, jobs_root=tmp_path)
    film = next(stage for stage in blocked.stages if stage.key == "film")
    assert film.status == "blocked"
    assert "都不能发" in film.detail

    _job(tmp_path, "job_good", report_id=report.id, usable=True, title=report.title)
    done = chain_of(db_session, report.id, jobs_root=tmp_path)
    film = next(stage for stage in done.stages if stage.key == "film")
    assert film.status == "done"
    assert "job_good" in film.detail


def test_a_job_from_another_database_does_not_pass_as_this_reports_film(
    db_session: Session, tmp_path: Path
) -> None:
    """目录里的 report_id 只在一个库内唯一：标题对不上的同号任务不算这条链的成片。"""

    report = _report(db_session)
    directory = tmp_path / "other_db_job"
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "final.mp4").write_bytes(b"film")
    (directory / "job.json").write_text(
        json.dumps(
            {
                "job_id": "other_db_job",
                "report": {"report_id": report.id, "title": "另一个库里的报告"},
                "draft_gate": {"usable": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chain = chain_of(db_session, report.id, jobs_root=tmp_path)
    film = next(stage for stage in chain.stages if stage.key == "film")
    assert film.status == "missing"
    assert "别的库" in film.detail


def test_schedule_and_export_stages_follow_the_calendar(db_session: Session) -> None:
    report = _report(db_session)
    jobs_root = Path("/nonexistent-jobs")

    only_schedule = _entry(db_session, report_id=report.id)
    chain = chain_of(db_session, report.id, jobs_root=jobs_root)
    schedule = next(stage for stage in chain.stages if stage.key == "schedule")
    export = next(stage for stage in chain.stages if stage.key == "export")
    assert schedule.status == "current"
    assert export.status == "current"
    assert "还没导出过交付包" in export.detail

    only_schedule.export_path = "content/exports/entry-1/20261120T120000"
    only_schedule.publish_state = PublishState.PUBLISHED
    only_schedule.published_url = "https://www.douyin.com/video/1"
    db_session.commit()
    finished = chain_of(db_session, report.id, jobs_root=jobs_root)
    assert next(stage for stage in finished.stages if stage.key == "schedule").status == "done"
    assert next(stage for stage in finished.stages if stage.key == "export").status == "done"
    # 排期与导出都齐了，但这条报告没做核查 → 整条还没走完，current 仍指着"第一段没做完的"。
    assert finished.finished is False
    assert finished.current == "checks"


def test_a_fully_working_chain_is_reported_as_finished(db_session: Session, tmp_path: Path) -> None:
    task = _task(db_session, status=ResearchTaskStatus.COMPLETED)
    report = _report(db_session, task_id=task.id)
    _check(db_session, report, human="confirmed")
    _script(db_session, report, status=ScriptStatus.APPROVED)
    _job(tmp_path, "job_ok", report_id=report.id, usable=True, title=report.title)
    entry = _entry(db_session, report_id=report.id, publish=True, export_path="content/exports/e1")

    chain = chain_of(db_session, report.id, jobs_root=tmp_path)
    assert chain.finished is True
    assert all(stage.status == "done" for stage in chain.stages), chain.as_dict()
    assert entry.id  # 排期确实进了查询范围


# --------------------------------------------------------------------------- 接口


def test_api_returns_the_chain_and_404_for_unknown(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(content_chain, "DEFAULT_JOBS_ROOT", tmp_path, raising=False)
    report = _report(db_session)

    response = client.get(f"/api/research-reports/{report.id}/chain")
    assert response.status_code == 200
    body = response.json()
    assert body["report_id"] == report.id
    assert [stage["key"] for stage in body["stages"]] == STAGE_ORDER
    assert body["finished"] is False
    assert body["current"] == "checks"

    missing = client.get("/api/research-reports/999999/chain")
    assert missing.status_code == 404
    assert missing.json()["error"]["details"]["reason"] == "unknown_report"
