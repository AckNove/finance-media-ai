"""选题建议：从已入库来源里按规则挑候选题目（不联网、不调模型）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.app.models.source import Source
from backend.app.research import topic_suggestions as ts

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
FRESH = "2026-11-10"
STALE = "2025-10-01"


def _candidate(
    title: str,
    *,
    source_id: int = 1,
    url: str = "https://www.pbc.gov.cn/a",
    publisher: str = "中国人民银行",
    published_at: str = FRESH,
    now: datetime = NOW,
) -> ts.Candidate:
    return ts.candidate_of(
        source_id=source_id,
        title=title,
        url=url,
        publisher=publisher,
        published_at=published_at,
        now=now,
    )


# --- 纯函数 ---------------------------------------------------------------


def test_normalize_title_drops_punctuation_and_case():
    assert ts.normalize_title("  央行：降准 0.5 个百分点！（2026） ") == "央行降准05个百分点2026"
    assert ts.normalize_title("") == ""


def test_similarity_is_one_for_the_same_title_and_zero_for_empty():
    left = ts.normalize_title("光伏组件价格战吃掉龙头现金流")
    right = ts.normalize_title("光伏组件价格战吃掉龙头现金流")
    assert ts.similarity(left, right) == pytest.approx(1.0)
    assert ts.similarity(left, "") == 0.0
    assert ts.similarity(left, ts.normalize_title("商业银行靠什么赚钱")) < 0.2


def test_numbers_in_keeps_whole_units():
    assert ts.numbers_in("隆基绿能2024年净利润亏损86.18亿元") == ("86.18亿元",)
    # 长单位优先：不能把"5.2万亿"切成"5.2万"
    assert ts.numbers_in("社融存量5.2万亿元") == ("5.2万亿元",)
    # 没有单位的裸数字（年份、数量）不算数据
    assert ts.numbers_in("2024年第四季度报告") == ()


def test_load_collection_keywords_covers_the_six_collections():
    keywords = ts.load_collection_keywords()
    assert set(keywords) == {
        "financial_knowledge",
        "market_briefing",
        "industry_research",
        "company_research",
        "football_cases",
        "personal_finance",
    } or len(keywords) >= 6
    assert any("利率" in word for word in keywords["financial_knowledge"])


def test_collections_of_matches_scope_words():
    keywords = ts.load_collection_keywords()
    hits = ts.collections_of("央行降准0.5个百分点，利率与汇率怎么走", keywords)
    assert "financial_knowledge" in hits


def test_score_candidate_explains_every_point():
    candidate = _candidate("央行降准0.5个百分点，释放长期资金1万亿元")
    score, reasons = ts.score_candidate(
        candidate, keywords=ts.load_collection_keywords(), publishers=2
    )
    assert score >= 24 + 30 + 8
    joined = "；".join(reasons)
    assert "官方" in joined
    assert "数据" in joined
    assert "2 个不同发布方" in joined


def test_aggregator_and_stale_source_scores_low():
    fresh_official = _candidate("国家统计局：10月社融存量同比增长8.2%")
    stale_aggregator = _candidate(
        "国家统计局：10月社融存量同比增长8.2%",
        url="https://baijiahao.baidu.com/x",
        publisher="百家号",
        published_at=STALE,
    )
    keywords = ts.load_collection_keywords()
    high, _ = ts.score_candidate(fresh_official, keywords=keywords)
    low, low_reasons = ts.score_candidate(stale_aggregator, keywords=keywords)
    assert high > low
    assert any("聚合" in reason for reason in low_reasons)


def test_suggest_drops_old_shell_and_duplicate_titles():
    keywords = ts.load_collection_keywords()
    items = [
        _candidate("央行降准0.5个百分点，释放长期资金1万亿元", source_id=1),
        # 超过窗口的直接丢
        _candidate("国家统计局：10月社融存量同比增长8.2%", source_id=2, published_at=STALE),
        # 壳标题
        _candidate("免责声明", source_id=3),
        # 与已有选题重复
        _candidate("商业银行是如何赚钱的", source_id=4),
    ]
    result = ts.suggest(
        items,
        existing_titles=["商业银行是如何赚钱的"],
        keywords=keywords,
        window_days=30,
    )
    titles = [item.title for item in result]
    assert titles == ["央行降准0.5个百分点，释放长期资金1万亿元"]


def test_suggest_merges_same_story_from_two_publishers():
    keywords = ts.load_collection_keywords()
    items = [
        _candidate("央行降准0.5个百分点，释放长期资金1万亿元", source_id=1),
        _candidate(
            "央行降准0.5个百分点，释放长期资金1万亿元",
            source_id=2,
            url="https://www.cs.com.cn/b",
            publisher="中国证券报",
        ),
    ]
    result = ts.suggest(items, keywords=keywords, window_days=30)
    assert len(result) == 1
    only = result[0]
    assert only.corroborated is True
    assert only.source_ids == (1, 2)
    assert len(only.publishers) == 2
    assert any("2 个不同发布方" in reason for reason in only.reasons)


def test_suggest_respects_min_score_and_limit():
    keywords = ts.load_collection_keywords()
    items = [
        _candidate(f"某聚合站点转载的第{n}条普通新闻", source_id=n, url="https://tieba.baidu.com/x",
                   publisher="贴吧", published_at="2026-11-12")
        for n in range(1, 4)
    ]
    assert ts.suggest(items, keywords=keywords, min_score=40) == []
    assert len(ts.suggest(items, keywords=keywords, min_score=0, limit=2)) == 2


def test_candidate_of_maps_tier_and_freshness():
    official = _candidate("国家统计局：10月社融存量同比增长8.2%")
    assert official.tier == "official"
    assert official.freshness == "fresh"
    assert official.age_days == 3

    unknown = ts.candidate_of(source_id=9, title="某网站的说法", url="https://example.org/x")
    assert unknown.tier == "unknown"
    assert unknown.freshness == "unknown"
    assert unknown.age_days is None


# --- 接口 -----------------------------------------------------------------

#: 每个用例给标题加独有标记：测试库是整场共享的，别去删别人的数据
#:（删 sources / topics 会撞外键：evidence 引用 source、topic_collections 引用 topic）。
def _add_source(db_session, title, *, url, publisher, days_ago):
    row = Source(
        title=title,
        url=url,
        publisher=publisher,
        published_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _suggestions(client, **params):
    response = client.get("/api/topics/suggestions", params=params)
    assert response.status_code == 200
    return response.json()


def test_suggestions_api_ranks_and_explains(client, db_session):
    mark = uuid.uuid4().hex[:8]
    title = f"央行降准0.5个百分点，释放长期资金1万亿元（{mark}）"
    _add_source(db_session, title, url="https://www.pbc.gov.cn/a", publisher="中国人民银行", days_ago=2)
    _add_source(db_session, title, url="https://www.cs.com.cn/b", publisher="中国证券报", days_ago=2)
    stale_title = f"某论坛老帖：听说要降息（{mark}）"
    _add_source(db_session, stale_title, url="https://tieba.baidu.com/p/9", publisher="贴吧", days_ago=400)

    body = _suggestions(client, window_days=30, limit=50)
    assert body["window_days"] == 30
    assert body["scanned"] >= 2

    hits = [item for item in body["items"] if mark in item["title"]]
    assert len(hits) == 1, "同一件事的两条来源应该合并成一条建议"
    top = hits[0]
    assert top["score"] >= 60
    assert top["corroborated"] is True
    assert top["tier"] == "official"
    assert top["tier_label"] == "官方/监管"
    assert top["reasons"], "每条建议都必须给出分数理由"
    assert any("2 个不同发布方" in reason for reason in top["reasons"])
    # 400 天前的那条聚合来源超出窗口，不该出现
    assert all(mark not in item["title"] or "老帖" not in item["title"] for item in body["items"])


def test_suggestions_api_skips_titles_already_used(client, db_session):
    mark = uuid.uuid4().hex[:8]
    title = f"央行降准0.5个百分点，释放长期资金1万亿元（{mark}）"
    _add_source(db_session, title, url="https://www.pbc.gov.cn/a", publisher="中国人民银行", days_ago=1)

    before = [item for item in _suggestions(client, limit=50)["items"] if mark in item["title"]]
    assert before, "先决条件：这条标题本来应该在建议里"

    created = client.post("/api/topics", json={"title": title})
    assert created.status_code == 201

    after = [item for item in _suggestions(client, limit=50)["items"] if mark in item["title"]]
    assert after == [], "已经建过同题选题后不该再建议"


def test_suggestions_api_ignores_a_forged_agent_header(client, db_session):
    """HTTP 端永远拿不到 agent 身份（伪造头只记 warning），所以照常 200。"""
    mark = uuid.uuid4().hex[:8]
    _add_source(
        db_session,
        f"央行降准0.5个百分点，释放长期资金1万亿元（{mark}）",
        url="https://www.pbc.gov.cn/a",
        publisher="中国人民银行",
        days_ago=1,
    )
    response = client.get(
        "/api/topics/suggestions",
        headers={"X-Caller": "agent", "X-Task-Id": "1", "X-Attempt-Token": "tkn"},
    )
    assert response.status_code == 200
    # 身份仍是人：伪造的 agent 头没有提升权限，也没有改变结果
    assert any(mark in item["title"] for item in response.json()["items"])


def test_suggestions_api_validates_window(client):
    assert client.get("/api/topics/suggestions", params={"window_days": 0}).status_code == 422
    assert client.get("/api/topics/suggestions", params={"limit": 999}).status_code == 422


def test_suggest_from_db_returns_a_serialisable_payload(db_session):
    mark = uuid.uuid4().hex[:8]
    _add_source(
        db_session,
        f"国家统计局：10月社融存量同比增长8.2%（{mark}）",
        url="https://www.stats.gov.cn/sj/zxfb/",
        publisher="国家统计局",
        days_ago=5,
    )
    payload = ts.suggest_from_db(db_session, window_days=30, limit=50)
    assert Decimal(str(payload["min_score"])) == Decimal("40")
    assert payload["scanned"] >= 1
    assert any(mark in item["title"] for item in payload["items"])
    assert "没有额外联网" in payload["note"]
