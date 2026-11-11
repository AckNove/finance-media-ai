"""Storyboard → engine timeline translation (V2.2 Phase 2)."""

from __future__ import annotations

import pytest

from backend.app.media.storyboard import REQUIRED_COLUMNS, Shot, parse_storyboard
from backend.app.media.timeline import (
    SUPPORTED_CLIP_KEYS,
    SUPPORTED_SHOT_KEYS,
    TimelineError,
    audio_mix,
    build_timeline,
    camera_block,
    subtitle_events,
    transition_block,
)

COLUMNS = list(REQUIRED_COLUMNS) + ["fps", "canvas_width", "canvas_height", "notes"]

CLIP = {"frames_dir": "plates/aa7ca4f30e03", "pattern": "%05d.png", "n": 120, "fps": 30, "w": 1080, "h": 1920}


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "shot_id": "sh01",
        "start_time": "0.0",
        "end_time": "4.0",
        "narrative_purpose": "开场",
        "narration": "旁白",
        "visual_description": "画面",
        "asset_id": "S01",
        "asset_type": "real_image",
        "asset_source": "NASA SVS",
        "license": "NASA public-domain media",
        "animation": "push in",
        "camera_motion": "push",
        "transition": "none",
        "subtitle": "第一句",
        "sound_effect": "whoosh",
        "safety_zone": "bottom",
        "fps": "30",
        "canvas_width": "1080",
        "canvas_height": "1920",
        "notes": "",
    }
    row.update(overrides)
    return row


def _storyboard(*rows: dict[str, str]):
    return parse_storyboard(list(rows) or [_row()], columns=COLUMNS)


def test_timeline_covers_the_canvas_and_carries_the_clip() -> None:
    storyboard = _storyboard(_row(), _row(shot_id="sh02", start_time="4.0", end_time="8.0"))
    timeline = build_timeline(storyboard, {"S01": CLIP})
    assert timeline["canvas"] == {"w": 1080, "h": 1920, "fps": 30, "frames": 240, "bg": "#0b1220"}
    assert [shot["f0"] for shot in timeline["shots"]] == [0, 120]
    assert timeline["shots"][-1]["f1"] == 240
    assert timeline["shots"][0]["clip"] == "S01"
    assert timeline["shots"][0]["src"] == 0
    assert timeline["shots"][0]["speed"] == 1
    for shot in timeline["shots"]:
        assert set(shot) <= SUPPORTED_SHOT_KEYS
    for clip in timeline["clips"].values():
        assert set(clip) <= SUPPORTED_CLIP_KEYS


def test_missing_clip_names_the_asset() -> None:
    with pytest.raises(TimelineError) as error:
        build_timeline(_storyboard(), {})
    assert error.value.code == "missing_clip"
    assert "S01" in str(error.value)


def test_unsupported_clip_field_is_rejected() -> None:
    with pytest.raises(TimelineError) as error:
        build_timeline(_storyboard(), {"S01": {**CLIP, "bogus": 1}})
    assert error.value.code == "unsupported_field"


def test_still_plate_declares_its_hold_as_a_shot_loop() -> None:
    still = {**CLIP, "n": 1}
    timeline = build_timeline(_storyboard(), {"S01": still})
    assert timeline["shots"][0]["loop"] == "loop"
    assert timeline["clips"]["S01"] == still
    moving = build_timeline(_storyboard(), {"S01": CLIP})["shots"][0]
    assert "loop" not in moving


def test_solid_shot_carries_colour_instead_of_a_clip() -> None:
    storyboard = _storyboard(_row(asset_type="solid", asset_source="#101826", camera_motion="static",
                                 transition="none", subtitle=""))
    timeline = build_timeline(storyboard, {})
    shot = timeline["shots"][0]
    assert shot["bg"] == "#101826"
    assert "clip" not in shot
    assert "cam" not in shot
    assert timeline["clips"] == {}


@pytest.mark.parametrize(
    ("motion", "expected"),
    [
        ("push", {"push": {"v": 0.03}, "fit": "cover", "cover": True}),
        ("pull", {"push": {"v": -0.03}, "fit": "cover", "cover": True}),
        ("pan_left", {"pan": {"dx": 0.05}, "fit": "cover", "cover": True}),
        ("pan_up", {"pan": {"dy": 0.05}, "fit": "cover", "cover": True}),
        ("punch", {"punch": {"v": 0.06, "d": 0.25}, "fit": "cover", "cover": True}),
        ("handheld", {"handheld": {"amp": 5, "hz": 0.6}, "fit": "cover", "cover": True}),
        ("shake", {"shake": {"amp": [12, 9], "d": 0.25}, "fit": "cover", "cover": True}),
        ("roll", {"roll": {"v": 0.03}, "fit": "cover", "cover": True}),
        ("static", {"fit": "cover", "cover": True}),
    ],
)
def test_camera_words_become_engine_blocks(motion: str, expected: dict) -> None:
    shot = Shot(shot_id="sh01", start_time=0.0, end_time=4.0, narrative_purpose="", narration="",
                visual_description="", asset_id="S01", asset_type="real_image", asset_source="",
                license="", animation="", camera_motion=motion, transition="none", subtitle="",
                sound_effect="", safety_zone="bottom", notes="")
    assert camera_block(shot) == expected


def test_transition_sits_on_the_incoming_shot_with_the_right_direction() -> None:
    base = dict(shot_id="sh02", start_time=0.0, end_time=4.0, narrative_purpose="", narration="",
                visual_description="", asset_id="S01", asset_type="real_image", asset_source="",
                license="", animation="", camera_motion="static", subtitle="", sound_effect="",
                safety_zone="bottom", notes="")
    assert transition_block(Shot(transition="none", **base), 0.4) is None
    assert transition_block(Shot(transition="dissolve", **base), 0.4) == {"type": "dissolve", "d": 0.4}
    assert transition_block(Shot(transition="push", **base), 0.3) == {"type": "push", "d": 0.3, "dir": "left"}
    assert transition_block(Shot(transition="wipe", **base), 0.3) == {"type": "wipe", "d": 0.3, "dir": "ltr"}
    assert transition_block(Shot(transition="slide", **base), 0.3) == {"type": "slide", "d": 0.3, "dir": "up"}
    assert transition_block(Shot(transition="spin", **base), 0.3) == {"type": "spin", "d": 0.3}


def test_subtitles_follow_the_safety_zone() -> None:
    storyboard = _storyboard(
        _row(safety_zone="top"),
        _row(shot_id="sh02", start_time="4.0", end_time="8.0", safety_zone="center", subtitle="第二句"),
        _row(shot_id="sh03", start_time="8.0", end_time="10.0", safety_zone="bottom", subtitle=""),
    )
    events = subtitle_events(storyboard, family="AKSC", size=54)
    assert len(events) == 2
    assert events[0]["pos"] == [0.5, 0.16]
    assert events[1]["pos"] == [0.5, 0.5]
    assert events[1]["f0"] == 120 and events[1]["f1"] == 240
    assert all(event["type"] == "caption" for event in events)


def test_audio_mix_builds_a_mix_block() -> None:
    block = audio_mix([{"src": "audio/score_bed.m4a", "at": 0, "volume": 0.8}], gain=0.9)
    assert block == {"mode": "mix", "gain": 0.9,
                     "tracks": [{"src": "audio/score_bed.m4a", "at": 0, "volume": 0.8}]}


def test_audio_mix_rejects_empty_and_broken_tracks() -> None:
    with pytest.raises(TimelineError) as error:
        audio_mix([])
    assert error.value.code == "empty_audio"
    with pytest.raises(TimelineError) as error:
        audio_mix([{"at": 0}])
    assert error.value.code == "bad_track"
    with pytest.raises(TimelineError) as error:
        audio_mix([{"src": "a.m4a", "bogus": 1}])
    assert error.value.code == "unsupported_field"


def test_audio_and_fonts_are_passed_through_when_supplied() -> None:
    timeline = build_timeline(
        _storyboard(),
        {"S01": CLIP},
        audio=audio_mix([{"src": "audio/score_bed.m4a"}]),
        fonts=[{"family": "AKSC", "url": "fonts/NotoSansSC-VF.ttf"}],
        text=subtitle_events(_storyboard(), family="AKSC", size=54),
    )
    assert timeline["audio"]["mode"] == "mix"
    assert timeline["fonts"][0]["url"].endswith("NotoSansSC-VF.ttf")
    assert timeline["text"][0]["type"] == "caption"
