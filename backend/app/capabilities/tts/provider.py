"""语音合成契约：provider 抽象、请求/结果类型、统一错误。

V5.0 要求「TTS 要真实接入并评估本地/Edge TTS，支持生成/处理/字幕/时间轴同步/校验/音量」。
本模块只定义契约与时间轴对齐所需的数据结构，具体 provider 见
:mod:`edge`（在线、免费、给真实词级时间）与 :mod:`sapi`（离线兜底，无词级时间）。

诚实边界：拿不到词级时间的 provider 必须把 ``estimated`` 标为 ``True``，
字幕按字符比例均分，并在结果里写明——绝不把估算时间说成实测时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable


class TtsError(RuntimeError):
    """合成失败。``reason`` 是稳定错误码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True, slots=True)
class TtsVoice:
    name: str
    locale: str = ""
    gender: str = ""
    provider: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "locale": self.locale,
            "gender": self.gender,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class WordTiming:
    """一个词/字在音轨上的起止时间（秒）。"""

    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class TtsRequest:
    text: str
    voice: str = "zh-CN-YunxiNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("要合成的文本不能为空")


@dataclass(frozen=True, slots=True)
class TtsResult:
    """一次合成的产物与可追溯信息。"""

    path: str
    provider: str
    voice: str
    text: str
    duration_seconds: float
    byte_size: int
    sha256: str
    words: tuple[WordTiming, ...] = ()
    sample_rate: int = 0
    estimated: bool = False
    meta: Any = field(default_factory=dict)

    @property
    def has_word_timings(self) -> bool:
        return bool(self.words) and not self.estimated

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "provider": self.provider,
            "voice": self.voice,
            "text": self.text,
            "duration_seconds": round(self.duration_seconds, 3),
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "word_count": len(self.words),
            "sample_rate": self.sample_rate,
            "estimated": self.estimated,
            "words": [
                {"text": word.text, "start": round(word.start, 3), "end": round(word.end, 3)}
                for word in self.words
            ],
            "meta": dict(self.meta) if isinstance(self.meta, dict) else {},
        }


@runtime_checkable
class TtsProvider(Protocol):
    """一个可用的语音合成后端。"""

    name: str

    def available(self) -> bool: ...

    def list_voices(self, *, locale_prefix: str = "zh") -> Sequence[TtsVoice]: ...

    def synthesize(self, request: TtsRequest, out_path: Path) -> TtsResult: ...


def estimate_word_timings(text: str, duration: float) -> tuple[WordTiming, ...]:
    """无词级时间时的均分估算（按字符数比例），并让调用方标 ``estimated``。

    中文按字、英文按词切分；只用于字幕排版，不用于音画同步校验。
    """
    if duration <= 0:
        return ()
    tokens = [token for token in _tokenize(text) if token]
    if not tokens:
        return ()
    weights = [max(1, len(token)) for token in tokens]
    total = sum(weights)
    timings: list[WordTiming] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        timings.append(WordTiming(text=token, start=cursor, end=cursor + span))
        cursor += span
    return tuple(timings)


_CJK_RANGE = ("\u4e00", "\u9fff")
_PUNCT = set("，。！？、；：\u201c\u201d\u2018\u2019（）《》—…·")


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    buffer = ""
    for char in text:
        if _CJK_RANGE[0] <= char <= _CJK_RANGE[1] or char in _PUNCT:
            if buffer:
                tokens.append(buffer)
                buffer = ""
            tokens.append(char)
        elif char.isspace():
            if buffer:
                tokens.append(buffer)
                buffer = ""
        else:
            buffer += char
    if buffer:
        tokens.append(buffer)
    return tokens


__all__ = [
    "TtsError",
    "TtsProvider",
    "TtsRequest",
    "TtsResult",
    "TtsVoice",
    "WordTiming",
    "estimate_word_timings",
]
