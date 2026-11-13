"""成稿闸门测试：只有人工确认过的结论可以进成稿。

口径（用户 2026-11-13 定）：语义核查的判定只是**模型意见**，必须有人在核查页
逐条确认（`human_review_status=confirmed`）之后，结论才允许进入成稿（脚本/分镜/成片）。
这里测的就是"不许绕过"：
- 没有核查记录 → 不许成稿；
- 全是 pending/rejected/needs_more_evidence → 不许成稿；
- 确认了一条 → 只有那一条进稿，其余结论只作为"未确认"列出；
- 接口层必须给 409 + 可读原因，不许静默生成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.capabilities.model import providers as model_providers
from backend.app.research.draft_gate import (
    DraftGateError,
    assert_draft_ready,
    confirmed_findings,
    readiness_of,
)
from backend.app.video import handoff as handoff_module
from backend.app.video.handoff import VideoHandoffError, create_video_job


class _Report:
    id = 5
    title = "光伏组件价格战"
    summary = "价格战通过三条渠道吃掉现金流。"
    findings = [
        {"text": "组件价格 2024 年跌到 0.68 元/W", "kind": "fact", "source_ids": ["S01"]},
        {"text": "龙头一体化企业毛利率转负", "kind": "fact", "source_ids": ["S02"]},
        {"text": "价格战是行业出清的唯一路径", "kind": "inference", "source_ids": ["S01"]},
    ]
    limitations = "样本仅覆盖几家龙头。"
    research_task_id = 8
    author_agent = "model+rules"
    verification_status = "unverified"


def _check(
    db_session: Any,
    *,
    index: int,
    status: str,
    verdict: str = "partially_supported",
    report_id: int | None = None,
    evidence_ids: list[int] | None = None,
) -> int:
    from backend.app.models.research import FindingCheck

    row = FindingCheck(
        research_report_id=report_id or _Report.id,
        finding_index=index,
        claim=f"结论 {index}",
        verdict=verdict,
        reason="模型给的理由",
        evidence_ids=evidence_ids or [],
        source_ids=["S01"],
        human_review_status=status,
        human_note="人工备注" if status != "pending" else "",
    )
    db_session.add(row)
    db_session.commit()
    return int(row.id)


def _seed_report(db_session: Any) -> Any:
    """把 `_Report` 真的写进测试库（外键要求报告行存在）。"""
    from backend.app.models.research import ResearchReport

    existing = db_session.get(ResearchReport, _Report.id)
    if existing is not None:
        return existing
    row = ResearchReport(
        id=_Report.id,
        title=_Report.title,
        summary=_Report.summary,
        findings=list(_Report.findings),
        limitations=_Report.limitations,
        research_task_id=None,
        author_agent=_Report.author_agent,
        verification_status="unverified",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture(autouse=True)
def clean_gate_tables() -> Any:
    """测试库是整场共享的：每个用例先把本文件用到的两张表清干净。"""
    from sqlalchemy import delete

    from backend.app.database.session import get_session_factory
    from backend.app.models.research import FindingCheck, ResearchReport

    session = get_session_factory()()
    try:
        session.execute(delete(FindingCheck))
        session.execute(
            delete(ResearchReport).where(ResearchReport.id.in_([_Report.id, 6]))
        )
        session.commit()
    finally:
        session.close()
    yield


# ------------------------------------------------------------------ 纯函数层


def test_no_checks_blocks_the_draft(db_session: Any) -> None:
    report = _seed_report(db_session)
    state = readiness_of(db_session, report)
    assert state.allowed is False
    assert state.reason == "no_checks"
    assert state.total_checks == 0
    assert "没有核查就不能成稿" in state.note
    assert confirmed_findings(db_session, report) == []


def test_pending_checks_still_block(db_session: Any) -> None:
    report = _seed_report(db_session)
    _check(db_session, index=0, status="pending", evidence_ids=[1])
    _check(db_session, index=1, status="needs_more_evidence")
    state = readiness_of(db_session, report)
    assert state.allowed is False
    assert state.reason == "not_confirmed"
    assert state.by_human_review == {"pending": 1, "needs_more_evidence": 1}
    with pytest.raises(DraftGateError) as error:
        assert_draft_ready(db_session, report)
    assert error.value.reason == "not_confirmed"
    assert error.value.readiness["allowed"] is False


def test_only_confirmed_findings_are_returned(db_session: Any) -> None:
    report = _seed_report(db_session)
    _check(db_session, index=0, status="confirmed", verdict="supported", evidence_ids=[11])
    _check(db_session, index=1, status="rejected", evidence_ids=[12])
    _check(db_session, index=2, status="pending", evidence_ids=[13])

    state = readiness_of(db_session, report)
    assert state.allowed is True
    assert state.confirmed == 1
    assert state.by_verdict == {"supported": 1, "partially_supported": 2}

    picked = confirmed_findings(db_session, report)
    assert [item["finding_index"] for item in picked] == [0]
    assert picked[0]["text"] == "组件价格 2024 年跌到 0.68 元/W"
    assert picked[0]["evidence_ids"] == [11]
    assert picked[0]["verdict"] == "supported"
    assert picked[0]["check_id"] > 0
    # 被驳回和待复核的结论一条都不许混进来
    texts = {item["text"] for item in picked}
    assert "龙头一体化企业毛利率转负" not in texts
    assert "价格战是行业出清的唯定路径" not in texts


def test_confirmed_check_without_a_matching_finding_is_not_counted(db_session: Any) -> None:
    """核查行对不上报告里的结论下标时，不算确认（宁可少写）。"""
    report = _seed_report(db_session)
    _check(db_session, index=9, status="confirmed", evidence_ids=[1])
    assert readiness_of(db_session, report).allowed is True  # 有人确认过
    assert confirmed_findings(db_session, report) == []  # 但没有一条能进稿


def test_manual_report_findings_use_the_statement_key(db_session: Any) -> None:
    """人工经接口创建的结论字段叫 statement，也要能进稿。"""
    from backend.app.models.research import ResearchReport

    row = ResearchReport(
        id=6,
        title="人工写的报告",
        summary="摘要。",
        findings=[{"statement": "净息差 1.52%", "nature": "fact", "evidence_ids": []}],
        limitations="样本有限。",
        verification_status="unverified",
    )
    db_session.add(row)
    db_session.commit()
    _check(db_session, index=0, status="confirmed", report_id=6, evidence_ids=[3])
    picked = confirmed_findings(db_session, row)
    assert picked and picked[0]["text"] == "净息差 1.52%"


# ------------------------------------------------------------------ 交接层


@pytest.fixture()
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "provider_disabled"}},
    )


def test_handoff_refuses_without_a_confirmed_finding(tmp_path: Path, no_model: None) -> None:
    with pytest.raises(VideoHandoffError) as error:
        create_video_job(_Report(), root=tmp_path, confirmed=[])
    assert error.value.reason == "draft_not_confirmed"
    assert "语义核查" in str(error.value)
    assert not any(tmp_path.iterdir()), "被拒绝时不许留下半个任务目录"


def test_handoff_writes_confirmed_and_unconfirmed_sections(
    tmp_path: Path, no_model: None
) -> None:
    class _Evidence:
        def __init__(self, evidence_id: int, source_id: int, claim: str) -> None:
            self.id = evidence_id
            self.source_id = source_id
            self.claim = claim
            self.original_excerpt = claim
            self.quote_locator = None
            self.verification_status = "verified"

    class _Source:
        def __init__(self, source_id: int, title: str) -> None:
            self.id = source_id
            self.title = title
            self.url = f"https://example.com/{source_id}"
            self.publisher = "某机构"
            self.published_at = None
            self.content_hash = "b" * 64
            self.credibility_level = "high"

    confirmed = [
        {
            "finding_index": 0,
            "check_id": 42,
            "verdict": "supported",
            "evidence_ids": [1],
            "source_ids": ["S01"],
        }
    ]
    scaffold = create_video_job(
        _Report(),
        evidence=[_Evidence(1, 1, "组件价格跌到 0.68 元/W"), _Evidence(2, 2, "毛利率转负")],
        sources=[_Source(1, "某协会"), _Source(2, "某公司年报")],
        root=tmp_path,
        confirmed=confirmed,
    )
    job = tmp_path / scaffold.job_id
    report_md = (job / "research" / "report.md").read_text(encoding="utf-8")
    assert "人工已确认结论：1 条" in report_md
    assert "组件价格 2024 年跌到 0.68 元/W" in report_md
    assert "尚未人工确认（不得进成稿）" in report_md
    # 两类标号必须一眼分得清：结论序号来自报告，核查记录号来自 finding_checks。
    assert "结论 #0" in report_md and "核查记录 #" in report_md
    assert "结论 #1（待人工复核）" in report_md
    assert "结论 #2（待人工复核）" in report_md

    import json

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["confirmed_findings"] == 1
    assert manifest["counts"]["draft_evidence"] == 1  # 只吃确认结论背后的证据
    assert "confirmed" in manifest["draft_gate"]["rule"]


# ------------------------------------------------------------------ 接口层


def test_readiness_endpoint_reports_the_gate(client, db_session: Any) -> None:
    created = client.post(
        "/api/research-reports",
        json={
            "title": "闸门接口",
            "summary": "摘要。",
            "limitations": "样本有限。",
            "findings": [
                {"statement": "结论一", "nature": "fact"},
                {"statement": "结论二", "nature": "inference"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    before = client.get(f"/api/research-reports/{report_id}/draft-readiness")
    assert before.status_code == 200, before.text
    assert before.json()["allowed"] is False
    assert before.json()["reason"] == "no_checks"
    assert "confirmed" in before.json()["rule"]

    check_id = _check(db_session, index=0, status="confirmed", report_id=report_id, evidence_ids=[1])
    after = client.get(f"/api/research-reports/{report_id}/draft-readiness")
    body = after.json()
    assert body["allowed"] is True
    assert body["confirmed"] == 1
    assert body["total_checks"] == 1

    # 人工复核走同一个接口，状态与闸门联动
    review = client.post(
        f"/api/research-reports/{report_id}/checks/{check_id}/review",
        json={"status": "rejected", "note": "原文只有区间，没有这个点值"},
    )
    assert review.status_code == 200, review.text
    final = client.get(f"/api/research-reports/{report_id}/draft-readiness").json()
    assert final["allowed"] is False
    assert final["reason"] == "not_confirmed"


def test_checks_list_survives_the_notes_and_quotes_columns(client, db_session: Any) -> None:
    """`GET /checks` 曾被两处列形态绊倒：`notes` 是数组、`supporting` 是 NULL。

    真实链路里执行器写的是 `notes=[\"usage_share:1/6\"]`，历史行还可能是 NULL；
    只要 schema 只认一种形态，整个列表就 500 —— 这里把三种形态一起钉住。
    """
    from backend.app.models.research import FindingCheck

    created = client.post(
        "/api/research-reports",
        json={
            "title": "列形态",
            "summary": "摘要。",
            "limitations": "样本有限。",
            "findings": [{"statement": "结论一", "nature": "fact"}],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    db_session.add_all(
        [
            FindingCheck(
                research_report_id=report_id,
                finding_index=0,
                claim="结论一",
                verdict="supported",
                reason="原文逐字可查",
                evidence_ids=[1],
                source_ids=["S01"],
                supporting=["净息差降至1.52%"],
                contradicting=None,
                missing=None,
                notes=["usage_share:1/6"],
                human_review_status="pending",
            ),
            FindingCheck(
                research_report_id=report_id,
                finding_index=1,
                claim="结论二",
                verdict="unverifiable",
                reason="来源只给区间",
                evidence_ids=[],
                source_ids=[],
                supporting=None,
                contradicting=[],
                missing=["缺少点值"],
                notes={"why": "没有可比口径"},
                human_review_status="pending",
            ),
        ]
    )
    db_session.commit()

    listed = client.get(f"/api/research-reports/{report_id}/checks?limit=10")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 2
    assert items[0]["notes"] == ["usage_share:1/6"]
    assert items[0]["supporting"] == ["净息差降至1.52%"]
    assert items[0]["contradicting"] == []
    assert items[1]["notes"] == {"why": "没有可比口径"}
    assert items[1]["missing"] == ["缺少点值"]
