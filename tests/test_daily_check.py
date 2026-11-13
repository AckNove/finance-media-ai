"""`backend.scripts.daily_check` 的回归测试（全离线，不联网、不调模型）。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.database.base import Base
from backend.app.database.session import build_engine
from backend.app.models.execution import ResearchSubtask, ResearchTask
from backend.app.models.metering import ModelCall
from backend.app.models.research import FindingCheck, ResearchReport
from backend.scripts import daily_check

NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'daily.db').as_posix()}"
    engine = build_engine(_settings(url))
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def _settings(url: str):
    from backend.app.core.config import get_settings

    return get_settings().model_copy(update={"database_url": url})


@pytest.fixture()
def session(db_url: str):
    engine = build_engine(_settings(db_url))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as item:
        yield item
    engine.dispose()


def _report(session, *, findings=None) -> ResearchReport:
    row = ResearchReport(
        title="商业银行是怎么赚钱的",
        summary="净息差在收窄。",
        findings=findings
        or [{"text": "净息差降至1.52%", "kind": "fact", "source_ids": ["S01"], "numbers": ["1.52%"]}],
        limitations="只覆盖公开数据。",
    )
    session.add(row)
    session.commit()
    return row


def test_empty_database_is_clean(tmp_path: Path, db_url: str) -> None:
    report = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        now=NOW,
    )
    assert report["tasks"]["by_status"] == {}
    assert report["checks"]["pending"] == 0
    assert report["cost"]["today"]["calls"] == 0
    # 没有备份目录本身就是一条要处理的事
    assert any("备份" in item for item in report["attention"])
    assert report["ok"] is False


def test_counts_tasks_subtasks_and_checks(tmp_path: Path, db_url: str, session) -> None:
    paused = ResearchTask(title="暂停的任务", objective="o", status="paused")
    failed = ResearchTask(
        title="失败的任务", objective="o", status="failed", failure_reason="bad_time"
    )
    done = ResearchTask(title="完成的任务", objective="o", status="completed")
    session.add_all([paused, failed, done])
    session.commit()

    session.add(
        ResearchSubtask(
            research_task_id=failed.id,
            sequence=1,
            question="q",
            task_type="research.collect",
            status="failed",
            attempts=2,
            failure_reason="budget_exceeded",
        )
    )
    report = _report(session)
    session.add_all(
        [
            FindingCheck(
                research_report_id=report.id,
                finding_index=0,
                claim="净息差降至1.52%",
                verdict="supported",
                human_review_status="pending",
                supporting=["净息差降至1.52%"],
            ),
            FindingCheck(
                research_report_id=report.id,
                finding_index=1,
                claim="另一条",
                verdict="partially_supported",
                human_review_status="pending",
            ),
            FindingCheck(
                research_report_id=report.id,
                finding_index=2,
                claim="已确认的",
                verdict="supported",
                human_review_status="confirmed",
            ),
        ]
    )
    session.commit()

    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        now=NOW,
    )
    assert payload["tasks"]["by_status"] == {"paused": 1, "failed": 1, "completed": 1}
    assert payload["tasks"]["paused"][0]["title"] == "暂停的任务"
    assert payload["checks"]["pending"] == 2
    assert payload["checks"]["pending_reports"] == [report.id]
    assert payload["checks"]["by_status"]["confirmed"] == 1
    # 子任务 updated_at 是刚刚，落在窗口内
    assert payload["subtasks"]["count"] == 1
    assert payload["subtasks"]["failed_in_window"][0]["failure_reason"] == "budget_exceeded"
    assert any("语义核查" in item for item in payload["attention"])


def test_old_failure_falls_outside_the_window(tmp_path: Path, db_url: str, session) -> None:
    old = ResearchTask(title="很久以前失败", objective="o", status="failed")
    old.updated_at = NOW.replace(tzinfo=None) - timedelta(days=5)
    session.add(old)
    session.commit()

    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        days=1,
        now=NOW,
    )
    assert payload["tasks"]["failed_in_window"] == []
    assert payload["tasks"]["by_status"]["failed"] == 1


def test_cost_today_only_counts_this_utc_day(tmp_path: Path, db_url: str, session) -> None:
    today = ModelCall(
        provider="deepseek",
        model="deepseek-flash",
        purpose="research.analyze",
        prompt_tokens=900,
        completion_tokens=100,
        total_tokens=1000,
        cost_estimate=0.003,
        status="ok",
        created_at=NOW.replace(tzinfo=None) - timedelta(hours=2),
    )
    yesterday = ModelCall(
        provider="deepseek",
        model="deepseek-flash",
        purpose="research.verify",
        prompt_tokens=400,
        completion_tokens=50,
        total_tokens=450,
        cost_estimate=0.002,
        status="ok",
        created_at=NOW.replace(tzinfo=None) - timedelta(days=1, hours=1),
    )
    session.add_all([today, yesterday])
    session.commit()

    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        days=2,
        now=NOW,
    )
    assert payload["cost"]["today"]["calls"] == 1
    assert payload["cost"]["today"]["cost_cny"] == pytest.approx(0.003)
    assert payload["cost"]["window"]["calls"] == 2
    assert payload["cost"]["window"]["tokens"] == 1450


def test_invalidated_jobs_are_listed(tmp_path: Path, db_url: str) -> None:
    jobs = tmp_path / "jobs"
    good = jobs / "job_ok"
    bad = jobs / "job_bad"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "job.json").write_text(
        json.dumps({"job_id": "job_ok", "draft_gate": {"usable": True, "check_ids": [1]}}),
        encoding="utf-8",
    )
    (bad / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job_bad",
                "draft_gate": {
                    "usable": False,
                    "invalidated_at": "2026-05-20T10:00:00+00:00",
                    "invalidated_reason": "人工把核查记录 1 的确认撤回到 rejected",
                },
            }
        ),
        encoding="utf-8",
    )
    (jobs / "broken").mkdir()
    (jobs / "broken" / "job.json").write_text("{not json", encoding="utf-8")

    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=jobs,
        backups=tmp_path / "backups",
        now=NOW,
    )
    assert payload["jobs"]["jobs"] == 3
    assert payload["jobs"]["invalidated_count"] == 1
    assert payload["jobs"]["invalidated"][0]["job_id"] == "job_bad"
    assert "撤回到 rejected" in payload["jobs"]["invalidated"][0]["reason"]


def test_stale_backup_is_flagged(tmp_path: Path, db_url: str) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    stale = backups / "finance-20260501-000000-000.db"
    stale.write_bytes(b"x")
    old = (NOW - timedelta(days=19)).timestamp()
    os.utime(stale, (old, old))

    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=backups,
        now=NOW,
    )
    assert payload["backup"]["newest"] == stale.name
    assert payload["backup"]["age_days"] == pytest.approx(19.0, abs=0.1)
    assert any("备份已经" in item for item in payload["attention"])

    fresh = backups / "finance-20260520-100000-000.db"
    fresh.write_bytes(b"y")
    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=backups,
        now=NOW,
    )
    assert payload["backup"]["newest"] == fresh.name
    assert payload["backup"]["count"] == 2


def test_protected_database_is_labelled(tmp_path: Path, db_url: str) -> None:
    payload = daily_check.collect(
        database_url="sqlite:///data/finance_media.db",
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        now=NOW,
    )
    assert payload["protected_database"] is True
    assert "真实开发库" in daily_check.render_text(payload)


def test_text_render_mentions_every_section(tmp_path: Path, db_url: str) -> None:
    payload = daily_check.collect(
        database_url=db_url,
        jobs_root=tmp_path / "jobs",
        backups=tmp_path / "backups",
        now=NOW,
    )
    text = daily_check.render_text(payload)
    for keyword in ("体检时间", "任务：", "子任务失败", "语义核查", "视频任务", "今天花费", "最近备份", "磁盘", "需要处理"):
        assert keyword in text


def test_main_returns_one_when_attention_needed(tmp_path: Path, db_url: str, capsys) -> None:
    code = daily_check.main(
        [
            "--database-url",
            db_url,
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--backups",
            str(tmp_path / "backups"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert code == 1


def test_main_returns_three_when_the_database_is_missing(tmp_path: Path, capsys) -> None:
    code = daily_check.main(
        [
            "--database-url",
            f"sqlite:///{(tmp_path / 'nope' / 'missing.db').as_posix()}",
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--backups",
            str(tmp_path / "backups"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["reason"] == "database_missing"
    assert "库文件不存在" in payload["message"]
