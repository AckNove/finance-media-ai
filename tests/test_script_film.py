"""从已批准的脚本直接开片子。

三条纪律用测试钉住：
1. 脚本没批准就不许开片子（审核是道关，不是装饰）；
2. 脚本背后的报告要是没了人工确认，照样拒绝——依据作废就不该出片；
3. 分镜排不出来只记原因，不拿规则模板顶替。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.video.script_film import (
    ScriptFilmError,
    create_job_from_script,
)

SEGMENTS = [
    {
        "text": "净息差从2.10%一路降到2.06%",
        "role": "opening",
        "source_ids": ["S01"],
        "numbers": ["2.10%", "2.06%"],
    },
    {
        "text": "2024年四季度净息差降至1.52%",
        "role": "evidence",
        "source_ids": ["S01"],
        "numbers": ["1.52%"],
    },
    {
        "text": "这不是某一家银行的麻烦",
        "role": "closing",
        "source_ids": [],
        "numbers": [],
    },
]


def _topic(db_session: Any) -> int:
    from backend.app.models.topic import Topic

    import uuid

    row = Topic(title=f"脚本开片选题 {uuid.uuid4().hex[:8]}", slug=f"t-{uuid.uuid4().hex[:8]}")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return int(row.id)


def _report(db_session: Any, *, findings: list[dict[str, Any]] | None = None) -> int:
    from backend.app.models.research import ResearchReport

    row = ResearchReport(
        title="净息差那条线",
        summary="摘要。",
        findings=findings if findings is not None else [{"text": "净息差降到 1.52%"}, {"text": "中小银行更难受"}],
        limitations="样本有限。",
        verification_status="unverified",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return int(row.id)


def _check(
    db_session: Any, *, report_id: int, index: int, status: str = "confirmed"
) -> int:
    from backend.app.models.research import FindingCheck

    row = FindingCheck(
        research_report_id=report_id,
        finding_index=index,
        claim=f"结论 {index}",
        verdict="supported",
        reason="模型给的理由",
        evidence_ids=[],
        source_ids=["S01"],
        human_review_status=status,
        human_note="人工看过",
    )
    db_session.add(row)
    db_session.commit()
    return int(row.id)


def _script(
    db_session: Any,
    *,
    topic_id: int,
    report_id: int | None = None,
    status: str = "approved",
    segments: list[dict[str, Any]] | None = None,
) -> int:
    from backend.app.models.enums import ScriptStatus, VideoTemplate
    from backend.app.services.script_service import ScriptService

    service = ScriptService(db_session)
    script = service.create(
        topic_id=topic_id,
        research_report_id=report_id,
        title="净息差为什么一路往下",
        template=VideoTemplate.FINANCIAL_MECHANISM,
        body="净息差从2.10%一路降到2.06%，2024年四季度降至1.52%，这不是某一家银行的麻烦。",
        segments=list(segments if segments is not None else SEGMENTS),
        disclaimer="本片为合规资料整理，不构成投资建议。",
    )
    if status != "draft":
        # 状态机只允许逐级走：draft → in_review → approved
        for step in ("in_review", status):
            service.set_status(int(script.id), ScriptStatus(step), actor="tester")
    return int(script.id)


def test_a_draft_script_cannot_become_a_film(db_session: Any, tmp_path: Path) -> None:
    script_id = _script(db_session, topic_id=_topic(db_session), status="draft")
    with pytest.raises(ScriptFilmError) as caught:
        create_job_from_script(db_session, script_id, root=tmp_path)
    assert caught.value.reason == "script_not_approved"
    assert "还没批准" in caught.value.message
    assert list(tmp_path.iterdir()) == []


def test_an_approved_script_without_a_report_makes_a_job(
    db_session: Any, tmp_path: Path
) -> None:
    script_id = _script(db_session, topic_id=_topic(db_session))
    film = create_job_from_script(db_session, script_id, root=tmp_path)

    assert film.blocked is False
    assert film.report_id is None
    assert film.shot_count >= 2
    assert film.total_seconds > 0

    job = Path(film.path)
    assert (job / "job.json").is_file()
    assert (job / "script" / "SCRIPT.md").is_file()
    assert (job / "storyboard" / "storyboard.csv").is_file()
    assert "净息差从2.10%一路降到2.06%" in (job / "script" / "SCRIPT.md").read_text(
        encoding="utf-8"
    )

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "script"
    assert manifest["draft_gate"]["script_id"] == script_id
    assert manifest["outline"]["source"] == "script"


def test_an_approved_script_whose_report_lost_its_confirmations_is_refused(
    db_session: Any, tmp_path: Path
) -> None:
    from backend.app.models.research import FindingCheck

    topic_id = _topic(db_session)
    report_id = _report(db_session)
    # 先确认→建脚本（建脚本本身就要求有确认过）→ 再把确认撤回
    check_id = _check(db_session, report_id=report_id, index=0)
    script_id = _script(db_session, topic_id=topic_id, report_id=report_id)

    row = db_session.get(FindingCheck, check_id)
    row.human_review_status = "rejected"
    db_session.commit()

    with pytest.raises(ScriptFilmError) as caught:
        create_job_from_script(db_session, script_id, root=tmp_path)
    assert caught.value.reason == "draft_not_confirmed"
    assert "人工确认" in caught.value.message
    assert list(tmp_path.iterdir()) == []


def test_a_confirmed_report_records_check_ids_for_invalidation(
    db_session: Any, tmp_path: Path
) -> None:
    from backend.app.video.handoff import jobs_using_checks

    topic_id = _topic(db_session)
    report_id = _report(db_session)
    check_id = _check(db_session, report_id=report_id, index=0)
    script_id = _script(db_session, topic_id=topic_id, report_id=report_id)

    film = create_job_from_script(db_session, script_id, root=tmp_path)
    manifest = json.loads((Path(film.path) / "job.json").read_text(encoding="utf-8"))
    assert manifest["draft_gate"]["check_ids"] == [check_id]
    assert manifest["report"]["report_id"] == report_id

    # 撤回确认后，这条任务要能被找出来标失效
    assert jobs_using_checks([check_id], root=tmp_path) == [Path(film.path)]


def test_the_same_job_id_twice_is_refused(db_session: Any, tmp_path: Path) -> None:
    script_id = _script(db_session, topic_id=_topic(db_session))
    create_job_from_script(db_session, script_id, job_id="film_demo", root=tmp_path)
    with pytest.raises(ScriptFilmError) as caught:
        create_job_from_script(db_session, script_id, job_id="film_demo", root=tmp_path)
    assert caught.value.reason == "job_exists"


def test_a_one_line_script_is_blocked_not_replaced(
    db_session: Any, tmp_path: Path
) -> None:
    script_id = _script(
        db_session,
        topic_id=_topic(db_session),
        segments=[SEGMENTS[0]],
    )
    film = create_job_from_script(db_session, script_id, root=tmp_path)
    assert film.blocked is True
    assert film.blocked_reason in {"too_few_lines", "empty_script", "storyboard_failed"}
    assert "不会拿规则模板顶替" in film.next_step
    # 目录仍然留着：脚本快照与原因都在，改完稿能重开一条
    job = Path(film.path)
    assert (job / "job.json").is_file()
    assert (job / "script" / "SCRIPT.md").is_file()
    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["blocked"] is True
    assert manifest["blocked_reason"] == film.blocked_reason


def test_unknown_script_raises(db_session: Any, tmp_path: Path) -> None:
    with pytest.raises(ScriptFilmError) as caught:
        create_job_from_script(db_session, 999_999, root=tmp_path)
    assert caught.value.reason == "unknown_script"


def test_the_endpoint_reports_the_reason_it_refused(
    client: Any, db_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.video import script_film as module

    monkeypatch.setattr(module, "JOBS_ROOT", tmp_path)

    topic_id = _topic(db_session)
    script_id = _script(db_session, topic_id=topic_id, status="draft")
    refused = client.post(f"/api/scripts/{script_id}/video-job", json={})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == "script_not_approved"

    missing = client.post("/api/scripts/999999/video-job", json={})
    assert missing.status_code == 404, missing.text


def test_the_endpoint_creates_the_job_when_the_script_is_approved(
    client: Any, db_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.video import script_film as module

    monkeypatch.setattr(module, "JOBS_ROOT", tmp_path)

    script_id = _script(db_session, topic_id=_topic(db_session))
    response = client.post(
        f"/api/scripts/{script_id}/video-job", json={"job_id": "film_api"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"] == "film_api"
    assert body["script_id"] == script_id
    assert body["blocked"] is False
    assert body["shot_count"] >= 2
    assert (tmp_path / "film_api" / "storyboard" / "storyboard.csv").is_file()
