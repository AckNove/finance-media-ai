"""采纳选题建议：来源不许丢、重复不许建、客户端的话不许当事实。

测试库是 session 级共享的，所以每个用例的标题都带唯一后缀——
否则前一个用例建的"很像的选题"会把后一个判成重复。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.models.source import Source
from backend.app.models.topic import Topic, TopicColumn
from backend.app.services.topic_intake import IntakeError, find_duplicate, intake_suggestion


def _unique(base: str) -> str:
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _source(session, *, title: str, publisher: str = "国家统计局") -> Source:
    row = Source(
        title=title,
        url=f"https://example.com/{uuid.uuid4().hex[:10]}",
        publisher=publisher,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _column(session, *, code: str, name: str) -> TopicColumn:
    existing = session.query(TopicColumn).filter_by(code=code).first()
    if existing is not None:
        return existing
    row = TopicColumn(code=code, name=name, display_order=1)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_intake_keeps_the_sources_it_came_from(db_session) -> None:
    marker = uuid.uuid4().hex[:6]
    first = _source(db_session, title=f"商业银行净息差降至1.52%-{marker}")
    second = _source(db_session, title=f"净息差连续三年收窄-{marker}", publisher="金融时报")

    result = intake_suggestion(
        db_session,
        title=_unique("商业银行的净息差为什么一路往下"),
        source_ids=[first.id, second.id],
        score=62,
        reasons=["官方来源，时效新", "多家报道"],
        adopt_note="想做一条机制解释",
    )

    assert result.topic.id > 0
    # 来源由服务端回库读：标题/发布方都在描述里，客户端传不了这些字
    assert f"商业银行净息差降至1.52%-{marker}" in result.description
    assert "金融时报" in result.description
    assert "国家统计局" in result.description
    assert "2026-10-01" in result.description
    # 分数与理由照抄"当时页面显示"，并写明不是事实判断
    assert "62 分" in result.description
    assert "不是事实判断" in result.description
    assert "想做一条机制解释" in result.description
    assert len(result.sources) == 2
    assert result.sources[0]["id"] == first.id
    assert "带上 2 条来源" in result.note


def test_intake_refuses_a_near_duplicate_title(db_session) -> None:
    base = _unique("商业银行的净息差为什么一路往下")
    db_session.add(Topic(slug=f"dup-{uuid.uuid4().hex[:8]}", title=base))
    db_session.commit()
    source = _source(db_session, title="某来源")
    before = db_session.query(Topic).count()

    with pytest.raises(IntakeError) as caught:
        intake_suggestion(db_session, title=f"{base}？", source_ids=[source.id])
    assert caught.value.reason == "duplicate_topic"
    assert "已经有一个很像的选题" in caught.value.message
    # 拒绝时不许留下半截
    assert db_session.query(Topic).count() == before


def test_find_duplicate_ignores_unrelated_titles(db_session) -> None:
    assert find_duplicate(db_session, _unique("储能电站的商业模式")) is None


def test_intake_refuses_sources_that_are_gone(db_session) -> None:
    with pytest.raises(IntakeError) as caught:
        intake_suggestion(db_session, title=_unique("随便一个题目"), source_ids=[99991, 99992])
    assert caught.value.reason == "unknown_sources"
    assert "99991" in caught.value.message


def test_intake_refuses_a_bare_title(db_session) -> None:
    with pytest.raises(IntakeError) as caught:
        intake_suggestion(db_session, title=_unique("只有标题"), source_ids=[])
    assert caught.value.reason == "no_sources"
    assert "新建选题" in caught.value.message


def test_intake_refuses_a_blank_title(db_session) -> None:
    source = _source(db_session, title="某来源")
    with pytest.raises(IntakeError) as caught:
        intake_suggestion(db_session, title="   ", source_ids=[source.id])
    assert caught.value.reason == "empty_title"


def test_intake_attaches_the_collection_the_title_hits(db_session) -> None:
    # 「利率」是合集 financial_knowledge 的关键词之一
    column = _column(db_session, code="financial_knowledge", name="金融知识")
    source = _source(db_session, title="某来源")

    result = intake_suggestion(
        db_session, title=_unique("利率下行对居民存款的影响"), source_ids=[source.id]
    )

    assert result.topic.column_id == column.id
    assert result.collections == ("financial_knowledge",)
    assert "挂到合集 financial_knowledge" in result.note


def test_intake_honours_an_explicit_column(db_session) -> None:
    code = f"probe_{uuid.uuid4().hex[:6]}"
    column = _column(db_session, code=code, name="探针栏目")
    source = _source(db_session, title="某来源")

    result = intake_suggestion(
        db_session,
        title=_unique("光伏组件价格战"),
        source_ids=[source.id],
        column_id=column.id,
    )

    assert result.topic.column_id == column.id
    assert result.collections == (code,)


def test_intake_says_so_when_the_column_was_never_created(db_session) -> None:
    """命中合集但库里没这个栏目——最常见的原因是还没 seed 过 taxonomy。"""

    code = "financial_knowledge"
    db_session.query(TopicColumn).filter_by(code=code).delete()
    db_session.commit()
    source = _source(db_session, title="某来源")

    result = intake_suggestion(
        db_session, title=_unique("利率下行对居民存款的影响"), source_ids=[source.id]
    )

    assert result.collections == ()
    assert code in result.note
    assert "seed_collections" in result.note


def test_intake_refuses_an_unknown_column(db_session) -> None:
    source = _source(db_session, title="某来源")
    with pytest.raises(IntakeError) as caught:
        intake_suggestion(
            db_session, title=_unique("光伏组件价格战"), source_ids=[source.id], column_id=424242
        )
    assert caught.value.reason == "unknown_column"


def test_api_creates_the_topic_with_its_sources(client: TestClient, db_session) -> None:
    marker = uuid.uuid4().hex[:6]
    source = _source(db_session, title=f"2025年组件价格跌破现金成本线-{marker}")
    title = _unique("光伏组件为什么跌破现金成本还要出货")

    response = client.post(
        "/api/topics/from-suggestion",
        json={
            "title": title,
            "source_ids": [source.id],
            "score": 71,
            "reasons": ["官方来源"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["topic"]["id"] > 0
    assert body["topic"]["title"] == title
    assert body["sources"][0]["id"] == source.id
    assert body["sources"][0]["title"] == f"2025年组件价格跌破现金成本线-{marker}"
    assert "71 分" in body["description"]
    assert body["note"].startswith("已建选题 #")


def test_api_reports_a_duplicate_as_conflict(client: TestClient, db_session) -> None:
    source = _source(db_session, title="某来源")
    title = _unique("储能电站的商业模式")
    first = client.post(
        "/api/topics/from-suggestion",
        json={"title": title, "source_ids": [source.id]},
    )
    assert first.status_code == 201

    second = client.post(
        "/api/topics/from-suggestion",
        json={"title": f"{title}？", "source_ids": [source.id]},
    )
    assert second.status_code == 409
    assert second.json()["error"]["details"]["reason"] == "duplicate_topic"


def test_api_reports_stale_sources_as_validation_error(
    client: TestClient, db_session
) -> None:
    response = client.post(
        "/api/topics/from-suggestion",
        json={"title": _unique("一个没来源的题目"), "source_ids": [98765]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "unknown_sources"


def test_api_rejects_unknown_fields(client: TestClient, db_session) -> None:
    """页面传来一坨别的字段就说明写法过期了——不要静默忽略。"""

    source = _source(db_session, title="某来源")
    response = client.post(
        "/api/topics/from-suggestion",
        json={
            "title": _unique("储能电站的商业模式"),
            "source_ids": [source.id],
            "source_titles": ["编的"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"
