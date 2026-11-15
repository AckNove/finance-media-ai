"""动态图表 + 数字动画：把"底图+字幕"换成"画面跟数据走"的画板。

为什么单独一个模块：早期成片每镜都是"深色底 + 底部字幕"，抽帧后 7/8 个镜头
几乎是同一张黑底图，观感就是会动的 PPT。用户明确要求走"动态图表 + 数字动画"。

这里只做四类画板，全部是纯 CSS/节点动画（OpenKatana 的画板契约里没有
background-image，也不能跑 CSS 动画，所以渐变色块靠"大圆 + blur + 缓慢位移"
堆出来，看起来像流光背景）：

* :func:`backdrop_document`  —— 会呼吸的底色（三个模糊圆缓慢漂移）
* :func:`number_roll_document` —— 大数字滚动（分帧淡入模拟计数）
* :func:`compare_bars_document` —— 对比条/占比条逐条长出来
* :func:`mechanism_document`  —— 机制图：两件事中间一根会动的箭头

所有 builder 都返回 OpenKatana 组合文档（``schema=1``），关键帧一律夹在
``0..frames-1`` 之间——越界会被引擎拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.app.video import textfit
from backend.app.video.cards import (
    CYAN,
    DIM,
    INK,
    LIME,
    MAGENTA,
    PAPER,
    FONT_FAMILY,
    FONT_SRC,
    CardDocument,
    _text,
    _track,
    card_path,
)

FPS = 30
ROOT_ID = "stage"


def _stage(
    *,
    frames: int,
    width: int,
    height: int,
    children: Sequence[dict[str, Any]],
    padding: str = "0 84px",
) -> dict[str, Any]:
    return {
        "id": ROOT_ID,
        "type": "box",
        "style": {
            "position": "relative",
            "display": "flex",
            "flex-direction": "column",
            "justify-content": "center",
            "align-items": "center",
            "gap": "26px",
            "width": f"{width}px",
            "height": f"{height}px",
            "padding": padding,
            "background-color": INK,
            "overflow": "hidden",
        },
        "children": list(children),
    }


def _document(
    *,
    frames: int,
    width: int,
    height: int,
    root: dict[str, Any],
    tracks: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": 1,
        "canvas": {"w": width, "h": height, "fps": FPS, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_SRC}],
        "plugins": [],
        "root": root,
        "tracks": list(tracks),
    }


# --------------------------------------------------------------------------- 底色


def _blob(node_id: str, *, size: int, color: str, left: str, top: str, opacity: float) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "box",
        "style": {
            "position": "absolute",
            "left": left,
            "top": top,
            "width": f"{size}px",
            "height": f"{size}px",
            "border-radius": "50%",
            "background-color": color,
            "opacity": f"{opacity}",
            "filter": "blur(120px)",
        },
    }


def backdrop_document(
    *,
    frames: int,
    width: int = 1080,
    height: int = 1920,
    accent: str = LIME,
    seed: int = 0,
) -> dict[str, Any]:
    """会呼吸的底色：三个模糊巨圆缓慢漂移，避免"整屏死黑"。"""
    palette = [accent, CYAN, MAGENTA]
    blobs = [
        _blob(
            f"blob{index}",
            size=760 + index * 90,
            color=palette[(index + seed) % len(palette)],
            left=f"{-18 + index * 26}%",
            top=f"{6 + index * 24}%",
            opacity=0.22 - index * 0.05,
        )
        for index in range(3)
    ]
    tracks: list[dict[str, Any]] = []
    for index in range(3):
        drift = 40 + index * 26
        tracks.append(
            _track(
                f"blob{index}",
                "translateX",
                [[0, -drift], [frames - 1, drift]],
                easing="smooth",
                frames=frames,
            )
        )
        tracks.append(
            _track(
                f"blob{index}",
                "translateY",
                [[0, drift // 2], [frames - 1, -drift // 2]],
                easing="smooth",
                frames=frames,
            )
        )
        tracks.append(
            _track(
                f"blob{index}",
                "scale",
                [[0, 1.0], [(frames - 1) // 2, 1.12], [frames - 1, 1.0]],
                easing="smooth",
                frames=frames,
            )
        )
    root = _stage(frames=frames, width=width, height=height, children=blobs, padding="0")
    return _document(frames=frames, width=width, height=height, root=root, tracks=tracks)


# --------------------------------------------------------------------- 大数字滚动


def _roll_values(value: float, unit: str, steps: int) -> list[str]:
    """把 0→value 拆成 steps 个"看起来在数数"的中间值（单位和数字一起滚）。

    小数位跟目标值一致（6.2 就一路显示一位小数），避免中途出现 0.0000001 这种。
    单位**跟着数字一起走**：早先把单位单独摆在数字右边，数字宽度一变单位就
    压在数字上（实测截图里 % 正好落在两个数字中间），所以合并成一个字符串。
    """
    decimals = 1 if abs(value - round(value)) > 1e-9 else 0
    return [f"{value * (index + 1) / steps:.{decimals}f}{unit}" for index in range(steps)]


def number_roll_document(
    *,
    value: float,
    unit: str,
    label: str,
    source_id: str = "",
    frames: int,
    width: int = 1080,
    height: int = 1920,
    kicker: str = "",
    steps: int = 10,
) -> dict[str, Any]:
    """大数字从 0 滚到目标值（分帧淡入模拟），下面一行是这句话在说什么。"""
    backdrop = backdrop_document(frames=frames, width=width, height=height, seed=1)
    steps = max(2, min(steps, max(2, frames // 3)))
    children: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = list(backdrop["tracks"])

    if kicker:
        children.append(_text("kicker", kicker, size=34, color=CYAN, spacing="0.18em"))
        tracks.append(_track("kicker", "reveal", [[0, 0], [min(10, frames - 1), 1]], frames=frames))

    # 数字与单位分开：单位小一号、贴右下，读起来像"6.2 %"而不是一个巨型字符串
    size = textfit.fit_size(
        [textfit.display_width(f"{value:.1f}")],
        base=300,
        canvas_width=width,
        budget=0.62,
        minimum=140,
    )
    holder: list[dict[str, Any]] = []
    span = max(1, (frames - 2) // steps)
    for index, text_value in enumerate(_roll_values(value, unit, steps)):
        node_id = f"n{index}"
        begin = index * span
        end = min(begin + span, frames - 1)
        # 每个中间值都**叠在同一个位置**（绝对定位），同一时刻只有一个是可见的；
        # 否则十个数会并排挤成一行，大数字被推到画面外（实测踩过）。
        holder.append(
            {
                "id": node_id,
                "type": "box",
                "style": {
                    "position": "absolute",
                    "left": "0",
                    "right": "0",
                    "top": "0",
                    "display": "flex",
                    "justify-content": "center",
                },
                "children": [_text(f"{node_id}-text", text_value, size=size, color=PAPER, weight=700)],
            }
        )
        # 交接是"硬切"：上一帧末尾归零、下一帧开头就 1，中间不留重影。
        keys = [[begin, 0], [min(begin + 2, frames - 1), 1]]
        if end < frames - 1:
            keys.append([end, 0])
        tracks.append(_track(node_id, "opacity", keys, frames=frames))
        tracks.append(
            _track(node_id, "scale", [[begin, 0.92], [min(begin + span, frames - 1), 1.0]], frames=frames)
        )
    # 最后一步停在画面上
    last_id = f"n{steps - 1}"
    tracks = [item for item in tracks if not (item["node"] == last_id and item["property"] == "opacity")]
    last_begin = (steps - 1) * span
    tracks.append(
        _track(last_id, "opacity", [[last_begin, 0], [min(last_begin + 2, frames - 1), 1]], frames=frames)
    )

    children.append(
        {
            "id": "number-block",
            "type": "box",
            "style": {
                "position": "relative",
                "display": "flex",
                "align-items": "flex-end",
                "justify-content": "center",
                "gap": "10px",
                "width": "100%",
                "height": f"{size + 40}px",
            },
            "children": holder,
        }
    )

    children.append(_text("label", label, size=40, color=PAPER))
    tracks.append(_track("label", "opacity", [[min(6, frames - 2), 0], [min(20, frames - 1), 1]], frames=frames))
    if source_id:
        children.append(_text("source", f"来源 {source_id}", size=32, color=DIM))
        tracks.append(_track("source", "opacity", [[min(12, frames - 2), 0], [min(26, frames - 1), 1]], frames=frames))

    root = _stage(frames=frames, width=width, height=height, children=children)
    root["children"] = list(backdrop["root"]["children"]) + list(children)
    return _document(frames=frames, width=width, height=height, root=root, tracks=tracks)


# ------------------------------------------------------------------- 对比条 / 占比


def compare_bars_document(
    *,
    items: Sequence[tuple[str, float]],
    source_id: str = "",
    frames: int,
    width: int = 1080,
    height: int = 1920,
    kicker: str = "",
    unit: str = "",
) -> dict[str, Any]:
    """横向对比条：每条按占比从 0 长出来，数字跟着条一起出现。"""
    if len(items) < 2:
        raise ValueError("对比条至少要两项")
    backdrop = backdrop_document(frames=frames, width=width, height=height, seed=2)
    top = max(abs(value) for _, value in items) or 1.0
    children: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = list(backdrop["tracks"])
    if kicker:
        children.append(_text("kicker", kicker, size=34, color=CYAN, spacing="0.18em"))
        tracks.append(_track("kicker", "reveal", [[0, 0], [min(10, frames - 1), 1]], frames=frames))

    row_nodes: list[dict[str, Any]] = []
    for index, (label, value) in enumerate(items):
        bar_width = max(0.04, abs(value) / top)
        bar_id = f"bar{index}"
        row_nodes.append(
            {
                "id": f"row{index}",
                "type": "box",
                "style": {
                    "display": "flex",
                    "flex-direction": "column",
                    "gap": "10px",
                    "width": "100%",
                },
                "children": [
                    {
                        "id": "rowhead",
                        "type": "box",
                        "style": {
                            "display": "flex",
                            "justify-content": "space-between",
                            "align-items": "baseline",
                            "width": "100%",
                        },
                        "children": [
                            _text(f"label{index}", label, size=38, color=PAPER),
                            _text(f"value{index}", f"{value:g}{unit}", size=46, color=LIME, weight=700),
                        ],
                    },
                    {
                        "id": "trackbar",
                        "type": "box",
                        "style": {
                            "display": "flex",
                            "width": "100%",
                            "height": "26px",
                            "border-radius": "13px",
                            "background-color": "#233049",
                        },
                        "children": [
                            {
                                "id": bar_id,
                                "type": "box",
                                "style": {
                                    "width": f"{bar_width * 100:.1f}%",
                                    "height": "26px",
                                    "border-radius": "13px",
                                    "background-color": [LIME, CYAN, MAGENTA][index % 3],
                                    "transform-origin": "left center",
                                },
                            }
                        ],
                    },
                ],
            }
        )
        begin = min(6 + index * 10, frames - 3)
        tracks.append(_track(f"label{index}", "opacity", [[begin, 0], [min(begin + 10, frames - 1), 1]], frames=frames))
        tracks.append(_track(f"value{index}", "opacity", [[begin, 0], [min(begin + 10, frames - 1), 1]], frames=frames))
        tracks.append(_track(bar_id, "scale", [[begin, 0.02], [min(begin + 18, frames - 1), 1.0]], frames=frames))

    children.append(
        {
            "id": "bars",
            "type": "box",
            "style": {
                "display": "flex",
                "flex-direction": "column",
                "gap": "34px",
                "width": "100%",
            },
            "children": row_nodes,
        }
    )
    if source_id:
        children.append(_text("source", f"来源 {source_id}", size=32, color=DIM))
        tracks.append(_track("source", "opacity", [[min(14, frames - 2), 0], [min(28, frames - 1), 1]], frames=frames))

    root = _stage(frames=frames, width=width, height=height, children=children)
    root["children"] = list(backdrop["root"]["children"]) + list(children)
    return _document(frames=frames, width=width, height=height, root=root, tracks=tracks)


# ---------------------------------------------------------------------- 机制图


def mechanism_document(
    *,
    top_label: str,
    top_text: str,
    bottom_label: str,
    bottom_text: str,
    source_id: str = "",
    frames: int,
    width: int = 1080,
    height: int = 1920,
    kicker: str = "",
) -> dict[str, Any]:
    """机制图：上块 → 会长的箭头 → 下块，用来讲"什么变了、所以什么变了"。"""
    backdrop = backdrop_document(frames=frames, width=width, height=height, seed=3)
    children: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = list(backdrop["tracks"])
    if kicker:
        children.append(_text("kicker", kicker, size=34, color=CYAN, spacing="0.18em"))
        tracks.append(_track("kicker", "reveal", [[0, 0], [min(10, frames - 1), 1]], frames=frames))

    def block(node_id: str, label: str, text: str, colour: str) -> dict[str, Any]:
        return {
            "id": node_id,
            "type": "box",
            "style": {
                "display": "flex",
                "flex-direction": "column",
                "gap": "12px",
                "width": "100%",
                "padding": "38px 34px",
                "border-radius": "28px",
                "background-color": "#1b2740",
                "border-left": f"10px solid {colour}",
            },
            "children": [
                _text(f"{node_id}-label", label, size=32, color=colour, spacing="0.14em"),
                _text(f"{node_id}-text", text, size=48, color=PAPER, weight=700),
            ],
        }

    children.append(block("top", top_label, top_text, CYAN))
    children.append(
        {
            "id": "arrow-wrap",
            "type": "box",
            "style": {
                "display": "flex",
                "justify-content": "center",
                "align-items": "center",
                "width": "100%",
                "height": "120px",
            },
            "children": [
                {
                    "id": "arrow",
                    "type": "box",
                    "style": {
                        "width": "10px",
                        "height": "96px",
                        "background-color": LIME,
                        "border-radius": "6px",
                        "transform-origin": "center top",
                    },
                }
            ],
        }
    )
    children.append(block("bottom", bottom_label, bottom_text, LIME))
    if source_id:
        children.append(_text("source", f"来源 {source_id}", size=32, color=DIM))

    tracks.append(_track("top", "opacity", [[0, 0], [min(12, frames - 1), 1]], frames=frames))
    tracks.append(_track("top", "translateY", [[0, -30], [min(14, frames - 1), 0]], frames=frames))
    tracks.append(_track("arrow", "scale", [[min(10, frames - 2), 0.02], [min(30, frames - 1), 1.0]], frames=frames))
    tracks.append(_track("bottom", "opacity", [[min(18, frames - 2), 0], [min(34, frames - 1), 1]], frames=frames))
    tracks.append(_track("bottom", "translateY", [[min(18, frames - 2), 30], [min(34, frames - 1), 0]], frames=frames))
    if source_id:
        tracks.append(_track("source", "opacity", [[min(24, frames - 2), 0], [min(38, frames - 1), 1]], frames=frames))

    root = _stage(frames=frames, width=width, height=height, children=children)
    root["children"] = list(backdrop["root"]["children"]) + list(children)
    return _document(frames=frames, width=width, height=height, root=root, tracks=tracks)


def write_composition(
    job_root: Path | str,
    *,
    asset_id: str,
    document: Mapping[str, Any],
    frames: int,
    shot_id: str = "",
) -> Any:
    """把一块动态画板写进任务目录（和数据卡同一套落盘与登记方式）。"""
    root = Path(job_root).resolve()
    target = card_path(root, asset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CardDocument(asset_id=asset_id, path=target, document=document, frames=frames, shot_id=shot_id)


__all__ = [
    "backdrop_document",
    "compare_bars_document",
    "mechanism_document",
    "number_roll_document",
    "write_composition",
]
