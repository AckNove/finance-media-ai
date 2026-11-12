"""证据链落库：来源去重、域名分类、报告骨架与证据行。

这些用例证明的是"抓到的资料能变成库里可追溯的行"，不是"抓取能通"。
真实抓取的验证在 ``tests/test_fetcher.py`` 与真实网络冒烟里。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import backend.app.models  # noqa: F401  注册全部模型
from backend.app.database.base import Base
from backend.app.models.enums import (
    CredibilityLevel,
    SourceType,
    VerificationStatus,
)
from backend.app.models.evidence import Evidence
from backend.app.models.research import ResearchReport
from backend.app.models.source import Source
from backend.app.research.digest import build_digest
from backend.app.research.persistence import (
    classify_source_type,
    credibility_for,
    digest_fingerprint,
    record_evidence,
    record_report,
    record_sources,
)

NOW = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory):
    path = tmp_path_factory.mktemp("research_db") / "research.db"
    created = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(created)
    yield created
    created.dispose()


@pytest.fixture()
def session(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        for table in Base.metadata.tables.values():
            connection.exec_driver_sql(f'DELETE FROM "{table.name}"')
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as active:
        yield active


_COLLECT = [
    {
        "subtask_id": 1,
        "sequence": 1,
        "task_type": "research.collect",
        "question": "采集",
        "output": {
            "queries": ["商业银行净息差"],
            "results": [
                {
                    "query": "商业银行净息差",
                    "hit_count": 2,
                    "hits": [],
                    "fetched": [
                        {
                            "url": "https://www.pbc.gov.cn/a.html",
                            "title": "货币政策执行报告",
                            "published_at": "2024-11-25T07:54",
                            "author": "",
                            "site_name": "www.pbc.gov.cn",
                            "sha256": "a" * 64,
                            "snapshot_path": "data/snapshots/www.pbc.gov.cn/a.html",
                            "char_count": 4200,
                            "excerpt": "商业银行净息差降至1.53%。",
                        },
                        {
                            "url": "https://finance.example.com/b",
                            "title": "行业观察",
                            "published_at": "",
                            "author": "张三",
                            "site_name": "finance.example.com",
                            "sha256": "b" * 64,
                            "snapshot_path": "data/snapshots/finance.example.com/b.html",
                            "char_count": 2600,
                            "excerpt": "净息差收窄压力仍在，不良率1.56%。",
                        },
                    ],
                }
            ],
            "fetched_pages": 2,
            "errors": [],
        },
    }
]


def _digest():
    return build_digest(
        title="商业银行如何赚钱",
        objective="搞清楚净息差",
        upstream=_COLLECT,
        queries=["商业银行净息差"],
        now=NOW,
    )


def test_source_type_and_credibility_follow_the_domain():
    assert classify_source_type("https://www.pbc.gov.cn/x") is SourceType.OFFICIAL_STATISTICS
    assert classify_source_type("https://www.cninfo.com.cn/x") is SourceType.EXCHANGE_DISCLOSURE
    assert classify_source_type("https://finance.example.com/x") is SourceType.OTHER
    assert classify_source_type(None) is SourceType.OTHER
    assert credibility_for("https://www.pbc.gov.cn/x") is CredibilityLevel.HIGH
    assert credibility_for("https://finance.example.com/x") is CredibilityLevel.UNKNOWN


def test_sources_are_written_with_metadata_and_deduplicated(session):
    digest = _digest()
    first = record_sources(session, digest.sources)
    session.commit()
    assert first.created == 2
    assert set(first.mapping) == {"S01", "S02"}

    rows = session.scalars(select(Source).order_by(Source.id)).all()
    assert [row.title for row in rows] == ["货币政策执行报告", "行业观察"]
    official = rows[0]
    assert official.publisher == "www.pbc.gov.cn"
    assert official.credibility_level is CredibilityLevel.HIGH
    assert official.published_at is not None, "发布日期要能解析进库"
    assert official.content_hash == "a" * 64
    assert official.accessed_at is not None
    assert "本地快照" in (official.notes or "")

    # 同一批来源再抓一次：不新增行，只更新访问时间
    again = record_sources(session, digest.sources)
    session.commit()
    assert again.created == 0
    assert again.updated == 2
    assert len(session.scalars(select(Source)).all()) == 2
    assert again.mapping == first.mapping


def test_existing_source_keeps_curated_fields(session):
    digest = _digest()
    record_sources(session, digest.sources)
    session.commit()
    row = session.scalar(select(Source).where(Source.url == "https://www.pbc.gov.cn/a.html"))
    row.credibility_level = CredibilityLevel.MEDIUM  # 人手工降级
    row.notes = "人工复核后改为中等"
    session.commit()
    record_sources(session, digest.sources)
    session.commit()
    again = session.scalar(select(Source).where(Source.url == "https://www.pbc.gov.cn/a.html"))
    assert again.credibility_level is CredibilityLevel.MEDIUM, "重抓不能覆盖人工判断"
    assert again.notes == "人工复核后改为中等"


def test_report_records_the_rule_built_summary(session):
    digest = _digest()
    report = record_report(session, digest=digest, research_task_id=7)
    session.commit()
    assert report.id is not None
    assert report.title == "商业银行如何赚钱"
    assert "未使用模型" in report.summary
    assert report.verification_status is VerificationStatus.UNVERIFIED
    assert report.research_task_id == 7
    assert report.findings, "数据点要原样列出来"
    assert report.limitations
    assert report.tier_stats["sources"] == 2


def test_report_limitations_carry_the_cross_check_result(session):
    """单一来源与冲突必须写进报告局限里，不能只躺在底稿 markdown 中。"""
    digest = _digest()
    cross = digest.corroboration
    assert cross is not None, "默认要跑跨来源比对"

    report = record_report(session, digest=digest, research_task_id=8)
    session.commit()

    limitations = report.limitations
    assert f"{len(cross.single_source)} 条说法只有单一来源" in limitations
    if cross.corroborated:
        assert "不等于数据正确" in limitations
    if cross.conflicts:
        assert "未判断谁对" in limitations
        assert "引用前必须回原文核对" in limitations
    stats = report.tier_stats["cross_check"]
    assert stats["checked_points"] == cross.checked_points
    assert stats["conflicts"] == cross.conflict_count
    assert stats["single_source"] == len(cross.single_source)


def test_evidence_is_verified_only_where_the_quote_was_found(session):
    digest = _digest()
    recorded = record_sources(session, digest.sources)
    report = record_report(session, digest=digest)
    session.commit()

    first_text = digest.data_points[0].text
    verification = {
        "results": [
            {"source_id": digest.data_points[0].source_id, "text": first_text, "status": "found"}
        ]
    }
    rows = record_evidence(
        session,
        report=report,
        digest=digest,
        source_ids=recorded.mapping,
        verification=verification,
    )
    session.commit()
    assert len(rows) == len(digest.data_points)
    verified = [row for row in rows if row.verification_status is VerificationStatus.VERIFIED]
    assert len(verified) == 1, "只有逐字命中的那条才算已验证"
    assert verified[0].verified_at is not None
    assert verified[0].research_report_id == report.id
    others = [row for row in rows if row.verification_status is VerificationStatus.UNVERIFIED]
    assert others and all(row.verified_at is None for row in others)
    assert all(row.original_excerpt for row in rows), "证据要带原文片段"

    stored = session.scalars(select(Evidence).order_by(Evidence.id)).all()
    assert len(stored) == len(rows)


def test_digest_fingerprint_is_stable_for_the_same_sources():
    assert digest_fingerprint(_digest()) == digest_fingerprint(_digest())
    other = build_digest(title="别的题目", objective="", upstream=[], queries=[], now=NOW)
    assert digest_fingerprint(other) != digest_fingerprint(_digest())


def test_report_without_sources_is_still_honest(session):
    empty = build_digest(title="空研究", objective="", upstream=[], queries=[], now=NOW)
    report = record_report(session, digest=empty)
    session.commit()
    assert report.tier_stats["sources"] == 0
    rows = record_evidence(
        session, report=report, digest=empty, source_ids={}, verification=None
    )
    assert rows == [], "没有来源就不该造证据行"
    stored_report = session.get(ResearchReport, report.id)
    assert stored_report is not None
