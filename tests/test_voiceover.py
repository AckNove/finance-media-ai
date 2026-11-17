"""真人录音接入的测试。

红线：不联网、不花钱、不依赖真实录音文件——用 Python 标准库现造 wav（静音），
再用 ffprobe 量它的真实时长，验证"时长一律实测"这条规矩真的成立。
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from backend.app.video import voiceover as vo

FFMPEG_DIR = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / "bin"


def _silence(path: Path, seconds: float, *, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * frames)
    return path


def _has_ffprobe() -> bool:
    return (FFMPEG_DIR / "ffprobe.exe").is_file()


pytestmark = pytest.mark.skipif(not _has_ffprobe(), reason="需要项目自带的 ffprobe 才能量时长")


@pytest.fixture()
def job(tmp_path: Path) -> Path:
    root = tmp_path / "job"
    (root / "audio" / "narration").mkdir(parents=True)
    (root / "script").mkdir(parents=True)
    return root


def test_probe_reads_the_real_duration(tmp_path: Path) -> None:
    clip = _silence(tmp_path / "a.wav", 1.5)
    assert vo.probe_seconds(clip) == pytest.approx(1.5, abs=0.05)


def test_per_line_copies_files_and_uses_measured_durations(job: Path, tmp_path: Path) -> None:
    source = tmp_path / "rec"
    _silence(source / "01.wav", 1.0)
    _silence(source / "02.wav", 2.0)

    result = vo.import_per_line(job, source, ["第一句话。", "第二句话。"])
    assert result.alignment == "per_line"
    assert [line.seconds for line in result.lines] == pytest.approx([1.0, 2.0], abs=0.05)
    assert result.lines[0].start == 0.0
    assert result.lines[1].start == pytest.approx(1.0, abs=0.05)
    assert result.total_seconds == pytest.approx(3.0, abs=0.1)
    for line in result.lines:
        assert Path(line.path).is_file(), "录音必须被复制进任务目录，不能只记外部路径"
        assert job in Path(line.path).parents


def test_per_line_rejects_count_mismatch(job: Path, tmp_path: Path) -> None:
    source = tmp_path / "rec"
    _silence(source / "01.wav", 1.0)
    with pytest.raises(vo.VoiceoverError) as error:
        vo.import_per_line(job, source, ["一句", "两句"])
    assert error.value.reason == "count_mismatch"


def test_per_line_rejects_empty_directory(job: Path, tmp_path: Path) -> None:
    source = tmp_path / "empty"
    source.mkdir()
    with pytest.raises(vo.VoiceoverError) as error:
        vo.import_per_line(job, source, ["一句"])
    assert error.value.reason == "no_audio"


def test_single_recording_is_marked_estimated(job: Path, tmp_path: Path) -> None:
    clip = _silence(tmp_path / "full.wav", 6.0)
    result = vo.import_single(job, clip, ["短句。", "这是一个明显更长的句子，占的时间应该更多一些。", "又一句。"])

    assert result.alignment == "estimated", "整段录音只能估算，必须如实标注"
    assert "估算" in result.note
    # 长句拿到的时长必须比短句多
    assert result.lines[1].seconds > result.lines[0].seconds
    # 估算也要保住总长：不超过录音总长
    assert result.total_seconds <= 6.0 + 1e-6


def test_single_recording_keeps_gaps_between_lines(job: Path, tmp_path: Path) -> None:
    clip = _silence(tmp_path / "full.wav", 4.0)
    result = vo.import_single(job, clip, ["甲。", "乙。"], gap=0.5)
    assert result.lines[1].start >= result.lines[0].end + 0.5 - 0.01


def test_write_and_load_round_trip(job: Path, tmp_path: Path) -> None:
    source = tmp_path / "rec"
    _silence(source / "01.wav", 1.2)
    result = vo.import_per_line(job, source, ["只有一句。"])
    target = vo.write_voiceover(result)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["alignment"] == "per_line"
    assert payload["total_seconds"] == pytest.approx(1.2, abs=0.05)

    loaded = vo.load_voiceover(job)
    assert loaded is not None
    assert loaded.lines[0].text == "只有一句。"
    assert loaded.alignment == "per_line"


def test_load_returns_none_without_recording(job: Path) -> None:
    assert vo.load_voiceover(job) is None


def test_srt_uses_measured_times(job: Path, tmp_path: Path) -> None:
    source = tmp_path / "rec"
    _silence(source / "01.wav", 1.0)
    _silence(source / "02.wav", 1.0)
    result = vo.import_per_line(job, source, ["甲。", "乙。"])

    srt = vo.to_srt(result)
    assert "00:00:00,000 --> 00:00:01,000" in srt
    assert "00:00:01,000 --> 00:00:02,000" in srt
    assert "甲。" in srt and "乙。" in srt


def test_missing_audio_reports_reason(job: Path, tmp_path: Path) -> None:
    with pytest.raises(vo.VoiceoverError) as error:
        vo.import_single(job, tmp_path / "nope.wav", ["一句"])
    assert error.value.reason == "missing_audio"


def test_bad_suffix_reports_reason(job: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "a.txt"
    bogus.write_text("x", encoding="utf-8")
    with pytest.raises(vo.VoiceoverError) as error:
        vo.import_single(job, bogus, ["一句"])
    assert error.value.reason == "bad_suffix"


def test_empty_lines_reports_reason(job: Path, tmp_path: Path) -> None:
    clip = _silence(tmp_path / "a.wav", 1.0)
    with pytest.raises(vo.VoiceoverError) as error:
        vo.import_single(job, clip, ["   ", ""])
    assert error.value.reason == "empty_lines"
