"""把"脚本"接进"分镜/成片"链路（`content/script` 产物 → storyboard.csv + 数据卡）。

为什么要这一层
--------------
在此之前系统有两条互不相干的喉舌：

* `content.script` 会把**人工确认过的结论**改写成 AK 风格的脚本，存进 `scripts` 表；
* 建视频任务时（`backend/app/video/handoff.py`）却只按报告里的数据点排一份**规则分镜**。

结果是：模型写的稿子很好看，片子却还是规则拼的句子。这里补上中间那段——
把脚本的每一句排成镜头、带数字的句子做成数据卡，写成分镜表交给渲染。

硬规矩
------
* 只排镜头，**不渲染、不配音、不改字**：句子保持脚本原样（模型已经逐句核对过来源与数字）。
* 数字卡只认**带单位的量**，且必须能在这一句里原样找到；找不到就不做卡，退回文字卡。
* 来源跟着句子走：`source_ids` 落到分镜的可追溯列上，内容质检靠它判"这一镜能不能回溯"。

"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.app.media.storyboard import (
    DEFAULT_CANVAS,
    DEFAULT_FPS,
    load_storyboard,
    write_storyboard,
)

from backend.app.content.stages import (
    CHARS_PER_SECOND,
    DISCLAIMER,
    ScriptDraft,
    ScriptLine,
    plan_shots,
)

#: 分镜表位置（与规则分镜一致，渲染阶段只认这一个路径）。
STORYBOARD_REL = Path("storyboard") / "storyboard.csv"

#: 说明文件：告诉人这份分镜是怎么来的。
NOTES_REL = Path("storyboard") / "FROM_SCRIPT.md"

#: 一屏放得下的说明字数；超出就交给 `caption_around` 处理。
MAX_CAPTION_CHARS = 24

#: 带单位的量（长单位在前，否则"亿元"会被"亿"抢先吃掉一半）。
#: 与 `video/outline.py` 的 `_UNITS` 保持同一套口径：脚本排分镜和规则分镜
#: 判断"这句话能不能上卡面"必须是同一个标准，否则同一句话在两套分镜里待遇不同。
_UNIT_NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个百分点|亿美元|亿元|万元|万亿|亿|倍"
    r"|千瓦时|千瓦|兆瓦|万吨|吨|GWh|MWh|kWh|GW|MW|%|％)"
)

#: 每类资产的镜头运动与转场（与规则分镜同一套：都要有运动，纯静止会被判冻结）。
_CARD_CAMERAS = ("push", "pan_right", "punch", "pan_up", "push")
_PLATE_CAMERAS = ("push", "push", "push", "push")
_TRANSITIONS = ("none", "dissolve", "slide", "dissolve", "wipe", "dip")


class ScriptStoryboardError(ValueError):
    """脚本排不了分镜。``reason`` 稳定，供接口与测试判断。

    取值：``empty_script``、``too_few_lines``、``bad_arguments``。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def draft_from_script(script: Any) -> ScriptDraft:
    """把 ``scripts`` 表的一行还原成 `ScriptDraft`（字段都在 `segments` 里）。"""

    def _get(name: str, default: Any = "") -> Any:
        if isinstance(script, Mapping):
            return script.get(name, default)
        return getattr(script, name, default)

    segments = _get("segments", None) or []
    lines: list[ScriptLine] = []
    for item in segments:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            ScriptLine(
                text=text,
                role=str(item.get("role") or "evidence"),
                source_ids=tuple(str(value) for value in (item.get("source_ids") or ())),
                numbers=tuple(str(value) for value in (item.get("numbers") or ())),
            )
        )
    if not lines:
        raise ScriptStoryboardError(
            "empty_script", "这份脚本没有分句（segments 为空），排不了分镜"
        )
    return ScriptDraft(
        title=str(_get("title", "") or ""),
        hook=str(_get("hook", "") or ""),
        lines=tuple(lines),
        disclaimer=str(_get("disclaimer", "") or DISCLAIMER),
        generated_by="script",
    )


def _card_number(text: str) -> str:
    """这一句里可以上卡面的量；没有带单位的量就返回空串。"""
    match = _UNIT_NUMBER.search(text)
    return match.group(0).strip() if match else ""


def _caption(text: str) -> str:
    """一屏说明：优先整句，太长就在标点处截断。"""
    from backend.app.video.outline import _caption_around  # 同一套"数字与单位完整"口径

    cleaned = " ".join(text.split())
    number = _card_number(cleaned)
    if number:
        return _caption_around(cleaned, number)
    if len(cleaned) <= MAX_CAPTION_CHARS:
        return cleaned
    return cleaned[: MAX_CAPTION_CHARS - 1] + "…"


def storyboard_from_draft(
    draft: ScriptDraft,
    *,
    job_root: Path | str,
    fps: int = DEFAULT_FPS,
    canvas: tuple[int, int] = DEFAULT_CANVAS,
) -> dict[str, Any]:
    """把脚本草稿排成分镜表并写好数据卡；返回回执（不渲染）。"""
    if len(draft.lines) < 2:
        raise ScriptStoryboardError(
            "too_few_lines", f"脚本只有 {len(draft.lines)} 句，排不出成片的镜头序列（至少 2 句）"
        )
    root = Path(job_root)
    shots = plan_shots(draft)
    rows: list[dict[str, Any]] = []
    cards: list[Any] = []
    # 时间轴按**整帧累加**：`plan_shots` 给的秒数是 len/4.2 这类浮点数（如 11.43 秒），
    # 直接写进分镜表会被 `load_storyboard` 判成 `bad_time`（时间必须落在整数帧上）。
    # 渲染器也是逐帧取图的，这里先对齐帧，后面就不用再算一次。
    frame_cursor = 0

    from backend.app.video.cards import register_cards, write_card

    for index, shot in enumerate(shots):
        seconds = float(shot.get("seconds") or 0.0)
        frames = max(2, int(round(seconds * fps)))
        start_frame, end_frame = frame_cursor, frame_cursor + frames
        frame_cursor = end_frame
        start, end = start_frame / fps, end_frame / fps
        narration = str(shot.get("narration") or "")
        source_ids = ",".join(str(value) for value in (shot.get("source_ids") or ()))
        number = _card_number(narration) if shot.get("asset_type") == "data_card" else ""
        card = None
        if number:
            source_label = source_ids.split(",")[0] if source_ids else ""
            card = write_card(
                root,
                asset_id=f"CARD_{shot['shot_id']}",
                number=number,
                caption=_caption(narration),
                source_id=source_label,
                frames=frames,
                width=canvas[0],
                height=canvas[1],
            )
            cards.append(card)
        rows.append(
            {
                "shot_id": shot["shot_id"],
                "start_time": f"{start:.9f}",
                "end_time": f"{end:.9f}",
                "narrative_purpose": str(shot.get("purpose") or ""),
                "narration": narration,
                "visual_description": (
                    "数据卡：放大数字 + 来源标注" if card else str(shot.get("visual") or "")
                ),
                "asset_id": card.asset_id if card else "",
                "asset_type": "composition" if card else "solid",
                "asset_source": card.relative_path if card else "#1d2a44",
                "license": "原创（程序生成）" if card else "原创（纯色底，程序生成）",
                "animation": "fade_in",
                "camera_motion": (
                    _CARD_CAMERAS[index % len(_CARD_CAMERAS)]
                    if card
                    else _PLATE_CAMERAS[index % len(_PLATE_CAMERAS)]
                ),
                "transition": _TRANSITIONS[index % len(_TRANSITIONS)],
                "subtitle": "" if card else _caption(narration),
                "sound_effect": "none",
                "safety_zone": "bottom",
                "notes": f"来自脚本；来源 {source_ids}" if source_ids else "来自脚本",
                "source_ids": source_ids,
                "source_start": "",
            }
        )

    path = root / STORYBOARD_REL
    write_storyboard(path, rows, fps=fps, width=canvas[0], height=canvas[1])
    # 写完立刻按正式格式读回来：宁可现在报错，也不要让渲染阶段才发现分镜不合法。
    board = load_storyboard(path)
    if cards:
        register_cards(
            root,
            cards,
            purposes={card.asset_id: f"数据卡 {card.asset_id}" for card in cards},
        )

    notes_path = root / NOTES_REL
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(
        "\n".join(
            [
                f"# 分镜来自脚本：{draft.title or '（无标题）'}",
                "",
                f"- 镜头数：{len(rows)}",
                f"- 总时长：{board.duration:.2f} 秒（{fps}fps）",
                f"- 数据卡：{len(cards)} 张",
                f"- 句子来源：{draft.generated_by}",
                "",
                "## 和规则分镜的区别",
                "",
                "这份分镜的每一句都来自 `scripts` 表里那份**已经过人工确认结论**改写出来的脚本，",
                "不是按报告里的数据点规则拼的。渲染、配音、内容质检与规则分镜完全一样。",
                "",
                "## 不要做的事",
                "",
                "* 不要在这里改字：脚本是模型逐句核对过来源与数字的产物，改字要让脚本重走一次审核。",
                "* 不要让这句话的来源变成空白：`source_ids` 空了，内容质检会把它判成「无来源」。",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "ok": True,
        "source": "script",
        "storyboard": str(path.relative_to(root).as_posix()),
        "notes": str(notes_path.relative_to(root).as_posix()),
        "shot_count": len(rows),
        "card_count": len(cards),
        "total_seconds": round(board.duration, 3),
        "frames": int(round(board.duration * fps)),
    }


def script_storyboard(
    script: Any,
    *,
    job_root: Path | str,
    fps: int = DEFAULT_FPS,
    canvas: tuple[int, int] = DEFAULT_CANVAS,
) -> dict[str, Any]:
    """从 `scripts` 表的一行直接排分镜（`draft_from_script` + `storyboard_from_draft`）。"""
    return storyboard_from_draft(
        draft_from_script(script), job_root=job_root, fps=fps, canvas=canvas
    )


def summarise_shots(rows: Sequence[Mapping[str, Any]]) -> str:
    """给人看的一行摘要（CLI 用）。"""
    if not rows:
        return "没有镜头"
    kinds: dict[str, int] = {}
    for row in rows:
        key = str(row.get("asset_type") or "")
        kinds[key] = kinds.get(key, 0) + 1
    detail = "、".join(f"{key} {value}" for key, value in sorted(kinds.items()))
    return f"{len(rows)} 个镜头（{detail}）"


__all__ = [
    "MAX_CAPTION_CHARS",
    "NOTES_REL",
    "STORYBOARD_REL",
    "ScriptStoryboardError",
    "draft_from_script",
    "script_storyboard",
    "storyboard_from_draft",
    "summarise_shots",
]
