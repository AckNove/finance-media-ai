"""规则化分镜草稿的测试。

要盯住的事：只引用原文、来源不足就拒绝、产出的表必须能通过正式校验、
每个数据点都能追到来源编号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.media.storyboard import load_storyboard
from backend.app.research.digest import DataPoint, Digest, SourceRef
from backend.app.video.outline import MAX_CAPTION_CHARS, OutlineError, build_outline


def _digest(points: int = 3, sources: int = 2) -> Digest:
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
    data = tuple(
        DataPoint(
            source_id=f"S{index % sources + 1:02d}",
            text=f"第 {index + 1} 句原文，含 1.{index}% 这样的数字。",
            numbers=(f"1.{index}",),
        )
        for index in range(points)
    )
    return Digest(
        title="商业银行是怎么赚钱的",
        objective="搞清楚商业银行的盈利结构",
        sources=refs,
        data_points=data,
    )


def test_outline_shots_come_from_the_digest(tmp_path: Path) -> None:
    result = build_outline(_digest(), job_root=tmp_path / "job", job_id="job")

    board = load_storyboard(result.storyboard_path)
    assert len(board.shots) == result.shot_count
    assert board.shots[0].subtitle == "商业银行是怎么赚钱的"
    # 中间的每一镜都是底稿原文，一个字不改；屏幕文字改由数据卡承载，
    # 所以底部字幕留空——否则同一句话会在卡片和字幕条上各出现一次。
    originals = [point.text for point in _digest().data_points]
    for shot in board.shots[1:-1]:
        assert shot.narration in originals
        assert shot.asset_type == "composition"
        assert shot.subtitle == ""
    assert board.shots[-1].narration.endswith("不是投资建议。")
    assert result.total_seconds == pytest.approx(
        sum(shot.seconds for shot in result.shots), rel=1e-6
    )


def test_every_data_shot_keeps_its_source_id(tmp_path: Path) -> None:
    result = build_outline(_digest(points=4, sources=3), job_root=tmp_path / "job")

    data_shots = [shot for shot in result.shots if shot.source_id]
    assert len(data_shots) == 4
    assert set(result.source_ids) <= {"S01", "S02", "S03"}
    notes = result.notes_path.read_text(encoding="utf-8")
    assert "纯规则" in notes
    assert "没有调用任何模型" in notes
    for shot in data_shots:
        assert shot.source_id in notes


def test_one_source_is_not_enough(tmp_path: Path) -> None:
    with pytest.raises(OutlineError) as error:
        build_outline(_digest(sources=1), job_root=tmp_path / "job")
    assert error.value.reason == "insufficient_sources"


def test_no_data_points_is_refused_not_padded(tmp_path: Path) -> None:
    with pytest.raises(OutlineError) as error:
        build_outline(_digest(points=0), job_root=tmp_path / "job")
    assert error.value.reason == "no_data_points"


def test_a_single_source_may_not_dominate_the_whole_film(tmp_path: Path) -> None:
    # 6 个点全来自 S01 与 S02 时，同来源连排队也不会超过两条
    result = build_outline(_digest(points=6, sources=2), job_root=tmp_path / "job", max_points=6)
    counts: dict[str, int] = {}
    for shot in result.shots:
        if shot.source_id:
            counts[shot.source_id] = counts.get(shot.source_id, 0) + 1
    assert all(count <= 2 for count in counts.values())


def test_long_sentences_are_truncated_on_screen_but_not_in_the_script(tmp_path: Path) -> None:
    base = _digest()
    long_text = "这是一句很长很长的原文，" * 5
    digest = Digest(
        title=base.title,
        objective=base.objective,
        sources=base.sources,
        data_points=(DataPoint(source_id="S01", text=long_text, numbers=("1.0",)),)
        + base.data_points,
    )
    result = build_outline(digest, job_root=tmp_path / "job")

    board = load_storyboard(result.storyboard_path)
    first_data = board.shots[1]
    # 口播稿一个字都不改
    assert first_data.narration == " ".join(long_text.split())
    # 屏幕上那行说明在标点处断开、长度受限，而且写在数据卡里
    assert first_data.subtitle == ""
    card = json.loads((tmp_path / "job" / first_data.asset_source).read_text(encoding="utf-8"))
    caption = next(node["text"] for node in card["root"]["children"] if node["id"] == "caption")
    assert caption.endswith("，")
    assert len(caption) <= MAX_CAPTION_CHARS
    assert len(caption) < len(first_data.narration)


def test_the_sheet_is_validated_before_it_is_returned(tmp_path: Path) -> None:
    result = build_outline(_digest(), job_root=tmp_path / "job")
    # 行数、帧数、时长三者自洽，且能被正式解析器读完
    board = load_storyboard(result.storyboard_path)
    assert board.total_frames == result.frames
    assert board.duration == pytest.approx(result.total_seconds, rel=1e-6)
    assert result.storyboard_path.relative_to(tmp_path / "job").as_posix() == (
        "storyboard/storyboard.csv"
    )
