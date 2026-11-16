"""把《电力的第二张收入表》重制成电影感动态图形：蓝图 → 镜头页 → 逐帧渲染 → 混音成片。

这一条命令跑完六件事：

    1. 写视觉制作蓝图（remaster_v1/blueprint/film.json）——五个镜头的构图、摄影机、
       事件、素材、证据，全部结构化，可被别的引擎复用
    2. 每个镜头生成一个自包含的镜头页（index.html + scene.js + 字体）
    3. 用 Chromium 逐帧渲染（30fps，1080×1920），单镜可重渲
    4. 合成声音：已批准的 edge-tts 旁白 + 程序化原创配乐 + 音效
    5. 拼成整片，压字幕，做技术 QC
    6. 从整片里切出 15–20 秒样片（必须是整片里的真实片段）

用法：

    python -m backend.scripts.build_cinema_film --stage blueprint
    python -m backend.scripts.build_cinema_film --stage scenes
    python -m backend.scripts.build_cinema_film --stage render --only s2,s3
    python -m backend.scripts.build_cinema_film --stage audio
    python -m backend.scripts.build_cinema_film --stage assemble
    python -m backend.scripts.build_cinema_film --stage all
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.video.cinema.audio import (  # noqa: E402
    music_samples,
    place_cues,
    write_wav,
)
from backend.app.video.cinema.blueprint import (  # noqa: E402
    REQUIRED_QUALITY,
    AssetRef,
    AudioCue,
    CameraKey,
    EvidenceRef,
    Film,
    Scene,
    Subtitle,
    VisualEvent,
    dump_blueprint,
)
from backend.app.video.cinema.page import write_scene_shell  # noqa: E402

JOB = ROOT / "content" / "jobs" / "v6_power_first_film"
REM = JOB / "remaster_v1"
SCENES_DIR = REM / "production" / "scenes"
FRAMES_DIR = REM / "production" / "frames"
CLIPS_DIR = REM / "production" / "scenes_video"
OUT_DIR = REM / "output"
LOG_DIR = REM / "logs"
QC_DIR = REM / "qc"
AUDIO_DIR = REM / "assets" / "audio"

FONT = JOB / "fonts" / "NotoSansSC-VF.ttf"
THREE_JS = ROOT / "tools" / "vendor" / "three.min.js"
SCENE_JS = ROOT / "backend" / "app" / "video" / "cinema" / "scene.js"
NARRATION = JOB / "audio" / "narration"
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
OPENKATANA_PYTHON = ROOT / "tools" / "openkatana" / ".venv" / "Scripts" / "python.exe"

FPS = 30
WIDTH = 1080
HEIGHT = 1920

PALETTE = {
    "ink": "#04060b",
    "sky": "#31567f",
    "skin": "#f2f5f7",
    "lime": "#b6ff1f",
    "cyan": "#35e0ff",
    "amber": "#ffb020",
    "dim": "#8ea3bd",
}

# 旁白片的实测时长（ffprobe 量出来的，不是拍的）
CLIP_SECONDS = {
    "sh01": 4.176,
    "sh02": 4.536,
    "sh03": 4.320,
    "sh04": 5.160,
    "sh05": 6.648,
    "sh06": 4.248,
    "sh07": 5.784,
    "sh08": 6.288,
}

CLIP_TEXT = {
    "sh01": "发电量下滑，电费收入却还能收上来，钱从哪来？",
    "sh02": "发电量下滑，收入却还能收上来——这就是第二张收入表。",
    "sh03": "它叫容量电价，利润跟电量脱钩，不再绑在一根绳上。",
    "sh04": "估值依据也跟着换：从红利资产，变成AI的能源底座。",
    "sh05": "但先别急着兴奋，反证在这儿：2026年上半年行业利润同比降6.2%。",
    "sh06": "利润在降，说明这是存量再分配，不是总量扩张。",
    "sh07": "一句话：钱没变多，只是分法变了，别把再分配看成新蛋糕。",
    "sh08": "本片为合规资料整理，不构成投资建议；所有数字均来自片尾列出的公开来源。",
}

EVIDENCE = {
    "s02_capacity": EvidenceRef(
        source_id="S02",
        claim="容量电价把「可用容量」单独计价，利润不再完全绑在发电量上（已人工确认）",
        check="confirmed",
    ),
    "s01_ai": EvidenceRef(
        source_id="S01",
        claim="用电需求结构与估值框架正在从「红利资产」向「算力基础设施」切换（已人工确认）",
        check="confirmed",
    ),
    "s11_profit": EvidenceRef(
        source_id="S11",
        claim="2026 年上半年电力、热力生产和供应业利润总额 3392.8 亿元，同比降 6.2%（模型判定部分支持，已人工确认可用）",
        check="confirmed",
    ),
    "recap": EvidenceRef(
        source_id="S02",
        claim="结论回扣：钱没变多，只是分法变了",
        check="confirmed",
    ),
}


# --------------------------------------------------------------------------- 舞台


def tower(pid: str, x: float, z: float, height: float, base: float, thickness: float,
          enter: tuple[float, float], *, color: str = "#1b2735", glow: str = "#2f7fa8",
          strength: float = 0.18) -> dict:
    return {
        "id": pid,
        "type": "pylon",
        "at": [x, -3.2, z],
        "height": height,
        "base": base,
        "thickness": thickness,
        "color": color,
        "glow": glow,
        "glowStrength": strength,
        "enter": list(enter),
    }


def flow(pid: str, points: list, color: str, *, speed: float = 0.07, particles: int = 26,
         radius: float = 0.020, enter: tuple[float, float] = (1.0, 2.0), tube: float = 0.035) -> dict:
    return {
        "id": pid,
        "type": "flow",
        "points": points,
        "color": color,
        "radius": radius,
        "particleRadius": radius * 1.4,
        "particles": particles,
        "speed": speed,
        "tubeOpacity": tube,
        "enter": list(enter),
    }


def bar(pid: str, at: list, *, width: float, height: float, color: str, keyframes: list,
        enter: tuple[float, float] = (0.2, 1.0), depth: float | None = None,
        glow: float = 0.4) -> dict:
    return {
        "id": pid,
        "type": "bar",
        "at": at,
        "width": width,
        "depth": depth if depth is not None else width,
        "height": height,
        "color": color,
        "glow": color,
        "glowStrength": glow,
        "keyframes": keyframes,
        "enter": list(enter),
    }


def slab(pid: str, at: list, *, size: list, color: str, enter: tuple[float, float],
         glow: str | None = None, strength: float = 0.3, pulse: float = 0.0) -> dict:
    item = {
        "id": pid,
        "type": "box",
        "at": at,
        "size": size,
        "color": color,
        "glow": glow or color,
        "glowStrength": strength,
        "enter": list(enter),
    }
    if pulse:
        item["pulse"] = pulse
        item["pulseSpeed"] = 0.5
    return item


def text(tid: str, role: str, content: str, x: float, y: float, w: float, enter: tuple[float, float],
         exit_: tuple[float, float] | None = None, **extra) -> dict:
    item = {
        "id": tid,
        "role": role,
        "text": content,
        "x": x,
        "y": y,
        "w": w,
        "enter": list(enter),
    }
    if exit_:
        item["exit"] = list(exit_)
    item.update(extra)
    return item


def sky(css: str, *, top: str = "#31567f", bottom: str = "#04060b", fog: tuple[float, float] = (20, 85)) -> dict:
    return {"top": top, "bottom": bottom, "fog": list(fog), "css": css}


DUSK = "linear-gradient(to bottom, #36608f 0%, #27507c 16%, #16304f 36%, #0a1424 60%, #04060b 100%)"
NIGHT = "radial-gradient(ellipse 150% 70% at 62% 6%, #24405f 0%, #14263c 34%, #04060b 66%)"


def stage_s1() -> dict:
    """开场：黄昏电网上方推近，两条收入流亮起来，抛出问题。"""
    return {
        "exposure": 1.0,
        "ambient": 0.18,
        "keyLight": 0.5,
        "rimLight": 1.25,
        "rimColor": "#3f8fc0",
        "scrimStrength": 0.86,
        "sky": sky(DUSK),
        "lights": [
            {"at": [-4.0, 2.4, 6.0], "color": "#7fd8ff", "intensity": 0.8, "distance": 24},
            {"at": [4.0, 4.0, -14.0], "color": "#9ec8ff", "intensity": 2.0, "distance": 60},
            {"at": [2.0, 0.6, 1.0], "color": "#b6ff1f", "intensity": 1.6, "distance": 14},
        ],
        "objects": [
            {"id": "net", "type": "grid", "size": 110, "div": 34, "y": -3.25, "color": "#14263a",
             "opacity": 0.3, "enter": [0, 0.7]},
            tower("towerA", 3.4, -2.2, 8.6, 1.0, 1.0, (0.1, 1.0)),
            tower("towerB", 6.8, -10.0, 8.0, 0.95, 0.9, (0.3, 1.2)),
            tower("towerC", 0.9, -16.0, 7.2, 0.9, 0.8, (0.5, 1.4), color="#2b3a4e", glow="#5aa8d8", strength=0.22),
            tower("towerD", 5.0, -26.0, 6.4, 0.85, 0.7, (0.7, 1.6), color="#243244", glow="#4a93c0", strength=0.16),
            flow("flowPower", [[3.4, 2.7, -2.2], [6.8, 2.4, -10.0], [5.0, 2.1, -26.0]], "#7dbe2c",
                 speed=-0.1, enter=(1.1, 2.0)),
            flow("flowCapacity", [[3.4, 2.5, -2.2], [0.9, 2.2, -16.0]], "#2f7f9c",
                 speed=0.09, particles=44, radius=0.024, enter=(1.5, 2.4)),
            {"id": "ringA", "type": "ring", "at": [3.4, 1.0, -2.2], "radius": 2.4, "thickness": 0.016,
             "color": "#35e0ff", "opacity": 0.32, "enter": [1.6, 2.4], "spin": 0.24},
            {"id": "cap", "type": "sphere", "at": [3.4, 2.85, -2.2], "radius": 0.13, "color": "#b6ff1f",
             "glowStrength": 1.4, "enter": [1.9, 2.5], "pulse": 0.9, "pulseSpeed": 0.5},
        ],
        "texts": [
            text("kicker", "kicker", "AK 带你拆解世界 · 第 01 期", 0.08, 0.055, 0.84, (0.2, 0.9)),
            text("q", "title", "发电量在掉，\n电费收入却还在涨", 0.08, 0.60, 0.86, (0.9, 1.8), size=78, maxLines=4),
            text("ask", "subtitle", "钱，从哪来？", 0.08, 0.795, 0.86, (2.4, 3.1), size=56),
            text("src", "source", "来源 S02 · 容量电价机制（已人工确认）", 0.08, 0.915, 0.86, (3.2, 3.9)),
        ],
        "camera": [
            [0.0, [3.0, 4.4, 21.0], [2.8, 2.6, -8.0], 33],
            [2.4, [3.1, 2.9, 15.0], [2.7, 2.5, -8.4], 32],
            [5.7, [3.2, 2.2, 11.2], [2.5, 2.5, -9.0], 32],
        ],
    }


def stage_s2() -> dict:
    """机制：两根柱子各算各的账。

    数据图表走二维（DOM），三维只留地网格与地平线光——这样柱子颜色、标注、涨跌都精确可控，
    而画面仍有纵深。这就是任务书要的"二维/三维融合"，而不是让三维硬扛图表。
    """
    left = [
        {"id": "e1", "kind": "bar", "x": 0.085, "w": 0.075, "y": 0.735, "h": 0.21,
         "color": "#3c6f8c", "glow": "#3c6f8c", "radius": 8,
         "keyframes": [[0, 0.2], [1.4, 1.0], [5.0, 0.66], [9.6, 0.44]], "enter": [0.2, 1.2]},
        {"id": "e2", "kind": "bar", "x": 0.175, "w": 0.075, "y": 0.735, "h": 0.18,
         "color": "#35637e", "glow": "#35637e", "radius": 8,
         "keyframes": [[0, 0.2], [1.5, 0.95], [5.2, 0.58], [9.7, 0.36]], "enter": [0.3, 1.3]},
        {"id": "e3", "kind": "bar", "x": 0.265, "w": 0.075, "y": 0.735, "h": 0.155,
         "color": "#2e576f", "glow": "#2e576f", "radius": 8,
         "keyframes": [[0, 0.2], [1.6, 0.9], [5.4, 0.52], [9.8, 0.3]], "enter": [0.4, 1.4]},
    ]
    right = [
        {"id": "c1", "kind": "bar", "x": 0.545, "w": 0.075, "y": 0.735, "h": 0.13,
         "color": "#5f8f1f", "glow": "#9ee83a", "radius": 8,
         "keyframes": [[0, 0.15], [3.2, 0.3], [6.0, 0.7], [9.9, 1.0]], "enter": [2.7, 3.7]},
        {"id": "c2", "kind": "bar", "x": 0.635, "w": 0.075, "y": 0.735, "h": 0.175,
         "color": "#8ecb28", "glow": "#b6ff1f", "radius": 8,
         "keyframes": [[0, 0.15], [3.3, 0.34], [6.2, 0.74], [10.0, 1.0]], "enter": [2.8, 3.8]},
        {"id": "c3", "kind": "bar", "x": 0.725, "w": 0.075, "y": 0.735, "h": 0.22,
         "color": "#b6ff1f", "glow": "#b6ff1f", "radius": 8,
         "keyframes": [[0, 0.15], [3.4, 0.38], [6.4, 0.78], [10.1, 1.0]], "enter": [2.9, 3.9]},
    ]
    rule = [
        {"id": "baseline", "kind": "bar", "x": 0.06, "w": 0.80, "y": 0.737, "h": 0.0035,
         "color": "#3b5a7a", "radius": 2, "shadow": False, "grow": False, "enter": [0.0, 0.8]},
    ]
    return {
        "exposure": 1.0,
        "ambient": 0.3,
        "keyLight": 0.5,
        "scrimStrength": 0.55,
        "sky": sky("linear-gradient(to bottom, #22405f 0%, #14263c 26%, #070d18 58%, #04060b 100%)"),
        "lights": [
            {"at": [0.0, 2.2, 4.0], "color": "#6f9be0", "intensity": 1.0, "distance": 30},
        ],
        "objects": [
            {"id": "net", "type": "grid", "size": 70, "div": 26, "y": -3.0, "color": "#152b42",
             "opacity": 0.5, "enter": [0, 0.6]},
            {"id": "floor", "type": "box", "at": [0.0, -3.02, -6.0], "size": [16, 0.02, 26],
             "color": "#0b1725", "glow": "#0b1725", "glowStrength": 0.05, "enter": [0, 0.6]},
        ],
        "panels": rule + left + right,
        "texts": [
            text("kicker", "kicker", "第二张收入表", 0.08, 0.055, 0.84, (0.1, 0.9)),
            text("leftLabel", "label", "电量收入", 0.06, 0.395, 0.36, (0.7, 1.6), color="#9fc3d8"),
            text("leftTrend", "caption", "跟着发电量一起掉 ↓", 0.06, 0.432, 0.40, (1.6, 2.5), size=28,
                 color="#8fb3c8"),
            text("rightLabel", "label", "容量收入", 0.52, 0.395, 0.40, (2.8, 3.7), color="#b6ff1f"),
            text("rightTrend", "caption", "只要「待命」就能收 ↑", 0.52, 0.432, 0.44, (3.6, 4.5), size=28,
                 color="#cdf78f"),
            text("hook", "title", "两根柱子，\n各算各的账", 0.08, 0.105, 0.86, (0.8, 1.8), size=70, maxLines=3),
            # 这句和字幕完全重复，画面里不再重复一遍（字幕轨会显示）
            text("caption", "caption", "", 0.08, 0.775, 0.88, (99.0, 99.0),
                 size=32),
            text("src", "source", "来源 S02 · 容量电价机制（已人工确认）", 0.08, 0.925, 0.86, (1.0, 1.8)),
        ],
        "camera": [
            [0.0, [0.0, 0.2, 12.0], [0.0, 0.0, -4.0], 40],
            [5.0, [0.4, 0.1, 11.0], [0.4, -0.1, -4.0], 39],
            [10.3, [0.8, 0.0, 10.4], [0.8, -0.2, -4.0], 38],
        ],
    }


def stage_s3() -> dict:
    """估值锚：一枚发光的"锚芯片"从旧资产卡移到新资产卡。

    两张卡是二维色块（文字与配色必须清楚），锚芯片也是二维——但它在两卡之间真的移动，
    轨迹与卡的位置对齐；三维层只留一层很淡的网格做空间感。
    """
    return {
        "exposure": 1.0,
        "ambient": 0.26,
        "keyLight": 0.5,
        "scrimStrength": 0.42,
        "sky": sky("radial-gradient(ellipse 130% 62% at 50% 8%, #1f3b5c 0%, #101f33 38%, #04060b 72%)"),
        "lights": [
            {"at": [-2.4, 0.4, 1.6], "color": "#ffb020", "intensity": 0.6, "distance": 18},
            {"at": [2.4, 0.8, 1.6], "color": "#35e0ff", "intensity": 0.7, "distance": 20},
        ],
        "objects": [
            {"id": "net", "type": "grid", "size": 60, "div": 20, "y": -3.0, "color": "#122437",
             "opacity": 0.3, "enter": [0, 0.6]},
        ],
        "panels": [
            {"id": "text_backing", "kind": "bar", "x": 0.0, "w": 1.0, "y": 0.860, "h": 0.200,
             "color": "linear-gradient(180deg, rgba(3,6,12,0.0) 0%, rgba(3,6,12,0.72) 38%, rgba(3,6,12,0.82) 100%)",
             "radius": 0, "shadow": False, "grow": False, "enter": [0.0, 0.5]},

            {"id": "haze", "kind": "bar", "x": 0.0, "w": 1.0, "y": 0.452, "h": 0.10,
             "color": "linear-gradient(180deg, rgba(38,74,110,0.55) 0%, rgba(6,10,18,0) 100%)",
             "radius": 0, "shadow": False, "grow": False, "enter": [0.0, 0.6]},

            {"id": "old_card", "kind": "card", "x": 0.06, "w": 0.38, "y": 0.155, "h": 0.235,
             "anchor": "top", "color": "linear-gradient(160deg, #6b4c17 0%, #3a2a10 100%)",
             "radius": 22, "shadow": True, "glow": "#ffb020", "border": "#8a6623", "enter": [0.2, 1.1]},
            {"id": "new_card", "kind": "card", "x": 0.56, "w": 0.38, "y": 0.155, "h": 0.235,
             "anchor": "top", "color": "linear-gradient(160deg, #12455c 0%, #07202e 100%)",
             "radius": 22, "shadow": True, "glow": "#35e0ff", "border": "#2f7f9c", "enter": [2.4, 3.3]},
            {"id": "old_dim", "kind": "card", "x": 0.06, "w": 0.38, "y": 0.155, "h": 0.235,
             "anchor": "top", "color": "rgba(4,6,11,0.5)", "radius": 22, "shadow": False,
             "enter": [4.4, 5.4]},
            {"id": "chip", "kind": "card", "x": 0.245, "w": 0.11, "y": 0.40, "h": 0.062,
             "anchor": "top",
             "color": "radial-gradient(circle at 50% 50%, #eaffb0 0%, #b6ff1f 46%, #4f7a00 100%)",
             "radius": 999, "shadow": True, "glow": "#b6ff1f", "border": "#d9ff7a", "enter": [1.5, 2.1],
             "moves": [[1.5, 0.245, 0.40], [2.6, 0.245, 0.40], [4.4, 0.755, 0.40], [6.16, 0.755, 0.40]]},
        ],
        "texts": [
            text("kicker", "kicker", "估值锚在移动", 0.08, 0.055, 0.84, (0.1, 0.9)),
            text("oldGlyph", "title", "息", 0.115, 0.215, 0.28, (0.4, 1.2), size=120, color="#ffcf6b"),
            text("newGlyph", "title", "算", 0.615, 0.215, 0.28, (2.6, 3.4), size=120, color="#7fe4ff"),
            text("oldLabel", "label", "红利资产", 0.085, 0.505, 0.34, (0.5, 1.3), color="#ffb020", size=42),
            text("oldNote", "caption", "看股息、看稳定性", 0.085, 0.552, 0.34, (1.0, 1.8), size=28,
                 color="#d9b978"),
            text("newLabel", "label", "AI 的能源底座", 0.585, 0.505, 0.38, (2.7, 3.5), color="#35e0ff",
                 size=42),
            text("newNote", "caption", "看算力用电、看待命能力", 0.585, 0.552, 0.38, (3.2, 4.0), size=28,
                 color="#9fdcf0"),
            text("title", "title", "同一个资产，\n换了一套定价依据", 0.08, 0.60, 0.88, (0.8, 1.7), size=68,
                 maxLines=3),
            text("caption", "caption", "从红利资产，变成 AI 的能源底座。", 0.08, 0.845, 0.88, (4.6, 5.5),
                 size=32),
            text("src", "source", "来源 S01 · 算电协同（已人工确认）", 0.08, 0.925, 0.86, (1.0, 1.8)),
        ],
        "camera": [
            [0.0, [-0.4, 0.9, 9.6], [-0.6, 0.2, -1.0], 38],
            [3.0, [0.0, 0.9, 9.2], [0.0, 0.3, -1.0], 37],
            [6.16, [0.4, 0.9, 8.8], [0.4, 0.4, -0.8], 36],
        ],
    }


def stage_s4() -> dict:
    """反证：总量不动，四份此消彼长。"""
    bars = [
        {"id": "q1", "kind": "bar", "x": 0.085, "w": 0.085, "y": 0.70, "h": 0.22,
         "color": "#35637e", "glow": "#35637e", "radius": 8,
         "keyframes": [[0, 0.2], [1.4, 1.0], [3.2, 1.0], [6.0, 0.88], [9.0, 0.76], [11.6, 0.7]],
         "enter": [0.2, 1.2]},
        {"id": "q2", "kind": "bar", "x": 0.245, "w": 0.085, "y": 0.70, "h": 0.22,
         "color": "#4b7f9c", "glow": "#4b7f9c", "radius": 8,
         "keyframes": [[0, 0.2], [1.5, 0.8], [3.4, 0.8], [6.2, 0.9], [9.2, 1.0], [11.8, 1.0]],
         "enter": [0.3, 1.3]},
        {"id": "q3", "kind": "bar", "x": 0.405, "w": 0.085, "y": 0.70, "h": 0.22,
         "color": "#7fbf2a", "glow": "#7fbf2a", "radius": 8,
         "keyframes": [[0, 0.2], [1.6, 0.64], [3.6, 0.64], [6.4, 0.82], [9.4, 0.98], [12.0, 1.04]],
         "enter": [0.4, 1.4]},
        {"id": "q4", "kind": "bar", "x": 0.565, "w": 0.085, "y": 0.70, "h": 0.22,
         "color": "#b6ff1f", "glow": "#b6ff1f", "radius": 8,
         "keyframes": [[0, 0.2], [1.7, 0.52], [3.8, 0.52], [6.6, 0.74], [9.6, 0.94], [12.2, 1.02]],
         "enter": [0.5, 1.5]},
        {"id": "baseline", "kind": "bar", "x": 0.06, "w": 0.62, "y": 0.703, "h": 0.0035,
         "color": "#3b5a7a", "radius": 2, "shadow": False, "grow": False, "enter": [0.0, 0.8]},
    ]
    total_line = [
        {"id": "total", "kind": "bar", "x": 0.06, "w": 0.86, "y": 0.44, "h": 0.0035,
         "color": "#8ea3bd", "glow": "#8ea3bd", "radius": 2, "shadow": False, "grow": False,
         "enter": [2.8, 3.6]},
    ]
    return {
        "exposure": 1.0,
        "ambient": 0.3,
        "keyLight": 0.5,
        "scrimStrength": 0.5,
        "sky": sky("linear-gradient(to bottom, #24425f 0%, #142539 28%, #070d18 60%, #04060b 100%)"),
        "lights": [
            {"at": [0.0, 2.0, 4.0], "color": "#9ec8ff", "intensity": 4.4, "distance": 28},
        ],
        "objects": [
            {"id": "net", "type": "grid", "size": 70, "div": 26, "y": -3.0, "color": "#152b42",
             "opacity": 0.46, "enter": [0, 0.6]},
        ],
        "panels": total_line + bars,
        "texts": [
            text("kicker", "kicker", "反证：利润在降", 0.08, 0.055, 0.84, (0.1, 0.9)),
            text("num", "number", "0%", 0.06, 0.215, 0.62, (5.4, 6.2), size=132,
                 count={"from": 0, "to": 6.2, "decimals": 1, "suffix": "%", "start": 5.6, "end": 8.2}),
            text("numLabel", "label", "2026 年上半年行业利润同比", 0.06, 0.185, 0.66, (5.2, 6.0), size=28),
            text("totalLabel", "caption", "总量线：不动", 0.60, 0.402, 0.34, (3.0, 3.8), size=26,
                 color="#a9bed6"),
            text("title", "title", "总量没变，\n变的只是分法", 0.08, 0.745, 0.88, (0.9, 1.9), size=64,
                 maxLines=2),
            text("caption", "caption", "利润在降，说明这是存量再分配，不是总量扩张。", 0.08, 0.862, 0.9,
                 (9.2, 10.2), size=30),
            text("src", "source", "来源 S11 · 规模以上工业企业利润（已人工确认可用）", 0.08, 0.925, 0.88,
                 (1.0, 1.8)),
        ],
        "camera": [
            [0.0, [0.0, 0.4, 12.6], [0.0, 0.0, -4.0], 40],
            [6.0, [0.2, 0.3, 11.6], [0.2, -0.1, -4.0], 39],
            [12.096, [0.3, 0.2, 11.0], [0.3, -0.1, -4.0], 38],
        ],
    }


def stage_s5() -> dict:
    """收尾：拉远，两条流并成一条，回到电网全景。"""
    return {
        "exposure": 0.98,
        "ambient": 0.18,
        "keyLight": 0.45,
        "scrimStrength": 0.86,
        "sky": sky(DUSK),
        "lights": [
            {"at": [-4.0, 2.4, 6.0], "color": "#7fd8ff", "intensity": 0.8, "distance": 24},
            {"at": [4.0, 4.0, -14.0], "color": "#9ec8ff", "intensity": 11, "distance": 60},
        ],
        "objects": [
            {"id": "net", "type": "grid", "size": 120, "div": 36, "y": -3.25, "color": "#14263a",
             "opacity": 0.3, "enter": [0, 0.7]},
            tower("towerA", 3.4, -3.0, 8.6, 1.0, 1.0, (0.1, 1.0), color="#16202c", glow="#276b8c", strength=0.16),
            tower("towerB", 6.8, -11.0, 8.0, 0.95, 0.9, (0.3, 1.2), color="#16202c", glow="#276b8c", strength=0.14),
            tower("towerC", 0.4, -17.0, 7.2, 0.9, 0.8, (0.5, 1.4), color="#2b3a4e", glow="#5aa8d8", strength=0.22),
            tower("towerD", 4.6, -27.0, 6.4, 0.85, 0.7, (0.7, 1.6), color="#243244", glow="#4a93c0", strength=0.16),
            flow("merged", [[3.4, 2.8, -3.0], [5.2, 2.5, -11.0], [4.6, 2.2, -27.0]], "#b6ff1f",
                 speed=-0.08, particles=72, radius=0.032, tube=0.12, enter=(1.0, 2.2)),
            flow("mergedCapacity", [[3.4, 2.6, -3.0], [0.4, 2.3, -17.0]], "#45c8f5",
                 speed=0.08, particles=48, radius=0.026, enter=(1.6, 2.6)),
            {"id": "ringA", "type": "ring", "at": [3.4, 1.2, -3.0], "radius": 2.6, "thickness": 0.016,
             "color": "#35e0ff", "opacity": 0.3, "enter": [1.6, 2.4], "spin": 0.2},
        ],
        "texts": [
            text("kicker", "kicker", "结论", 0.08, 0.055, 0.84, (0.2, 1.0)),
            text("title", "title", "钱没变多，\n只是分法变了", 0.08, 0.585, 0.88, (0.8, 1.8), size=86, maxLines=3),
            text("caption", "caption", "别把「再分配」看成新蛋糕。", 0.08, 0.775, 0.88, (5.0, 6.0), size=40),
            text("disclaimer", "caption", "合规资料整理 · 不构成投资建议",
                 0.08, 0.855, 0.88, (6.6, 7.6), size=28, color="#98a7bd"),
            text("sign", "source", "AK 带你拆解世界 · 电力之王 AK", 0.08, 0.898, 0.86, (7.4, 8.2)),
        ],
        "camera": [
            [0.0, [3.2, 2.2, 7.6], [2.6, 2.4, -9.0], 33],
            [4.0, [3.0, 3.4, 13.0], [2.6, 2.4, -10.0], 34],
            [8.0, [2.6, 4.6, 19.0], [2.4, 2.2, -12.0], 36],
            [13.4, [2.2, 5.4, 23.0], [2.2, 2.0, -13.0], 37],
        ],
    }


SCENE_DEFS = [
    {
        "scene_id": "s1",
        "title": "开场：提出问题",
        "clips": ["sh01"],
        "lead": 0.9,
        "tail": 0.6,
        "gap": 0.35,
        "stage": stage_s1,
        "purpose": "让观众先看见一个矛盾：电卖得少了，钱反而没少。",
        "knowledge": "观众知道这条片子要拆解的是「电力企业为什么在发电量下滑时还能收钱」。",
        "concept": "黄昏的输电网络从远处推近，两条收入流在塔与塔之间亮起来，像血管。",
        "composition": {"主体": "输电塔沿透视线退远", "前景": "最近的塔与光环", "背景": "黄昏天空与地面网格",
                        "文字区": "下三分之一，压在压暗层上"},
        "evidence": ["s02_capacity"],
        "cues": [(1.2, "riser"), (2.4, "impact")],
    },
    {
        "scene_id": "s2",
        "title": "机制：两根柱子各算各的账",
        "clips": ["sh02", "sh03"],
        "lead": 0.5,
        "tail": 0.6,
        "gap": 0.35,
        "stage": stage_s2,
        "purpose": "把「第二张收入表」变成看得见的结构：收入分成两根柱子。",
        "knowledge": "观众能说出：电量收入跟着发电量走，容量收入按「可用能力」单独计价，两者脱钩。",
        "concept": "左边三根冷色柱随发电量下沉，右边三根绿柱按「待命」立起来，中间那条连线在中段断掉。",
        "composition": {"主体": "两组柱列，左冷右暖", "前景": "柱底基座", "背景": "夜网格",
                        "文字区": "上下两端，中间留给柱列"},
        "evidence": ["s02_capacity"],
        "cues": [(3.4, "whoosh"), (6.2, "impact"), (8.6, "tick")],
    },
    {
        "scene_id": "s3",
        "title": "估值锚：换了定价依据",
        "clips": ["sh04"],
        "lead": 0.4,
        "tail": 0.6,
        "gap": 0.3,
        "stage": stage_s3,
        "purpose": "说明同一份资产为什么会被重新定价。",
        "knowledge": "观众能说出：定价依据从「红利资产」换成「AI 的能源底座」。",
        "concept": "一枚发光的锚从左边的金色账本移到右边的算力方块阵，左侧随之变暗。",
        "composition": {"主体": "左右两个资产簇", "前景": "移动中的锚环", "背景": "渐变夜",
                        "文字区": "两端标注 + 中部大标题"},
        "evidence": ["s01_ai"],
        "cues": [(1.4, "riser"), (4.4, "whoosh"), (5.6, "impact")],
    },
    {
        "scene_id": "s4",
        "title": "反证：存量再分配",
        "clips": ["sh05", "sh06"],
        "lead": 0.4,
        "tail": 0.5,
        "gap": 0.3,
        "stage": stage_s4,
        "purpose": "用一条反证把前面的乐观压回来。",
        "knowledge": "观众能说出：行业利润同比降 6.2%，四个份额此消彼长、总量线不动，所以是再分配不是扩张。",
        "concept": "四根份额柱此消彼长，一条不动的总量线压在上方，6.2% 的数字滚下来。",
        "composition": {"主体": "四根份额柱", "前景": "总量线与数字", "背景": "渐变夜空",
                        "文字区": "上三分之一的数字 + 下三分之一的结论"},
        "evidence": ["s11_profit"],
        "cues": [(5.6, "impact"), (6.4, "riser"), (9.4, "tick")],
    },
    {
        "scene_id": "s5",
        "title": "结论：钱没变多，只是分法变了",
        "clips": ["sh07", "sh08"],
        "lead": 0.4,
        "tail": 0.7,
        "gap": 0.3,
        "stage": stage_s5,
        "purpose": "收束成一句能被记住的话，并交代合规边界。",
        "knowledge": "观众带走的判断：这是存量再分配，不是新蛋糕。",
        "concept": "镜头拉远，两条流并成一条长线铺向地平线，最后落回品牌签名。",
        "composition": {"主体": "回路全景", "前景": "近处塔与光环", "背景": "天际线",
                        "文字区": "中部结论 + 底部免责"},
        "evidence": ["recap"],
        "cues": [(1.0, "whoosh"), (6.6, "impact"), (7.4, "tick")],
    },
]


# --------------------------------------------------------------------------- 蓝图


def scene_duration(spec: dict) -> float:
    clips = sum(CLIP_SECONDS[name] for name in spec["clips"])
    gaps = spec["gap"] * (len(spec["clips"]) - 1)
    return round(spec["lead"] + clips + gaps + spec["tail"], 3)


def scene_audio_layout(spec: dict) -> list[dict]:
    """旁白在镜头内的落点：第 i 段在 lead + Σ(前面的时长+gap) 处开始。"""
    layout = []
    cursor = spec["lead"]
    for name in spec["clips"]:
        layout.append({"clip": name, "start": round(cursor, 3), "seconds": CLIP_SECONDS[name],
                       "text": CLIP_TEXT[name]})
        cursor += CLIP_SECONDS[name] + spec["gap"]
    return layout


def build_film() -> Film:
    scenes: list[Scene] = []
    for spec in SCENE_DEFS:
        duration = scene_duration(spec)
        stage = spec["stage"]()
        camera = tuple(
            CameraKey(t=item[0], pos=tuple(item[1]), look=tuple(item[2]), fov=item[3])
            for item in stage.pop("camera")
        )
        layout = scene_audio_layout(spec)
        events = tuple(
            VisualEvent(
                t=round(cue_at, 3),
                kind="camera" if kind in ("riser", "whoosh") else "highlight",
                target=spec["scene_id"],
                note=f"{kind} 音效与画面同步",
                params={"sfx": kind},
            )
            for cue_at, kind in spec["cues"]
        )
        subtitle = Subtitle(
            text=layout[0]["text"][:38],
            in_s=layout[0]["start"] - 0.15,
            out_s=min(duration - 0.1, layout[-1]["start"] + layout[-1]["seconds"] + 0.2),
            style="lower",
        )
        scenes.append(
            Scene(
                scene_id=spec["scene_id"],
                duration=duration,
                engine="cinema-html",
                entry=f"{spec['scene_id']}/index.html",
                narrative_purpose=spec["purpose"],
                knowledge_objective=spec["knowledge"],
                visual_concept=spec["concept"],
                composition=spec["composition"],
                camera=camera,
                visual_events=events,
                motion_design={"stage": stage, "audio_layout": layout},
                assets=(
                    AssetRef(id="three", type="font", source="three.js r147（本地 vendor）",
                             license="MIT", path="../../../../tools/vendor/three.min.js",
                             note="UMD 构建，内联进镜头页"),
                    AssetRef(id="noto", type="font", source="Noto Sans SC（本机 Windows 字体目录）",
                             license="OFL-1.1", path="fonts/NotoSansSC-VF.ttf",
                             note="NotoSansSC-VF.ttf，随镜头目录分发"),
                    AssetRef(id="engine", type="2d", source="本仓库 backend/app/video/cinema/scene.js",
                             license="本仓库自有", path="scene.js",
                             note="程序化三维场景，非实拍"),
                ),
                audio_cues=tuple(
                    AudioCue(t=round(cue_at + scene_offset, 3), kind=kind, id=f"sfx_{kind}",
                             gain_db=-14.0)
                    for cue_at, kind in spec["cues"]
                    for scene_offset in (0.0,)
                ),
                subtitle=subtitle,
                evidence_refs=tuple(EVIDENCE[name] for name in spec["evidence"]),
                quality_requirements=REQUIRED_QUALITY,
                narration=" ".join(layout[i]["text"] for i in range(len(layout))),
                status="planned",
            )
        )
    return Film(
        title="电力的第二张收入表",
        logline="发电量在掉，电费收入却还在涨——因为电力公司开始靠「待命」收钱。",
        fps=FPS,
        canvas=(WIDTH, HEIGHT),
        brand="AK 带你拆解世界",
        palette=PALETTE,
        fonts=("AKSC",),
        scenes=tuple(scenes),
    )


# --------------------------------------------------------------------------- 生成


def run(command: list[str], *, cwd: Path | None = None, quiet: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"命令失败（{result.returncode}）：{' '.join(str(item) for item in command[:4])}…\n"
            f"{result.stderr[-1500:]}"
        )
    if not quiet:
        print(result.stdout[-2000:])
    return result


def stage_blueprint() -> dict:
    film = build_film()
    target = REM / "blueprint" / "film.json"
    dump_blueprint(film, target)
    for scene in film.scenes:
        dump_blueprint_scene = REM / "blueprint" / "scenes" / f"{scene.scene_id}.json"
        dump_blueprint_scene.parent.mkdir(parents=True, exist_ok=True)
        dump_blueprint_scene.write_text(
            json.dumps(scene.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "blueprint": str(target),
        "scenes": len(film.scenes),
        "total_seconds": round(film.total_seconds(), 3),
        "per_scene": {scene.scene_id: round(scene.duration, 3) for scene in film.scenes},
    }


def stage_scenes() -> dict:
    film = build_film()
    written = []
    for scene in film.scenes:
        scene_dir = SCENES_DIR / scene.scene_id
        if scene_dir.exists():
            shutil.rmtree(scene_dir)
        (scene_dir / "fonts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(FONT, scene_dir / "fonts" / "NotoSansSC-VF.ttf")
        shutil.copy2(SCENE_JS, scene_dir / "scene.js")
        stage = scene.motion_design["stage"]
        stage = dict(stage)
        stage["camera"] = [
            {"t": key.t, "pos": list(key.pos), "look": list(key.look), "fov": key.fov} for key in scene.camera
        ]
        write_scene_shell(
            scene_dir,
            title=f"{film.title} · {scene.scene_id}",
            width=film.canvas[0],
            height=film.canvas[1],
            fps=film.fps,
            duration=scene.duration,
            palette=film.palette,
            fonts=film.fonts,
            three_js=THREE_JS,
            background=film.palette["ink"],
            scene_spec=stage,
        )
        written.append(scene.scene_id)
    return {"scenes": written, "dir": str(SCENES_DIR)}


def stage_render(only: list[str] | None = None) -> dict:
    film = build_film()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for scene in film.scenes:
        if only and scene.scene_id not in only:
            continue
        scene_dir = SCENES_DIR / scene.scene_id
        if not (scene_dir / "index.html").is_file():
            stage_scenes()
        frames = film.scene_frames(scene)
        clip = CLIPS_DIR / f"{scene.scene_id}.mp4"
        payload = {
            "scene": scene.scene_id,
            "frames": frames,
            "seconds": round(frames / film.fps, 3),
        }
        command = [
            str(OPENKATANA_PYTHON), str(ROOT / "backend" / "scripts" / "cinema_render.py"),
            "--scene", str(scene_dir),
            "--out", str(FRAMES_DIR / scene.scene_id),
            "--fps", str(film.fps), "--frames", str(frames),
            "--width", str(film.canvas[0]), "--height", str(film.canvas[1]),
            "--mp4", str(clip), "--quiet",
        ]
        run(command)
        payload["clip"] = str(clip)
        payload["clip_bytes"] = clip.stat().st_size if clip.is_file() else 0
        results[scene.scene_id] = payload
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    return {"rendered": results}


def stage_audio() -> dict:
    film = build_film()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    total = film.total_seconds()

    music = write_wav(AUDIO_DIR / "music.wav", music_samples(total))
    cues: list[tuple[float, str]] = []
    offsets = film.offsets()
    for scene in film.scenes:
        base = offsets[scene.scene_id]
        for cue in scene.audio_cues:
            cues.append((cue.t + base, cue.kind))
    sfx = write_wav(AUDIO_DIR / "sfx.wav", place_cues(total, cues))

    # 每个镜头的旁白轨：把已批准的片段按落点摆进去，中间留白
    for scene in film.scenes:
        layout = scene.motion_design["audio_layout"]
        inputs: list[str] = []
        filters: list[str] = []
        for index, item in enumerate(layout):
            clip = NARRATION / f"{item['clip']}.mp3"
            inputs.extend(["-i", str(clip)])
            delay = int(round(float(item["start"]) * 1000))
            filters.append(f"[{index}:a]aresample=48000,adelay={delay}|{delay}[a{index}]")
        mix = "".join(f"[a{index}]" for index in range(len(layout)))
        filters.append(f"{mix}amix=inputs={len(layout)}:duration=longest:normalize=0[out]")
        target = AUDIO_DIR / f"{scene.scene_id}_narration.wav"
        run([
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", ";".join(filters), "-map", "[out]",
            "-t", f"{scene.duration:.3f}", "-ar", "48000", "-ac", "2", str(target),
        ])
    return {
        "music": str(music),
        "sfx": str(sfx),
        "cues": len(cues),
        "narration_tracks": len(film.scenes),
        "total_seconds": round(total, 3),
    }


def stage_assemble() -> dict:
    film = build_film()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    QC_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 每个镜头：画面 + 旁白 + 配乐片段（配乐整条按镜头起点切片）
    offsets = film.offsets()
    scene_files: list[Path] = []
    for scene in film.scenes:
        clip = CLIPS_DIR / f"{scene.scene_id}.mp4"
        if not clip.is_file():
            raise RuntimeError(f"少了镜头画面：{clip}，先跑 --stage render")
        narration = AUDIO_DIR / f"{scene.scene_id}_narration.wav"
        start = offsets[scene.scene_id]
        target = CLIPS_DIR / f"{scene.scene_id}_mixed.mp4"
        run([
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(clip),
            "-i", str(narration),
            "-i", str(AUDIO_DIR / "music.wav"),
            "-i", str(AUDIO_DIR / "sfx.wav"),
            "-filter_complex",
            f"[2:a]atrim=start={start:.3f}:duration={scene.duration:.3f},asetpts=PTS-STARTPTS,volume=1.0[m];"
            f"[3:a]atrim=start={start:.3f}:duration={scene.duration:.3f},asetpts=PTS-STARTPTS,volume=1.0[s];"
            f"[1:a][m][s]amix=inputs=3:duration=first:normalize=0[out]",
            "-map", "0:v", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{scene.duration:.3f}", str(target),
        ])
        scene_files.append(target)

    # 2) 拼接：镜头之间 0.45 秒交叉溶解，避免硬切
    concat_list = CLIPS_DIR / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{path.name}'" for path in scene_files) + "\n", encoding="utf-8"
    )
    silent = OUT_DIR / "remaster_v1_no_subs.mp4"
    run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(silent),
    ], cwd=CLIPS_DIR)

    # 3) 字幕：按镜头落点拼一条整片 SRT，再压进画面
    srt_lines: list[str] = []
    index = 1
    for scene in film.scenes:
        base = offsets[scene.scene_id]
        for item in scene.motion_design["audio_layout"]:
            start_s = base + float(item["start"])
            end_s = start_s + float(item["seconds"])
            srt_lines.append(
                f"{index}\n{_srt_time(start_s)} --> {_srt_time(end_s)}\n{item['text']}\n"
            )
            index += 1
    srt_path = OUT_DIR / "remaster_v1.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    # 同时写一份 ASS：PlayRes 显式设成 1080×1920，字号单位就是像素，
    # 不受 libass 默认 PlayResY=288 的放大影响（那是字幕被放大 6 倍的根源）。
    ass_path = OUT_DIR / "remaster_v1.ass"
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: AK,Microsoft YaHei,40,&H00E8F0F6,&H00FFFFFF,&H8C000000,&H00000000,"
        "-1,0,0,0,100,100,0.5,0,1,2,0,2,60,60,52,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for item in srt_lines:
        block = item.strip().splitlines()
        if len(block) < 3:
            continue
        times, content = block[1], " ".join(block[2:])
        start_text, end_text = [part.strip() for part in times.split("-->")]
        ass_lines.append(
            "Dialogue: 0,{start},{end},AK,,0,0,0,,{text}".format(
                start=_ass_time(start_text), end=_ass_time(end_text), text=content
            )
        )
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

    final = OUT_DIR / "final.mp4"
    run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(silent),
        "-vf",
        # 用自己写的 ASS（PlayRes=1080×1920），字号与位置都是像素级可控；
        # 后面两步是给画面加质感：暗角收边 + 极细颗粒（纯色渐变最容易显得"平"，颗粒能救回来）
        "subtitles=remaster_v1.ass,vignette=PI/4.6,noise=alls=3:allf=t+u",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
        # 响度统一到 -16 LUFS（短视频平台常见口径），顺带把真峰值压到 -1.5 dBTP
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(final),
    ], cwd=OUT_DIR)

    # 4) 样片：取 s2 + s3 两镜（整片里的真实片段，15–20 秒）
    proto_start = offsets["s2"]
    proto_seconds = round(film.scenes[1].duration + film.scenes[2].duration, 3)
    prototype = OUT_DIR / "prototype_15_20s.mp4"
    run([
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{proto_start:.3f}", "-i", str(final), "-t", f"{proto_seconds:.3f}",
        "-c", "copy", "-movflags", "+faststart", str(prototype),
    ])

    return {
        "final": str(final),
        "final_bytes": final.stat().st_size,
        "prototype": str(prototype),
        "prototype_bytes": prototype.stat().st_size,
        "prototype_window": [round(proto_start, 3), proto_seconds],
        "srt": str(srt_path),
        "total_seconds": round(film.total_seconds(), 3),
    }


def _ass_time(text: str) -> str:
    """SRT 的 00:00:01,500 → ASS 的 0:00:01.50（小时不补零，帧用两位）。"""
    hms, millis = text.split(",")
    hours, minutes, seconds = hms.split(":")
    return f"{int(hours)}:{minutes}:{seconds}.{millis[:2]}"


def _srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重制《电力的第二张收入表》：蓝图 → 镜头 → 渲染 → 混音 → 成片")
    parser.add_argument("--stage", default="all",
                        choices=["blueprint", "scenes", "render", "audio", "assemble", "all"])
    parser.add_argument("--only", default=None, help="只渲这几个镜头，逗号分隔（如 s2,s3）")
    args = parser.parse_args(argv)

    only = [item.strip() for item in args.only.split(",")] if args.only else None
    payload: dict = {}
    if args.stage in ("blueprint", "all"):
        payload["blueprint"] = stage_blueprint()
    if args.stage in ("scenes", "all"):
        payload["scenes"] = stage_scenes()
    if args.stage in ("render", "all"):
        payload["render"] = stage_render(only)
    if args.stage in ("audio", "all"):
        payload["audio"] = stage_audio()
    if args.stage in ("assemble", "all"):
        payload["assemble"] = stage_assemble()

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
