"""V2.2 Phase 1：音视频工具链解析与能力探测的测试。

设计原则：
- **不做假断言**。要求真跑二进制的地方就是真跑；二进制不在时用 skip，而不是让断言退化。
- 列表解析用固定样本覆盖"单字母标记"这一真实坑（`-muxers` 的标记就一个 `E`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.capabilities.media import ffmpeg as media
from backend.app.core.config import PROJECT_ROOT

PROJECT_FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
PROJECT_FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
requires_project_build = pytest.mark.skipif(
    not (PROJECT_FFMPEG.is_file() and PROJECT_FFPROBE.is_file()),
    reason="项目独立 FFmpeg 构建未安装（python -m backend.scripts.install_ffmpeg）",
)


# --- 列表解析（纯函数，不依赖二进制） ---------------------------------


def test_listing_names_handles_single_letter_flags():
    """`-muxers` / `-demuxers` 的标记只有一个字母，解析不能要求 2 个字符。"""
    text = (
        " Muxers:\n"
        " E  mp4             MP4 (MPEG-4 Part 14)\n"
        "  E  mov             QuickTime / MOV\n"
        " D  image2          image2 sequence\n"
    )
    assert media._listing_names(text) == {"mp4", "mov", "image2"}


def test_listing_names_handles_multi_flag_columns():
    text = (
        " Encoders:\n"
        " V..... libx264              H.264 (codec h264)\n"
        " A....D aac                  AAC (Advanced Audio Coding)\n"
    )
    assert media._listing_names(text) == {"libx264", "aac"}


def test_listing_names_ignores_header_and_blank_lines():
    text = "Encoders:\n------\n\n V..... libx264  H.264\n"
    assert media._listing_names(text) == {"libx264"}


def test_requirements_cover_the_task_book_capabilities():
    """任务书 Phase 1 要求检查的十类能力必须都在 REQUIRED 项里。"""
    required = {item.key for item in media.REQUIREMENTS if item.required}
    expected = {
        "h264_decode",
        "h264_encode",
        "aac_encode",
        "ass_filter",
        "mp4_demux",
        "mp4_mux",
        "audio_mix",
        "video_trim",
        "video_concat",
        "fps_convert",
        "ffprobe_metadata",
        "volume",
        "loudnorm",
    }
    assert expected <= required
    assert "drawtext" not in required  # 可选能力不能被算作必需


# --- 工具链对象（构造体，不依赖二进制） -------------------------------


def test_toolchain_without_binaries_is_unavailable():
    toolchain = media.MediaToolchain(ffmpeg=None, ffprobe=None, source="none")
    assert toolchain.available is False
    assert toolchain.satisfies_minimum is False
    assert toolchain.version_text == "unknown"
    assert toolchain.describe()["available"] is False


def test_toolchain_minimum_version_rule():
    fake = Path(__file__)
    old = media.MediaToolchain(ffmpeg=fake, ffprobe=fake, source="x", version=(4, 2, 2))
    new = media.MediaToolchain(ffmpeg=fake, ffprobe=fake, source="x", version=(5, 1))
    assert old.satisfies_minimum is False  # 4.2.2 正是本机系统版本，必须被判为不达标
    assert new.satisfies_minimum is True
    assert new.version_text == "5.1"


def test_require_toolchain_raises_when_unavailable():
    with pytest.raises(media.MediaToolError) as excinfo:
        media.require_toolchain(media.MediaToolchain(ffmpeg=None, ffprobe=None, source="none"))
    assert "install_ffmpeg" in str(excinfo.value)


def test_require_toolchain_raises_when_below_minimum():
    fake = Path(__file__)
    with pytest.raises(media.MediaToolError) as excinfo:
        media.require_toolchain(
            media.MediaToolchain(ffmpeg=fake, ffprobe=fake, source="x", version=(4, 2, 2))
        )
    assert "5.1" in str(excinfo.value)


def test_candidate_order_prefers_config_over_project_then_path(tmp_path):
    configured = tmp_path / "custom-bin"
    candidates = media._candidate_dirs({"video": {"ffmpeg_dir": str(configured)}}, None)
    labels = [label for label, _ in candidates]
    assert labels == ["config", "project"]
    assert candidates[0][1] == configured


def test_relative_ffmpeg_dir_resolves_against_project_root():
    candidates = media._candidate_dirs({"video": {"ffmpeg_dir": "tools/ffmpeg/bin"}}, None)
    assert candidates[0] == ("config", PROJECT_ROOT / "tools" / "ffmpeg" / "bin")


def test_explicit_dir_wins_over_config(tmp_path):
    candidates = media._candidate_dirs({"video": {"ffmpeg_dir": str(tmp_path / "a")}}, tmp_path / "b")
    assert candidates[0] == ("explicit", tmp_path / "b")


# --- 真机：解析、版本、能力、错误处理 ---------------------------------


@requires_project_build
def test_resolve_toolchain_prefers_project_build():
    toolchain = media.resolve_toolchain()
    assert toolchain.available is True
    assert toolchain.ffmpeg == PROJECT_FFMPEG
    assert toolchain.ffprobe == PROJECT_FFPROBE
    assert toolchain.source in {"config", "project"}
    assert toolchain.satisfies_minimum is True


@requires_project_build
def test_read_version_matches_pinned_record():
    record = json.loads((PROJECT_ROOT / "tools" / "ffmpeg" / "VERSION.json").read_text(encoding="utf-8"))
    line, version = media.read_version(PROJECT_FFMPEG)
    assert line == record["ffmpeg_version_line"]
    assert version is not None and version[:2] >= media.MIN_FFMPEG_VERSION
    assert record["hash_matches_declared"] is True
    assert record["not_on_path"] is True


@requires_project_build
def test_probe_capabilities_passes_all_required():
    report = media.probe_capabilities(media.resolve_toolchain())
    assert report.ok is True, [item.key for item in report.missing_required]
    assert report.missing_required == ()
    present = {item.key for item in report.capabilities if item.present}
    for key in ("h264_encode", "aac_encode", "ass_filter", "mp4_mux", "loudnorm", "concat_demux"):
        assert key in present


@requires_project_build
def test_probe_capabilities_survives_missing_toolchain():
    report = media.probe_capabilities(media.MediaToolchain(ffmpeg=None, ffprobe=None, source="none"))
    assert report.ok is False
    assert len(report.missing_required) == len([r for r in media.REQUIREMENTS if r.required])


@requires_project_build
def test_run_ffmpeg_raises_mediatoolerror_on_bad_arguments():
    toolchain = media.resolve_toolchain()
    with pytest.raises(media.MediaToolError) as excinfo:
        media.run_ffmpeg(toolchain, ["-i", "no-such-file-here.mp4", "-f", "null", "-"])
    error = excinfo.value
    assert error.command[0] == str(toolchain.ffmpeg)
    assert error.describe()["stderr_tail"]


@requires_project_build
def test_run_ffprobe_reads_real_streams(tmp_path):
    toolchain = media.resolve_toolchain()
    sample = tmp_path / "probe.mp4"
    media.run_ffmpeg(
        toolchain,
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=0.5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(sample),
        ],
    )
    info = json.loads(
        media.run_ffprobe(
            toolchain, ["-v", "error", "-print_format", "json", "-show_streams", str(sample)]
        )
    )
    assert info["streams"][0]["codec_name"] == "h264"
    assert info["streams"][0]["width"] == 64


@requires_project_build
def test_fps_mode_option_is_available():
    """OpenKatana 的 `prepare()` 依赖 `-fps_mode`，这条必须真跑一次而不是看帮助文本。"""
    toolchain = media.resolve_toolchain()
    output = media.run_ffmpeg(
        toolchain,
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=0.5",
            "-fps_mode",
            "passthrough",
            "-f",
            "null",
            "-",
        ],
    )
    assert "Unrecognized option" not in output
