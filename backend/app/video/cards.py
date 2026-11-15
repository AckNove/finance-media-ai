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

from backend.app.video import textfit

__all__ = [
    "CARD_DIR",
    "CardDocument",
    "CARD_CAPTION_BASE",
    "CARD_CAPTION_BUDGET",
    "card_document",
    "card_path",
    "register_cards",
    "resize_cards",
    "shot_of_card",
    "write_card",
]

CARD_DIR = Path("assets") / "compositions"

INK = "#1d2a44"
PAPER = "#f2f5f7"
DIM = "#94a3b8"
LIME = "#b6ff1f"
CYAN = "#35e0ff"
MAGENTA = "#ff2bd6"

FONT_FAMILY = "AKSC"
FONT_SRC = "fonts/NotoSansSC-VF.ttf"

#: 说明文字的基准字号；一行放不下时按 `video/textfit.py` 的同一套口径缩小。
CARD_CAPTION_BASE = 50

#: 说明文字最多占画幅宽度的多少（比字幕略宽：卡面四周本来就有留白）。
CARD_CAPTION_BUDGET = 0.86

_ALLOWED_EASINGS = ("linear", "smooth", "step")


@dataclass(frozen=True)
class CardDocument:
    """一张数据卡：文件名、文档本体、用它渲染出的帧数。

    `shot_id` 是"这张卡给哪个镜头用"，要写进素材清单的 `used_in_shots`。
    以前那一列填的是 asset_id（`CARD_sh02`），于是素材页写"用于镜头 CARD_sh02"、
    分镜页却找不到叫 `CARD_sh02` 的镜头——两边对不上，人一核对就懵。
    """

    asset_id: str
    path: Path
    document: dict[str, Any]
    frames: int
    shot_id: str = ""

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
    # 说明可能长到 18-19 个字：50px 全角字一行放不下 1080 宽的卡面，会把整句顶出
    # 画面（字幕那边踩过同样的坑）。这里用同一套估算口径按需缩小，只降不升。
    caption_size = textfit.fit_text_size(
        [caption],
        base=CARD_CAPTION_BASE,
        canvas_width=width,
        budget=CARD_CAPTION_BUDGET,
        minimum=30,
    )
    children.append(_text("caption", caption, size=caption_size, color=PAPER))
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


def backdrop_nodes(*, frames: int, accent: str = LIME, seed: int = 0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """柔光底层：三个模糊大圆缓慢漂移，返回 (节点, 轨道)。

    为什么不用渐变：OpenKatana 的画板只认白名单里的 CSS 属性，`background-image`
    与 CSS 动画都不能用，所以只能拿"大圆 + blur + 位移"堆出流光底色。
    """
    palette = [accent, CYAN, MAGENTA]
    nodes: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    for index in range(3):
        node_id = f"blob{index}"
        nodes.append(
            {
                "id": node_id,
                "type": "box",
                "style": {
                    "position": "absolute",
                    "left": f"{-18 + index * 26}%",
                    "top": f"{6 + index * 24}%",
                    "width": f"{760 + index * 90}px",
                    "height": f"{760 + index * 90}px",
                    "border-radius": "50%",
                    "background-color": palette[(index + seed) % len(palette)],
                    "opacity": f"{0.22 - index * 0.05}",
                    "filter": "blur(120px)",
                },
            }
        )
        drift = 46 + index * 28
        tracks.append(_track(node_id, "translateX", [[0, -drift], [frames - 1, drift]], frames=frames))
        tracks.append(_track(node_id, "translateY", [[0, drift // 2], [frames - 1, -drift // 2]], frames=frames))
        tracks.append(
            _track(node_id, "scale", [[0, 1.0], [(frames - 1) // 2, 1.12], [frames - 1, 1.0]], frames=frames)
        )
    return nodes, tracks


def headline_document(
    *,
    text: str,
    source_id: str = "",
    frames: int,
    width: int = 1080,
    height: int = 1920,
    kicker: str = "",
    accent: str = LIME,
) -> dict[str, Any]:
    """生成一张"整句大字"画板文档（给没有数字的镜头用）。

    为什么需要它：只把句子写进字幕、画面给一块底色，看起来就是"会动的 PPT"。
    这里把这句话本身做成画面主体——最多三行、按可用宽度自动缩字号，关键数字
    （若句中恰好带单位数字）单独用强调色，其余为纸白色。
    """
    if frames < 2:
        raise ValueError("frames 至少为 2，否则动画没有可用的时间轴")

    body = text.strip()
    if not body:
        raise ValueError("headline 的正文不能为空")
    # 按标点切句、每行最多 10 个字，最多三行：竖屏 1080 宽下 10 个全角字约 720px，
    # 留得住左右边距；再多行就会把正文字号压到看不清。
    lines = _headline_lines(body)
    size = textfit.fit_text_size(
        lines, base=88, canvas_width=width, budget=0.82, minimum=44
    )

    blobs, blob_tracks = backdrop_nodes(frames=frames, accent=accent)
    children: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = list(blob_tracks)
    if kicker:
        children.append(_text("kicker", kicker, size=34, color=CYAN, spacing="0.16em"))
        tracks.append(_track("kicker", "reveal", [[0, 0], [min(10, frames - 1), 1]], frames=frames))
    line_nodes = []
    for index, line in enumerate(lines):
        node_id = f"line{index + 1}"
        line_nodes.append(node_id)
        children.append(_text(node_id, line, size=size, color=PAPER, weight=700))
        begin = min(4 + index * 6, frames - 2)
        tracks.append(_track(node_id, "opacity", [[begin, 0], [min(begin + 12, frames - 1), 1]], frames=frames))
        tracks.append(
            _track(node_id, "translateY", [[begin, 28], [min(begin + 14, frames - 1), 0]], frames=frames)
        )
    if source_id:
        children.append(_text("source", f"来源 {source_id}", size=32, color=DIM))

    # 让非文字元素一直动：纯文字画板如果动画停在半路就不再变像素，会被判成"冻结画面"。
    # **不能靠整块文字缓慢放大**——实测那样每帧都在改字号对齐方式，引擎做
    # "倒着放一遍比对"时会报 `Composition is not deterministic when seeking backwards`。
    # 换成一根色条慢慢长长：动的是纯色块，不涉及字形重排，确定性没问题。
    tracks.append(_track("accent", "scale", [[0, 0.12], [frames - 1, 1.0]], frames=frames))

    return {
        "schema": 1,
        "canvas": {"w": width, "h": height, "fps": 30, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_SRC}],
        "plugins": [],
        "root": {
            "id": "stage",
            "type": "box",
            "style": {
                "position": "relative",
                "display": "flex",
                "flex-direction": "column",
                "justify-content": "center",
                "align-items": "center",
                "gap": "18px",
                "width": "100%",
                "height": "100%",
                "padding": "0 96px",
                "background-color": INK,
                "overflow": "hidden",
            },
            "children": blobs + [
                {
                    # 一根贴着文字块的色条，用来提供持续运动（见上面注释）。
                    "id": "accent",
                    "type": "box",
                    "style": {
                        "width": "320px",
                        "height": "8px",
                        "border-radius": "4px",
                        "background-color": accent,
                        "transform-origin": "left center",
                        "opacity": "0.9",
                    },
                },
                {
                    "id": "lines",
                    "type": "box",
                    "style": {
                        "position": "relative",
                        "display": "flex",
                        "flex-direction": "column",
                        "align-items": "center",
                        "gap": "14px",
                    },
                    "children": children,
                }
            ],
        },
        "tracks": tracks,
    }


def _headline_lines(text: str, *, per_line: int = 10, max_lines: int = 3) -> list[str]:
    """把一句话切成"每行不超过 per_line 个字"的若干行（最多 max_lines 行）。"""
    body = "".join(part.strip() for part in text.split("\n"))
    if len(body) <= per_line:
        return [body]
    lines: list[str] = []
    rest = body
    while rest and len(lines) < max_lines:
        if len(rest) <= per_line:
            lines.append(rest)
            rest = ""
            break
        cut = per_line
        # 尽量在标点/空格处断行，别把词劈开
        for offset in range(0, min(6, len(rest) - per_line)):
            char = rest[per_line - offset - 1]
            if char in "，。、；：！？,.!?;: ":
                cut = per_line - offset
                break
        lines.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        # 超出三行就把剩下的并进最后一行
        lines[-1] = f"{lines[-1]}{rest}"
    return lines


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
    shot_id: str = "",
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
    return CardDocument(
        asset_id=asset_id, path=target, document=document, frames=frames, shot_id=shot_id
    )


def shot_of_card(asset_id: str) -> str:
    """从素材号猜镜头号（`CARD_sh02` → `sh02`）。

    只在调用方没显式给 `shot_id` 时兜底用；猜不出来就返回空串，不硬编一个。
    """
    text = str(asset_id or "").strip()
    for prefix in ("CARD_", "card_"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return ""


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
            # 这一列是"给哪个镜头用"，必须是镜头号而不是素材号，否则两边对不上
            used_in_shots=card.shot_id or shot_of_card(card.asset_id),
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
