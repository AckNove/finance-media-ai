"""把成片排进内容日历：视频页面上那个"排进日历"按钮背后的接口行为。

要点：排期只记"打算哪天发什么"，不发布；排完要能从排期看到底片就绪度，
并且要能指回来源报告，否则运营数据没法归因。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.core.config import PROJECT_ROOT


def _make_job(root: Path, job_id: str, *, with_film: bool, usable: bool = True) -> Path:
    job = root / job_id
    (job / "output").mkdir(parents=True)
    (job / "assets").mkdir(parents=True)
    if with_film:
        (job / "output" / "final.mp4").write_bytes(b"film")
    gate = {
        "rule": "只有 human_review_status=confirmed 的结论可以进入成稿",
        "check_ids": [1],
        "confirmed_findings": [0],
        "usable": usable,
        "invalidated_at": "" if usable else "2026-11-14T05:00:00+00:00",
        "invalidated_reason": "" if usable else "人工把核查记录 1 的确认撤回到 rejected",
    }
    payload = {
        "job_id": job_id,
        "stage": "rendered",
        "draft_gate": gate,
        "report": {
            "report_id": 11,
            "research_task_id": 3,
            "title": "储能行业的利润在哪个环节",
            "verification_status": "unverified",
        },
    }
    (job / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return job


@pytest.fixture()
def jobs_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from backend.app.api.routes import workspace as workspace_routes
    from backend.app.services import calendar_readiness

    root = tmp_path / "jobs"
    root.mkdir()
    monkeypatch.setattr(workspace_routes, "JOBS_ROOT", root)
    # 排期那边的"底片就绪度"读的是它自己的默认目录，两个都要指到临时目录，
    # 否则会去仓库真实的 content/jobs 里找这个假任务，一律报 job_missing。
    monkeypatch.setattr(calendar_readiness, "DEFAULT_JOBS_ROOT", root)
    return root


def test_job_summary_points_back_at_the_report(client: Any, jobs_root: Path) -> None:
    _make_job(jobs_root, "chain_film", with_film=True)

    summary = client.get("/api/workspace/jobs/chain_film").json()["summary"]

    assert summary["report"]["report_id"] == 11
    assert summary["report"]["research_task_id"] == 3
    assert summary["report"]["title"] == "储能行业的利润在哪个环节"


def test_summary_without_a_job_json_does_not_invent_a_report(
    client: Any, jobs_root: Path
) -> None:
    (jobs_root / "bare_job" / "output").mkdir(parents=True)

    summary = client.get("/api/workspace/jobs/bare_job").json()["summary"]

    assert summary["report"] == {}


def test_scheduling_a_rendered_film_shows_it_is_ready(client: Any, jobs_root: Path) -> None:
    _make_job(jobs_root, "ready_film", with_film=True)

    response = client.post(
        "/api/calendar",
        json={
            "title": "储能行业的利润在哪个环节",
            "platform": "bilibili",
            "status": "rendered",
            "video_job_id": "ready_film",
            "research_report_id": None,
        },
    )

    assert response.status_code == 201
    entry = response.json()
    assert entry["video_job_id"] == "ready_film"
    assert entry["readiness"]["status"] == "ready"
    assert entry["readiness"]["usable"] is True
    assert entry["publish_state"] == "not_published"


def test_scheduling_a_film_whose_check_was_withdrawn_says_so(
    client: Any, jobs_root: Path
) -> None:
    _make_job(jobs_root, "stale_film", with_film=True, usable=False)

    entry = client.post(
        "/api/calendar",
        json={"title": "撤回过确认的那条", "video_job_id": "stale_film"},
    ).json()

    assert entry["readiness"]["status"] == "draft_invalid"
    assert entry["readiness"]["usable"] is False
    assert "撤回" in entry["readiness"]["next_step"]


def test_scheduling_a_job_that_was_never_rendered_says_so(
    client: Any, jobs_root: Path
) -> None:
    _make_job(jobs_root, "no_film", with_film=False)

    entry = client.post(
        "/api/calendar",
        json={"title": "还没渲染的片子", "video_job_id": "no_film"},
    ).json()

    assert entry["readiness"]["status"] == "film_missing"
    assert "渲染" in entry["readiness"]["next_step"]


def test_the_repository_jobs_are_listed_with_their_reports(client: Any) -> None:
    """仓库里真实的成片任务：只要写了 job.json 的 report，就该带出来。"""

    jobs = client.get("/api/workspace/jobs").json()["jobs"]
    with_report = [item for item in jobs if item.get("report", {}).get("report_id")]
    assert with_report, "至少有一个任务目录应该记录了来源报告"
    job_id = with_report[0]["job_id"]
    assert (PROJECT_ROOT / "content" / "jobs" / job_id / "job.json").is_file()
