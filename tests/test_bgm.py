"""背景音乐曲库与授权校验的测试。

全部离线：不联网、不下载音频。只有 ``test_mix_brings_the_track_into_the_video``
会真调本机 ffmpeg 生成 1 秒素材（没有 ffmpeg 就跳过）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.app.video.bgm import (
    ATTRIBUTION_REQUIRED,
    COMMERCIAL_LICENCES,
    BgmError,
    BgmTrack,
    check_library,
    licence_summary,
    load_library,
    pick,
    require_usable,
)

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"


def _library(tmp_path: Path, tracks: list[dict[str, object]], *, note: str = "") -> Path:
    target = tmp_path / "library.yaml"
    payload: dict[str, object] = {"tracks": tracks}
    if note:
        payload["note"] = note
    target.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return target


def _touch(tmp_path: Path, name: str, *, suffix: str = ".mp3") -> str:
    path = tmp_path / name
    path.write_bytes(b"ID3fake-audio-bytes")
    return path.name + suffix if not name.endswith(suffix) else path.name


def _track(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "calm-01",
        "title": "舒缓钢琴",
        "file": "calm-01.mp3",
        "licence": "cc0",
        "attribution": "",
        "source_url": "https://example.com/calm-01",
        "duration_seconds": 96,
        "mood": "舒缓",
    }
    base.update(overrides)
    return base


def test_missing_library_is_an_empty_library_not_an_error(tmp_path: Path) -> None:
    library = load_library(tmp_path / "nope.yaml")
    assert library.tracks == ()
    assert library.usable() == []
    assert "不是故障" in library.default_note()


def test_a_fully_registered_track_is_usable(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    library = load_library(_library(tmp_path, [_track()]))
    assert len(library.tracks) == 1
    track = require_usable(library.tracks[0], root=tmp_path)
    assert track.commercial_ok is True
    assert track.needs_attribution is False
    payload = track.as_dict(tmp_path)
    assert payload["exists"] is True
    assert payload["licence_key"] == "cc0"


def test_licence_outside_the_whitelist_is_refused(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    library = load_library(_library(tmp_path, [_track(licence="unknown-free-download")]))
    with pytest.raises(BgmError) as caught:
        require_usable(library.tracks[0], root=tmp_path)
    assert caught.value.reason == "licence_not_commercial"
    assert "白名单" in caught.value.message


def test_attribution_licence_requires_an_author_line(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    library = load_library(_library(tmp_path, [_track(licence="cc-by-4.0")]))
    with pytest.raises(BgmError) as caught:
        require_usable(library.tracks[0], root=tmp_path)
    assert caught.value.reason == "attribution_missing"

    fixed = load_library(
        _library(tmp_path, [_track(id="calm-02", file="calm-02.mp3", licence="cc-by-4.0", attribution="作者 A / calm")])
    )
    _touch(tmp_path, "calm-02.mp3")
    assert require_usable(fixed.tracks[0], root=tmp_path).needs_attribution is True


def test_a_registered_track_whose_file_is_gone_is_refused(tmp_path: Path) -> None:
    library = load_library(_library(tmp_path, [_track()]))
    with pytest.raises(BgmError) as caught:
        require_usable(library.tracks[0], root=tmp_path)
    assert caught.value.reason == "missing_file"


def test_a_non_audio_suffix_is_refused(tmp_path: Path) -> None:
    (tmp_path / "calm-01.mp4").write_bytes(b"not-audio")
    library = load_library(_library(tmp_path, [_track(file="calm-01.mp4")]))
    with pytest.raises(BgmError) as caught:
        require_usable(library.tracks[0], root=tmp_path)
    assert caught.value.reason == "unsupported_format"


def test_missing_required_fields_are_named(tmp_path: Path) -> None:
    target = _library(tmp_path, [{"id": "x", "title": "有标题但没文件"}])
    with pytest.raises(BgmError) as caught:
        load_library(target)
    assert caught.value.reason == "missing_field"
    assert "file" in caught.value.message and "licence" in caught.value.message


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    with pytest.raises(BgmError) as caught:
        load_library(_library(tmp_path, [_track(), _track()]))
    assert caught.value.reason == "duplicate_id"


def test_check_library_reports_why_a_track_cannot_be_used(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    library = load_library(
        _library(
            tmp_path,
            [
                _track(),
                _track(id="bad-01", file="bad-01.mp3", licence="unknown"),
                _track(id="gone-02", file="gone-02.mp3", licence="royalty-free"),
            ],
        )
    )
    report = {item["id"]: item for item in check_library(library)}
    assert report["calm-01"]["usable"] is True
    assert report["bad-01"]["usable"] is False and report["bad-01"]["reason"] == "licence_not_commercial"
    assert report["gone-02"]["usable"] is False and report["gone-02"]["reason"] == "missing_file"


def test_pick_names_the_tracks_it_does_know(tmp_path: Path) -> None:
    _touch(tmp_path, "calm-01.mp3")
    library = load_library(_library(tmp_path, [_track()]))
    with pytest.raises(BgmError) as caught:
        pick("nope", library=library)
    assert caught.value.reason == "unknown_track"
    assert "calm-01" in caught.value.message
    assert pick("calm-01", library=library).id == "calm-01"


def test_the_licence_whitelist_is_explicit() -> None:
    assert COMMERCIAL_LICENCES == {
        "cc0",
        "public-domain",
        "public_domain",
        "cc-by",
        "cc-by-4.0",
        "cc-by-sa",
        "royalty-free",
        "purchased",
        "own-work",
    }
    assert ATTRIBUTION_REQUIRED <= COMMERCIAL_LICENCES
    assert licence_summary([BgmTrack(id="a", title="a", file="a.mp3", licence="CC0")]) == {"cc0": 1}


def test_the_shipped_library_is_empty_and_licensed_nothing_wrong() -> None:
    """仓库自带的曲库是空的——这是诚实状态，不是缺功能。"""
    library = load_library(ROOT / "content" / "assets" / "bgm" / "library.yaml")
    assert library.tracks == ()
    assert "不是故障" in library.default_note()
    assert "synthesize_score" in library.default_note()


def test_the_cli_lists_and_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from backend.scripts import bgm_check

    _touch(tmp_path, "calm-01.mp3")
    target = _library(tmp_path, [_track()])
    assert bgm_check.main(["--library", str(target), "--list"]) == 0
    out = capsys.readouterr().out
    assert "calm-01" in out and "可用 1 / 1" in out

    assert bgm_check.main(["--library", str(target), "--check", "calm-01"]) == 0
    assert "可用：calm-01" in capsys.readouterr().out

    assert bgm_check.main(["--library", str(target), "--check", "nope"]) == 1
    assert "曲库里没有 nope" in capsys.readouterr().out


def test_the_cli_refuses_a_track_with_an_unknown_licence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from backend.scripts import bgm_check

    _touch(tmp_path, "bad-01.mp3")
    target = _library(tmp_path, [_track(id="bad-01", file="bad-01.mp3", licence="unknown")])
    assert bgm_check.main(["--library", str(target), "--check", "bad-01"]) == 1
    assert "白名单" in capsys.readouterr().out


@pytest.mark.skipif(not FFMPEG.is_file(), reason="项目内没有 ffmpeg")
def test_mix_brings_the_track_into_the_video(tmp_path: Path) -> None:
    """真调 ffmpeg：1 秒静音视频 + 1 秒正弦音 → 混音后必须真有音轨。"""
    tone = tmp_path / "calm-01.wav"
    subprocess.run(
        [str(FFMPEG), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(tone)],
        check=True,
        capture_output=True,
    )
    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            str(FFMPEG), "-y", "-f", "lavfi", "-i", "color=c=#151d2e:s=320x180:d=1:r=15",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
        ],
        check=True,
        capture_output=True,
    )
    library = load_library(_library(tmp_path, [_track(file="calm-01.wav", licence="royalty-free")]))
    track = require_usable(library.tracks[0], root=tmp_path)

    from backend.scripts import mix_bgm

    out = tmp_path / "mixed.mp4"
    assert mix_bgm.mix(video=video, track=track, out=out, volume=0.35, root=tmp_path) == 0
    assert out.is_file() and out.stat().st_size > 0
    probe = subprocess.run(
        [str(FFMPEG.with_name("ffprobe.exe")), "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "audio" in probe.stdout
