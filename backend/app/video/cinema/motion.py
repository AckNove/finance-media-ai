"""程序化解释动画：不依赖任何外部素材，纯 HTML/CSS/JS 逐帧渲染。

为什么单独一个模块
------------------
`photo.py` 负责"真实素材 + 数据层"，这个模块负责"实拍拍不出来的关系和机制"：
两条收入线怎么分叉、一笔钱怎么在几个主体之间流转、一个数字怎么滚上去。

三个模板（都由 `materials_plan.json` 的意图决定）
------------------------------------------------
- `bars`    ：两根（或多根）横向柱对比，柱子从 0 长出来 —— 用来讲"两种钱不一样"
- `flow`    ：三个方块自上而下 + 箭头依次出现 —— 用来讲"机制/流程/钱怎么走"
- `number`  ：一个大数字从 0 滚到目标值 —— 用来讲"降了 6.2% 这种量"

确定性要求（与 `photo.py` 一致）
--------------------------------
所有位移/缩放/透明度都是 `t` 的纯函数，禁用 CSS 动画与 `animation-delay`，
否则引擎的"正放/倒放一致性"检查会失败。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

FPS = 30
WIDTH = 1080
HEIGHT = 1920
FONT_STACK = '"Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif'

INK = "#0a1220"
PAPER = "#f2f5f7"
DIM = "#93a2b8"
ACCENT = "#b6ff1f"
COOL = "#5aa9d6"
WARM = "#ffb03a"


class MotionSceneError(RuntimeError):
    pass


@dataclass
class MotionItem:
    """一条要画的元素（柱子/方块/数字）。"""

    label: str = ""
    note: str = ""
    value: float = 0.0
    unit: str = ""
    color: str = ACCENT


@dataclass
class MotionScene:
    scene_id: str
    template: str  # bars | flow | number
    seconds: float
    kicker: str = ""
    title: str = ""
    items: tuple[MotionItem, ...] = field(default_factory=tuple)
    source: str = ""
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS


def _bars_nodes(scene: MotionScene) -> tuple[str, str]:
    rows: list[str] = []
    tracks: list[dict] = []
    count = max(1, len(scene.items))
    slot = 0.62 / count
    for index, item in enumerate(scene.items):
        top = 0.34 + slot * index
        track = {
            "kind": "bar",
            "top": round(top, 4),
            "width": 0.52,
            "color": item.color,
            "at": round(0.35 + index * 0.45, 3),
            "grow": 0.75,
        }
        tracks.append(track)
        rows.append(
            f'<div class="bar" data-at="{track["at"]}" data-grow="{track["grow"]}" '
            f'style="top:{top * 100:.1f}%;width:{track["width"] * 100:.0f}%;background:{item.color}"></div>'
            f'<div class="bar-label" data-at="{track["at"] + track["grow"] * 0.9:.3f}" '
            f'style="top:{top * 100:.1f}%">{item.label}<span class="bar-note">{item.note}</span></div>'
        )
    return "\n".join(rows), json.dumps(tracks, ensure_ascii=False)


def _flow_nodes(scene: MotionScene) -> tuple[str, str]:
    boxes = scene.items or (
        MotionItem(label="发电", note="按电量卖"),
        MotionItem(label="输送", note="过网付费"),
        MotionItem(label="待命", note="按容量结算"),
    )
    rows: list[str] = []
    tracks: list[dict] = []
    count = len(boxes)
    slot = 0.46 / max(1, count)
    for index, item in enumerate(boxes):
        top = 0.30 + slot * index
        at = 0.30 + index * 0.55
        tracks.append({"kind": "box", "top": round(top, 4), "at": round(at, 3), "grow": 0.5})
        rows.append(
            f'<div class="flow-box" data-at="{at}" data-grow="0.5" style="top:{top * 100:.1f}%">'
            f'<div class="flow-label" style="color:{item.color}">{item.label}</div>'
            f'<div class="flow-note">{item.note}</div></div>'
        )
        if index < count - 1:
            arrow_at = at + 0.42
            tracks.append({"kind": "arrow", "top": round(top + slot * 0.72, 4), "at": round(arrow_at, 3), "grow": 0.3})
            rows.append(
                f'<div class="flow-arrow" data-at="{arrow_at}" data-grow="0.3" '
                f'style="top:{(top + slot * 0.72) * 100:.1f}%"></div>'
            )
    return "\n".join(rows), json.dumps(tracks, ensure_ascii=False)


def _number_payload(scene: MotionScene) -> dict:
    item = scene.items[0] if scene.items else MotionItem(value=0.0, unit="")
    return {
        "value": float(item.value),
        "unit": item.unit,
        "decimals": 1 if abs(item.value) < 100 else 0,
        "label": item.label,
        "note": item.note,
    }


PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{scene_id}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {width}px; height: {height}px; background: {ink}; overflow: hidden; }}
  #stage {{
    position: relative; width: {width}px; height: {height}px; overflow: hidden;
    transform-origin: 50% 50%;
    background: radial-gradient(120% 80% at 50% 0%, #16233a 0%, #0b1420 55%, #070d16 100%);
    font-family: {font};
  }}
  .glow {{
    position: absolute; border-radius: 50%; filter: blur(120px); opacity: 0.5;
  }}
  .kicker {{
    position: absolute; left: 9%; top: 12%; color: {cool}; font-size: 30px; letter-spacing: 2px;
  }}
  .title {{
    position: absolute; left: 9%; right: 9%; top: 15.5%; color: {paper}; font-size: 62px;
    line-height: 1.26; font-weight: 700;
  }}
  .rules {{
    position: absolute; left: 9%; top: 27.5%; width: 22%; height: 6px; background: {accent};
    transform-origin: left center;
  }}
  .bar {{
    position: absolute; left: 9%; height: 84px; transform-origin: left center;
    border-radius: 8px; transform: scaleX(0.001);
  }}
  .bar-label {{
    position: absolute; left: 9%; color: {paper}; font-size: 38px; transform: translateY(-58px);
  }}
  .bar-note {{ color: {dim}; font-size: 28px; margin-left: 16px; }}
  .flow-box {{
    position: absolute; left: 9%; width: 82%; height: 118px; border-radius: 14px;
    background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.16);
    padding: 18px 26px; opacity: 0;
  }}
  .flow-label {{ font-size: 42px; font-weight: 700; }}
  .flow-note {{ color: {dim}; font-size: 28px; margin-top: 6px; }}
  .flow-arrow {{
    position: absolute; left: 14%; width: 6px; height: 42px; background: {accent};
    transform-origin: top center; transform: scaleY(0.001); opacity: 0;
  }}
  .number {{
    position: absolute; left: 9%; top: 38%; color: {accent}; font-size: 300px; font-weight: 800;
    letter-spacing: -6px;
  }}
  .number-label {{ position: absolute; left: 9%; top: 30%; color: {paper}; font-size: 46px; }}
  .number-note {{ position: absolute; left: 9%; top: 68%; right: 9%; color: {dim}; font-size: 34px; line-height: 1.5; }}
  .source {{ position: absolute; left: 9%; bottom: 11%; color: {dim}; font-size: 26px; }}
  .credit {{ position: absolute; left: 9%; bottom: 5%; color: #6f7f96; font-size: 22px; }}
</style>
</head>
<body>
<div id="stage">
  <div class="glow" style="left:-8%;top:4%;width:62%;height:34%;background:#1d4a6b"></div>
  <div class="glow" style="right:-14%;top:22%;width:58%;height:30%;background:#3a2a52"></div>
  <div class="kicker">{kicker}</div>
  <div class="title">{title}</div>
  <div class="rules" data-at="0" data-grow="{duration}"></div>
  {body}
  <div class="source">{source}</div>
  <div class="credit">AK 视频生产系统 · 程序化解释动画（非实拍）</div>
</div>
<script>
const SCENE = {scene_json};
const nodes = Array.from(document.querySelectorAll("[data-at]"));
const numberNode = document.getElementById("number");

function smooth(x) {{ return x * x * (3 - 2 * x); }}

function formatNumber(value) {{
  const n = SCENE.number;
  const fixed = Number(value).toFixed(n.decimals);
  return fixed + (n.unit || "");
}}

function seek(t) {{
  for (const node of nodes) {{
    const at = Number(node.dataset.at || 0);
    const grow = Math.max(0.05, Number(node.dataset.grow || 0.4));
    const p = Math.min(1, Math.max(0, (t - at) / grow));
    const eased = smooth(p);
    node.style.opacity = String(Math.min(1, p * 2.2));
    if (node.classList.contains("bar")) node.style.transform = `scaleX(${{Math.max(0.001, eased)}})`;
    if (node.classList.contains("flow-arrow")) node.style.transform = `scaleY(${{Math.max(0.001, eased)}})`;
    if (node.classList.contains("flow-box")) node.style.transform = `translateY(${{(1 - eased) * 16}}px)`;
    if (node.classList.contains("rules")) node.style.transform = `scaleX(${{0.14 + 0.86 * eased}})`;
  }}
  // 整帧缓慢推近：动画镜里的元素很少，只靠元素动容易被判定格，整帧位移最保险
  const stage = document.getElementById("stage");
  const push = 1 + 0.05 * Math.min(1, Math.max(0, t / Math.max(0.5, SCENE.duration)));
  stage.style.transform = `scale(${{push}})`;
  const glows = document.querySelectorAll(".glow");
  glows.forEach((glow, i) => {{
    const drift = Math.sin((t + i * 1.7) * 0.35) * 46;
    glow.style.transform = `translate(${{drift}}px, ${{drift * 0.4}}px)`;
  }});
  if (numberNode) {{
    const dur = SCENE.duration * 0.62;
    const p = Math.min(1, Math.max(0, t / Math.max(0.4, dur)));
    numberNode.textContent = formatNumber(SCENE.number.value * smooth(p));
  }}
}}

window.__AK = {{ fps: SCENE.fps, duration: SCENE.duration, width: SCENE.width, height: SCENE.height }};
window.__seek = seek;
seek(0);
window.__ready = true;
</script>
</body>
</html>
"""


def _shorten(text: str, limit: int) -> str:
    """标题只留一句，超出截断加省略号（整段念白由字幕负责，不重复放在画面上）。"""

    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _escape(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_motion_scene(scene: MotionScene, *, scene_dir: Path) -> dict:
    """写一个自包含的动画镜头页，返回回执。"""

    if scene.template not in {"bars", "flow", "number"}:
        raise MotionSceneError(f"未知动画模板：{scene.template}")

    if scene.template == "bars":
        body, tracks = _bars_nodes(scene)
    elif scene.template == "flow":
        body, tracks = _flow_nodes(scene)
    else:
        payload = _number_payload(scene)
        body = (
            f'<div class="number-label">{_escape(payload["label"])}</div>'
            f'<div class="number" id="number">0</div>'
            f'<div class="number-note">{_escape(payload["note"])}</div>'
        )
        tracks = "[]"

    scene_json = json.dumps({
        "fps": scene.fps,
        "duration": round(float(scene.seconds), 3),
        "width": scene.width,
        "height": scene.height,
        "template": scene.template,
        "tracks": json.loads(tracks),
        "number": _number_payload(scene),
    }, ensure_ascii=False)

    html = PAGE.format(
        scene_id=_escape(scene.scene_id),
        width=scene.width,
        height=scene.height,
        ink=INK,
        paper=PAPER,
        dim=DIM,
        accent=ACCENT,
        cool=COOL,
        font=FONT_STACK,
        kicker=_escape(scene.kicker),
        title=_escape(_shorten(scene.title, 26)),
        body=body,
        source=_escape(scene.source),
        duration=round(float(scene.seconds), 3),
        scene_json=scene_json,
    )

    scene_dir = Path(scene_dir)
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "index.html").write_text(html, encoding="utf-8")
    return {
        "scene": scene.scene_id,
        "template": scene.template,
        "dir": str(scene_dir),
        "seconds": round(float(scene.seconds), 3),
        "frames": int(round(scene.seconds * scene.fps)),
    }


__all__ = ["MotionItem", "MotionScene", "MotionSceneError", "build_motion_scene"]
