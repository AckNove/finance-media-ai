"""Storyboard CSV: the sheet the whole pipeline reads (V2.2 Phase 2)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend.app.media.storyboard import (
    REQUIRED_COLUMNS,
    Storyboard,
    StoryboardError,
    load_storyboard,
    parse_storyboard,
    write_storyboard,
)

HEADER = list(REQUIRED_COLUMNS) + ["fps", "canvas_width", "canvas_height", "notes"]


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "shot_id": "sh01",
        "start_time": "0.0",
        "end_time": "4.0",
        "narrative_purpose": "开场",
        "narration": "这是一句旁白。",
        "visual_description": "地球静帧慢推",
        "asset_id": "S01",
        "asset_type": "real_image",
        "asset_source": "NASA SVS",
        "license": "NASA public-domain media",
        "animation": "push in",
        "camera_motion": "push",
        "transition": "none",
        "subtitle": "字幕一",
        "sound_effect": "whoosh",
        "safety_zone": "bottom",
        "fps": "30",
        "canvas_width": "1080",
        "canvas_height": "1920",
        "notes": "",
    }
    row.update(overrides)
    return row


def _parse(rows: list[dict[str, str]], **kwargs) -> Storyboard:
    return parse_storyboard(rows, columns=HEADER, **kwargs)


def test_required_columns_are_the_documented_sixteen() -> None:
    assert len(REQUIRED_COLUMNS) == 16
    assert REQUIRED_COLUMNS[0] == "shot_id"
    assert "asset_type" in REQUIRED_COLUMNS
    assert "safety_zone" in REQUIRED_COLUMNS


def test_two_contiguous_shots_parse_with_totals() -> None:
    rows = [
        _row(),
        _row(shot_id="sh02", start_time="4.0", end_time="9.5", asset_type="real_video",
             camera_motion="pan_left", transition="dissolve", safety_zone="center"),
    ]
    storyboard = _parse(rows)
    assert storyboard.duration == 9.5
    assert storyboard.total_frames == 285
    assert [shot.shot_id for shot in storyboard.shots] == ["sh01", "sh02"]
    assert storyboard.shots[1].frame_range(30) == (120, 285)
    assert storyboard.asset_ids() == ("S01",)


def test_counts_report_the_acceptance_facts() -> None:
    rows = [
        _row(),
        _row(shot_id="sh02", start_time="4.0", end_time="8.0", asset_type="composition",
             camera_motion="punch", transition="glitch"),
        _row(shot_id="sh03", start_time="8.0", end_time="12.0", asset_type="real_video",
             camera_motion="push", transition="glitch", subtitle=""),
    ]
    counts = _parse(rows).counts()
    assert counts["shots"] == 3
    assert counts["real_image"] == 1
    assert counts["real_video"] == 1
    assert counts["composition"] == 1
    assert counts["subtitles"] == 2
    assert counts["transitions"] == 1
    assert counts["camera_motions"] == 2


def test_sound_effect_events_are_derived_from_the_rows() -> None:
    """A count of "sound effects" must come from the sheet, not from a list in code."""
    rows = [
        _row(sound_effect="whoosh_low"),
        _row(shot_id="sh02", start_time="4.0", end_time="8.0", sound_effect="none"),
        _row(shot_id="sh03", start_time="8.0", end_time="12.0", sound_effect=""),
        _row(shot_id="sh04", start_time="12.0", end_time="16.0", sound_effect="soft_hit"),
    ]
    storyboard = _parse(rows)
    events = storyboard.sound_effect_events()
    assert [(event.shot_id, event.name, event.at) for event in events] == [
        ("sh01", "whoosh_low", 0.0),
        ("sh04", "soft_hit", 12.0),
    ]
    assert storyboard.sound_effect_names() == ("whoosh_low", "soft_hit")
    counts = storyboard.counts()
    assert counts["sound_effects"] == 2
    assert counts["sound_effect_names"] == 2


def test_a_shot_can_explicitly_declare_no_sound_effect() -> None:
    """`none` is a statement ("this shot has no cue"), not a cue named "none"."""
    storyboard = _parse([_row(sound_effect="none")])
    assert storyboard.sound_effect_events() == ()
    assert storyboard.counts()["sound_effects"] == 0


def test_missing_column_is_rejected() -> None:
    columns = [column for column in HEADER if column != "license"]
    with pytest.raises(StoryboardError) as error:
        parse_storyboard([], columns=columns)
    assert error.value.code == "missing_columns"


def test_unknown_column_is_rejected() -> None:
    with pytest.raises(StoryboardError) as error:
        parse_storyboard([], columns=HEADER + ["bogus"])
    assert error.value.code == "unknown_columns"


def test_empty_sheet_is_rejected() -> None:
    with pytest.raises(StoryboardError) as error:
        parse_storyboard([], columns=HEADER)
    assert error.value.code == "empty"


def test_duplicate_shot_id_is_rejected() -> None:
    rows = [_row(), _row()]
    with pytest.raises(StoryboardError) as error:
        _parse(rows)
    assert error.value.code == "duplicate_shot"


def test_gap_between_shots_is_rejected() -> None:
    rows = [_row(), _row(shot_id="sh02", start_time="5.0", end_time="8.0")]
    with pytest.raises(StoryboardError) as error:
        _parse(rows)
    assert error.value.code == "non_contiguous"


def test_backwards_time_is_rejected() -> None:
    with pytest.raises(StoryboardError) as error:
        _parse([_row(end_time="0.0")])
    assert error.value.code == "bad_time"


def test_unknown_asset_type_and_camera_are_rejected() -> None:
    with pytest.raises(StoryboardError) as error:
        _parse([_row(asset_type="stock_footage")])
    assert error.value.code == "unknown_vocabulary"
    with pytest.raises(StoryboardError) as error:
        _parse([_row(camera_motion="dolly_zoom")])
    assert error.value.code == "unknown_vocabulary"


def test_unknown_safety_zone_is_rejected() -> None:
    with pytest.raises(StoryboardError) as error:
        _parse([_row(safety_zone="none")])
    assert error.value.code == "unknown_vocabulary"


def test_inconsistent_canvas_across_rows_is_rejected() -> None:
    rows = [_row(), _row(shot_id="sh02", start_time="4.0", end_time="8.0", canvas_width="720")]
    with pytest.raises(StoryboardError) as error:
        _parse(rows)
    assert error.value.code == "inconsistent_column"


def test_round_trip_through_disk_keeps_chinese_text(tmp_path: Path) -> None:
    target = tmp_path / "storyboard.csv"
    write_storyboard(target, [_row(), _row(shot_id="sh02", start_time="4.0", end_time="8.0")])
    storyboard = load_storyboard(target)
    assert storyboard.shots[0].narration == "这是一句旁白。"
    assert storyboard.shots[0].subtitle == "字幕一"
    with target.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(REQUIRED_COLUMNS) + ["fps", "canvas_width", "canvas_height", "notes"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(StoryboardError) as error:
        load_storyboard(tmp_path / "nope.csv")
    assert error.value.code == "missing_file"
