"""来源可信度分级与时效判断的口径测试。

这一层全是规则：域名分档、日期解析、天数换算。测试的目的不是"跑通"，
而是把**每条口径钉死**——官方站点算官方、媒体站点算媒体、没发布时间算未知，
以及"发布时间比参照时间还晚"这种脏数据不能被悄悄裁成 0 天。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.research import source_quality as sq
from backend.app.research.digest import Digest, SourceRef, build_digest

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


# --- 站点分级 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.pbc.gov.cn/goutongjiaoliu/113456/index.html", "official"),
        ("http://www.stats.gov.cn/sj/zxfb/2026.html", "official"),
        ("https://www.sse.com.cn/market/", "official"),
        ("https://www.cninfo.com.cn/new/disclosure", "primary"),
        ("https://ir.longi.com/reports/2024.pdf", "primary"),
        ("https://www.yicai.com/news/1024.html", "media"),
        ("https://www.reuters.com/markets/x", "media"),
        ("https://www.baidu.com/link?url=abc", "aggregator"),
        ("https://zhuanlan.zhihu.com/p/123", "aggregator"),
        ("https://some-random-blog.example/post/1", "unknown"),
    ],
)
def test_tiers_follow_the_domain_rules(url: str, expected: str) -> None:
    assert sq.tier_of(url) == expected


def test_a_gov_suffix_is_official_even_if_not_in_the_allowlist() -> None:
    assert sq.tier_of("https://www.example.gov.cn/a") == "official"
    assert sq.tier_of("https://agency.gov/report") == "official"


def test_unknown_host_is_unknown_not_media() -> None:
    """判不出来就写 unknown——**不许因为"看起来像官网"就抬档**。"""

    assert sq.tier_of("https://finance.unknown-site.name/x") == "unknown"
    assert sq.tier_of("") == "unknown"
    assert sq.tier_of("not a url") == "unknown"


# --- 日期解析 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["2026-11-01", "2026/11/01", "2026年11月1日", "2026.11.01", "2026-11-01 08:30", "2026-11-01T08:30:00Z"],
)
def test_common_published_formats_parse(raw: str) -> None:
    parsed = sq.parse_published_at(raw)
    assert parsed is not None
    assert parsed.date().isoformat() == "2026-11-01"


def test_unparsable_and_empty_dates_stay_unknown() -> None:
    assert sq.parse_published_at("") is None
    assert sq.parse_published_at("昨天") is None
    assert sq.freshness_of("", now=NOW) == ("unknown", None)
    assert sq.freshness_of("昨天", now=NOW) == ("unknown", None)


# --- 时效分档 ---------------------------------------------------------------


def test_freshness_thresholds_are_the_documented_ones() -> None:
    assert sq.freshness_of("2026-11-01", now=NOW) == ("fresh", 12)
    assert sq.freshness_of("2026-08-01", now=NOW) == ("recent", 104)
    assert sq.freshness_of("2025-01-01", now=NOW) == ("stale", 681)


def test_as_of_lets_the_report_use_the_digest_date_not_today() -> None:
    """底稿写的是 8 月的日期，就不该按今天的标准判"过期"。"""

    assert sq.freshness_of("2026-02-01", now=NOW, as_of="2026-02-10") == ("fresh", 9)


def test_a_future_published_at_is_reported_not_clamped() -> None:
    """页面时间比参照时间晚：如实报负数天数并归 unknown，不悄悄裁成 0。"""

    freshness, days = sq.freshness_of("2027-01-01", now=NOW)
    assert freshness == "unknown"
    assert days is not None and days < 0


# --- 单条评估与汇总 ---------------------------------------------------------


def _source(**kwargs) -> SourceRef:
    base = {"source_id": "S01", "title": "t", "url": "https://www.yicai.com/a"}
    base.update(kwargs)
    return SourceRef(**base)


def test_assess_marks_media_and_stale_as_needing_an_original_check() -> None:
    item = sq.assess(_source(published_at="2025-01-01"), now=NOW)
    assert item.tier == "media"
    assert item.freshness == "stale"
    assert item.needs_original_check is True
    assert any("口径可能已变" in note for note in item.notes)


def test_official_and_fresh_does_not_need_an_original_check() -> None:
    item = sq.assess(
        _source(url="https://www.pbc.gov.cn/x", published_at="2026-11-01"), now=NOW
    )
    assert item.tier == "official"
    assert item.needs_original_check is False
    assert item.age_label == "12 天"


def test_missing_publish_time_is_called_out_explicitly() -> None:
    item = sq.assess(_source(published_at=""), now=NOW)
    assert item.freshness == "unknown"
    assert item.age_days is None
    assert any("没有给出发布时间" in note for note in item.notes)
    assert item.as_dict()["published_at"] == ""


def test_summary_counts_every_tier_and_flags() -> None:
    qualities = sq.assess_all(
        [
            _source(source_id="S01", url="https://www.pbc.gov.cn/a", published_at="2026-11-01"),
            _source(source_id="S02", url="https://www.yicai.com/b", published_at="2025-03-01"),
            _source(source_id="S03", url="https://zhuanlan.zhihu.com/p/1", published_at=""),
        ],
        now=NOW,
    )
    summary = sq.summarise(qualities)
    assert summary["total"] == 3
    assert summary["by_tier"]["official"] == 1
    assert summary["by_tier"]["media"] == 1
    assert summary["by_tier"]["aggregator"] == 1
    assert summary["missing_published_at"] == 1
    # 只有"官方 + 近 30 天"这一条不需要回原文；过期媒体与无发布时间的自媒体都要回
    assert summary["needs_original_check"] == 2
    assert qualities[0].needs_original_check is False
    assert qualities[1].needs_original_check is True
    assert qualities[2].needs_original_check is True
    assert "不代表这条内容已被核实" in summary["note"]


def test_markdown_table_lists_every_source_and_the_tally() -> None:
    qualities = sq.assess_all(
        [_source(source_id="S01", url="https://www.cninfo.com.cn/x", published_at="2026-10-01")],
        now=NOW,
    )
    markdown = sq.to_markdown(qualities)
    assert "| 编号 | 站点 | 分级 |" in markdown
    assert "S01" in markdown
    assert "一手披露" in markdown
    assert "分级统计" in markdown


def test_markdown_says_no_sources_instead_of_an_empty_table() -> None:
    assert "（没有来源）" in sq.to_markdown([])


# --- 接进底稿 ---------------------------------------------------------------


def test_digest_markdown_carries_the_quality_section() -> None:
    """底稿必须自己带上可信度与时效，而不是等人去另外跑一个脚本。"""

    digest = build_digest(
        title="来源质量",
        objective="测试",
        upstream=[
            {
                "sequence": 1,
                "task_type": "research.collect",
                "output": {
                    "planner": "keywords",
                    "queries": ["商业银行 净息差"],
                    "results": [
                        {
                            "query": "商业银行 净息差",
                            "hit_count": 1,
                            "hits": [],
                            "fetched": [
                                {
                                    "url": "https://www.yicai.com/news/1.html",
                                    "title": "净息差降至1.53%",
                                    "site_name": "第一财经",
                                    "published_at": "2026-11-01",
                                    "author": "",
                                    "sha256": "a" * 64,
                                    "snapshot_path": "",
                                    "char_count": 100,
                                    "excerpt": "净息差降至1.53%，比上年同期下降0.03个百分点。" * 5,
                                }
                            ],
                        }
                    ],
                    "fetched_pages": 1,
                    "errors": [],
                },
            }
        ],
        queries=["商业银行 净息差"],
        now=datetime(2026, 11, 13, 9, 0, tzinfo=UTC),
    )
    markdown = digest.to_markdown()
    assert "来源可信度与时效" in markdown
    assert "财经媒体" in markdown
    payload = digest.as_dict()
    assert payload["source_quality"]["summary"]["total"] == 1
    assert payload["source_quality"]["items"][0]["tier"] == "media"


def test_digest_without_sources_does_not_print_an_empty_quality_table() -> None:
    digest = Digest(title="空", objective="", sources=(), data_points=())
    markdown = digest.to_markdown()
    assert "来源可信度与时效" not in markdown
    assert "没有任何来源被抓取成功" in markdown
