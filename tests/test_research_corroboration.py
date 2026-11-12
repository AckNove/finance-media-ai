"""跨来源交叉印证的测试。

这一层的产出会被人当成"证据分级"用，所以测试盯的不是"算法聪明"，而是**规则不许乱认**：

* 只有真·多来源、且上下文确实在说同一件事，才允许标印证；
* 年份这类噪声不许冒充"同一个数"；
* 同一件事两种说法要标出来，但**不许**替人判断谁对；
* 长句太短/上下文不够像时宁可不认，不许误合并。
"""

from __future__ import annotations

from backend.app.research.corroborate import (
    DEFAULT_MIN_OVERLAP,
    Conflict,
    Corroboration,
    context_overlap,
    corroborate,
    corroborate_from_dicts,
    key_numbers,
    number_key,
)
from backend.app.research.digest import DataPoint, build_digest

SAME_CLAIM_A = "2024年商业银行净息差为1.52%，同比下降17个基点"
SAME_CLAIM_B = "2024年商业银行净息差为1.52%，同比下降17个基点附近"
DIFFERENT_CLAIM = "2024年商业银行净息差为1.50%，同比下降19个基点"


def _point(source_id: str, text: str, numbers: tuple[str, ...]) -> DataPoint:
    return DataPoint(source_id=source_id, text=text, numbers=numbers)


def test_two_sources_saying_the_same_number_are_corroborated() -> None:
    result = corroborate(
        [
            _point("S01", SAME_CLAIM_A, ("2024", "1.52", "17")),
            _point("S02", SAME_CLAIM_B, ("2024", "1.52", "17")),
        ]
    )
    # 一句话里两个数字都被两个来源说到 → 一个数字一条记录，共两条
    assert result.corroborated_count == 2
    assert result.conflicts == ()
    assert {item.numbers for item in result.corroborated} == {("1.52",), ("17",)}
    for item in result.corroborated:
        assert item.source_count == 2
        assert set(item.source_ids) == {"S01", "S02"}


def test_one_source_alone_is_not_corroboration() -> None:
    """同一个来源里说了两遍不算印证——印证的语义是"独立来源"。"""
    result = corroborate(
        [
            _point("S01", SAME_CLAIM_A, ("2024", "1.52", "17")),
            _point("S01", SAME_CLAIM_B, ("2024", "1.52", "17")),
        ]
    )
    assert result.corroborated_count == 0
    assert result.conflicts == ()
    assert len(result.single_source) == 2


def test_years_do_not_count_as_a_shared_number() -> None:
    """2024 / 2023 这类年份在每句话里都有，不能拿来当"同一个数"。"""
    left = _point("S01", SAME_CLAIM_A, ("2024", "1.52", "17"))
    right = _point("S03", DIFFERENT_CLAIM, ("2024", "1.50", "19"))
    assert key_numbers(left) == ("1.52", "17")
    assert key_numbers(right) == ("1.50", "19")
    result = corroborate([left, right])
    assert result.corroborated_count == 0
    assert result.conflict_count == 1
    conflict = result.conflicts[0]
    assert {conflict.left.source_id, conflict.right.source_id} == {"S01", "S03"}
    assert conflict.overlap >= DEFAULT_MIN_OVERLAP


def test_conflict_never_says_who_is_right() -> None:
    """冲突只陈述"两处说法不一致"，不许出现谁对谁错/可信度这类判断词。"""
    result = corroborate(
        [
            _point("S01", SAME_CLAIM_A, ("2024", "1.52", "17")),
            _point("S03", DIFFERENT_CLAIM, ("2024", "1.50", "19")),
        ]
    )
    markdown = result.to_markdown()
    assert "不判断谁对" in markdown
    for banned in ("正确", "可信度", "应该是", "错误"):
        assert banned not in markdown


def test_the_same_disagreement_is_reported_once() -> None:
    """三个来源都说 A、一个说 B 时，不该冒出三条一模一样的冲突。"""
    result = corroborate(
        [
            _point("S01", SAME_CLAIM_A, ("2024", "1.52", "17")),
            _point("S02", SAME_CLAIM_B, ("2024", "1.52", "17")),
            _point("S03", DIFFERENT_CLAIM, ("2024", "1.50", "19")),
            _point("S04", DIFFERENT_CLAIM + "，与监管口径略有差异", ("2024", "1.50", "19")),
        ]
    )
    assert result.corroborated_count == 4
    assert {item.numbers for item in result.corroborated} == {
        ("1.52",),
        ("17",),
        ("1.50",),
        ("19",),
    }
    assert result.conflict_count == 1
    assert len({(item.left.numbers, item.right.numbers) for item in result.conflicts}) == 1


def test_unrelated_sentences_are_not_merged() -> None:
    """上下文不像的两句，即使都带数字也不许连成一簇。"""
    left = _point("S01", "不良贷款率1.52%，资产质量总体稳定", ("1.52",))
    right = _point("S02", "商业银行净息差收窄至1.52%，压力主要来自资产端", ("1.52",))
    assert context_overlap(
        _context_of(left.text), _context_of(right.text)
    ) < DEFAULT_MIN_OVERLAP
    result = corroborate([left, right])
    assert result.corroborated_count == 0
    assert result.conflict_count == 0
    assert len(result.single_source) == 2


def _context_of(text: str) -> str:
    from backend.app.research.corroborate import _context

    return _context(text)


def test_short_context_is_skipped_instead_of_mis_merged() -> None:
    """上下文去掉数字后只剩几个字，不足以判断"说的是同一件事"。"""
    result = corroborate(
        [
            _point("S01", "占比1.2%", ("1.2",)),
            _point("S02", "占比1.3%", ("1.3",)),
        ]
    )
    assert result.corroborated_count == 0
    assert result.conflicts == ()


def test_points_without_numbers_are_ignored() -> None:
    result = corroborate([_point("S01", "银行靠息差赚钱", ()), _point("S02", "无数字", ())])
    assert result.checked_points == 0
    assert result.corroborated == ()
    assert result.conflicts == ()


def test_number_key_is_stable_and_ignores_years() -> None:
    point = _point("S01", SAME_CLAIM_A, ("17", "2024", "1.52", "1.52"))
    assert number_key(point) == "1.52|17"


def test_result_round_trips_through_dicts() -> None:
    digest = build_digest(
        title="底稿",
        objective="商业银行如何赚钱",
        upstream=[],
    )
    result = corroborate_from_dicts(digest.as_dict()["data_points"])
    assert isinstance(result, Corroboration)
    assert result.checked_points == 0


def test_corroborate_from_dicts_reads_real_payload_shape() -> None:
    payload = [
        {"source_id": "S01", "text": SAME_CLAIM_A, "numbers": ["2024", "1.52", "17"]},
        {"source_id": "S02", "text": SAME_CLAIM_B, "numbers": ["2024", "1.52", "17"]},
        {"source_id": "S03", "text": DIFFERENT_CLAIM, "numbers": ["2024", "1.50", "19"]},
    ]
    result = corroborate_from_dicts(payload)
    assert result.checked_points == 3
    assert result.corroborated_count == 2
    assert result.conflict_count == 1


def test_corroborate_from_dicts_skips_broken_rows() -> None:
    result = corroborate_from_dicts([{"text": "没有来源"}, {"source_id": "S01"}])
    assert result.checked_points == 0


def test_min_sources_can_be_raised() -> None:
    """要求三个来源才算印证时，两个来源就只能算"单一来源"。"""
    points = [
        _point("S01", SAME_CLAIM_A, ("1.52",)),
        _point("S02", SAME_CLAIM_B, ("1.52",)),
    ]
    assert corroborate(points, min_sources=2).corroborated_count == 1
    assert corroborate(points, min_sources=3).corroborated_count == 0


def test_conflict_dataclass_serialises_both_sides() -> None:
    conflict = Conflict(
        left=_point("S01", SAME_CLAIM_A, ("1.52",)),
        right=_point("S03", DIFFERENT_CLAIM, ("1.50",)),
        overlap=0.91234,
    )
    payload = conflict.as_dict()
    assert payload["overlap"] == 0.912
    assert payload["left"]["source_id"] == "S01"
    assert payload["right"]["numbers"] == ["1.50"]


def test_digest_carries_cross_check_and_markdown_section() -> None:
    upstream = [
        {
            "output": {
                "queries": ["商业银行 净息差"],
                "results": [
                    {
                        "query": "商业银行 净息差",
                        "fetched": [
                            {
                                "url": "https://example.com/a",
                                "title": "A",
                                "excerpt": SAME_CLAIM_A,
                                "sha256": "a" * 64,
                                "char_count": len(SAME_CLAIM_A),
                            },
                            {
                                "url": "https://example.com/b",
                                "title": "B",
                                "excerpt": SAME_CLAIM_B,
                                "sha256": "b" * 64,
                                "char_count": len(SAME_CLAIM_B),
                            },
                        ],
                    }
                ],
            }
        }
    ]
    digest = build_digest(title="净息差", objective="商业银行如何赚钱", upstream=upstream)
    assert digest.corroboration is not None
    assert digest.corroboration.corroborated_count >= 1
    assert "corroboration" in digest.as_dict()
    markdown = digest.to_markdown()
    assert "跨来源比对（规则计算，未使用模型）" in markdown
    assert "不判断谁对" in markdown


def test_digest_can_skip_cross_check() -> None:
    digest = build_digest(title="空底稿", cross_check=False)
    assert digest.corroboration is None
    assert "corroboration" not in digest.as_dict()
    assert "跨来源比对" not in digest.to_markdown()
