"""语音合成与字幕排版。

分两层：

* **离线单测**（默认全部执行）：契约、估算时间、字幕断句、SRT/ASS 格式、
  provider 降级顺序——都不联网、不花钱。
* **真实网络集成**（``FM_EDGE_TTS_NETWORK=1`` 时才跑）：真的调 edge-tts 合成一句
  中文，断言拿到词级时间、MP3 可被 ffprobe 读出时长。默认跳过，避免 CI 依赖外网。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

import pytest

from backend.app.capabilities import tts
from backend.app.capabilities.tts.provider import (
    TtsError,
    TtsRequest,
    TtsResult,
    TtsVoice,
    WordTiming,
    estimate_word_timings,
)
from backend.app.capabilities.tts.subtitle import (
    Cue,
    cues_to_ass,
    cues_to_srt,
    group_words_into_cues,
    shift_cues,
    total_duration,
)

NETWORK_ENABLED = os.environ.get("FM_EDGE_TTS_NETWORK") == "1"


def _words(*pairs: tuple[str, float, float]) -> tuple[WordTiming, ...]:
    return tuple(WordTiming(text=text, start=start, end=end) for text, start, end in pairs)


# ---------------------------------------------------------------- 契约与估算


def test_empty_text_is_rejected_at_request_level() -> None:
    with pytest.raises(ValueError):
        TtsRequest(text="   ")


def test_estimated_timings_cover_the_whole_duration_without_overlap() -> None:
    words = estimate_word_timings("净息差是银行赚钱的核心", 3.0)

    assert words[0].start == pytest.approx(0.0, abs=1e-6)
    assert words[-1].end == pytest.approx(3.0, abs=1e-6)
    for previous, current in zip(words, words[1:]):
        assert current.start == pytest.approx(previous.end, abs=1e-6)


def test_estimated_timings_split_chinese_per_character_and_keep_latin_words() -> None:
    words = estimate_word_timings("ROE 是核心", 2.0)
    texts = [word.text for word in words]

    assert "ROE" in texts
    assert "核" in texts and "心" in texts


def test_estimated_timings_of_empty_duration_are_empty() -> None:
    assert estimate_word_timings("abc", 0.0) == ()


# ---------------------------------------------------------------- 字幕断句


def test_cues_break_on_sentence_punctuation() -> None:
    words = _words(("净", 0.0, 0.2), ("息", 0.2, 0.4), ("差", 0.4, 0.6), ("。", 0.6, 0.65), ("其", 0.7, 0.9))

    cues = group_words_into_cues(words, max_chars=40)

    assert len(cues) == 2
    assert cues[0].text.endswith("。")
    assert cues[1].text == "其"


def test_cues_break_when_the_speaker_pauses() -> None:
    words = _words(("甲", 0.0, 0.3), ("乙", 0.3, 0.6), ("丙", 1.6, 1.9))

    cues = group_words_into_cues(words, max_gap=0.6, max_chars=40)

    assert len(cues) == 2
    assert cues[0].text == "甲乙"
    assert cues[1].text == "丙"


def test_cues_break_when_the_line_gets_too_long() -> None:
    words = _words(*[(f"{i}", i * 0.2, i * 0.2 + 0.2) for i in range(12)])

    cues = group_words_into_cues(words, max_chars=5, max_duration=60)

    assert all(len(cue.text) <= 5 for cue in cues)
    assert "".join(cue.text for cue in cues) == "".join(word.text for word in words)


def test_cues_break_when_a_line_runs_too_long_in_time() -> None:
    words = _words(*[(f"{i}", i * 1.0, i * 1.0 + 0.95) for i in range(6)])

    cues = group_words_into_cues(words, max_chars=99, max_duration=2.0)

    assert len(cues) >= 3
    assert all(cue.duration <= 2.0 + 1e-6 for cue in cues)


def test_empty_words_produce_no_cues() -> None:
    assert group_words_into_cues(()) == ()


# ---------------------------------------------------------------- 输出格式


def test_srt_uses_comma_milliseconds_and_sequential_numbers() -> None:
    cues = (Cue(start=0.0, end=1.234, text="第一句"), Cue(start=2.5, end=3.0, text="第二句"))

    srt = cues_to_srt(cues)

    assert "1\n00:00:00,000 --> 00:00:01,234\n第一句" in srt
    assert "2\n00:00:02,500 --> 00:00:03,000\n第二句" in srt


def test_ass_declares_canvas_font_and_safe_margins() -> None:
    ass = cues_to_ass((Cue(start=0.0, end=1.0, text="净息差"),), width=1080, height=1920, font="AKSC")

    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Style: AK,AKSC,46" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,AK,,0,0,0,,净息差" in ass


def test_ass_escapes_newlines_into_dialogue_breaks() -> None:
    ass = cues_to_ass((Cue(start=0.0, end=1.0, text="上\n下"),))

    assert "上\\N下" in ass


def test_shift_and_total_duration_are_consistent() -> None:
    cues = (Cue(start=0.0, end=1.0, text="a"), Cue(start=1.0, end=2.5, text="b"))

    moved = shift_cues(cues, 4.0)

    assert moved[0].start == pytest.approx(4.0)
    assert moved[-1].end == pytest.approx(6.5)
    assert total_duration(moved) == pytest.approx(6.5)


# ---------------------------------------------------------------- provider 装配


class _FakeProvider:
    name = "fake"

    def __init__(self, *, works: bool, estimated: bool = False, available: bool = True) -> None:
        self.works = works
        self.estimated = estimated
        self.available_flag = available
        self.calls = 0

    def available(self) -> bool:
        return self.available_flag

    def list_voices(self, *, locale_prefix: str = "zh") -> Sequence[TtsVoice]:
        return (TtsVoice(name="fake-voice", locale="zh-CN", provider=self.name),)

    def synthesize(self, request: TtsRequest, out_path: Path) -> TtsResult:
        self.calls += 1
        if not self.works:
            raise TtsError("synthesis_failed", "fake failure")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"ID3fake")
        return TtsResult(
            path=str(out_path),
            provider=self.name,
            voice=request.voice,
            text=request.text,
            duration_seconds=2.0,
            byte_size=7,
            sha256="0" * 64,
            words=estimate_word_timings(request.text, 2.0),
            estimated=self.estimated,
            meta={"timing_source": "estimated" if self.estimated else "measured"},
        )


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(TtsError) as excinfo:
        tts.get_tts_provider("nope")

    assert excinfo.value.reason == "unknown_provider"


def test_available_providers_reports_both_backends() -> None:
    status = tts.available_providers()

    assert set(status) == {"edge", "sapi"}
    assert all(isinstance(value, bool) for value in status.values())


def test_synthesize_falls_back_to_the_next_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = _FakeProvider(works=False)
    working = _FakeProvider(works=True)
    monkeypatch.setattr(tts, "get_tts_provider", lambda name: broken if name == "edge" else working)

    result = tts.synthesize("净息差", tmp_path / "out.mp3")

    assert result.provider == "fake"
    assert broken.calls == 1 and working.calls == 1


def test_unavailable_provider_is_skipped_without_being_called(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    offline = _FakeProvider(works=True, available=False)
    working = _FakeProvider(works=True)
    monkeypatch.setattr(tts, "get_tts_provider", lambda name: offline if name == "edge" else working)

    result = tts.synthesize("净息差", tmp_path / "out.mp3")

    assert result.provider == "fake"
    assert offline.calls == 0 and working.calls == 1


def test_synthesize_raises_when_every_provider_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tts, "get_tts_provider", lambda name: _FakeProvider(works=False))

    with pytest.raises(TtsError) as excinfo:
        tts.synthesize("净息差", tmp_path / "out.mp3")

    assert excinfo.value.reason == "no_provider"
    assert "edge: synthesis_failed" in str(excinfo.value)
    assert "sapi: synthesis_failed" in str(excinfo.value)


def test_captions_fall_back_to_estimated_words_when_provider_gives_none() -> None:
    result = TtsResult(
        path="x.wav",
        provider="sapi",
        voice="Microsoft Huihui Desktop",
        text="净息差是核心",
        duration_seconds=2.0,
        byte_size=10,
        sha256="0" * 64,
        estimated=True,
    )

    cues = tts.captions(result)

    assert cues
    assert total_duration(cues) == pytest.approx(2.0, abs=1e-6)


# ---------------------------------------------------------------- 真实网络（可选）


@pytest.mark.skipif(not NETWORK_ENABLED, reason="设置 FM_EDGE_TTS_NETWORK=1 才跑真实 edge-tts 合成")
def test_edge_tts_really_synthesizes_chinese_with_word_boundaries(tmp_path: Path) -> None:
    result = tts.synthesize(
        "净息差是银行赚钱的核心，这决定了估值的天花板。",
        tmp_path / "real.mp3",
        provider="edge",
    )

    assert result.provider == "edge"
    assert result.path.endswith(".mp3")
    assert Path(result.path).exists()
    assert result.byte_size > 5_000
    assert result.duration_seconds > 2.0
    assert len(result.words) >= 5
    assert result.estimated is False
    assert result.meta["boundary"] == "WordBoundary"
    first = result.words[0]
    assert 0.0 <= first.start < first.end <= result.duration_seconds + 0.5

    cues = tts.captions(result)
    assert cues
    assert cues[0].text
