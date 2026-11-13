"""跨站转载识别的测试。

要证明的三件事：
1. 正文一样的两个页面会被合并，而且**只算一个来源**（不能变成"多来源印证"）；
2. 只是"说了同一个数字"的两条短摘录**不会**被误合并（宁可漏判，不能错并）；
3. 合并是只读的：原始字段一个都不改，只在 `republished_from` 上留个指向。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.research import dedupe
from backend.app.research.corroborate import corroborate
from backend.app.research.digest import DataPoint, Digest, SourceRef, build_digest

#: 一篇"长稿"：长度要超过 dedupe.MIN_TEXT_CHARS，这样才参与判定
ARTICLE = (
    "国家统计局11月15日发布数据显示，2024年10月份社会消费品零售总额同比增长4.8%，"
    "其中除汽车以外的消费品零售额增长4.9%。按经营单位所在地分，城镇消费品零售额增长4.7%，"
    "乡村消费品零售额增长5.3%。按消费类型分，餐饮收入增长3.2%，商品零售增长5.0%。"
    "1—10月份，社会消费品零售总额同比增长3.5%，全国网上零售额同比增长8.8%。"
)
#: 同一篇稿子被另一家站点转载：开头多一句来源说明，正文一致
REPRINT = "本文转自国家统计局网站。" + ARTICLE
#: 另一件事，正文毫不相干
UNRELATED = (
    "光伏组件价格在2025年继续下跌，龙头企业的现金流承压，行业进入整合期。"
    "多家厂商宣布减产检修，硅料价格同步走低，二三线企业开工率降到五成以下，"
    "组件现货报价已经跌破部分企业的现金成本线，行业协会呼吁避免低价无序竞争。"
)


def test_identical_texts_are_grouped_with_the_first_one_as_canonical() -> None:
    groups = dedupe.group_republished({"S01": ARTICLE, "S02": ARTICLE, "S03": UNRELATED})
    assert len(groups) == 1
    assert groups[0].canonical == "S01"
    assert groups[0].duplicates == ("S02",)
    assert groups[0].as_dict()["duplicates"] == ["S02"]


def test_a_prefix_added_by_the_reprint_site_still_counts_as_the_same_article() -> None:
    """转载方常在开头加一句"本文转自…"，正文一致就该合并。"""
    assert dedupe.similarity(ARTICLE, REPRINT) >= dedupe.SIMILAR_THRESHOLD
    groups = dedupe.group_republished({"S01": ARTICLE, "S02": REPRINT})
    assert [group.duplicates for group in groups] == [("S02",)]


def test_a_different_article_is_never_merged() -> None:
    assert dedupe.group_republished({"S01": ARTICLE, "S02": UNRELATED}) == []


def test_short_quotes_are_not_considered_because_collisions_are_too_likely() -> None:
    """两条 30 字的摘录说了同一句话，不能当成"同一篇文章"。"""
    short_a = "2024年商业银行净息差为1.52%，同比下降17个基点"
    short_b = "2024年商业银行净息差为1.52%，同比下降17个基点附近"
    assert dedupe.fingerprint(short_a) == ""  # 太短，不参与
    assert dedupe.group_republished({"S01": short_a, "S02": short_b}) == []


def test_fingerprint_ignores_spacing_and_punctuation() -> None:
    sloppy = "国家统计局 11月15日 发布数据显示，2024年10月份社会消费品零售总额同比增长4.8%。" * 3
    tidy = "国家统计局11月15日发布数据显示2024年10月份社会消费品零售总额同比增长4.8%" * 3
    assert dedupe.fingerprint(sloppy) == dedupe.fingerprint(tidy)
    assert len(dedupe.fingerprint(ARTICLE)) == 64


def test_republished_pages_only_count_as_one_source_when_corroborating() -> None:
    """同一篇被两个站点发布，不算"两个来源互相印证"。"""
    points = [
        DataPoint(source_id="S01", text=ARTICLE, numbers=("4.8%",)),
        DataPoint(source_id="S02", text=REPRINT, numbers=("4.8%",)),
    ]
    # 不告诉它这是同一篇：两条转载体被当成两个独立来源 → 误判成"印证"
    without_aliases = corroborate(points, min_overlap=0.5)
    assert without_aliases.corroborated_count == 1
    assert without_aliases.corroborated[0].source_count == 2

    # 告诉它这是同一篇：只剩一个独立来源 → 退化成"单来源"，不再冒充印证
    with_aliases = corroborate(points, min_overlap=0.5, aliases={"S02": "S01"})
    assert with_aliases.corroborated_count == 0
    assert len(with_aliases.single_source) == 2


def test_alias_keeps_one_quote_per_independent_source() -> None:
    """同一篇的转载页不能各占一格引文：真实的两条独立来源才该各占一格。"""
    third = (
        "国家统计局11月15日发布数据显示，2024年10月份社会消费品零售总额同比增长4.8%，"
        "其中除汽车以外的消费品零售额增长4.9%。乡村消费品零售额增长5.3%，"
        "餐饮收入增长3.2%，商品零售增长5.0%。1—10月份社会消费品零售总额同比增长3.5%。"
    )
    points = [
        DataPoint(source_id="S01", text=ARTICLE, numbers=("4.8%",)),
        DataPoint(source_id="S02", text=REPRINT, numbers=("4.8%",)),
        DataPoint(source_id="S03", text=third, numbers=("4.8%",)),
    ]
    result = corroborate(points, min_overlap=0.5, aliases={"S02": "S01"})
    assert result.corroborated_count == 1
    picked = {point.source_id for point in result.corroborated[0].points}
    assert picked == {"S01", "S03"}  # 转载页 S02 没有单独占一格
    assert result.corroborated[0].source_count == 2


def _upstream(excerpts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "output": {
                "queries": ["社零"],
                "results": [
                    {
                        "query": "社零",
                        "fetched": [
                            {
                                "url": f"https://example.com/{index}",
                                "title": f"稿件 {index}",
                                "excerpt": text,
                                "sha256": f"{index:064d}",
                                "char_count": len(text),
                            }
                            for index, (_label, text) in enumerate(excerpts, start=1)
                        ],
                    }
                ],
            }
        }
    ]


def test_digest_marks_the_reprint_and_keeps_both_rows() -> None:
    digest = build_digest(
        title="社零",
        objective="社零增速",
        upstream=_upstream([("S01", ARTICLE), ("S02", REPRINT), ("S03", UNRELATED)]),
    )
    assert digest.source_count == 3
    assert digest.independent_source_count == 2
    by_id = {source.source_id: source for source in digest.sources}
    assert by_id["S02"].republished_from == "S01"
    assert by_id["S01"].republished_from == ""
    # 原始字段一个都不改：抓到的仍然是抓到的
    assert by_id["S02"].url == "https://example.com/2"
    payload = digest.as_dict()
    assert payload["independent_source_count"] == 2
    assert payload["meta"]["republished"] == {"S02": "S01"}
    markdown = digest.to_markdown()
    assert "合并跨站转载后 2 个独立来源" in markdown
    assert "跨站转载合并" in markdown
    assert "不能把同一篇文章的多个转载页当成多来源印证" in "".join(digest.notes)


def test_digest_without_reprints_says_nothing_about_them() -> None:
    digest = build_digest(
        title="社零", objective="社零增速", upstream=_upstream([("S01", ARTICLE), ("S02", UNRELATED)])
    )
    assert digest.independent_source_count == digest.source_count == 2
    assert "跨站转载合并" not in digest.to_markdown()


def test_digest_source_ref_defaults_to_not_republished() -> None:
    ref = SourceRef(source_id="S01", title="t", url="https://example.com/a")
    assert ref.republished_from == ""
    assert ref.as_dict()["republished_from"] == ""


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (ARTICLE, ARTICLE, 1.0),
        (ARTICLE, UNRELATED, 0.0),
        ("", ARTICLE, 0.0),
    ],
)
def test_similarity_bounds(left: str, right: str, expected: float) -> None:
    assert dedupe.similarity(left, right) == pytest.approx(expected)


def test_duplicate_map_is_flat_and_ordered() -> None:
    groups = dedupe.group_republished(
        {"S01": ARTICLE, "S02": REPRINT, "S03": UNRELATED}
    )
    assert dedupe.duplicate_map(groups) == {"S02": "S01"}
    assert Digest  # 保证导入被使用，避免 lint 抱怨
