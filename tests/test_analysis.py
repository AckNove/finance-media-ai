"""数据分析与图表的测试。

重点在**算得对、画得稳**：

- 数字解析：中文财经文本里的千分位、括号负数、单位后缀、缺失值；
- 指标：同比/CAGR/占比/HHI 的边界（除零不返回 1e309，返回 None）；
- 出图：同样输入**两次渲染字节一致**（视频重渲时哈希要对得上）、
  图上留了出处行、中文字形不是 fallback。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.analysis import (
    bar_chart,
    cagr,
    column,
    concentration,
    describe,
    line_chart,
    margin,
    parse_number,
    share,
    share_bar,
    top_n,
    yoy,
)


# ------------------------------------------------------------------ 数字解析


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.5", 1234.5),
        ("4.28万亿元", 4.28e12),
        ("123.4亿元", 123.4e8),
        ("12.3%", 12.3),
        ("-1.05％", -1.05),
        ("（123）", -123.0),
        (1234, 1234.0),
        ("", None),
        ("--", None),
        ("不适用", None),
        ("无", None),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_number_reads_financial_text(raw: object, expected: float | None) -> None:
    parsed = parse_number(raw)
    if expected is None:
        assert parsed is None
    else:
        # 4.28 × 1e12 这类乘法有浮点尾巴，按近似比
        assert parsed == pytest.approx(expected, rel=1e-9)


def test_parse_number_does_not_turn_percent_into_fraction() -> None:
    # 12.3% 就是 12.3，不是 0.123：指标层统一按"百分数"口径，避免千人千面
    assert parse_number("12.3%") == 12.3
    assert parse_number("12.3％") == 12.3


def test_parse_number_applies_default_unit_scale() -> None:
    assert parse_number("123", default_unit_scale=1e8) == 123e8
    # 已经写了单位就以文本为准，不再重复乘
    assert parse_number("123亿元", default_unit_scale=1e4) == 123e8


# ------------------------------------------------------------------ 取列


def test_column_skips_unreadable_rows_and_keeps_row_numbers() -> None:
    rows = [
        {"年": "2022", "收入": "1,000"},
        {"年": "2023", "收入": "—"},
        {"年": "2024", "收入": "1,200"},
    ]
    parsed = column(rows, "收入", label_column="年")
    assert parsed.values == (1000.0, 1200.0)
    assert parsed.labels == ("2022", "2024")
    assert parsed.source_rows == (1, 3)
    assert parsed.size == 2


def test_column_can_insist_every_row_parses() -> None:
    rows = [{"收入": "1000"}, {"收入": "n/a"}]
    with pytest.raises(Exception) as error:
        column(rows, "收入", require_all=True)
    assert "读不出数字" in str(error.value)


def test_column_rejects_unknown_column_name() -> None:
    with pytest.raises(KeyError):
        column([{"收入": "1"}], "利润")


# ------------------------------------------------------------------ 指标


def test_yoy_and_cagr() -> None:
    assert yoy(120, 100) == pytest.approx(20.0)
    assert yoy(80, 100) == pytest.approx(-20.0)
    assert yoy(1, 0) is None  # 上期为 0 不制造无穷大
    assert cagr(100, 121, 2) == pytest.approx(10.0, abs=0.01)
    assert cagr(0, 100, 2) is None
    assert cagr(100, 121, 0) is None


def test_share_and_margin_are_none_when_denominator_is_zero() -> None:
    assert share(25, 100) == pytest.approx(25.0)
    assert share(25, 0) is None
    assert margin(4.28, 100) == pytest.approx(4.28)
    assert margin(1, 0) is None


def test_concentration_is_hhi_in_basis_points() -> None:
    assert concentration([50, 30, 20]) == pytest.approx(3800.0)
    assert concentration([100]) == pytest.approx(10000.0)
    assert concentration([]) is None
    assert concentration([0, 0]) is None
    assert concentration([-1, 2]) is None


def test_top_n_sorts_and_truncates() -> None:
    items = [("A", 3), ("B", 10), ("C", 7)]
    assert top_n(items, 2) == [("B", 10), ("C", 7)]
    assert top_n(items, 0) == []


def test_describe_reports_nothing_for_empty_series() -> None:
    empty = describe([])
    assert empty["count"] == 0
    assert empty["mean"] is None  # 不用 0 冒充

    stats = describe([100.0, 110.0, 121.0])
    assert stats["count"] == 3
    assert stats["min"] == 100.0
    assert stats["max"] == 121.0
    assert stats["median"] == 110.0
    assert stats["change"] == 21.0
    assert stats["change_pct"] == pytest.approx(21.0)


# ------------------------------------------------------------------ 出图


def test_line_chart_is_byte_identical_across_two_renders(tmp_path: Path) -> None:
    first = line_chart(
        [("净息差", [1.9, 1.8, 1.7, 1.54])],
        ["2021", "2022", "2023", "2024"],
        output=tmp_path / "a.png",
        title="净息差下行",
        unit="%",
        source_note="来源：某银行年报（示例数据）",
    )
    second = line_chart(
        [("净息差", [1.9, 1.8, 1.7, 1.54])],
        ["2021", "2022", "2023", "2024"],
        output=tmp_path / "b.png",
        title="净息差下行",
        unit="%",
        source_note="来源：某银行年报（示例数据）",
    )
    assert first.path.is_file() and first.path.stat().st_size > 5000
    # 同输入必须同字节：否则视频重渲时素材哈希会变，证据链断掉
    assert first.sha256 == second.sha256
    assert (first.width, first.height) == (1080, 1350)


def test_line_chart_rejects_mismatched_lengths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        line_chart([("A", [1, 2])], ["2024"], output=tmp_path / "x.png")


def test_bar_chart_draws_labels_and_values(tmp_path: Path) -> None:
    result = bar_chart(
        ["利息净收入", "手续费", "投资收益"],
        [4280.0, 1180.0, 640.0],
        output=tmp_path / "bar.png",
        title="收入构成",
        unit="亿元",
        source_note="来源：示例",
    )
    assert result.kind == "bar"
    assert result.point_count == 3
    assert result.path.is_file()


def test_share_bar_needs_positive_total(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        share_bar([("A", 0), ("B", 0)], output=tmp_path / "share.png")


def test_share_bar_writes_png(tmp_path: Path) -> None:
    parts = [("利息净收入", 70.0), ("手续费", 20.0), ("其他", 10.0)]
    result = share_bar(parts, output=tmp_path / "share.png", title="收入结构", source_note="来源：示例")
    assert result.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.point_count == 3
    # 堆叠图同样要可复现：图例、配色、边距都不能带随机成分
    again = share_bar(parts, output=tmp_path / "share2.png", title="收入结构", source_note="来源：示例")
    assert result.sha256 == again.sha256


def test_charts_use_a_real_cjk_font(tmp_path: Path) -> None:
    result = bar_chart(["利息", "手续费"], [1.0, 2.0], output=tmp_path / "font.png")
    # 机器上应当能找到 Noto Sans SC / 微软雅黑；找不到就会退回 fallback（中文会变方框）
    assert result.font_family != "fallback"
