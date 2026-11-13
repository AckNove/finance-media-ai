"""视频脚本接口测试：版本号、状态机、以及"审核通过≠可发布"。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import confirm_findings
from backend.app.models.enums import VerificationStatus
from backend.app.schemas.research import ResearchReportCreate
from backend.app.services.research_service import ResearchReportService


def _topic(client: TestClient, title: str = "净息差为什么一直在降") -> int:
    response = client.post("/api/topics", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _report(db_session: Session, topic_id: int) -> int:
    report = ResearchReportService(db_session).create(
        ResearchReportCreate(
            title="净息差研究",
            summary="净息差收窄。",
            limitations="只覆盖三家银行。",
            findings=[{"statement": "净息差1.52%", "nature": "fact"}],
            topic_id=topic_id,
            verification_status=VerificationStatus.VERIFIED,
        )
    )
    # 脚本是成稿本身：闸门要求至少有一条结论被人工确认过。
    confirm_findings(db_session, report)
    return report.id


def _script(client: TestClient, topic_id: int, report_id: int | None = None) -> dict:
    response = client.post(
        "/api/scripts",
        json={
            "topic_id": topic_id,
            "research_report_id": report_id,
            "title": "净息差为什么一直在降",
            "hook": "银行最赚钱的生意正在变薄。",
            "body": "第一段。\n第二段。",
            "segments": [{"index": 1, "narration": "第一段。", "duration_seconds": 12}],
            "disclaimer": "本片不构成投资建议。",
            "estimated_duration": 180,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_script_is_versioned_per_topic_and_template(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    first = _script(client, topic_id)
    second = _script(client, topic_id)

    assert first["version"] == 1
    assert second["version"] == 2
    assert first["status"] == "draft"
    # 字数按去空白算，不是按字节
    assert first["word_count"] == len("第一段。第二段。")
    assert first["disclaimer"] == "本片不构成投资建议。"

    listed = client.get("/api/scripts", params={"topic_id": topic_id}).json()
    assert listed["total"] == 2


def test_status_machine_only_allows_the_planned_moves(
    client: TestClient, db_session: Session
) -> None:
    topic_id = _topic(client)
    script = _script(client, topic_id)
    script_id = script["id"]

    # 不能从 draft 直接跳到 approved
    jump = client.patch(f"/api/scripts/{script_id}/status", json={"status": "approved"})
    assert jump.status_code == 409
    assert jump.json()["error"]["details"]["allowed"] == ["in_review"]

    assert (
        client.patch(f"/api/scripts/{script_id}/status", json={"status": "in_review"}).status_code
        == 200
    )
    approved = client.patch(
        f"/api/scripts/{script_id}/status",
        json={"status": "approved", "actor": "主编"},
    ).json()
    assert approved["status"] == "approved"
    # 谁批的、什么时候批的必须留痕
    assert approved["approved_by"] == "主编"
    assert approved["approved_at"] is not None


def test_rejecting_sends_it_back_to_draft(client: TestClient, db_session: Session) -> None:
    topic_id = _topic(client)
    script_id = _script(client, topic_id)["id"]

    client.patch(f"/api/scripts/{script_id}/status", json={"status": "in_review"})
    rejected = client.patch(f"/api/scripts/{script_id}/status", json={"status": "rejected"})
    assert rejected.status_code == 200
    back = client.patch(f"/api/scripts/{script_id}/status", json={"status": "draft"})
    assert back.status_code == 200
    assert back.json()["status"] == "draft"


def test_a_pass_review_does_not_publish_the_script(
    client: TestClient, db_session: Session
) -> None:
    """审核通过 ≠ 可以发布：发布另有流程，这里只验证不会顺带改状态。"""
    topic_id = _topic(client)
    report_id = _report(db_session, topic_id)
    script_id = _script(client, topic_id, report_id)["id"]
    client.patch(f"/api/scripts/{script_id}/status", json={"status": "in_review"})

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
    assert after["status"] == "in_review"
    assert after["approved_at"] is None


def test_a_script_may_not_borrow_another_topics_report(
    client: TestClient, db_session: Session
) -> None:
    first_topic = _topic(client, "选题甲")
    second_topic = _topic(client, "选题乙")
    report_id = _report(db_session, first_topic)

    response = client.post(
        "/api/scripts",
        json={
            "topic_id": second_topic,
            "research_report_id": report_id,
            "title": "串选题的脚本",
            "body": "正文。",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "research_report_id"


def test_unknown_topic_or_empty_body_is_rejected(client: TestClient) -> None:
    missing = client.post(
        "/api/scripts", json={"topic_id": 999999, "title": "x", "body": "正文。"}
    )
    assert missing.status_code == 422

    topic_id = _topic(client, "选题丙")
    blank = client.post(
        "/api/scripts", json={"topic_id": topic_id, "title": "x", "body": "   "}
    )
    assert blank.status_code == 422
