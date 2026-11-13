"""把一份研究底稿（digest）变成可渲染的分镜表——纯规则，不用模型。

为什么需要这一步：V5.0 要求"从报告直接建视频任务"，但真实模型当前是阻塞状态
（没有 API Key）。阻塞不该让整条生产线停摆，也不该让程序假装写了脚本。所以这里
只做**机械转换**：把底稿里已经落库、已经带来源编号的数据点排成镜头，每一镜的
口播稿就是那句原文，屏幕上打出来的是它来自哪个来源。

这个模块刻意不做的事（写在这里是为了以后有人想"顺手加一点"时先看见边界）：

* 不写观点、不做因果推断、不生成文案——一句话只能来自底稿里的原文；
* 不挑选"传播价值高"的句子——那是模型或人的工作，规则只按顺序取；
* 不补全缺失字段：素材缺口就让后续渲染阶段如实报错。

所以产物文件名带 ``OUTLINE.md`` 说明它是规则草稿：谁想把它当成品直接发布，
先看这份说明。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.app.media.storyboard import DEFAULT_CANVAS, DEFAULT_FPS, load_storyboard

STORYBOARD_REL = Path("storyboard") / "storyboard.csv"
NOTES_REL = Path("storyboard") / "OUTLINE.md"

#: 每张数据卡停留多久。4.5 秒是旁白念一句 20~30 字比较自然的长度。
SECONDS_PER_POINT = 4.5

#: 开场与结尾卡的时长。
SECONDS_OPENING = 4.0
SECONDS_CLOSING = 5.0

#: 流水线内部阶段名，会出现在报告标题前面（"结论整合：…"）。念给观众听时要剥掉。
_STAGE_LABELS = (
    "结论整合：",
    "结论整合:",
    "事实核查（强制闸门）：",
    "事实核查：",
    "分析：",
    "资料收集：",
    "研究规划：",
    "底稿：",
)

#: 一屏最多放几个数据点。超过就别塞，宁可少讲两句。
MAX_POINTS = 6

#: 字幕一行放不下太长的句子，超出就截断并加省略号（口播稿保持完整）。
#: 46px 字号下 1080 宽画布一行约能放 18 个汉字，26 个会顶出画外（实测被裁）。
MAX_CAPTION_CHARS = 18

_COLORS = "#1d2a44"
# 每个镜头都要有运动：纯色卡静止不动会被 freezedetect 判成冻结（Phase 2 实测
# 66.8 秒冻结）。这里全部用轻微推镜/摇镜，不用 static。
# 纯色底板是"整幅渐变视频"，一平移就会把画面挪出素材边界、露出比底板更亮的画布
# 底色（抽帧实测在右边露出一条竖条）。所以底板只用推/冲击这类纯缩放运镜；
# 数据卡自带与画布同色的底，可以放心平移。
_PLATE_CAMERAS = ("push", "push", "push", "push")
_CARD_CAMERAS = ("push", "pan_right", "punch", "pan_up", "push")
_TRANSITIONS = ("none", "dissolve", "slide", "dissolve", "wipe", "dip")

#: 数字后面紧跟这些词时它是日历的一部分（"11月2日"、"2024年"、"第三季度"），
#: 不是可上卡面的"量"。
_CALENDAR_SUFFIXES = ("月", "日", "号", "年", "季度", "月份", "财年", "年期")

#: 数字后面跟着这些才算"带单位"，卡面要把单位一起写出来。
_UNITS = ("个百分点", "亿元", "万元", "亿美元", "万亿", "亿", "倍", "%", "％")

#: 正则里的单位分支（长的写在前面，否则"亿元"会被"亿"抢先吃掉一半）。
#: 只认"量"的单位：家/人/之类会把"42家A股上市银行…营业收入4.28万亿元"的 42
#: 顶到卡面，而那句话真正要讲的是 4.28 万亿元。
_UNIT_PATTERN = r"\s*(%|％|个百分点|亿美元|亿元|万元|万亿|亿|倍)"


class OutlineError(ValueError):
    """分镜草稿生成失败。``reason`` 稳定，供接口与测试判断。

    取值：``insufficient_sources``、``no_data_points``、``bad_arguments``。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class OutlineShot:
    shot_id: str
    seconds: float
    narration: str
    subtitle: str
    source_id: str
    number: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "seconds": self.seconds,
            "narration": self.narration,
            "subtitle": self.subtitle,
            "source_id": self.source_id,
            "number": self.number,
        }


@dataclass(frozen=True)
class OutlineResult:
    job_id: str
    storyboard_path: Path
    notes_path: Path
    shots: tuple[OutlineShot, ...]
    total_seconds: float
    frames: int
    source_ids: tuple[str, ...]
    warnings: tuple[str, ...] = field(default=())

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "storyboard": str(self.storyboard_path),
            "notes": str(self.notes_path),
            "shots": [shot.as_dict() for shot in self.shots],
            "shot_count": len(self.shots),
            "total_seconds": round(self.total_seconds, 3),
            "frames": self.frames,
            "source_ids": list(self.source_ids),
            "warnings": list(self.warnings),
            "generated_by": "rules",
        }


def _caption(text: str) -> str:
    """屏幕字幕：取原文的一段，不重写句子。

    优先在标点处断开（读起来是完整一句），实在没有标点才硬截并加省略号。
    """
    clean = " ".join(str(text).split())
    if len(clean) <= MAX_CAPTION_CHARS:
        return clean
    head = clean[:MAX_CAPTION_CHARS]
    cut = max(head.rfind(mark) for mark in "，、；：。！？")
    if cut >= MAX_CAPTION_CHARS // 2:
        return head[: cut + 1]
    return clean[: MAX_CAPTION_CHARS - 1] + "…"


def _is_calendar_number(text: str, number: str) -> bool:
    """这个数字是不是"日期/年份"的一部分（11月2日、2024年、第三季度…）。

    卡面主数字要的是"量"（1.69%、1362.90亿元），不是日历。真实素材里
    「11月2日，多家电商平台的尾款支付…」这种句子会把 11 顶到卡面正中，
    写成一个巨大的、毫无含义的"11"——所以日期数字必须排除。

    判定规则：这个数字在原文里出现过的**每一处**后面都紧跟日历词，才算日历数字；
    只要有一处是当量用的（"11亿" "11%"），就仍然算数字。
    """
    start = 0
    seen = False
    while True:
        index = text.find(number, start)
        if index < 0:
            return seen
        rest = text[index + len(number) :].lstrip()
        if not rest[:1] or rest[:1] not in _CALENDAR_SUFFIXES:
            return False
        seen = True
        start = index + len(number)


def _number_set(point: Any) -> tuple[str, ...]:
    """一句话里的关键数字（去掉年份与日期）。用来挡住"同一组数字换个说法再来一遍"。

    注意：从数据库行还原的底稿（`handoff._digest_from_rows`）只有 `text`，`numbers` 是空的。
    这时必须自己从原文里抠数字，否则卡面数字全是空、整条片子退化成纯色底。
    """
    try:
        from backend.app.research.corroborate import key_numbers

        numbers = tuple(key_numbers(point))
    except Exception:  # noqa: BLE001 - 规则层不该因为分类器出问题而罢工
        numbers = tuple(str(number) for number in getattr(point, "numbers", ()) or ())
    if numbers:
        return tuple(sorted(set(numbers)))
    from backend.app.research.digest import _numbers_in
    from backend.app.research.corroborate import _YEAR

    text = str(getattr(point, "text", "") or "")
    return tuple(
        sorted(
            {
                number
                for number in _numbers_in(text)
                if not _YEAR.match(number) and not _is_calendar_number(text, number)
            }
        )
    )


def _number_with_unit(text: str, numbers: Sequence[str]) -> str:
    """取第一个带单位的数字（"1.52%" 而不是 "1.52"）；没有任何带单位的数字就返回空串。

    卡片上只写 "1.52" 会被读成"1.52 个什么东西"，单位必须在场；而单位是原文里
    紧跟在数字后面的那部分，所以直接回原文里抓，不自造映射表。

    **没有单位就不上卡**：真实抓到的句子里还有「21深度」「11月2日」这种，把 21 或 11
    写成巨大的标题数字纯属误导。宁可让这个镜头用底板，也不让错的数字上屏。

    "第一个"按**原文出现位置**算，不按数字大小：一句话里同时有 "降至1.52%" 和
    "收窄0.03个百分点" 时，卡面要的是先说的那个主数字，不是排序后最小的那个。
    """
    best: tuple[int, str] | None = None
    for number in numbers:
        match = re.search(re.escape(number) + _UNIT_PATTERN, text)
        if match is None:
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), f"{number}{match.group(1)}")
    return best[1] if best is not None else ""


def caption_around(text: str, number: str) -> str:
    """公开版：给定原句与要上卡的数字，返回一屏放得下的说明（数字与单位必须完整）。

    脚本分镜（`backend/app/content/stages.py`）复用同一套口径，避免两处各写一份
    "怎么把句子截到 18 个字还不切断数字"。
    """

    return _caption_around(text, number)


def _caption_around(text: str, number: str) -> str:
    """取这个数字前后的那一小段（不超过一行）做卡面说明。

    "2024年第四季度，净息差环比下降0.01个百分点" 的关键信息在数字附近；直接截
    前 18 个字只会得到"2024年第四季度，"，卡片上就只剩一个孤零零的数字。

    两条硬规矩：**数字与单位必须完整**（不能切出"1362."这种半截数），**从句子
    中间切进来必须加前导省略号**（否则读成"度的净息差环比下降"莫名其妙）。
    """
    bare = number
    for unit in _UNITS:
        if bare.endswith(unit):
            bare = bare[: -len(unit)]
            break
    bare = bare.strip()
    if not bare:
        return _caption(text)
    index = text.find(bare)
    if index < 0:
        return _caption(text)
    end = index + len(bare)
    for unit in _UNITS:  # 数字后面的单位一并算进"必须完整"的范围
        if text.startswith(unit, end):
            end += len(unit)
            break
    start = 0
    for mark in "，,；;。！？：｜| \n":
        position = text.rfind(mark, 0, index)
        if position >= 0:
            start = max(start, position + 1)
    # 不要把年份切成"24年"这种半截：起点落在数字串里就把整串带上
    while start > 0 and text[start].isdigit() and text[start - 1].isdigit():
        start -= 1
    if end - start <= MAX_CAPTION_CHARS:
        return text[start:end].strip(" ，,；;。！？：｜|")
    # 一行装不下"起点到数字"：让数字收在行尾，数字本身必须完整
    head = max(0, end - MAX_CAPTION_CHARS)
    while head > 0 and text[head].isdigit() and text[head - 1].isdigit():
        head -= 1
    window = text[head:end].strip(" ，,；;。！？：｜|")
    if head > 0:
        # 从句子中间切进来必须留省略号；但"…的净息差"这种以虚词打头仍然难读，
        # 再往前吃掉一两个虚词，读起来才像"…净息差环比下降"。
        while window[:1] in ("的", "地", "得", "了", "是", "在", "为", "与", "和", "、"): 
            window = window[1:]
        window = "…" + window
    return window


def _short_title(title: str, *, limit: int = 24) -> str:
    """把底稿标题压成一句能念的短标题：取第一个句子，或前 ``limit`` 个字。

    研究任务的标题往往就是整句研究目标，原样念出来开场要二十多秒；这里只取
    "这一段讲什么"的那半句，剩下的留给正文。流水线阶段名（结论整合、事实核查…）
    是内部标签，念出来像机器在自言自语，先剥掉。
    """

    text = " ".join(str(title).split())
    for label in _STAGE_LABELS:
        if text.startswith(label):
            text = text[len(label) :].lstrip("：: ")
            break
    for mark in ("。", "！", "？", "；", ";", "!", "?"):
        head, sep, _ = text.partition(mark)
        if sep and head.strip():
            text = head.strip()
            break
    if len(text) > limit:
        cut = text[:limit]
        # 别把一个数字切成半截（"…研究隆基绿能 2" 这种看着像错字）
        while cut and (cut[-1].isdigit() or cut[-1].isspace()):
            cut = cut[:-1]
        cut = cut.rstrip("，,、：: （(")
        text = (cut or text[:limit]) + "…"
    return text or "未命名研究"


def _points(digest: Any, limit: int) -> list[Any]:
    """按来源顺序取数据点：每来源最多两条，同一组数字/同一句话只讲一次。"""
    picked: list[Any] = []
    per_source: dict[str, int] = {}
    seen_numbers: set[tuple[str, ...]] = set()
    seen_text: set[str] = set()
    for point in getattr(digest, "data_points", ()) or ():
        source_id = str(getattr(point, "source_id", "") or "")
        text = str(getattr(point, "text", "") or "").strip()
        if not source_id or not text:
            continue
        if per_source.get(source_id, 0) >= 2:
            continue
        # 不同来源常常一字不改地转述同一句通稿，屏幕上不能出现两次一样的话
        signature = "".join(text.split())[:20]
        numbers = _number_set(point)
        if signature in seen_text or (numbers and numbers in seen_numbers):
            continue
        picked.append(point)
        per_source[source_id] = per_source.get(source_id, 0) + 1
        seen_text.add(signature)
        if numbers:
            seen_numbers.add(numbers)
        if len(picked) >= limit:
            break
    return picked


def build_outline(
    digest: Any,
    *,
    job_root: Path,
    job_id: str | None = None,
    fps: int = DEFAULT_FPS,
    canvas: tuple[int, int] = DEFAULT_CANVAS,
    max_points: int = MAX_POINTS,
) -> OutlineResult:
    """把底稿排成开场 + N 张数据卡 + 结尾的分镜表，并校验能通过 ``load_storyboard``。"""
    if max_points < 1:
        raise OutlineError("bad_arguments", "max_points 至少为 1")
    root = Path(job_root)
    sources = tuple(getattr(digest, "sources", ()) or ())
    if len(sources) < 2:
        raise OutlineError(
            "insufficient_sources",
            f"只有 {len(sources)} 个来源，证据不足，不做视频分镜（至少 2 个来源）",
        )

    points = _points(digest, max_points)
    if not points:
        raise OutlineError("no_data_points", "底稿里没有可引用的数据点，先补充资料再排分镜")

    title = str(getattr(digest, "title", "") or "").strip() or "未命名研究"
    # 底稿标题常常就是整句研究目标（三四十字），直接念出来开场会拖到二十多秒、
    # 画面上还要被截断。开场只念"这是什么"，取第一个句子或前 N 个字（以先到者为准）。
    spoken = _short_title(title)
    openings = OutlineShot(
        shot_id="sh01",
        seconds=SECONDS_OPENING,
        narration=f"这一条讲的是：{spoken}。下面每一句话都能追到原文。",
        subtitle=_short_title(title, limit=MAX_CAPTION_CHARS),
        source_id="",
    )
    shots: list[OutlineShot] = [openings]
    for index, point in enumerate(points):
        text = " ".join(str(point.text).split())
        numbers = _number_set(point)
        shots.append(
            OutlineShot(
                shot_id=f"sh{index + 2:02d}",
                seconds=SECONDS_PER_POINT,
                narration=text,
                subtitle=_caption(text),
                source_id=str(point.source_id),
                number=_number_with_unit(text, numbers),
            )
        )
    source_ids = tuple(dict.fromkeys(shot.source_id for shot in shots if shot.source_id))
    cited = "、".join(source_ids)
    closing = OutlineShot(
        shot_id=f"sh{len(shots) + 1:02d}",
        seconds=SECONDS_CLOSING,
        narration=(
            f"以上 {len(points)} 个数字来自 {len(sources)} 份资料（{cited}）。"
            "这只是资料汇编，不是投资建议。"
        ),
        subtitle="资料汇编 · 不是投资建议",
        source_id="",
    )
    shots.append(closing)

    rows: list[dict[str, str]] = []
    cards: list[Any] = []
    cursor = 0.0
    for index, shot in enumerate(shots):
        start = round(cursor, 3)
        end = round(cursor + shot.seconds, 3)
        cursor = end
        # 有数字的镜头做成数据卡：数字放大、来源标小。纯色底只留给开场与结尾。
        card = None
        if shot.number and 0 < index < len(shots) - 1:
            from backend.app.video.cards import write_card

            card = write_card(
                root,
                asset_id=f"CARD_{shot.shot_id}",
                number=shot.number,
                caption=_caption_around(shot.narration, shot.number),
                source_id=shot.source_id,
                frames=max(2, int(round(shot.seconds * fps))),
                width=canvas[0],
                height=canvas[1],
            )
            cards.append(card)
        rows.append(
            {
                "shot_id": shot.shot_id,
                "start_time": f"{start:g}",
                "end_time": f"{end:g}",
                "narrative_purpose": "开场" if index == 0 else (
                    "收尾" if index == len(shots) - 1 else "引用数据点"
                ),
                "narration": shot.narration,
                "visual_description": (
                    "纯色底 + 大字排版" if card is None else "数据卡：放大数字 + 来源标注"
                ),
                "asset_id": card.asset_id if card else "",
                "asset_type": "composition" if card else "solid",
                "asset_source": card.relative_path if card else _COLORS,
                "license": "原创（程序生成）" if card else "原创（纯色底，程序生成）",
                "animation": "fade_in",
                "camera_motion": (
                    _CARD_CAMERAS[index % len(_CARD_CAMERAS)]
                    if card
                    else _PLATE_CAMERAS[index % len(_PLATE_CAMERAS)]
                ),
                "transition": _TRANSITIONS[index % len(_TRANSITIONS)],
                # 卡片上已经有这句说明了，底部再叠一条同样的字幕是重复。
                "subtitle": "" if card else shot.subtitle,
                "sound_effect": "none",
                "safety_zone": "bottom",
                "fps": str(fps),
                "canvas_width": str(canvas[0]),
                "canvas_height": str(canvas[1]),
                "notes": f"规则草稿；来源 {shot.source_id}" if shot.source_id else "规则草稿",
                # 可追溯性落到结构化列上：内容质检靠这一列判「这一镜的说法能不能回溯」
                "source_ids": shot.source_id,
                "source_start": "",
            }
        )

    storyboard_path = root / STORYBOARD_REL
    storyboard_path.parent.mkdir(parents=True, exist_ok=True)
    _write_rows(storyboard_path, rows)
    # 写完立刻按正式格式读回来：宁可现在报错，也不要让渲染阶段才发现分镜不合法。
    load_storyboard(storyboard_path)
    if cards:
        # 数据卡要进素材清单：没登记的素材，渲染阶段一律拒收。
        from backend.app.video.cards import register_cards

        register_cards(
            root,
            cards,
            purposes={card.asset_id: f"数据卡 {card.asset_id}" for card in cards},
        )

    notes_path = root / NOTES_REL
    notes_path.write_text(_notes_text(job_id or root.name, title, shots, sources), encoding="utf-8")

    total = round(cursor, 3)
    warnings = (
        f"只引用了 {len(sources)} 份资料里的 {len(source_ids)} 份",
    ) if len(source_ids) < len(sources) else ()

    return OutlineResult(
        job_id=job_id or root.name,
        storyboard_path=storyboard_path,
        notes_path=notes_path,
        shots=tuple(shots),
        total_seconds=total,
        frames=int(round(total * fps)),
        source_ids=source_ids,
        warnings=warnings,
    )


def retime_rows(
    rows: Sequence[dict[str, Any]],
    durations: Mapping[str, float],
    *,
    pad: float = 0.35,
    min_seconds: float = 2.0,
    fps: int = 30,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按实际配音时长重排每个镜头的起止时间，返回 (新行, 说明)。

    口播稿多长，镜头就该多长——否则 4 秒的镜头里塞 8 秒旁白，后半句会被切掉。
    这里只改 `start_time`/`end_time`，不动文案、素材与运镜。时间一律对齐到整帧，
    因为分镜校验要求「起止时间必须落在整帧上」。
    """
    frame = 1.0 / max(fps, 1)

    def frames_of(seconds: float) -> int:
        return int(round(seconds * fps))

    updated: list[dict[str, Any]] = []
    notes: list[str] = []
    cursor_frames = 0
    for row in rows:
        shot_id = str(row.get("shot_id", ""))
        span = float(row["end_time"]) - float(row["start_time"])
        need = span
        measured = 0.0
        if durations:
            measured = float(durations.get(shot_id, 0.0))
            # 有配音就以配音为准：短了就缩、长了就拉。只放大不缩小会在镜头尾部
            # 留下几秒纯静音（`silencedetect` 直接判失败），而且观众会觉得卡住了。
            # 没配到音的镜头（measured == 0）保持原时长，不能压成 min_seconds。
            if measured > 0:
                need = max(min_seconds, measured + pad)
        length_frames = (
            frames_of(need)
            if measured > 0
            else max(frames_of(span), frames_of(need))
        )
        start_frames = cursor_frames
        end_frames = start_frames + max(length_frames, 1)
        # 分镜校验要求「起始帧对齐误差 < 1e-6 秒」，所以秒数按 帧/帧率 精确写出，
        # 不能只保留三位小数（8.267 秒 × 30 帧 = 248.01 帧，会被判非法）。
        new_row = dict(row)
        new_row["start_time"] = f"{start_frames * frame:.9f}"
        new_row["end_time"] = f"{end_frames * frame:.9f}"
        if (end_frames - start_frames) * frame > span + 1e-9:
            notes.append(
                f"{shot_id}: {span:g}s → {(end_frames - start_frames) * frame:.2f}s"
                f"（配音 {durations.get(shot_id, 0.0):.2f}s）"
            )
        elif (end_frames - start_frames) * frame < span - 1e-9:
            notes.append(
                f"{shot_id}: {span:g}s → {(end_frames - start_frames) * frame:.2f}s"
                f"（配音只有 {durations.get(shot_id, 0.0):.2f}s，缩到配音长度）"
            )
        cursor_frames = end_frames
        updated.append(new_row)
    return updated, notes


def retime_storyboard(
    storyboard: Any,
    durations: Mapping[str, float],
    *,
    pad: float = 0.35,
    min_seconds: float = 2.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """把已解析的分镜按配音时长重排；返回可直接写盘的行。"""
    rows: list[dict[str, Any]] = []
    for shot in storyboard.shots:
        row: dict[str, Any] = {
            column: getattr(shot, column, "")
            for column in (
                "shot_id",
                "narrative_purpose",
                "narration",
                "visual_description",
                "asset_id",
                "asset_type",
                "asset_source",
                "license",
                "animation",
                "camera_motion",
                "transition",
                "subtitle",
                "sound_effect",
                "safety_zone",
                "notes",
                "source_start",
                "source_ids",
            )
        }
        row["start_time"] = f"{shot.start_time:.3f}"
        row["end_time"] = f"{shot.end_time:.3f}"
        # 这一镜引用了哪几份资料：写进 `source_ids` 列，别只藏在 notes 的自由文本里
        row["source_ids"] = getattr(shot, "source_ids", "") or ""
        rows.append(row)
    return retime_rows(rows, durations, pad=pad, min_seconds=min_seconds, fps=storyboard.fps)


def _write_rows(path: Path, rows: Sequence[dict[str, str]]) -> None:
    header = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _notes_text(job_id: str, title: str, shots: Iterable[OutlineShot], sources: Sequence[Any]) -> str:
    lines = [
        f"# {job_id} 分镜草稿（规则生成）",
        "",
        f"- 标题：{title}",
        "- 生成方式：**纯规则**（`backend/app/video/outline.py`），没有调用任何模型。",
        "- 每镜的口播稿都是底稿里的原文，没有改写、没有推断、没有补全。",
        "- 它是一份可渲染的草稿，不是可以直接发布的文案。发布前必须人工看一遍。",
        "",
        "## 逐镜来源",
        "",
        "| 镜头 | 时长 | 来源 | 口播稿（原文） |",
        "| --- | --- | --- | --- |",
    ]
    for shot in shots:
        lines.append(
            f"| {shot.shot_id} | {shot.seconds:g}s | {shot.source_id or '—'} | {shot.narration} |"
        )
    lines += ["", "## 本文引用到的资料", ""]
    keep = {shot.source_id for shot in shots if shot.source_id}
    for source in sources:
        source_id = str(getattr(source, "source_id", "") or "")
        if source_id not in keep:
            continue
        lines.append(
            f"- `{source_id}` {getattr(source, 'title', '')} · {getattr(source, 'url', '')}"
            f" · 发布 {getattr(source, 'published_at', '') or '未解析到'}"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "MAX_POINTS",
    "NOTES_REL",
    "OutlineError",
    "OutlineResult",
    "OutlineShot",
    "SECONDS_PER_POINT",
    "STORYBOARD_REL",
    "build_outline",
    "caption_around",
]
