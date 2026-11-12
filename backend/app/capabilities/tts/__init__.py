"""语音合成：契约 + edge-tts（在线，真实词级时间）+ SAPI（离线，估算时间）。

对外只需要两件事：

* :func:`synthesize` —— 给文本和输出路径，拿到音频文件 + 词级时间 + 字幕句；
* :func:`captions` —— 把一次合成结果直接变成可烧进视频的字幕。

``provider="auto"`` 的顺序是 ``edge`` → ``sapi``：能用真实词级时间的先用，
不可用时退到离线，并在 ``TtsResult.estimated`` 里如实标出来。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from backend.app.capabilities.tts.edge import DEFAULT_VOICE, EdgeTtsProvider
from backend.app.capabilities.tts.provider import (
    TtsError,
    TtsProvider,
    TtsRequest,
    TtsResult,
    TtsVoice,
    WordTiming,
    estimate_word_timings,
)
from backend.app.capabilities.tts.sapi import SapiTtsProvider
from backend.app.capabilities.tts.subtitle import (
    Cue,
    cues_to_ass,
    cues_to_srt,
    group_words_into_cues,
    shift_cues,
    total_duration,
)

PROVIDER_ORDER = ("edge", "sapi")


def get_tts_provider(name: str) -> TtsProvider:
    if name == "edge":
        return EdgeTtsProvider()
    if name == "sapi":
        return SapiTtsProvider()
    raise TtsError("unknown_provider", f"未知的语音提供方：{name}")


def available_providers() -> dict[str, bool]:
    status: dict[str, bool] = {}
    for name in PROVIDER_ORDER:
        try:
            status[name] = get_tts_provider(name).available()
        except TtsError:
            status[name] = False
    return status


def synthesize(
    text: str,
    out_path: Path | str,
    *,
    provider: str = "auto",
    voice: str | None = None,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> TtsResult:
    """合成一段文本。``provider="auto"`` 按 :data:`PROVIDER_ORDER` 逐个尝试。"""
    target = Path(out_path)
    candidates: Sequence[str]
    if provider == "auto":
        candidates = PROVIDER_ORDER
    else:
        candidates = (provider,)

    errors: list[str] = []
    for name in candidates:
        backend = get_tts_provider(name)
        if not backend.available():
            errors.append(f"{name}: unavailable")
            continue
        chosen_voice = voice or (DEFAULT_VOICE if name == "edge" else "")
        if name == "sapi" and target.suffix.lower() != ".wav":
            # SAPI 只能写 wav：换个临时名，交给调用方后续转码
            target_for_backend = target.with_suffix(".wav")
        else:
            target_for_backend = target
        request = TtsRequest(
            text=text,
            voice=chosen_voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        try:
            result = backend.synthesize(request, target_for_backend)
        except TtsError as exc:
            errors.append(f"{name}: {exc.reason}")
            continue
        if target_for_backend != target:
            meta = dict(result.meta) if isinstance(result.meta, dict) else {}
            meta["requested_path"] = str(target)
            return TtsResult(
                path=result.path,
                provider=result.provider,
                voice=result.voice,
                text=result.text,
                duration_seconds=result.duration_seconds,
                byte_size=result.byte_size,
                sha256=result.sha256,
                words=result.words,
                sample_rate=result.sample_rate,
                estimated=result.estimated,
                meta=meta,
            )
        return result

    raise TtsError("no_provider", "没有可用的语音提供方：" + "; ".join(errors))


def captions(
    result: TtsResult,
    *,
    max_chars: int = 18,
    max_duration: float = 4.5,
    max_gap: float = 0.6,
) -> tuple[Cue, ...]:
    """把合成结果的词级时间变成字幕句。没有词级时间时按估算时间排。"""
    words: Iterable[WordTiming] = result.words
    if not words and result.duration_seconds > 0:
        words = estimate_word_timings(result.text, result.duration_seconds)
    return group_words_into_cues(
        tuple(words),
        max_chars=max_chars,
        max_duration=max_duration,
        max_gap=max_gap,
    )


__all__ = [
    "Cue",
    "DEFAULT_VOICE",
    "EdgeTtsProvider",
    "PROVIDER_ORDER",
    "SapiTtsProvider",
    "TtsError",
    "TtsProvider",
    "TtsRequest",
    "TtsResult",
    "TtsVoice",
    "WordTiming",
    "available_providers",
    "captions",
    "cues_to_ass",
    "cues_to_srt",
    "estimate_word_timings",
    "get_tts_provider",
    "group_words_into_cues",
    "shift_cues",
    "synthesize",
    "total_duration",
]
