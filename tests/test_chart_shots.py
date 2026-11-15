"""图表镜头（B-06）的测试。

要盯住的事只有三件：**只画同指标同单位的量**、**横轴必须说得出是"哪一期/哪个来源"**、
**画出来的图真的落盘并登记进素材清单**。任何一条做不到，就应该退回数据卡——
一张口径错位的图，比没有图糟糕得多。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.media.storyboard import load_storyboard
from backend.app.research.digest import DataPoint, Digest, SourceRef
from backend.app.video.chart_shots import (
    MAX_CHARTS,
    ChartShotError,
    plan_charts,
    visual_mix,
    write_chart,
)
from backend.app.video.outline import MAX_CARDS, build_outline, unit_numbers


def _point(text: str, source_id: str = "S01") -> DataPoint:
    return DataPoint(source_id=source_id, text=text, numbers=())


def _digest(points: list[DataPoint], sources: int = 2) -> Digest:
    refs = tuple(
        SourceRef(
            source_id=f"S{index + 1:02d}",
            title=f"资料 {index + 1}",
            url=f"https://example.com/{index + 1}",
            site_name="example",
            author="",
            published_at="2025-01-01",
            sha256="a" * 64,
            snapshot_path="",
            char_count=1000,
            query="净息差",
        )
        for index in range(sources)
    )
    return Digest(
        title="商业银行净息差为什么一路往下",
        objective="搞清商业银行的盈利结构",
        sources=refs,
        data_points=tuple(points),
    )


SERIES = [
    _point("2020年商业银行净息差为2.10%。", "S01"),
    _point("2022年商业银行净息差降至1.91%。", "S02"),
    _point("2024年商业银行净息差为1.52%。", "S01"),
]


# --------------------------------------------------------------------------- 抠量


def test_unit_numbers_reads_metric_and_period() -> None:
    numbers = unit_numbers("2024年第四季度，商业银行净息差降至1.52%。")
    assert [item.text for item in numbers] == ["1.52%"]
    item = numbers[0]
    assert item.value == pytest.approx(1.52)
    assert item.unit == "%"
    # 关键词是"它是什么量"，不能把"2024年"带进来（带进来就把不同期的组拆散了）
    assert "年" not in item.keyword
    assert "净息差" in item.keyword
    assert item.label == "2024年第四季度"


def test_unit_numbers_skips_calendar_dates_and_bare_digits() -> None:
    assert unit_numbers("11月2日，多家电商平台的尾款支付活动开始。") == ()
    assert unit_numbers("其中 42 家上市银行里，只有一家没有披露。") == ()


def test_unit_numbers_keeps_the_unit_it_found() -> None:
    numbers = unit_numbers("2023年组件产能920GW，产量518.1GW。")
    assert [(item.value, item.unit) for item in numbers] == [(920.0, "GW"), (518.1, "GW")]


# --------------------------------------------------------------------------- 分组


def test_time_series_becomes_one_chart() -> None:
    plans, consumed = plan_charts(SERIES)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.unit == "%"
    assert plan.labels == ("2020年", "2022年", "2024年")
    assert plan.values == pytest.approx((2.10, 1.91, 1.52))
    assert plan.display == ("2.10%", "1.91%", "1.52%")
    assert plan.point_indexes == (0, 1, 2)
    assert consumed == {0, 1, 2}
    assert plan.asset_id.startswith("CHART_")


def test_different_metrics_are_never_drawn_on_one_axis() -> None:
    plans, consumed = plan_charts(
        [
            _point("2023年组件产能920GW。", "S01"),
            _point("2025年名义产能突破1000GW。", "S02"),
        ]
    )
    assert plans == []
    assert consumed == set()


def test_change_words_are_not_metrics() -> None:
    # "营业收入下降36%"与"组件收入下降33%"是两件事，不能画成一条叫"下降"的线
    plans, _ = plan_charts(
        [
            _point("2024年营业收入下降了36.23%。", "S01"),
            _point("组件业务收入下降33.13%。", "S02"),
        ]
    )
    assert plans == []


def test_period_and_source_labels_are_not_mixed() -> None:
    plans, _ = plan_charts(
        [
            _point("2024年组件产能920GW。", "S01"),
            _point("组件产能1000GW。", "S02"),
        ]
    )
    assert plans == []


def test_a_lone_number_never_becomes_a_chart() -> None:
    plans, consumed = plan_charts([_point("2024年商业银行净息差为1.52%。", "S01")])
    assert plans == []
    assert consumed == set()


def test_charts_are_capped() -> None:
    points = []
    for index, keyword in enumerate(["净息差", "产能", "库存"]):
        points.append(_point(f"2020年{keyword}为{index + 1}.10%。", "S01"))
        points.append(_point(f"2024年{keyword}为{index + 2}.20%。", "S02"))
    plans, _ = plan_charts(points)
    assert len(plans) <= MAX_CHARTS


def test_two_groups_never_share_a_data_point() -> None:
    plans, consumed = plan_charts(
        [
            _point("2020年净息差为2.10%，产能920GW。", "S01"),
            _point("2024年净息差为1.52%，产能1000GW。", "S02"),
        ]
    )
    # 两个指标都在同一批句子里：各自成组，但同一句只能被一张图用掉一次
    assert len(plans) <= MAX_CHARTS
    indexes = [index for plan in plans for index in plan.point_indexes]
    assert len(indexes) == len(set(indexes))
    assert consumed


# --------------------------------------------------------------------------- 落盘


def test_chart_is_written_and_registered(tmp_path: Path) -> None:
    plans, _ = plan_charts(SERIES)
    plan = write_chart(tmp_path, plans[0], canvas=(540, 960), shot_id="sh02")
    assert plan.path is not None and plan.path.is_file()
    assert plan.path.stat().st_size > 1000
    assert plan.relative_path.startswith("assets/charts/")

    manifest = (tmp_path / "assets_manifest.csv").read_text(encoding="utf-8")
    assert plan.asset_id in manifest
    assert "self-generated" in manifest or "原创" in manifest


def test_write_chart_reports_a_stable_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plans, _ = plan_charts(SERIES)
    monkeypatch.setattr("backend.app.analysis.charts.bar_chart", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(ChartShotError) as error:
        write_chart(tmp_path, plans[0], canvas=(540, 960), shot_id="sh02")
    assert error.value.reason == "chart_failed"


# --------------------------------------------------------------------------- 与分镜连起来


def test_outline_puts_a_chart_where_the_cards_were(tmp_path: Path) -> None:
    result = build_outline(_digest(SERIES), job_root=tmp_path, canvas=(540, 960))
    rows = load_storyboard(result.storyboard_path).shots
    charts = [row for row in rows if row.asset_id.startswith("CHART_")]
    assert len(charts) == 1
    chart = charts[0]
    assert chart.asset_type == "generated_image"
    assert chart.asset_source.startswith("assets/charts/")
    # 图上已经有标题与轴标签，底部不再叠一条重复字幕
    assert chart.subtitle == ""
    # 三个数据点被一张图替代 → 这三句不再各出一张数据卡
    assert [row.asset_id for row in rows if row.asset_type == "composition"] == []


def test_cards_are_capped_so_the_film_is_not_a_slide_deck(tmp_path: Path) -> None:
    points = [
        _point(f"2024年第{index + 1}季度，第{index + 1}项指标为{index + 1}.5%。", f"S{index % 2 + 1:02d}")
        for index in range(6)
    ]
    result = build_outline(_digest(points), job_root=tmp_path, canvas=(540, 960))
    rows = load_storyboard(result.storyboard_path).shots
    cards = [row for row in rows if row.asset_type == "composition"]
    assert len(cards) <= MAX_CARDS < 6


def test_visual_mix_reports_what_is_actually_on_screen(tmp_path: Path) -> None:
    result = build_outline(_digest(SERIES), job_root=tmp_path, canvas=(540, 960))
    rows = [
        {
            "asset_type": row.asset_type,
            "asset_id": row.asset_id,
        }
        for row in load_storyboard(result.storyboard_path).shots
    ]
    mix = visual_mix(rows)
    assert mix["total"] == len(rows)
    assert mix["charts"] == 1
    assert 0.0 <= mix["card_ratio"] <= 1.0
    # 说明文件里要如实写出来，别只留在内存里
    notes = result.notes_path.read_text(encoding="utf-8")
    assert "画面构成" in notes
