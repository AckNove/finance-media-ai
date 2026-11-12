"""字幕排版：把词级时间合成一句句字幕，再输出 SRT / ASS。

排版规则（都是为了"人能读"）：

* 一行最多 ``max_chars`` 个字符（中文按 1 个宽度算），超了就换行/断句；
* 一句字幕最长 ``max_duration`` 秒，太长就按词切；
* 相邻词间隔超过 ``max_gap`` 秒就断开（那是说话人在停顿）；
* 句末标点（。！？；）一定断开。

时间来自 :class:`backend.app.capabilities.tts.provider.WordTiming`：
**有词级实测时间就用实测，没有就由调用方传入估算值并自己标 estimated**——
本模块不猜，也不隐藏来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from backend.app.capabilities.tts.provider import WordTiming

DEFAULT_MAX_CHARS = 18
DEFAULT_MAX_DURATION = 4.5
DEFAULT_MAX_GAP = 0.6

_BREAK_PUNCT = set("。！？；!?;")
_SOFT_PUNCT = set("，、：,:…—")


@dataclass(frozen=True, slots=True)
class Cue:
    """一条字幕：起止时间 + 文本。"""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict[str, object]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


def group_words_into_cues(
    words: Sequence[WordTiming],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_duration: float = DEFAULT_MAX_DURATION,
    max_gap: float = DEFAULT_MAX_GAP,
) -> tuple[Cue, ...]:
    """把词级时间聚成字幕。空输入返回空元组。"""
    cues: list[Cue] = []
    buffer: list[WordTiming] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = "".join(word.text for word in buffer).strip()
        if text:
            cues.append(Cue(start=buffer[0].start, end=buffer[-1].end, text=text))
        buffer = []

    for word in words:
        if buffer:
            previous = buffer[-1]
            gap = word.start - previous.end
            projected = sum(len(item.text) for item in buffer) + len(word.text)
            too_long = projected > max_chars
            too_slow = (word.end - buffer[0].start) > max_duration
            if gap > max_gap or too_long or too_slow:
                flush()
        buffer.append(word)
        if word.text and word.text[-1] in _BREAK_PUNCT:
            flush()
        elif word.text and word.text[-1] in _SOFT_PUNCT and sum(len(i.text) for i in buffer) >= max_chars - 4:
            flush()
    flush()
    return tuple(cues)


def _srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def cues_to_srt(cues: Iterable[Cue]) -> str:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{_srt_time(cue.start)} --> {_srt_time(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines)


def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_cs, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, centis = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: AK,{font},{size},&H00FFFFFF,&H000000FF,&H00101820,&H80000000,0,0,0,0,100,100,0,0,1,{outline},1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def cues_to_ass(
    cues: Iterable[Cue],
    *,
    width: int = 1080,
    height: int = 1920,
    font: str = "AKSC",
    size: int = 46,
    outline: int = 3,
    margin_lr: int = 90,
    margin_v: int = 210,
) -> str:
    """生成 ASS 字幕（竖屏安全区默认留 210px 底边距，不压平台 UI）。"""
    header = ASS_HEADER.format(
        width=width,
        height=height,
        font=font,
        size=size,
        outline=outline,
        margin_lr=margin_lr,
        margin_v=margin_v,
    )
    lines: list[str] = [header]
    for cue in cues:
        text = cue.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},AK,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def shift_cues(cues: Sequence[Cue], offset: float) -> tuple[Cue, ...]:
    """整段平移（把单句音轨对齐到它在整片里的起点）。"""
    return tuple(
        Cue(start=cue.start + offset, end=cue.end + offset, text=cue.text) for cue in cues
    )


def total_duration(cues: Sequence[Cue]) -> float:
    return max((cue.end for cue in cues), default=0.0)


__all__ = [
    "ASS_HEADER",
    "Cue",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_DURATION",
    "DEFAULT_MAX_GAP",
    "cues_to_ass",
    "cues_to_srt",
    "group_words_into_cues",
    "shift_cues",
    "total_duration",
]
