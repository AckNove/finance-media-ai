"""产业链示意图：把"上游 → 中游 → 下游"画成会动的画板。

为什么单独做一层：数据卡只能显示**一个数字**，讲产业链时需要的是"东西怎么流动、
利润卡在哪一环"。这类图必须能一眼看出结构，所以固定三行（上游/中游/下游），
每行放该环节的具体节点，节点从上到下依次出现，再用一条流动的箭头把顺序说清楚。

硬约束（和 `cards.py` 同一套画板契约）：

- 只用画板支持的样式白名单（flex 布局、宽高、背景色、字号、颜色、字距）；
- 轨道只允许 ``opacity / reveal / translateX / translateY / rotate / scale``，
  easing 只有 ``linear / smooth / step``；
- 关键帧必须落在 ``0 .. frames-1``；
- **最后一帧仍要有变化**——纯色底一旦静止，``freezedetect`` 会判"画面冻结"。
  所以节点的 ``scale`` 轨道一路拉到最后一帧。

数据来源只有一处：研究结论里**挂到产业链骨架节点上**的那些句子
（``backend.app.research.frameworks.CHAIN`` 的 upstream / midstream / downstream）。
挂不上的句子不上图——图上不写没有依据的东西。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.app.video.cards import (
    CARD_DIR,
    CYAN,
    DIM,
    FONT_FAMILY,
    FONT_SRC,
    INK,
    LIME,
    PAPER,
    _text,
    _track,
)

__all__ = [
    "CHAIN_STAGES",
    "CHAIN_SHOT_SECONDS",
    "ChainDocument",
    "MAX_NODES_PER_STAGE",
    "chain_document",
    "chain_path",
    "insert_chain_shot",
    "nodes_from_findings",
    "resize_chains",
    "write_chain",
]

#: 三行固定顺序：上游 → 中游 → 下游（顺序本身就是信息，不按数据多少排）
CHAIN_STAGES: tuple[tuple[str, str], ...] = (
    ("upstream", "上游"),
    ("midstream", "中游"),
    ("downstream", "下游"),
)

#: 每行最多几个节点（再多字就糊了，宁可少画）
MAX_NODES_PER_STAGE = 3
#: 单个节点最多几个字（超出截断并加省略号）
MAX_NODE_CHARS = 12
#: 示意镜头默认时长（秒）：三行依次出现需要时间，太短看不清结构
CHAIN_SHOT_SECONDS = 5.0
#: 自动插进分镜时用的素材编号
CHAIN_ASSET_ID = "CHAIN_01"


@dataclass(frozen=True)
class ChainDocument:
    """一张产业链示意图：资产编号、落盘路径、画板文档、帧数。"""

    asset_id: str
    path: Path
    document: dict[str, Any]
    frames: int

    @property
    def relative_path(self) -> str:
        return f"{CARD_DIR.as_posix()}/{self.path.name}"


def _pill(node_id: str, label: str, *, width: int = 300) -> dict[str, Any]:
    """一个环节节点：圆角块 + 文字。"""
    return {
        "id": node_id,
        "type": "box",
        "style": {
            "display": "flex",
            "align-items": "center",
            "justify-content": "center",
            "width": f"{width}px",
            "min-height": "96px",
            "border-radius": "18px",
            "background-color": "#243352",
            "padding": "18px 22px",
        },
        "children": [_text(f"{node_id}_text", label, size=34, color=PAPER)],
    }


def _stage_row(
    key: str, label: str, nodes: Sequence[str], *, width: int
) -> dict[str, Any]:
    """一行：左边固定的环节名，右边是这一环的节点。

    节点宽度按"这一行有几个节点"算：一个节点占满，两个各半，三个各三分之一。
    这样 12 个字的节点也能排在一行里，不会被挤成两行。
    """
    label_width = 120
    gap = 18
    usable = width - label_width - gap * (len(nodes) + 1)
    node_width = max(220, usable // max(1, len(nodes)))
    children: list[dict[str, Any]] = [
        {
            "id": f"{key}_label_box",
            "type": "box",
            "style": {
                "display": "flex",
                "align-items": "center",
                "justify-content": "flex-start",
                "width": f"{label_width}px",
            },
            "children": [
                _text(f"{key}_label", label, size=30, color=CYAN, spacing="0.10em")
            ],
        }
    ]
    for position, node in enumerate(nodes):
        children.append(_pill(f"{key}_n{position}", node, width=node_width))
    return {
        "id": f"{key}_row",
        "type": "box",
        "style": {
            "display": "flex",
            "flex-direction": "row",
            "align-items": "stretch",
            "justify-content": "flex-start",
            "gap": f"{gap}px",
            "width": f"{width}px",
        },
        "children": children,
    }



def chain_document(
    *,
    title: str,
    stages: Mapping[str, Sequence[str]],
    frames: int,
    width: int = 1080,
    height: int = 1920,
    note: str = "",
) -> dict[str, Any]:
    """生成产业链示意图的画板文档。

    ``stages`` 只用 ``upstream / midstream / downstream`` 三个键；空阶段仍会画出来，
    并写「（本环缺证据）」，让人一眼看到哪一环没查到东西。
    """
    if frames < 2:
        raise ValueError("frames 至少为 2，否则动画没有可用的时间轴")

    rows: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    arrows: list[str] = []

    title_node = _text("title", title, size=58, color=PAPER, weight=700)
    if note:
        note_node = _text("note", note, size=28, color=DIM)
    else:
        note_node = None

    # 三行按出现顺序做时间轴：整段时长里三等分，保证每一行都能在片子里露过面
    span = max(6, int(round((frames - 6) / 3)))
    for index, (key, label) in enumerate(CHAIN_STAGES):
        labels = [str(item).strip() for item in (stages.get(key) or ()) if str(item).strip()]
        labels = [_clip(item) for item in labels[:MAX_NODES_PER_STAGE]]
        if not labels:
            labels = ["（本环缺证据）"]
        row = _stage_row(key, label, labels, width=width - 120)
        rows.append(row)

        start = min(6 + index * span, frames - 2)
        end = min(start + max(8, span - 2), frames - 1)
        tracks.append(_track(f"{key}_row", "opacity", [[start, 0], [end, 1]], frames=frames))
        tracks.append(
            _track(f"{key}_row", "translateX", [[start, -28], [end, 0]], frames=frames)
        )
        if index < len(CHAIN_STAGES) - 1:
            arrows.append(f"{key}_arrow")

    content_children: list[dict[str, Any]] = [title_node]
    if note_node is not None:
        content_children.append(note_node)
    for index, row in enumerate(rows):
        content_children.append(row)
        if index < len(rows) - 1:
            arrow_id = f"{CHAIN_STAGES[index][0]}_arrow"
            content_children.append(
                {
                    "id": arrow_id,
                    "type": "box",
                    "style": {
                        "display": "flex",
                        "align-items": "center",
                        "justify-content": "center",
                        "height": "70px",
                        "width": "100%",
                    },
                    "children": [_text(f"{arrow_id}_text", "▼", size=44, color=LIME)],
                }
            )
            arrow_start = min(span // 2 + index * span, frames - 2)
            arrow_end = min(arrow_start + max(6, span // 2), frames - 1)
            tracks.append(
                _track(arrow_id, "opacity", [[arrow_start, 0], [arrow_end, 1]], frames=frames)
            )
            # 箭头持续下沉：既是"往下流"的意思，也保证最后一帧还在变
            tracks.append(
                _track(
                    arrow_id,
                    "translateY",
                    [[arrow_start, -10], [frames - 1, 6]],
                    easing="linear",
                    frames=frames,
                )
            )

    root: dict[str, Any] = {
        "id": "root",
        "type": "box",
        "style": {
            "display": "flex",
            "flex-direction": "column",
            "align-items": "center",
            "justify-content": "center",
            "gap": "40px",
            "width": f"{width}px",
            "height": f"{height}px",
            "padding": "0 55px",
            "background-color": INK,
        },
        "children": [
            {
                "id": "content",
                "type": "box",
                "style": {
                    "display": "flex",
                    "flex-direction": "column",
                    "align-items": "center",
                    "justify-content": "center",
                    "gap": "34px",
                    "width": "100%",
                },
                "children": content_children,
            }
        ],
    }
    # 整块内容缓慢推近：让"静止画面"这件事不可能发生
    tracks.append(_track("content", "scale", [[0, 1.0], [frames - 1, 1.05]], frames=frames))
    tracks.append(_track("title", "opacity", [[0, 0], [min(8, frames - 1), 1]], frames=frames))

    return {
        "schema": 1,
        "canvas": {"w": width, "h": height, "fps": 30, "frames": frames},
        "fonts": [{"family": FONT_FAMILY, "src": FONT_SRC}],
        "root": root,
        "tracks": tracks,
    }


def _clip(text: str) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= MAX_NODE_CHARS:
        return clean
    return clean[: MAX_NODE_CHARS - 1] + "…"


def chain_path(job_root: Path | str, asset_id: str) -> Path:
    return Path(job_root) / CARD_DIR / f"{asset_id.lower()}.json"


def write_chain(
    job_root: Path | str,
    *,
    asset_id: str,
    title: str,
    stages: Mapping[str, Sequence[str]],
    frames: int,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    note: str = "",
    register: bool = True,
) -> ChainDocument:
    """把示意图写进任务目录（同内容重复写是幂等的），并登记成 B 类素材。"""
    root = Path(job_root).resolve()
    document = chain_document(
        title=title, stages=stages, frames=frames, width=width, height=height, note=note
    )
    document["canvas"]["fps"] = int(fps)
    target = chain_path(root, asset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if register:
        from backend.app.video.generated import register_generated_asset

        register_generated_asset(
            root,
            target,
            kind="composition",
            purpose=f"产业链示意图 {asset_id}",
            credit="本仓库按研究结论排版生成（上游/中游/下游三段示图）",
            used_in_shots=asset_id,
            asset_id=asset_id,
        )
    return ChainDocument(asset_id=asset_id, path=target, document=document, frames=frames)


def nodes_from_findings(
    findings: Iterable[Mapping[str, Any]],
    *,
    per_stage: int = MAX_NODES_PER_STAGE,
) -> dict[str, list[str]]:
    """从结论里挑出三行节点：只认挂到产业链骨架节点上的句子。

    - 每条结论的 ``section`` 必须是 ``upstream / midstream / downstream``；
    - 句子取第一个短句（逗号前），最多 ``MAX_NODE_CHARS`` 字；
    - 同一行按出现顺序去重，最多 ``per_stage`` 条。

    挂不上骨架的结论**不上图**：图上不写没有依据的东西，缺就写"（本环缺证据）"。
    """
    stages: dict[str, list[str]] = {key: [] for key, _ in CHAIN_STAGES}
    for item in findings:
        section = str(item.get("section") or "").strip().lower()
        if section not in stages:
            continue
        text = str(item.get("text") or item.get("claim") or item.get("statement") or "").strip()
        if not text:
            continue
        head = text.split("，")[0].split(",")[0].strip()
        label = _clip(head)
        if not label or label in stages[section]:
            continue
        if len(stages[section]) >= per_stage:
            continue
        stages[section].append(label)
    return stages


def _row_of(shot: Any) -> dict[str, str]:
    """把已解析的镜头摊成 CSV 行。

    不能直接用 ``Shot.as_dict()``：它不含 ``notes``/``source_ids``，而且多一个
    非列的 ``duration``。这里按分镜表的真实列取，时间写成字符串避免精度噪音。
    """
    from backend.app.media.storyboard import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

    columns = [name for name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if name not in (
        "fps", "canvas_width", "canvas_height",
    )]
    row: dict[str, str] = {}
    for name in columns:
        if name in ("start_time", "end_time"):
            row[name] = f"{float(getattr(shot, name, 0.0) or 0.0):.6f}"
        else:
            row[name] = str(getattr(shot, name, "") or "")
    return row


def insert_chain_shot(
    job_root: Path | str,
    *,
    title: str,
    stages: Mapping[str, Sequence[str]],
    asset_id: str = CHAIN_ASSET_ID,
    seconds: float = CHAIN_SHOT_SECONDS,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    source_ids: str = "",
) -> dict[str, Any]:
    """生成产业链画板，并把它插进已有分镜（开场之后），随后重排时间轴。

    为什么插在开场之后：观众得先知道"这件事发生在哪一环"，后面的数字才有位置感。
    插完必须重排每个人的起止时间——分镜校验要求镜头有序、连续、不重叠，
    改写一行而不重排会让后面所有镜头的时间错位。
    """
    from backend.app.media.storyboard import load_storyboard, write_storyboard
    from backend.app.video.outline import STORYBOARD_REL, retime_rows

    root = Path(job_root).resolve()
    path = root / STORYBOARD_REL
    if not path.is_file():
        raise FileNotFoundError(f"分镜表不存在：{path}")

    frames = max(2, int(round(seconds * fps)))
    document = write_chain(
        root,
        asset_id=asset_id,
        title=title,
        stages=stages,
        frames=frames,
        width=width,
        height=height,
        fps=fps,
    )

    board = load_storyboard(path)
    rows = [_row_of(shot) for shot in board.shots]
    filled = [key for key, nodes in stages.items() if nodes]
    narration = f"先看结构：这件事发生在产业链的{len(filled)}个环节里，链条是这样传下去的。"
    row: dict[str, str] = {
        "shot_id": "",
        "start_time": "0",
        "end_time": f"{seconds:g}",
        "narrative_purpose": "产业链结构",
        "narration": narration,
        "visual_description": "产业链示意图：上游 / 中游 / 下游三段依次出现，箭头指示传导方向",
        "asset_id": asset_id,
        "asset_type": "composition",
        "asset_source": str(document.relative_path),
        "license": "原创（程序生成）",
        "animation": "fade_in",
        "camera_motion": "static",
        "transition": "dissolve",
        "subtitle": "产业链：上游 → 中游 → 下游",
        "sound_effect": "none",
        "safety_zone": "bottom",
        "notes": "产业链示意图；节点只取挂到上游/中游/下游的结论",
        "source_ids": source_ids,
    }
    position = 1 if len(rows) > 1 else len(rows)
    rows.insert(position, row)
    # 插入一镜后重编镜头号，避免出现两个 sh02；配音按 shot_id 对齐，编号必须唯一。
    for index, item in enumerate(rows, start=1):
        item["shot_id"] = f"sh{index:02d}"
    retimed, notes = retime_rows(rows, {}, fps=board.fps)

    write_storyboard(path, retimed, fps=board.fps, width=board.width, height=board.height)
    # 写完立刻按正式格式读回来：宁可现在报错，也不要让渲染阶段才发现分镜不合法。
    checked = load_storyboard(path)
    return {
        "asset_id": asset_id,
        "shot_id": retimed[position]["shot_id"],
        "frames": frames,
        "seconds": round(seconds, 3),
        "inserted_at": position,
        "shot_count": len(checked.shots),
        "total_seconds": round(checked.duration, 3),
        "stages": {key: list(nodes) for key, nodes in stages.items()},
        "notes": notes,
    }


def _stages_of(document: Mapping[str, Any]) -> dict[str, list[str]]:
    """从已有画板里读回三行节点（重排时长时要按原样重建，不能换个内容）。"""
    stages: dict[str, list[str]] = {}
    children = list(document.get("root", {}).get("children", []))
    content = next((item for item in children if item.get("id") == "content"), None)
    nodes = list((content or {}).get("children", [])) or children
    for key, _ in CHAIN_STAGES:
        row = next((item for item in nodes if item.get("id") == f"{key}_row"), None)
        if row is None:
            continue
        labels: list[str] = []
        for pill in list(row.get("children", []))[1:]:
            text = ""
            for child in pill.get("children", []):
                if child.get("type") == "text":
                    text = str(child.get("text") or "")
                    break
            if text and text != "（本环缺证据）":
                labels.append(text)
        stages[key] = labels
    return stages


def resize_chains(
    job_root: Path | str,
    storyboard: Any,
    *,
    fps: int | None = None,
) -> list[str]:
    """按分镜里每个产业链镜头的真实时长重写画板帧数。

    配音多长镜头就多长（重排后往往 7 秒以上）。画板只有 150 帧、镜头有 210 帧时，
    后 60 帧一直是最后一帧，`freezedetect` 就会判"画面冻结"。所以重排之后
    必须按新时长重写一次画板——和数据卡的 ``resize_cards`` 是同一个道理。
    """
    root = Path(job_root).resolve()
    rate = int(fps or getattr(storyboard, "fps", 30) or 30)
    touched: list[str] = []
    for shot in getattr(storyboard, "shots", ()):
        asset_id = (getattr(shot, "asset_id", "") or "").strip()
        if (getattr(shot, "asset_type", "") or "") != "composition" or not asset_id:
            continue
        target = chain_path(root, asset_id)
        if not target.is_file():
            continue
        document = json.loads(target.read_text(encoding="utf-8"))
        stages = _stages_of(document)
        if not stages:
            continue  # 不是产业链画板（可能是数据卡），交给 resize_cards
        frames = max(2, int(round((shot.end_time - shot.start_time) * rate)))
        if int(document.get("canvas", {}).get("frames", 0)) == frames:
            continue
        title = ""
        for item in document.get("root", {}).get("children", []):
            if item.get("id") == "content":
                for child in item.get("children", []):
                    if child.get("id") == "title":
                        title = str(child.get("text") or "")
                        break
        write_chain(
            root,
            asset_id=asset_id,
            title=title or "产业链结构",
            stages={key: labels or ["（本环缺证据）"] for key, labels in stages.items()},
            frames=frames,
            width=int(document["canvas"]["w"]),
            height=int(document["canvas"]["h"]),
            fps=rate,
        )
        touched.append(asset_id)
    return touched
