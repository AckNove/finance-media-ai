"""审核流水接口测试：审核要落到库里，结论要回写到目标对象。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import confirm_findings
from backend.app.models.enums import ReviewTargetType, VerificationStatus
from backend.app.services.research_service import ResearchReportService
from backend.app.schemas.research import ResearchReportCreate


def _report(db_session: Session, title: str = "商业银行盈利结构") -> int:
    report = ResearchReportService(db_session).create(
        ResearchReportCreate(
            title=title,
            summary="净息差收窄是主因。",
            limitations="只覆盖三家银行。",
            findings=[{"statement": "净息差降至1.52%", "nature": "fact"}],
            verification_status=VerificationStatus.UNVERIFIED,
        )
    )
    # 报告要能生脚本，就得先有一条人工确认过的结论（成稿闸门）。
    confirm_findings(db_session, report)
    return report.id


def test_a_pass_review_is_recorded_and_flips_the_report_status(
    client: TestClient, db_session: Session
) -> None:
    report_id = _report(db_session)

    response = client.post(
        "/api/reviews",
        json={
            "target_type": "research_report",
            "target_id": report_id,
            "verdict": "pass",
            "reviewer_name": "主编",
            "stage": "final",
            "comment": "数字与原文一致，可以引用。",
            "checklist": {"引文核对": True, "来源可信度": True},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reviewer_type"] == "human"
    assert body["target_version"] == 1

    listed = client.get(
        "/api/reviews", params={"target_type": "research_report", "target_id": report_id}
    ).json()
    assert listed["total"] == 1
    assert listed["items"][0]["verdict"] == "pass"

    detail = client.get(f"/api/research-reports/{report_id}").json()
    assert detail["verification_status"] == "verified"


def test_needs_revision_marks_the_report_disputed(
    client: TestClient, db_session: Session
) -> None:
    report_id = _report(db_session, "另一份报告")

    response = client.post(
        "/api/reviews",
        json={
            "target_type": "research_report",
            "target_id": report_id,
            "verdict": "needs_revision",
            "reviewer_name": "财经编辑",
            "comment": "第三段的同比口径没说清。",
        },
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/api/research-reports/{report_id}").json()["verification_status"] == (
        "disputed"
    )


def test_needs_revision_without_a_comment_is_refused(
    client: TestClient, db_session: Session
) -> None:
    """"要改"必须说清改什么，否则下一轮没人知道要改哪儿。"""
    report_id = _report(db_session, "第三份报告")

    response = client.post(
        "/api/reviews",
        json={
            "target_type": "research_report",
            "target_id": report_id,
            "verdict": "needs_revision",
            "reviewer_name": "财经编辑",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "comment"


def test_reviewing_a_missing_target_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/reviews",
        json={
            "target_type": "research_report",
            "target_id": 999999,
            "verdict": "pass",
            "reviewer_name": "主编",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["details"]["target_type"] == "research_report"


def test_script_review_does_not_silently_change_its_status(
    client: TestClient, db_session: Session
) -> None:
    """脚本状态机由内容生产流程驱动；审核通过不等于可以发布。"""
    topic = client.post("/api/topics", json={"title": "脚本审核用选题"}).json()
    report_id = _report(db_session, "给脚本用的报告")
    script = client.post(
        "/api/scripts",
        json={
            "topic_id": topic["id"],
            "research_report_id": report_id,
            "title": "净息差为什么一直在降",
            "body": "第一段。\n第二段。",
        },
    )
    assert script.status_code == 201, script.text
    script_id = script.json()["id"]
    before = script.json()["status"]

    response = client.post(
        "/api/reviews",
        json={
            "target_type": "script",
            "target_id": script_id,
            "verdict": "pass",
            "reviewer_name": "主编",
        },
    )
    assert response.status_code == 201, response.text
    after = client.get(f"/api/scripts/{script_id}").json()
    assert after["status"] == before
    assert after["approved_at"] is None


def test_review_list_filters_by_verdict(client: TestClient, db_session: Session) -> None:
    report_id = _report(db_session, "第四份报告")
    for verdict, comment in (("pass", None), ("reject", "口径错误，不能用。")):
        client.post(
            "/api/reviews",
            json={
                "target_type": "research_report",
                "target_id": report_id,
                "verdict": verdict,
                "reviewer_name": "主编",
                "comment": comment,
            },
        )

    rejected = client.get("/api/reviews", params={"verdict": "reject"}).json()
    assert rejected["total"] >= 1
    assert all(item["verdict"] == "reject" for item in rejected["items"])

    # reject 是"这份报告结论不可用"
    assert client.get(f"/api/research-reports/{report_id}").json()["verification_status"] == (
        "refuted"
    )


def test_review_detail_round_trips(client: TestClient, db_session: Session) -> None:
    report_id = _report(db_session, "第五份报告")
    created = client.post(
        "/api/reviews",
        json={
            "target_type": "research_report",
            "target_id": report_id,
            "verdict": "pass",
            "reviewer_name": "主编",
            "findings": [{"item": "净息差", "ok": True}],
        },
    ).json()
    detail = client.get(f"/api/reviews/{created['id']}").json()
    assert detail["findings"] == [{"item": "净息差", "ok": True}]
    assert detail["target_type"] == ReviewTargetType.RESEARCH_REPORT.value
