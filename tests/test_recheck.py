"""重新核查（`backend/app/research/recheck.py`）的行为约定。

这一层的用途只有一个：当年核查漏过条（模型截断重试把没问到的结论丢了）时，
让人能按现有证据重判一遍，把缺的核查行补回来。三条底线必须守住：

1. 只重判，不重新检索（证据就是当初入库的那些）；
2. 人工已经确认过的结论，重跑后仍是确认状态（按 finding_index 继承）；
3. 报告不存在 / 没结论 / 没证据时，明确报原因，不假装核查过。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.models.evidence import Evidence
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.research import recheck as recheck_module
from backend.app.research.recheck import RecheckError, recheck_report

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest.fixture()
def session_factory(db_session: Any) -> Any:
    """与 db_session 共用同一个连接的新 session 工厂。

    `recheck_report` 用 `with session_factory() as session:`，传 session 本身会被 close 掉，
    所以这里给它一个"每次新建、退出即关"的工厂，和线上 `get_session_factory()` 形状一致。
    """
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False, future=True)
    yield maker


@pytest.fixture()
def clean_tables(db_session: Any) -> Any:
    for table in (FindingCheck, Evidence, ResearchReport):
        for row in db_session.execute(select(table)).scalars().all():
            db_session.delete(row)
    db_session.commit()
    yield
    for table in (FindingCheck, Evidence, ResearchReport):
        for row in db_session.execute(select(table)).scalars().all():
            db_session.delete(row)
    db_session.commit()


def _report(db_session: Any, *, findings: list[dict[str, Any]]) -> ResearchReport:
    row = ResearchReport(
        title="重核查测试报告",
        summary="摘要",
        findings=findings,
        limitations="探测数据",
        verification_status="unverified",
        outline=[{"source_id": "S01", "url": "https://example.com/a", "title": "来源甲"}],
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _evidence(db_session: Any, report: ResearchReport, *, source_row_id: int) -> None:
    db_session.add(
        Evidence(
            research_report_id=report.id,
            source_id=source_row_id,
            claim="容量电价不按发电量结算",
            original_excerpt="容量电价按可用容量而非发电量结算。",
        )
    )
    db_session.commit()


def _source(db_session: Any, *, url: str) -> int:
    from backend.app.models.source import Source

    row = Source(title="来源甲", url=url, publisher="测试")
    db_session.add(row)
    db_session.commit()
    return int(row.id)


class _StubChecked:
    """冒充 `semantic.SemanticReport`，只提供 record_checks 需要的字段。"""

    def __init__(self, checks: list[Any]) -> None:
        self.checks = tuple(checks)
        self.provider = "deepseek"
        self.model = "deepseek-flash"
        self.prompt_tokens = 1200
        self.completion_tokens = 400
        self.cost_cny = 0.0043
        self.notes = ("retried:2 次",)

    def to_markdown(self, *, objective: str = "") -> str:  # pragma: no cover - 不在这层用
        return ""

    def as_dict(self) -> dict[str, Any]:  # pragma: no cover - 不在这层用
        return {}


def _stub_factory(count: int) -> Any:
    from backend.app.research.semantic import FindingCheck as SemanticCheck

    def fake_check_findings(findings, units, **kwargs):  # noqa: ANN001, ARG001
        checks = [
            SemanticCheck(
                index=index,
                claim=str(finding["text"]),
                claim_kind="fact",
                verdict="supported" if index % 2 == 0 else "insufficient_evidence",
                reason="模型理由",
                evidence_ids=[unit.evidence_id for unit in units][:1],
                source_ids=["S01"],
                supporting=[],
                contradicting=[],
                missing=[],
                issues=[],
                notes=("usage_share:1/3",),
            )
            for index, finding in enumerate(findings[:count])
        ]
        return _StubChecked(checks)

    return fake_check_findings


def test_recheck_rewrites_every_finding(db_session: Any, session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(db_session, findings=[{"text": f"结论 {i}"} for i in range(3)])
    _evidence(db_session, report, source_row_id=_source(db_session, url="https://example.com/a"))
    monkeypatch.setattr(recheck_module, "_check_findings", _stub_factory(3), raising=False)

    result = recheck_report(session_factory, report.id)

    assert result.findings_total == 3
    assert result.checks == 3
    assert result.by_verdict == {"supported": 2, "insufficient_evidence": 1}
    assert result.cost_cny == pytest.approx(0.0043)
    rows = db_session.execute(
        select(FindingCheck).where(FindingCheck.research_report_id == report.id)
    ).scalars().all()
    assert len(rows) == 3
    assert {row.finding_index for row in rows} == {0, 1, 2}


def test_recheck_keeps_the_human_verdict(db_session: Any, session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """人已经确认过的第 1 条，重跑之后不许被打回 pending。"""
    report = _report(db_session, findings=[{"text": "结论 A"}, {"text": "结论 B"}])
    _evidence(db_session, report, source_row_id=_source(db_session, url="https://example.com/a"))
    db_session.add(
        FindingCheck(
            research_report_id=report.id,
            finding_index=1,
            claim="结论 B",
            verdict="supported",
            reason="旧判定",
            evidence_ids=[],
            source_ids=["S01"],
            human_review_status="confirmed",
            human_note="对照过原文",
        )
    )
    db_session.commit()
    monkeypatch.setattr(recheck_module, "_check_findings", _stub_factory(2), raising=False)

    recheck_report(session_factory, report.id)

    rows = {
        row.finding_index: row
        for row in db_session.execute(
            select(FindingCheck).where(FindingCheck.research_report_id == report.id)
        ).scalars().all()
    }
    assert rows[1].human_review_status == "confirmed"
    assert rows[1].human_note == "对照过原文"
    assert rows[0].human_review_status == "pending"


def test_recheck_refuses_without_findings(db_session: Any, session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(db_session, findings=[])
    monkeypatch.setattr(recheck_module, "_check_findings", _stub_factory(0), raising=False)

    with pytest.raises(RecheckError) as caught:
        recheck_report(session_factory, report.id)

    assert caught.value.reason == "no_findings"


def test_recheck_refuses_without_evidence(db_session: Any, session_factory: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(db_session, findings=[{"text": "结论 A"}])
    monkeypatch.setattr(recheck_module, "_check_findings", _stub_factory(1), raising=False)

    with pytest.raises(RecheckError) as caught:
        recheck_report(session_factory, report.id)

    assert caught.value.reason == "no_evidence"


def test_recheck_unknown_report(db_session: Any, session_factory: Any) -> None:
    with pytest.raises(RecheckError) as caught:
        recheck_report(session_factory, 999999)

    assert caught.value.reason == "unknown_report"


def test_recheck_api_returns_the_summary(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.api.routes import research_reports as routes

    def fake(session_factory, report_id, **kwargs):  # noqa: ANN001, ARG001
        return SimpleNamespace(
            as_dict=lambda: {
                "report_id": int(report_id),
                "findings_total": 6,
                "checks": 6,
                "by_verdict": {"supported": 4, "insufficient_evidence": 2},
                "by_human_review": {"pending": 6},
                "provider": "deepseek",
                "model": "deepseek-flash",
                "prompt_tokens": 3400,
                "completion_tokens": 1800,
                "cost_cny": 0.0233,
                "retried": False,
                "note": "按现有证据重判了 6 条结论（报告里共 6 条）",
            }
        )

    monkeypatch.setattr(routes, "recheck_report", fake)
    response = client.post("/api/research-reports/2/checks/recheck")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["checks"] == 6
    assert body["by_verdict"]["supported"] == 4


def test_recheck_api_reports_the_reason(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.api.routes import research_reports as routes

    def fake(session_factory, report_id, **kwargs):  # noqa: ANN001, ARG001
        raise RecheckError("no_evidence", "这份报告没有入库证据")

    monkeypatch.setattr(routes, "recheck_report", fake)
    response = client.post("/api/research-reports/2/checks/recheck")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "domain_validation_error"
    assert body["error"]["details"]["reason"] == "no_evidence"
