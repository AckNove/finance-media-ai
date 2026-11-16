"""电影感画面引擎（cinema-html）：浏览器 WebGL + DOM 逐帧渲染。

这一层只做一件事：把**视觉制作蓝图**里的一镜，渲成一串图片，再交给 ffmpeg。
它不认识研究报告、不认识脚本表，只认识 ``blueprint.Scene``——这是"内容/视觉/引擎"
三层分离的落点。

为什么是浏览器而不是三维软件：Blender 是 GB 级依赖、渲染一张要几十秒；
Chromium 本机就有、three.js 走本地 vendor、一帧 60–120ms。对"每秒都要有信息变化"
的知识动画，浏览器足够，而且**逐帧确定性**可以拿捏（没有 CSS 动画、没有
requestAnimationFrame 补间，一切由 t 算出来）。

    blueprint.py  结构化镜头设计（内容 → 视觉 → 引擎的唯一接口）
    page.py       镜头页外壳与运行时契约（__AK / AKScene / __ready / __seek）
    renderer.py   Playwright 起 Chromium、逐帧截图、ffmpeg 编码
    audio.py      程序化原创配乐与音效 + 与旁白混音

实际渲染走 ``backend/scripts/cinema_render.py``（只依赖标准库 + Playwright，
可以用 ``tools/openkatana/.venv`` 的解释器跑）。
"""

from __future__ import annotations

from .blueprint import (
    ASSET_TYPES,
    ENGINES,
    EVENT_KINDS,
    REQUIRED_QUALITY,
    AssetRef,
    AudioCue,
    BlueprintError,
    CameraKey,
    EvidenceRef,
    Film,
    Scene,
    Subtitle,
    VisualEvent,
    dump_blueprint,
    load_blueprint,
)
from .page import build_page, write_scene_shell

__all__ = [
    "ASSET_TYPES",
    "ENGINES",
    "EVENT_KINDS",
    "REQUIRED_QUALITY",
    "AssetRef",
    "AudioCue",
    "BlueprintError",
    "CameraKey",
    "EvidenceRef",
    "Film",
    "Scene",
    "Subtitle",
    "VisualEvent",
    "build_page",
    "dump_blueprint",
    "load_blueprint",
    "write_scene_shell",
]
