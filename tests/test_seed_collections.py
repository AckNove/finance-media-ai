"""六大合集写入脚本：幂等、以 YAML 为唯一真相、拒绝污染真实开发库。"""

from __future__ import annotations

from sqlalchemy import select

from backend.app.core.config import load_yaml_config
from backend.app.database.session import get_engine
from backend.app.models.topic import TopicColumn
from backend.scripts import seed_collections as seed_mod


def _yaml_collections() -> list[dict]:
    return list(load_yaml_config("content_taxonomy.yaml")["collections"])


def _reset_collections() -> None:
    """每个用例从干净状态开始（测试库是 session 级共享的）。"""
    from sqlalchemy import delete

    from backend.app.database.session import get_session_factory

    with get_session_factory()() as session:
        session.execute(delete(TopicColumn))
        session.commit()


def test_seed_uses_yaml_as_single_source_of_truth():
    """脚本里的合集必须来自 config/content_taxonomy.yaml，不能在代码里再抄一份。"""
    collections = seed_mod.load_collections()
    assert [item["id"] for item in collections] == [item["id"] for item in _yaml_collections()]
    assert len(collections) == 6


def test_seed_creates_six_system_collections():
    _reset_collections()
    engine = get_engine()

    stats = seed_mod.seed_collections(engine)

    assert stats == {"total": 6, "created": 6, "updated": 0, "unchanged": 0}
    from backend.app.database.session import get_session_factory

    with get_session_factory()() as session:
        rows = session.scalars(select(TopicColumn).order_by(TopicColumn.display_order)).all()
    assert len(rows) == 6
    assert all(row.is_system for row in rows)
    assert all(row.is_active for row in rows)
    assert all(row.taxonomy_version == seed_mod.TAXONOMY_VERSION for row in rows)
    assert [row.code for row in rows] == [item["id"] for item in _yaml_collections()]


def test_seed_is_idempotent():
    _reset_collections()
    engine = get_engine()

    seed_mod.seed_collections(engine)
    second = seed_mod.seed_collections(engine)

    assert second == {"total": 6, "created": 0, "updated": 0, "unchanged": 6}


def test_seed_repairs_flags_without_changing_identity():
    """被人手工改坏的 is_system / is_active 应被修正，但 id 不能变。"""
    from sqlalchemy import update

    from backend.app.database.session import get_session_factory

    engine = get_engine()
    seed_mod.seed_collections(engine)

    factory = get_session_factory()
    with factory() as session:
        original = session.scalar(select(TopicColumn).where(TopicColumn.code == "football_finance"))
        assert original is not None
        original_id: int = original.id
        session.execute(
            update(TopicColumn)
            .where(TopicColumn.id == original_id)
            .values(is_system=False, is_active=False, taxonomy_version=None)
        )
        session.commit()

    stats = seed_mod.seed_collections(engine)
    assert stats["updated"] == 1

    with factory() as session:
        repaired = session.scalar(select(TopicColumn).where(TopicColumn.code == "football_finance"))
    assert repaired is not None
    assert repaired.id == original_id
    assert repaired.is_system is True
    assert repaired.is_active is True
    assert repaired.taxonomy_version == seed_mod.TAXONOMY_VERSION


def test_seed_dry_run_writes_nothing():
    from backend.app.database.session import get_session_factory

    _reset_collections()
    engine = get_engine()
    stats = seed_mod.seed_collections(engine, dry_run=True)

    assert stats["created"] == 6
    with get_session_factory()() as session:
        assert session.scalars(select(TopicColumn)).all() == []


def test_cli_refuses_protected_development_database(capsys):
    """M2.0 明令禁止向 data/finance_media.db 写数据，脚本必须自己拦住。"""
    exit_code = seed_mod.main(
        ["--database-url", "sqlite:///C:/Users/25803/finance-media-ai/data/finance_media.db"]
    )

    assert exit_code == 2
    assert "受保护" in capsys.readouterr().out
    assert seed_mod.is_protected("sqlite:///./data/finance_media.db") is True
    assert seed_mod.is_protected("sqlite:///C:/tmp/temp.db") is False
