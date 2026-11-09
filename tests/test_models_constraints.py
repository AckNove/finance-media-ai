"""数据库模型层：真实 SQLite 约束（CHECK / UNIQUE / FK）与命名约定。"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app.database.base import Base
from backend.app.models import (
    Evidence,
    KnowledgeItem,
    KnowledgeRelation,
    ResearchReport,
    Source,
    Topic,
)

EXPECTED_TABLES = {
    "topics",
    "topic_columns",
    "knowledge_items",
    "knowledge_relations",
    "sources",
    "evidence",
    "research_reports",
    "agent_runs",
    "review_records",
    "scripts",
    "media_assets",
    "publications",
    "institutions",
    "financial_instruments",
    "financial_products",
    "industries",
    "football_cases",
    "financial_cases",
}


def test_metadata_contains_all_domain_tables():
    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables))


def test_naming_convention_is_applied_to_metadata():
    convention = Base.metadata.naming_convention
    assert "ix" in convention
    assert "uq" in convention
    assert "ck" in convention
    assert "fk" in convention
    assert "pk" in convention


def test_topic_priority_check_constraint_rejects_out_of_range(db_session):
    db_session.add(Topic(slug="ck-priority", title="优先级越界", priority=9))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_topic_slug_is_unique_across_rows(db_session):
    db_session.add(Topic(slug="dup-slug", title="第一条"))
    db_session.commit()

    db_session.add(Topic(slug="dup-slug", title="第二条"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_knowledge_relation_rejects_self_loop(db_session):
    item = KnowledgeItem(slug="self-loop-item", title="自环测试条目")
    db_session.add(item)
    db_session.commit()

    db_session.add(
        KnowledgeRelation(
            source_item_id=item.id,
            target_item_id=item.id,
            relation_type="related",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_evidence_requires_a_claim_target(db_session):
    source = Source(title="约束测试来源", source_type="other", content_hash="a" * 64)
    db_session.add(source)
    db_session.commit()

    db_session.add(Evidence(source_id=source.id, claim="没有任何目标的主张"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_keys_are_enforced_by_connection(db_session):
    """source_id 指向不存在的来源必须被拒绝，证明 PRAGMA foreign_keys 生效。"""
    db_session.add(
        Evidence(
            source_id=999999,
            claim="来源不存在的证据",
            knowledge_item_id=None,
            research_report_id=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_research_report_limitations_cannot_be_null(db_session):
    db_session.add(
        ResearchReport(title="缺少局限说明", summary="摘要", findings=[], limitations=None)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_inspector_sees_constraints_and_indexes(db_session):
    inspector = inspect(db_session.get_bind())

    topic_checks = {c["name"] for c in inspector.get_check_constraints("topics")}
    assert any(name and "priority" in name for name in topic_checks)

    evidence_checks = {c["name"] for c in inspector.get_check_constraints("evidence")}
    assert any(name and name.endswith("has_claim_target") for name in evidence_checks)

    # SQLite 下 `unique=True, index=True` 会落成唯一索引；表级 UniqueConstraint
    # （如 publications 的平台唯一发布约束）由命名约定生成 uq_ 名称。
    slug_indexes = {
        entry["name"]: entry["unique"] for entry in inspector.get_indexes("knowledge_items")
    }
    assert bool(slug_indexes.get("ix_knowledge_items_slug"))

    publication_uniques = {u["name"] for u in inspector.get_unique_constraints("publications")}
    assert "uq_publication_per_platform" in publication_uniques

    fks = inspector.get_foreign_keys("knowledge_relations")
    assert any(fk["referred_table"] == "knowledge_items" for fk in fks)


def test_timestamps_are_populated_automatically(db_session):
    topic = Topic(slug="timestamp-check", title="时间戳检查")
    db_session.add(topic)
    db_session.commit()

    assert topic.created_at is not None
    assert topic.updated_at is not None
    assert topic.updated_at >= topic.created_at


def test_sqlite_roundtrip_of_knowledge_json_fields(db_session):
    item = KnowledgeItem(
        slug="pricing-logic-check",
        title="定价逻辑可读回",
        definition="定义",
        pricing_logic="转股价值 = 100 / 转股价 * 正股价格",
        football_analogy="像球员买断条款",
        analogy_limitations="债券有固定到期日，球员合同没有兑付顺序",
        jurisdiction="CN",
        currency="CNY",
        unit="元",
    )
    db_session.add(item)
    db_session.commit()
    db_session.expire_all()

    reloaded = db_session.get(KnowledgeItem, item.id)
    assert reloaded is not None
    assert reloaded.currency == "CNY"
    assert reloaded.verification_status.value == "unverified"
    assert reloaded.version == 1


def test_raw_sql_sees_expected_table_count(db_session):
    rows = db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ).scalars()
    names = set(rows)
    assert EXPECTED_TABLES.issubset(names)
