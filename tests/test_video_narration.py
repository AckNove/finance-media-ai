"""旁白合成与时间轴音轨的测试。

全部用假的合成器，不联网、不产生费用；真实的 edge-tts 链路由
``FM_EDGE_TTS_NETWORK=1`` 的用例单独覆盖（见 ``tests/test_tts.py``）。

这里要盯住四件事：
1. 只读 ``narration`` 列，不把 ``subtitle`` 当旁白；
2. 音频按镜头起始时间落位，**不拉伸镜头**，超长要如实记数；
3. 一个镜头失败不影响其他镜头，失败原因要写出来；
4. TTS 全不可用时**不伪造配音**，音轨为空且给出原因。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.capabilities.tts.provider import TtsError, TtsResult, WordTiming
from backend.app.media.storyboard import load_storyboard
from backend.app.video.narration import narration_notes, synthesize_narration

STORYBOARD = """shot_id,start_time,end_time,narrative_purpose,narration,visual_description,asset_id,asset_type,asset_source,license,animation,camera_motion,transition,subtitle,sound_effect,safety_zone,fps,canvas_width,canvas_height,notes
sh01,0,4,开场口播,商业银行赚钱主要靠息差。,开场镜头,S01,real_image,apollo.jpg,NASA-PD,fade,static,,开场字幕,none,bottom,30,1080,1920,
sh02,4,8,机制解释,息差就是贷款利率减去存款利率。,机制示意,S02,real_image,marble.jpg,NASA-PD,grow,push,dissolve,机制字幕,none,bottom,30,1080,1920,
sh03,8,12,结尾,这条片子只是引擎自检。,结尾卡,S03,real_image,earth.jpg,NASA-PD,fade,static,,,none,bottom,30,1080,1920,
"""


@pytest.fixture()
def job(tmp_path: Path) -> Path:
    root = tmp_path / "job"
    (root / "storyboard").mkdir(parents=True)
    (root / "storyboard" / "storyboard.csv").write_text(STORYBOARD, encoding="utf-8")
    return root


def _storyboard(job: Path):
    return load_storyboard(job / "storyboard" / "storyboard.csv")


def _fake_synth(durations: dict[str, float] | None = None, *, fail: set[str] | None = None):
    durations = durations or {}
    failing = fail or set()

    def synthesize(text: str, out_path: Path, **kwargs):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if text in failing:
            raise TtsError("not_installed", "假的失败")
        out_path.write_bytes(b"ID3" + text.encode("utf-8") * 4)
        seconds = durations.get(text, 2.0)
        words = tuple(
            WordTiming(text=char, start=i * 0.2, end=i * 0.2 + 0.18)
            for i, char in enumerate(text[:8])
        )
        return TtsResult(
            path=out_path,
            provider="fake",
            voice="zh-CN-Test",
            text=text,
            duration_seconds=seconds,
            byte_size=out_path.stat().st_size,
            sha256="0" * 64,
            words=words,
            sample_rate=24000,
            estimated=False,
            meta={},
        )

    return synthesize


def test_only_the_narration_column_is_spoken(job: Path) -> None:
    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=_fake_synth())

    spoken = [clip.text for clip in result.clips]
    assert spoken == [
        "商业银行赚钱主要靠息差。",
        "息差就是贷款利率减去存款利率。",
        "这条片子只是引擎自检。",
    ]
    assert all("开场字幕" not in text and "机制字幕" not in text for text in spoken)


def test_each_clip_lands_at_its_shot_start_and_is_not_stretched(job: Path) -> None:
    # 第二段说得很长（6 秒），但镜头只有 4 秒
    synth = _fake_synth({"息差就是贷款利率减去存款利率。": 6.0})
    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=synth)

    assert [track["at"] for track in result.tracks] == [0.0, 4.0, 8.0]
    assert [track["src"] for track in result.tracks] == [
        "audio/narration/sh01.mp3",
        "audio/narration/sh02.mp3",
        "audio/narration/sh03.mp3",
    ]
    overrun = result.overruns
    assert len(overrun) == 1
    assert overrun[0].shot_id == "sh02"
    assert overrun[0].overrun_seconds == pytest.approx(2.0)
    # 镜头时长没有被改写
    assert overrun[0].shot_seconds == pytest.approx(4.0)


def test_files_hashes_and_sidecars_are_written(job: Path) -> None:
    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=_fake_synth())

    for clip in result.clips:
        path = job / clip.file
        assert path.is_file()
        assert clip.byte_size == path.stat().st_size
        assert len(clip.sha256) == 64
    assert result.json_path is not None and result.json_path.is_file()
    assert result.srt_path is not None and result.srt_path.is_file()
    srt = result.srt_path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> " in srt
    # 假合成器只给前 8 个字词级时间，SRT 里就应当只出现这 8 个字
    assert "商业银行赚钱主要" in srt.replace("\n", "")
    payload = result.json_path.read_text(encoding="utf-8")
    assert '"clip_count": 3' in payload


def test_one_failing_shot_does_not_stop_the_others(job: Path) -> None:
    synth = _fake_synth(fail={"息差就是贷款利率减去存款利率。"})
    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=synth)

    assert [clip.shot_id for clip in result.clips] == ["sh01", "sh03"]
    assert len(result.problems) == 1
    assert "sh02" in result.problems[0]
    assert "not_installed" in result.problems[0]


def test_no_tts_means_no_fake_voice(job: Path) -> None:
    def broken(text: str, out_path: Path, **kwargs):
        raise TtsError("no_provider", "没有可用的语音提供方：edge: not_installed; sapi: not_installed")

    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=broken)

    assert result.ok is False
    assert result.tracks == ()
    assert result.clips == ()
    assert result.json_path is None, "没有音频就不该写一份看起来有内容的 json"
    notes = narration_notes(result, shot_count=3)
    assert "没有生成旁白音轨" in notes[0]
    assert "成片没有旁白" in notes[0]


def test_shots_without_narration_are_skipped_silently(tmp_path: Path) -> None:
    root = tmp_path / "job"
    (root / "storyboard").mkdir(parents=True)
    (root / "storyboard" / "storyboard.csv").write_text(
        STORYBOARD.replace("这条片子只是引擎自检。", ""), encoding="utf-8"
    )

    result = synthesize_narration(load_storyboard(root / "storyboard" / "storyboard.csv"),
                                  job_root=root, synthesize=_fake_synth())
    assert [clip.shot_id for clip in result.clips] == ["sh01", "sh02"]
    assert result.problems == ()


def test_notes_report_coverage_and_overruns(job: Path) -> None:
    synth = _fake_synth({"商业银行赚钱主要靠息差。": 5.5})
    result = synthesize_narration(_storyboard(job), job_root=job, synthesize=synth)

    notes = narration_notes(result, shot_count=3)
    assert "3/3 个镜头有配音" in notes[0]
    assert "1 段比镜头长" in notes[1]
    assert "sh01 超 1.50s" in notes[1]
    assert "没有拉伸镜头" in notes[1]
