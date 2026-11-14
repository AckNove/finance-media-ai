"""分镜 → 时间轴 → 成片 → 质检这条链路的测试。

引擎、ffprobe、ffmpeg 全部用替身，所以这些用例零网络、零渲染开销，
但它们验证的是**真实的分支**：

- 没有分镜 / 没有素材清单 / 分镜用了未登记素材 → 明确报错，不硬着头皮跑；
- 分镜合法 → 真的写出 timeline.json，且字幕来自 subtitle 列；
- 渲染回执里的产物路径、SHA-256、引擎调用顺序都对；
- 质检会真的把黑帧/冻结/静音判成失败，而不是一律"通过"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.capabilities.media.engine import EngineVersion, ImportResult
from backend.app.media.storyboard import load_storyboard
from backend.app.video.render import (
    RenderError,
    prepare_clips,
    probe_media,
    render_job,
    scan_media,
)

STORYBOARD_ROWS = [
    {
        "shot_id": "sh01",
        "start_time": "0",
        "end_time": "3",
        "narrative_purpose": "开场抛出问题",
        "narration": "银行靠什么赚钱？",
        "visual_description": "标题卡",
        "asset_id": "",
        "asset_type": "solid",
        "asset_source": "#0b1220",
        "license": "原创（纯色底，程序生成）",
        "animation": "fade_in",
        "camera_motion": "static",
        "transition": "none",
        "subtitle": "银行靠什么赚钱？",
        "sound_effect": "none",
        "safety_zone": "bottom",
        "fps": "30",
        "canvas_width": "1080",
        "canvas_height": "1920",
    },
    {
        "shot_id": "sh02",
        "start_time": "3",
        "end_time": "7",
        "narrative_purpose": "展示收入结构",
        "narration": "利息净收入占七成",
        "visual_description": "柱状图卡片",
        "asset_id": "C_CHART",
        "asset_type": "generated_image",
        "asset_source": "generated",
        "license": "原创",
        "animation": "bar_grow",
        "camera_motion": "push",
        "transition": "dissolve",
        "subtitle": "利息净收入占七成",
        "sound_effect": "whoosh_low",
        "safety_zone": "bottom",
        "fps": "30",
        "canvas_width": "1080",
        "canvas_height": "1920",
    },
]

MANIFEST_ROWS = [
    {
        "asset_id": "C_CHART",
        "asset_class": "B",
        "kind": "image",
        "file": "assets/chart.png",
        "source_id": "",
        "publisher": "本仓库程序生成",
        "source_url": "",
        "retrieved_at": "2026-11-12T00:00:00Z",
        "sha256": "",
        "license": "原创",
        "commercial_use": "allowed",
        "credit": "本仓库",
        "verification_status": "verified",
        "used_in_shots": "sh02",
        "purpose": "收入结构演示",
    }
]


def _write_job(root: Path, *, storyboard_rows=None, manifest_rows=None, with_storyboard=True,
               with_manifest=True) -> Path:
    job = root / "demo_job"
    (job / "storyboard").mkdir(parents=True, exist_ok=True)
    (job / "assets").mkdir(parents=True, exist_ok=True)
    (job / "project").mkdir(parents=True, exist_ok=True)
    (job / "fonts").mkdir(parents=True, exist_ok=True)
    (job / "assets" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    # 字体只要存在就行：替身引擎不真的排版，但真实引擎会因为缺字体拒收时间轴。
    (job / "fonts" / "NotoSansSC-VF.ttf").write_bytes(b"fake-font")

    if with_storyboard:
        rows = storyboard_rows if storyboard_rows is not None else STORYBOARD_ROWS
        header = list(rows[0].keys())
        lines = [",".join(header)]
        for row in rows:
            lines.append(",".join(str(row[column]) for column in header))
        (job / "storyboard" / "storyboard.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if with_manifest:
        rows = manifest_rows if manifest_rows is not None else MANIFEST_ROWS
        header = list(rows[0].keys())
        lines = [",".join(header)]
        for row in rows:
            lines.append(",".join(str(row[column]) for column in header))
        (job / "assets_manifest.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return job


class _FakeEngine:
    """替身引擎：只记录调用顺序，产出一个有内容的假 MP4。"""

    def __init__(self, media_path: Path, *, valid: bool = True) -> None:
        self.media_path = media_path
        self.calls: list[str] = []
        self.import_windows: list[dict] = []
        self._valid = valid

    def save_composition_reloading(self, path: str, document: dict) -> dict:
        self.calls.append(f"save_composition:{path}")
        return {"revision": "comp-1"}

    def render_composition(self, path: str, *, width: int | None = None,
                           height: int | None = None, video: bool = True) -> dict:
        self.calls.append(f"render_composition:{path}")
        return {"clip": {"frames_dir": "compositions/comp-1/frames", "pattern": "%05d.png",
                         "n": 120, "fps": 30, "w": 1080, "h": 1920}}

    def version(self) -> EngineVersion:
        return EngineVersion(name="fake", version="0.1.3", ffmpeg="ffmpeg 8.1.2", ffprobe="ffprobe")

    def import_media(self, source: Path, *, rights: str | None = None,
                     start: float | None = None, duration: float | None = None) -> ImportResult:
        suffix = "" if start in (None, 0) else f"@{start:g}s"
        self.calls.append(f"import_media:{source.name}{suffix}")
        self.import_windows.append({"source": source.name, "start": start, "duration": duration})
        return ImportResult(
            kind="image",
            path=source,
            sha256="a" * 64,
            width=1080,
            height=1350,
            fps=30.0,
            duration=None,
            has_audio=False,
            raw={"clip": {"frames_dir": "plates/C_CHART", "pattern": "%05d.png",
                          "n": 1, "fps": 30, "w": 1080, "h": 1350}},
        )

    def validate_timeline(self, timeline: dict) -> dict:
        self.calls.append("validate_timeline")
        return {"valid": self._valid, "frames": timeline["canvas"]["frames"], "warnings": []}

    def save_timeline(self, timeline: dict, *, revision: str | None = None) -> dict:
        self.calls.append("save_timeline")
        return {"revision": "abcd1234"}

    def render(self, *, request_id: str, timeout: float | None = None) -> dict:
        self.calls.append(f"render:{request_id}")
        self.media_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_path.write_bytes(b"fake-mp4-payload")
        return {"job_id": "fake-job", "status": "succeeded", "video": str(self.media_path)}


def _good_probe(_: list[str]) -> str:
    return json.dumps(
        {
            "format": {"duration": "7.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                 "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
    )


def _quiet_ffmpeg(_: list[str]) -> str:
    return ""


# ------------------------------------------------------------------ 前置条件


def test_missing_storyboard_is_reported_not_guessed(tmp_path: Path) -> None:
    job = _write_job(tmp_path, with_storyboard=False)
    with pytest.raises(RenderError) as error:
        render_job(job, engine=_FakeEngine(tmp_path / "out.mp4"))
    assert error.value.reason == "missing_storyboard"
    assert "分镜" in str(error.value)


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    job = _write_job(tmp_path, with_manifest=False)
    with pytest.raises(RenderError) as error:
        render_job(job, engine=_FakeEngine(tmp_path / "out.mp4"))
    assert error.value.reason == "missing_manifest"


def test_unregistered_asset_is_rejected(tmp_path: Path) -> None:
    rows = [dict(STORYBOARD_ROWS[0]), dict(STORYBOARD_ROWS[1], asset_id="NOT_REGISTERED")]
    job = _write_job(tmp_path, storyboard_rows=rows)
    with pytest.raises(RenderError) as error:
        render_job(job, engine=_FakeEngine(tmp_path / "out.mp4"))
    assert error.value.reason == "unknown_asset"
    assert "NOT_REGISTERED" in str(error.value)


def test_invalid_storyboard_surfaces_the_validator_code(tmp_path: Path) -> None:
    broken = [dict(STORYBOARD_ROWS[0], end_time="3"), dict(STORYBOARD_ROWS[1], start_time="9")]
    job = _write_job(tmp_path, storyboard_rows=broken)
    with pytest.raises(RenderError) as error:
        render_job(job, engine=_FakeEngine(tmp_path / "out.mp4"))
    assert error.value.reason == "invalid_storyboard"


def _video_manifest(asset_id: str = "S03") -> list[dict]:
    return [
        dict(
            MANIFEST_ROWS[0],
            asset_id=asset_id,
            kind="video",
            file="assets/earth.mp4",
            license="NASA public domain",
            commercial_use="allowed",
        )
    ]


def _write_video_job(root: Path, *, storyboard_rows: list[dict]) -> Path:
    job = _write_job(root, storyboard_rows=storyboard_rows, manifest_rows=_video_manifest())
    (job / "assets" / "earth.mp4").write_bytes(b"fake-video")
    return job


def test_one_registered_video_can_be_used_for_two_entry_points(tmp_path: Path) -> None:
    """分镜里的 S03A / S03B 是同一份登记素材的两个入点，不该被当成未登记素材。"""
    rows = [
        dict(STORYBOARD_ROWS[0], asset_id="S03A", asset_type="real_video",
             source_start="0", end_time="3"),
        dict(STORYBOARD_ROWS[1], asset_id="S03B", asset_type="real_video",
             source_start="12", start_time="3", end_time="7"),
    ]
    job = _write_video_job(tmp_path, storyboard_rows=rows)
    engine = _FakeEngine(tmp_path / "out.mp4")
    report = render_job(job, engine=engine, do_render=False, do_qc=False)

    assert [row["asset_id"] for row in report.assets] == ["S03A", "S03B"]
    assert {row["registered_as"] for row in report.assets} == {"S03"}
    assert engine.import_windows == [
        {"source": "earth.mp4", "start": None, "duration": 3.0},
        {"source": "earth.mp4", "start": 12.0, "duration": 4.0},
    ]
    assert "import_media:earth.mp4@12s" in engine.calls


def test_composition_asset_goes_through_the_compositor(tmp_path: Path) -> None:
    """组合是程序画出来的画面，不能当普通媒体文件导入，必须走组合渲染。"""
    job = _write_job(tmp_path)
    document = {
        "schema": 1,
        "canvas": {"w": 1080, "h": 1920, "fps": 30, "frames": 120},
        "root": {"id": "root", "type": "box"},
        "tracks": [],
    }
    (job / "assets" / "chart.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    rows = [dict(MANIFEST_ROWS[0], kind="composition", file="assets/chart.json")]
    job = _write_job(tmp_path, manifest_rows=rows)
    engine = _FakeEngine(tmp_path / "out.mp4")
    report = render_job(job, engine=engine, do_render=False, do_qc=False)

    assert [call for call in engine.calls if "composition" in call][:2] == [
        "save_composition:assets/chart.json",
        "render_composition:assets/chart.json",
    ]
    composition_rows = [row for row in report.assets if row["asset_id"] == "C_CHART"]
    assert composition_rows and composition_rows[0]["frames"] == 120
    assert report.frames == 210


# ------------------------------------------------------------------ 时间轴


def test_timeline_only_run_writes_a_valid_document(tmp_path: Path) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4")
    report = render_job(job, engine=engine, do_render=False)

    assert report.stage == "timeline_ready"
    assert report.blocked is False
    assert report.storyboard_shots == 2
    assert report.frames == 210  # 7 秒 × 30fps
    document = json.loads(report.timeline_path.read_text(encoding="utf-8"))
    # 画布底色与数据卡同色，运镜平移时不会露出异色边
    assert document["canvas"] == {"w": 1080, "h": 1920, "fps": 30, "frames": 210, "bg": "#1d2a44"}
    assert [shot["f0"] for shot in document["shots"]] == [0, 90]
    # 字幕来自 subtitle 列，不是 narration 自动生成
    assert [item["text"] for item in document["text"]] == ["银行靠什么赚钱？", "利息净收入占七成"]
    assert "import_media:chart.png" in engine.calls
    assert "validate_timeline" in engine.calls


def test_engine_rejecting_the_timeline_stops_before_rendering(tmp_path: Path) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4", valid=False)
    with pytest.raises(RenderError) as error:
        render_job(job, engine=engine)
    assert error.value.reason == "invalid_timeline"
    assert not any(call.startswith("render:") for call in engine.calls)


def _fake_narration(_text: str, out_path: Path, **_kwargs):
    from backend.app.capabilities.tts.provider import TtsResult

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"ID3fake")
    return TtsResult(
        path=out_path,
        provider="fake",
        voice="zh-CN-Test",
        text=str(_text),
        duration_seconds=1.5,
        byte_size=out_path.stat().st_size,
        sha256="b" * 64,
        words=(),
        sample_rate=24000,
        estimated=False,
        meta={},
    )


def test_narration_adds_voice_tracks_to_the_timeline(tmp_path: Path) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4")
    report = render_job(
        job,
        engine=engine,
        do_render=False,
        narration=True,
        synthesize_narration_fn=_fake_narration,
    )

    document = json.loads(report.timeline_path.read_text(encoding="utf-8"))
    assert document["audio"]["mode"] == "mix"
    sources = [track["src"] for track in document["audio"]["tracks"]]
    assert sources == ["audio/narration/sh01.mp3", "audio/narration/sh02.mp3"]
    assert report.narration["clip_count"] == 2
    assert any("旁白" in note for note in report.notes)
    # 旁白只来自 narration 列，字幕仍来自 subtitle 列
    assert [item["text"] for item in document["text"]] == ["银行靠什么赚钱？", "利息净收入占七成"]


def test_missing_tts_leaves_the_film_silent_but_says_so(tmp_path: Path) -> None:
    from backend.app.capabilities.tts.provider import TtsError

    def broken(text: str, out_path: Path, **_kwargs):
        raise TtsError("no_provider", "没有可用的语音提供方")

    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4")
    report = render_job(
        job,
        engine=engine,
        do_render=False,
        narration=True,
        synthesize_narration_fn=broken,
    )

    document = json.loads(report.timeline_path.read_text(encoding="utf-8"))
    assert "audio" not in document
    assert report.narration["ok"] is False
    assert any("没有生成旁白音轨" in note for note in report.notes)


# ------------------------------------------------------------------ 渲染与质检


def test_render_copies_output_and_records_hash(tmp_path: Path) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "engine_out" / "video.mp4")
    report = render_job(job, engine=engine, ffprobe=_good_probe, ffmpeg=_quiet_ffmpeg)

    assert report.stage == "rendered"
    assert report.output_path == job / "output" / "final.mp4"
    assert report.output_path.is_file()
    assert len(report.output_sha256) == 64
    assert report.output_byte_size == len(b"fake-mp4-payload")
    assert report.qc["ok"] is True
    assert report.qc["failures"] == []
    assert report.timeline_revision == "abcd1234"
    # request_id 绑定时间轴版本
    assert any(call == "render:job-demo_job-abcd1234" for call in engine.calls)
    assert report.ok is True


def test_qc_fails_on_long_black_and_freeze(tmp_path: Path) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "engine_out" / "video.mp4")

    def noisy_ffmpeg(args: list[str]) -> str:
        if "blackdetect" in " ".join(args):
            return "[blackdetect] black_start:1 black_end:2.4 black_duration:1.4"
        if "freezedetect" in " ".join(args):
            return "[freezedetect] freeze_start:3 freeze_duration:2.0"
        return ""

    report = render_job(job, engine=engine, ffprobe=_good_probe, ffmpeg=noisy_ffmpeg)
    assert report.qc["ok"] is False
    assert "no_long_black" in report.qc["failures"]
    assert "no_long_freeze" in report.qc["failures"]
    # 产物照样留下：失败的是画质判定，不是文件
    assert report.output_path.is_file()


def test_probe_media_reads_canvas_and_fps(tmp_path: Path) -> None:
    media = tmp_path / "x.mp4"
    media.write_bytes(b"x")
    info = probe_media(media, run_ffprobe=_good_probe)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["fps"] == 30.0
    assert info["duration"] == 7.0
    assert info["has_audio"] is True
    assert info["streams"] == 2


def test_scan_media_sums_segment_durations(tmp_path: Path) -> None:
    media = tmp_path / "x.mp4"
    media.write_bytes(b"x")

    def fake_ffmpeg(args: list[str]) -> str:
        text = " ".join(args)
        if "blackdetect" in text:
            return "black_start:0.2 black_end:0.5 black_duration:0.3"
        if "silencedetect" in text:
            return "silence_start:1.0 silence_duration:2.5"
        return ""

    scan = scan_media(media, run_ffmpeg=fake_ffmpeg)
    assert scan["black_segments"] == 1
    assert scan["black_seconds"] == pytest.approx(0.3)
    assert scan["silence_seconds"] == pytest.approx(2.5)
    assert scan["freeze_segments"] == 0


def test_scan_media_skips_silence_detection_when_the_film_has_no_audio(tmp_path: Path) -> None:
    """没有音轨的片子不能拿去跑 silencedetect——ffmpeg 会直接以 -22 退出，把整份质检带崩。"""

    media = tmp_path / "x.mp4"
    media.write_bytes(b"x")
    seen: list[str] = []

    def fake_ffmpeg(args: list[str]) -> str:
        text = " ".join(args)
        seen.append(text)
        if "blackdetect" in text:
            return "black_start:0.2 black_end:0.5 black_duration:0.3"
        return ""

    scan = scan_media(media, run_ffmpeg=fake_ffmpeg, has_audio=False)

    assert not any("silencedetect" in args for args in seen), "没有音轨就不该调静音检测"
    assert scan["silence_seconds"] == 0.0
    assert scan["silence_longest"] == 0.0
    assert "没有音轨" in scan["silence_note"]
    # 黑帧照常检测，不能因为没声音就整段跳过
    assert scan["black_seconds"] == pytest.approx(0.3)


def test_solid_shots_use_a_moving_plate_not_a_still_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """纯色卡也要动：单帧均匀色块缩放后像素不变，会被 freezedetect 判成冻结。"""
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4")
    moved = job / "assets" / "plates" / "solid_1d2a44.mp4"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_bytes(b"fake-gradient")
    monkeypatch.setattr(
        "backend.app.video.render._solid_gradient", lambda *a, **k: moved
    )
    storyboard = load_storyboard(job / "storyboard" / "storyboard.csv")
    clips, rows, _ = prepare_clips(
        storyboard, job_root=job, engine=engine, animate_plates=True
    )
    assert any(name.endswith(".mp4") for name in engine.calls)
    solid_rows = [row for row in rows if str(row["asset_id"]).startswith("solid_")]
    assert solid_rows and solid_rows[0]["file"].endswith(".mp4")
    assert clips


def test_solid_shots_fall_back_to_a_still_plate_when_ffmpeg_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _write_job(tmp_path)
    engine = _FakeEngine(tmp_path / "out.mp4")
    monkeypatch.setattr(
        "backend.app.video.render._solid_gradient", lambda *a, **k: None
    )
    storyboard = load_storyboard(job / "storyboard" / "storyboard.csv")
    _, rows, _ = prepare_clips(
        storyboard, job_root=job, engine=engine, animate_plates=True
    )
    solid_rows = [row for row in rows if str(row["asset_id"]).startswith("solid_")]
    assert solid_rows and solid_rows[0]["file"].endswith(".png")
    assert (job / solid_rows[0]["file"]).is_file()
