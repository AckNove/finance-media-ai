"""真实拍摄素材镜头：把一张登记过的实拍照片变成可逐帧渲染的镜头页。

为什么单独写一个模块，而不是把图片塞进 OpenKatana 组合：
OpenKatana 的组合只认它自己的节点类型与样式白名单，图片得先 import 进工程；
而我们已经有 `cinema_render.py` 这条"Chromium 逐帧截图"的渲染链，直接产出一个
自包含 HTML 更简单，也更容易让摄影机运动（推、移、摇）做到帧级确定。

硬规矩（都是为了"不骗人"）：
1. 只接受**登记过**的素材：`source_register.csv` 里 `commercial_use=allowed` 才算数；
2. 画面上必须能读出这帧是资料画面：`credit`（作者 + 授权）与 `label`（"资料画面"）由参数强制传入；
3. 所有运动都是 t 的纯函数，没有随机、没有 CSS 动画，倒着渲染结果一致。

用法见 `build_photo_scene()`。
"""

from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

FPS = 30
FONT_SRC = "fonts/NotoSansSC-VF.ttf"


class PhotoSceneError(RuntimeError):
    """素材没登记、授权不允许、文件不存在——都不能"先渲了再说"。"""


@dataclass(frozen=True)
class PhotoMove:
    """摄影机运动：从 (z0, x0, y0) 缓动到 (z1, x1, y1)。x/y 是画幅宽高的比例偏移。"""

    z0: float = 1.06
    z1: float = 1.16
    x0: float = 0.0
    x1: float = 0.0
    y0: float = 0.0
    y1: float = 0.0
    ease: str = "smooth"


@dataclass(frozen=True)
class PhotoText:
    """画面上的文字层。role 决定字号与位置，与 scene.js 的角色表保持一致。"""

    role: str
    text: str
    y: float
    at: float = 0.0
    size: int | None = None
    color: str = "#f2f5f7"
    align: str = "left"


@dataclass(frozen=True)
class PhotoOverlay:
    """压在实拍画面上的数据层。

    为什么要有它：任务书要求"真实场景里展示数据"，而不是另起一张图表页。
    所以数据以半透明色块 + 标签的形式长在真实画面上——下面还是变电站本体，
    观众看到的是"这套设备的收入由两条线组成"，不是"一张柱状图"。

    kind="bar" 从底线往上长；kind="rule" 是一条水平基准线；kind="chip" 是一枚小胶囊标签。
    x/y/w/h 都是画面宽高的比例；at 是出现时间（秒），grow 是生长时长（秒）。
    """

    id: str
    kind: str
    x: float
    y: float
    w: float
    h: float
    color: str = "#b6ff1f"
    label: str = ""
    note: str = ""
    at: float = 0.6
    grow: float = 0.9
    label_side: str = "above"  # above | below | inside
    label_y: float | None = None  # 显式指定标签所在高度比例（None 表示贴着柱子顶）
    label_color: str | None = None  # 标签文字色（默认跟柱子同色，深底上容易读不清）


@dataclass(frozen=True)
class PhotoScene:
    scene_id: str
    asset_id: str
    photo_rel: str  # 相对镜头目录的图片路径
    duration: float
    move: PhotoMove = field(default_factory=PhotoMove)
    texts: tuple[PhotoText, ...] = ()
    overlays: tuple[PhotoOverlay, ...] = ()
    label: str = "资料画面"
    credit: str = ""
    grade: str = "cinema"
    # 视频镜头：photo_rel 指向预先裁好的 mp4；video_start 是源片段里的起始秒，
    # video_rate 是播放倍速（小于 1 = 慢放，纪录片里常用）
    video: bool = False
    video_start: float = 0.0
    video_rate: float = 1.0
    width: int = 1080
    height: int = 1920


GRADES: dict[str, str] = {
    # 电厂的灰蓝天光：压高光、加一点对比，别让水泥发白
    "cinema": "contrast(1.12) saturate(0.86) brightness(0.94)",
    # 落日剪影：暖调推上去，暗部压住
    "dusk": "contrast(1.18) saturate(1.08) brightness(0.92) sepia(0.08)",
    # 车间/夜景：冷调、低饱和
    "industrial": "contrast(1.16) saturate(0.72) brightness(0.9) hue-rotate(-6deg)",
}

PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @font-face {{
    font-family: "AKSC";
    src: url("{font}") format("truetype");
    font-display: block;
  }}
  html, body {{ margin: 0; padding: 0; background: #05070c; }}
  #stage {{
    position: relative;
    width: {width}px;
    height: {height}px;
    overflow: hidden;
    background: #05070c;
    font-family: "AKSC", "Microsoft YaHei", sans-serif;
  }}
  #photo {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    transform-origin: 50% 50%;
    will-change: transform;
    filter: {grade};
  }}
  /* 电影感：上下压暗 + 一级调色（青蓝阴影 / 暖高光） */
  .grade-top {{
    position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(6,14,26,0.55) 0%, rgba(6,14,26,0.0) 28%);
    mix-blend-mode: multiply;
  }}
  .grade-bottom {{
    position: absolute; inset: 0;
    background: linear-gradient(0deg, rgba(4,8,16,0.92) 0%, rgba(4,8,16,0.55) 22%, rgba(4,8,16,0.0) 46%);
  }}
  .vignette {{
    position: absolute; inset: 0;
    background: radial-gradient(ellipse at 50% 46%, rgba(0,0,0,0) 42%, rgba(0,0,0,0.55) 100%);
    pointer-events: none;
  }}
  .ak-text {{
    position: absolute;
    left: 72px; right: 72px;
    color: #f2f5f7;
    line-height: 1.22;
    white-space: pre-wrap;
    text-shadow: 0 2px 18px rgba(0,0,0,0.85);
    will-change: opacity, transform;
  }}
  #label {{
    position: absolute;
    left: 40px; top: 40px;
    padding: 8px 16px;
    border: 1px solid rgba(242,245,247,0.35);
    border-radius: 4px;
    color: rgba(242,245,247,0.86);
    font-size: 24px;
    letter-spacing: 2px;
    background: rgba(4,8,16,0.45);
    opacity: 0.9;
  }}
  #credit {{
    position: absolute;
    left: 72px; bottom: 26px;
    color: rgba(242,245,247,0.5);
    font-size: 20px;
    letter-spacing: 0.4px;
  }}
  .ak-overlay {{
    position: absolute;
    pointer-events: none;
    will-change: transform, opacity;
  }}
  .ak-overlay-label {{
    position: absolute;
    white-space: nowrap;
    font-size: 28px;
    letter-spacing: 0.5px;
    padding: 4px 12px;
    border-radius: 8px;
    background: rgba(4,8,14,0.62);
    text-shadow: 0 2px 14px rgba(0,0,0,0.9);
    will-change: opacity;
  }}
</style>
</head>
<body>
<div id="stage">
  {media_tag}
  <div class="grade-top"></div>
  <div class="grade-bottom"></div>
  <div class="vignette"></div>
  <div id="label">{label}</div>
  {overlay_nodes}
  {text_nodes}
  <div id="credit">{credit}</div>
</div>
<script>
const SCENE = {scene_json};
const photo = document.getElementById("photo");
const VIDEO = photo && photo.tagName === "VIDEO" ? photo : null;
const nodes = Array.from(document.querySelectorAll(".ak-text"));
const overlays = Array.from(document.querySelectorAll(".ak-overlay"));
const overlayLabels = Array.from(document.querySelectorAll(".ak-overlay-label"));

function smooth(x) {{ return x * x * (3 - 2 * x); }}
function ease(t) {{
  if (SCENE.move.ease === "linear") return t;
  return smooth(Math.min(1, Math.max(0, t)));
}}

async function seek(t) {{
  const k = ease(SCENE.duration > 0 ? t / SCENE.duration : 1);
  const m = SCENE.move;
  const z = m.z0 + (m.z1 - m.z0) * k;
  const dx = (m.x0 + (m.x1 - m.x0) * k) * 100;
  const dy = (m.y0 + (m.y1 - m.y0) * k) * 100;
  const maxShift = Math.max(0, (z - 1) * 50 * 0.8);
  const tx = Math.max(-maxShift, Math.min(maxShift, dx));
  const ty = Math.max(-maxShift, Math.min(maxShift, dy));
  photo.style.transform = `translate(${{-tx}}%, ${{-ty}}%) scale(${{z}})`;
  if (VIDEO) {{
    // 视频按帧定位：先算目标时间，再等 seeked 事件，确保抓到的就是这一帧
    const target = SCENE.videoStart + t * SCENE.videoRate;
    await new Promise((resolve) => {{
      // 定位到位后再等两帧：否则截到的是上一帧（关键帧间隔大的片子尤其明显）
      const done = () => requestAnimationFrame(() => requestAnimationFrame(resolve));
      if (Math.abs(VIDEO.currentTime - target) < 0.002 && VIDEO.readyState >= 2) {{ done(); return; }}
      VIDEO.addEventListener("seeked", done, {{ once: true }});
      VIDEO.currentTime = Math.min(target, (VIDEO.duration || target) - 0.02);
    }});
  }}
  for (const node of nodes) {{
    const at = Number(node.dataset.at || 0);
    const span = 0.45;
    const p = Math.min(1, Math.max(0, (t - at) / span));
    node.style.opacity = String(p);
    node.style.transform = `translateY(${{(1 - p) * 18}}px)`;
  }}
  for (const node of overlays) {{
    const at = Number(node.dataset.at || 0);
    const grow = Math.max(0.05, Number(node.dataset.grow || 0.9));
    const p = Math.min(1, Math.max(0, (t - at) / grow));
    const eased = smooth(p);
    node.style.opacity = String(Math.min(1, p * 3));
    if (node.dataset.kind === "bar") {{
      node.style.transform = `scaleY(${{Math.max(0.001, eased)}})`;
    }} else {{
      node.style.transform = `scaleX(${{Math.max(0.001, eased)}})`;
    }}
  }}
  for (const node of overlayLabels) {{
    const at = Number(node.dataset.at || 0);
    const p = Math.min(1, Math.max(0, (t - at) / 0.4));
    node.style.opacity = String(p);
  }}
}}

window.__AK = {{ fps: SCENE.fps, duration: SCENE.duration, width: SCENE.width, height: SCENE.height }};
window.__seek = seek;

async function ready() {{
  if (VIDEO) {{
    await new Promise((resolve) => {{
      if (VIDEO.readyState >= 2) {{ resolve(); return; }}
      VIDEO.addEventListener("loadeddata", resolve, {{ once: true }});
    }});
  }}
  await seek(0);
  window.__ready = true;
}}
if (VIDEO) {{
  if (VIDEO.readyState >= 2) {{ ready(); }} else {{ VIDEO.addEventListener("loadeddata", ready, {{ once: true }}); }}
  VIDEO.addEventListener("error", () => {{ window.__errors = ["视频加载失败"]; window.__ready = true; }});
}} else if (photo.complete && photo.naturalWidth > 0) {{
  ready();
}} else {{
  photo.addEventListener("load", ready);
  photo.addEventListener("error", () => {{ window.__errors = ["图片加载失败"]; window.__ready = true; }});
}}
</script>
</body>
</html>
"""


def _overlay_nodes(overlays: tuple[PhotoOverlay, ...], width: int, height: int) -> str:
    parts: list[str] = []
    for item in overlays:
        left = item.x * width
        top = item.y * height
        w = item.w * width
        h = item.h * height
        if item.kind == "bar":
            style = (
                f"left:{left:.0f}px;top:{top - h:.0f}px;width:{w:.0f}px;height:{h:.0f}px;"
                f"background:{item.color};border-radius:6px 6px 0 0;"
                "transform-origin:50% 100%;transform:scaleY(0.001);"
                "box-shadow:0 0 40px rgba(182,255,31,0.25);"
            )
        elif item.kind == "tick":
            # 刻度线：细、短、从上往下落一点，用来做"量程"
            style = (
                f"left:{left:.0f}px;top:{top - h:.0f}px;width:{max(2, w * width):.0f}px;height:{h:.0f}px;"
                f"background:{item.color};opacity:0;transform-origin:50% 100%;transform:scaleY(0.001);"
            )
        elif item.kind == "rule":
            style = (
                f"left:{left:.0f}px;top:{top:.0f}px;width:{w:.0f}px;height:{max(2, h):.0f}px;"
                f"background:{item.color};transform-origin:0 50%;transform:scaleX(0.001);"
            )
        elif item.kind == "scrim":
            style = (
                f"left:{left:.0f}px;top:{top:.0f}px;width:{w:.0f}px;height:{h:.0f}px;"
                f"background:{item.color};border-radius:12px;opacity:0;"
            )
        elif item.kind == "callout":
            style = (
                f"left:{left:.0f}px;top:{top:.0f}px;width:{max(2, w * width):.0f}px;height:{max(2, h * height):.0f}px;"
                f"border-left:2px solid {item.color};opacity:0;"
            )
        else:  # chip
            style = (
                f"left:{left:.0f}px;top:{top:.0f}px;width:{w:.0f}px;height:{h:.0f}px;"
                f"background:{item.color};border-radius:999px;opacity:0;"
            )
        parts.append(
            f'<div class="ak-overlay" data-kind="{item.kind}" data-at="{item.at:.3f}" '
            f'data-grow="{item.grow:.3f}" style="{style}"></div>'
        )
        if item.label:
            if item.label_y is not None:
                label_top = item.label_y * height
            elif item.label_side == "below":
                label_top = top + 12
            elif item.label_side == "inside":
                label_top = top - h / 2 - 18
            else:
                label_top = top - h - 44
            parts.append(
                f'<div class="ak-overlay-label" data-at="{item.at + item.grow:.3f}" style="'
                f"left:{left:.0f}px;top:{label_top:.0f}px;color:{item.label_color or item.color};"
                f'opacity:0">{item.label}</div>'
            )
        if item.note:
            parts.append(
                f'<div class="ak-overlay-label" data-at="{item.at + item.grow:.3f}" style="'
                f"left:{left:.0f}px;top:{label_top + 40:.0f}px;color:rgba(242,245,247,0.72);"
                f'font-size:24px;opacity:0">{item.note}</div>'
            )
    return "\n  ".join(parts)


def _text_nodes(texts: tuple[PhotoText, ...], height: int) -> str:
    parts: list[str] = []
    for item in texts:
        size = item.size or {"kicker": 30, "title": 62, "caption": 34, "number": 168, "source": 26}[item.role]
        parts.append(
            f'<div class="ak-text" data-at="{item.at:.3f}" style="'
            f"top:{item.y * height:.0f}px;font-size:{size}px;color:{item.color};"
            f'text-align:{item.align};opacity:0">{item.text}</div>'
        )
    return "\n  ".join(parts)


def _assert_registered(register_csv: Path, asset_id: str) -> dict[str, str]:
    if not register_csv.is_file():
        raise PhotoSceneError(f"找不到素材登记表：{register_csv}")
    with register_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_id") != asset_id:
                continue
            if row.get("commercial_use") != "allowed":
                raise PhotoSceneError(
                    f"{asset_id} 的授权不允许商业使用（commercial_use={row.get('commercial_use')}），不能进成片"
                )
            return row
    raise PhotoSceneError(f"素材 {asset_id} 没有登记，不能进成片")


_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(raw: str, limit: int = 90) -> str:
    """维基的 Artist/Credit 字段是 HTML（有时还是被截断的半截标签）。

    截图专用的页面里绝不能出现未闭合标签：浏览器会把后面的 <script> 一起吞掉，
    表现是「页面能开、视频能加载，但脚本一个字都没执行」。所以先剥标签再转义。
    """
    text = raw or ""
    # 先砍掉末尾没闭合的半个标签（例如 `<a href="...`），再剥完整标签
    text = re.sub(r"<[^>]*$", "", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def build_photo_scene(
    scene: PhotoScene,
    *,
    project_root: Path,
    scene_dir: Path,
    register_csv: Path,
) -> dict:
    """写一个自包含镜头目录（index.html + 素材拷贝 + 字体），返回回执。

    实拍图片直接拷；视频镜头拷贝的是**已经裁好 9:16 的 mp4**（裁切在 build_v2_scenes 里做，
    这样页面只管播放，不用在浏览器里做几何变换，帧也稳）。
    """
    row = _assert_registered(register_csv, scene.asset_id)
    if scene.video:
        source_path = scene_dir / "clip.mp4"
        if not source_path.is_file():
            raise PhotoSceneError(f"{scene.asset_id} 的裁切片段还没生成：{source_path}")
    else:
        source_path = project_root / row["local_path"]
        if not source_path.is_file():
            raise PhotoSceneError(f"{scene.asset_id} 的文件不在了：{source_path}")

    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "fonts").mkdir(parents=True, exist_ok=True)
    target_photo = scene_dir / ("clip.mp4" if scene.video else "photo.jpg")
    if not scene.video and (not target_photo.exists() or target_photo.stat().st_size != source_path.stat().st_size):
        target_photo.write_bytes(source_path.read_bytes())

    font_src = project_root / "content" / "jobs" / "v6_power_first_film" / "fonts" / "NotoSansSC-VF.ttf"
    target_font = scene_dir / FONT_SRC
    if font_src.is_file() and not target_font.exists():
        target_font.write_bytes(font_src.read_bytes())

    publisher = _plain_text(row.get("publisher_or_subject", ""), 60)
    license_name = _plain_text(row.get("license", ""), 24)
    credit = scene.credit or f"{publisher or 'Wikimedia Commons 作者'} · {license_name}"
    credit = _plain_text(credit, 90)
    if row.get("attribution_required") == "yes" and "CC" not in credit:
        credit = f"{credit} · CC 署名"

    html = PAGE_TEMPLATE.format(
        title=scene.scene_id,
        font=FONT_SRC,
        width=scene.width,
        height=scene.height,
        grade=GRADES.get(scene.grade, GRADES["cinema"]),
        media_tag=(
            '<video id="photo" src="clip.mp4" muted playsinline preload="auto"></video>'
            if scene.video
            else '<img id="photo" src="photo.jpg" alt="">'
        ),
        label=scene.label,
        credit=credit,
        overlay_nodes=_overlay_nodes(scene.overlays, scene.width, scene.height),
        text_nodes=_text_nodes(scene.texts, scene.height),
        scene_json=json.dumps(
            {
                "fps": FPS,
                "duration": scene.duration,
                "width": scene.width,
                "height": scene.height,
                "move": {
                    "z0": scene.move.z0,
                    "z1": scene.move.z1,
                    "x0": scene.move.x0,
                    "x1": scene.move.x1,
                    "y0": scene.move.y0,
                    "y1": scene.move.y1,
                    "ease": scene.move.ease,
                },
                "videoStart": scene.video_start,
                "videoRate": scene.video_rate,
            },
            ensure_ascii=False,
        ),
    )
    (scene_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    return {
        "scene": scene.scene_id,
        "asset_id": scene.asset_id,
        "dir": str(scene_dir),
        "duration": scene.duration,
        "frames": int(round(scene.duration * FPS)),
        "credit": credit,
        "license": row.get("license", ""),
        "source_url": row.get("source_url", ""),
    }
