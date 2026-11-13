"""把"一个数字 + 一句说明"做成引擎能渲染的数据卡画板。

为什么需要这一层：规则草稿如果只给纯色底 + 一行字幕，成片就是"会动的幻灯片"，
没有任何视觉层级。数字卡把关键数字放大、来源标小，观众一眼能看到重点，同时
仍然只搬原文、不生成任何新结论。

画板文档必须是 OpenKatana 认识的形状（见 `tools/openkatana/openkatana/composition.py`
的 `STYLES` / `TRACKS` 白名单）：样式键不在白名单里会被引擎拒收，运动只能由
`tracks` 驱动，`easing` 只有 `linear|smooth|step`，关键帧必须落在 `0..frames-1`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "CARD_DIR",
    "CardDocument",
    "card_document",
    "card_path",
    "register_cards",
    "resize_cards",
    "write_card",
]

CARD_DIR = Path("assets") / "compositions"

INK = "#1d2a44"
PAPER = "#f2f5f7"
DIM = "#94a3b8"
LIME = "#b6ff1f"
CYAN = "#35e0ff"

FONT_FAMILY = "AKSC"
FONT_SRC = "fonts/NotoSansSC-VF.ttf"

_ALLOWED_EASINGS = ("linear", "smooth", "step")


@dataclass(frozen=True)
class CardDocument:
    """一张数据卡：文件名、文档本体、用它渲染出的帧数。"""

    asset_id: str
    path: Path
    document: dict[str, Any]
    frames: int

    @property
    def relative_path(self) -> str:
        return f"{CARD_DIR.as_posix()}/{self.path.name}"


def _text(node_id: str, text: str, *, size: int, color: str, weight: int = 400,
          spacing: str | None = None) -> dict[str, Any]:
    style: dict[str, str] = {
        "font-family": FONT_FAMILY,
        "font-size": f"{size}px",
        "font-weight": str(weight),
        "color": color,
        "line-height": "1.25",
        "text-align": "center",
    }
    if spacing:
        style["letter-spacing"] = spacing
    return {"id": node_id, "type": "text", "text": text, "style": style}


def _track(node: str, prop: str, keys: Sequence[Sequence[float]], *, easing: str = "smooth",
           frames: int) -> dict[str, Any]:
    if easing not in _ALLOWED_EASINGS:
        raise ValueError(f"easing 只能是 {_ALLOWED_EASINGS}，收到 {easing!r}")
    clamped = [[max(0, min(int(round(float(frame))), frames - 1)), value] for frame, value in keys]
    return {"node": node, "property": prop, "keys": clamped, "easing": easing}


def card_document(
    *,
    number: str,
    caption: str,
    source_id: str,
    frames: int,
    width: int = 1080,
    height: int = 1920,
    kicker: str = "原文数据",
) -> dict[str, Any]:
    """生成一张数据卡的画板文档。

    只有两层信息：放大到能一眼看见的数字，和它所在句子的前 18 个字；右下角标
    来源编号。数字从 0.86 倍淡入放大，说明稍后淡入，避免整屏同时出现。
    """
    if frames < 2:
        raise ValueError("frames 至少为 2，否则动画没有可用的时间轴")
    children: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    if kicker:
        children.append(_text("kicker", kicker, size=34, color=CYAN, spacing="0.16em"))
        tracks.append(_track("kicker", "reveal", [[0, 0], [min(10, frames - 1), 1]], frames=frames))
    children.append(_text("number", number, size=176, color=LIME, weight=700))
    children.append(_text("caption", caption, size=50, color=PAPER))
    if source_id:
        children.append(_text("source", f"来源 {source_id}", size=32, color=DIM))

    tracks.extend(
        [
            # 数字整段缓慢放大：纯色卡片如果动画停在中途就不再变像素，会被
            # freezedetect 判成"冻结画面"。让变化持续到最后一帧。
            _track("number", "scale", [[0, 0.86], [frames - 1, 1.06]], frames=frames),
            _track("number", "opacity", [[0, 0], [min(8, frames - 1), 1]], frames=frames),
            _track(
                "caption",
                "opacity",
                [[min(6, frames - 1), 0], [min(20, frames - 1), 1]],
                frames=frames,
            ),
            _track(
                "caption",
                "translateY",
                [[min(6, frames - 1), 26], [frames - 1, -6]],
                frames=frames,
            ),
        ]
    )
    if source_id:
        tracks.append(
            _track(
                "source",
                "opacity",
                [[min(14, frames - 1), 0], [min(26, frames - 1), 1]],
                frames=frames,
            )
        )

    root = {
        "id": "root",
        "type": "box",
        "style": {
            "display": "flex",
            "flex-direction": "column",
            "align-items": "center",
            "justify-content": "center",
            "gap": "46px",
            "width": f"{width}px",
            "height": f"{height}px",
            "padding": "0 120px",
            "background-color": INK,
        },
        "children": children,
    }
    return {
        "schema": 1,
        "canvas": {"w": width, "h": height, "fps": 30, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_SRC}],
        "root": root,
        "tracks": tracks,
    }


def card_path(job_root: Path | str, asset_id: str) -> Path:
    return Path(job_root) / CARD_DIR / f"{asset_id.lower()}.json"


def write_card(
    job_root: Path | str,
    *,
    asset_id: str,
    number: str,
    caption: str,
    source_id: str,
    frames: int,
    width: int = 1080,
    height: int = 1920,
) -> CardDocument:
    """把数据卡写进任务目录（同内容重复写是幂等的）。"""
    root = Path(job_root).resolve()
    document = card_document(
        number=number,
        caption=caption,
        source_id=source_id,
        frames=frames,
        width=width,
        height=height,
    )
    target = card_path(root, asset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return CardDocument(asset_id=asset_id, path=target, document=document, frames=frames)


def register_cards(job_root: Path | str, cards: Iterable[CardDocument],
                   *, purposes: dict[str, str] | None = None) -> list[str]:
    """把数据卡登记进素材清单（B 类、自带授权），返回登记成功的 asset_id。"""
    from backend.app.video.generated import register_generated_asset

    root = Path(job_root).resolve()
    registered: list[str] = []
    for card in cards:
        purpose = (purposes or {}).get(card.asset_id, "")
        register_generated_asset(
            root,
            card.path,
            kind="composition",
            purpose=purpose,
            credit="本仓库按原文数据排版生成（纯色底数据卡）",
            used_in_shots=card.asset_id,
            asset_id=card.asset_id,
        )
        registered.append(card.asset_id)
    return registered


def resize_cards(job_root: Path | str, storyboard: Any, *, fps: int | None = None) -> list[str]:
    """按分镜里每张卡的真实时长重写画板帧数。

    卡片先按"每点 4.5 秒"生成，但旁白多长镜头就多长（重排后往往 7 秒以上）。
    画板只有 135 帧、镜头有 210 帧时，后 75 帧一直是最后一帧，`freezedetect`
    就会判"画面冻结"。所以重排之后必须按新时长重写一次画板。
    """
    root = Path(job_root).resolve()
    rate = int(fps or getattr(storyboard, "fps", 30) or 30)
    touched: list[str] = []
    for shot in getattr(storyboard, "shots", ()):
        asset_id = (getattr(shot, "asset_id", "") or "").strip()
        if (getattr(shot, "asset_type", "") or "") != "composition" or not asset_id:
            continue
        target = card_path(root, asset_id)
        if not target.is_file():
            continue
        document = json.loads(target.read_text(encoding="utf-8"))
        children = document.get("root", {}).get("children", [])
        number = next((n.get("text", "") for n in children if n.get("id") == "number"), "")
        if not str(number).strip():
            # 这一格里没有 number 节点，说明它不是数据卡（例如产业链示意图）。
            # 按卡片的形状重写它会把整张图替换成空白——这里必须跳过，交给它自己的
            # resize 逻辑（见 backend.app.video.chain.resize_chains）。
            continue
        caption = next((n.get("text", "") for n in children if n.get("id") == "caption"), "")
        source = next((n.get("text", "") for n in children if n.get("id") == "source"), "")
        source_id = source.replace("来源", "").strip()
        frames = max(2, int(round((shot.end_time - shot.start_time) * rate)))
        if int(document.get("canvas", {}).get("frames", 0)) == frames:
            continue
        write_card(
            root,
            asset_id=asset_id,
            number=number,
            caption=caption,
            source_id=source_id,
            frames=frames,
            width=int(document["canvas"]["w"]),
            height=int(document["canvas"]["h"]),
        )
        touched.append(asset_id)
    if touched:
        register_cards(
            root,
            [
                CardDocument(
                    asset_id=asset_id,
                    path=card_path(root, asset_id),
                    document={},
                    frames=0,
                )
                for asset_id in touched
            ],
            purposes={asset_id: f"数据卡 {asset_id}" for asset_id in touched},
        )
    return touched
