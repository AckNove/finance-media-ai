"""声音：程序化原创配乐与音效，加旁白混音。

为什么自己合成而不是找曲子：任务书要求"配音、音乐、音效必须有可追溯来源及使用许可"。
自己用正弦/三角波算出来的东西，来源就是这份代码，许可就是本仓库——比在网上找一首
"免费商用"的曲子更干净，也不会哪天被版权方追着要授权。

三条口径：

1. **只用标准库**：不引入 numpy/soundfile，48kHz 单声道 47 秒大约 200 万采样，
   纯 Python 算一遍是秒级的事，换来的是"这条链在哪个 Python 里都能跑"。
2. **确定性**：所有随机量都用固定 seed 的线性同余生成，同一个参数永远同一首曲子。
3. **配乐只做底**：默认压在 −23 dBFS 左右，不跟旁白抢；不做自动闪避（律动一抽一抽
   反而更吵），靠电平差让人声始终在最前面。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Iterable, Sequence

SAMPLE_RATE = 48_000
MUSIC_LEVEL = 0.07          # 配乐基准幅度（约 −23 dBFS）
SFX_LEVEL = 0.5
TWO_PI = 2.0 * math.pi


class _Lcg:
    """固定种子的线性同余发生器：要"随机感"，但不要"每次不一样"。"""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF or 1

    def next_float(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF

    def between(self, low: float, high: float) -> float:
        return low + (high - low) * self.next_float()


def _envelope(t: float, duration: float, attack: float, release: float) -> float:
    if t < 0 or t > duration:
        return 0.0
    if t < attack:
        return t / attack
    if t > duration - release:
        return max(0.0, (duration - t) / release)
    return 1.0


def music_samples(
    duration: float,
    *,
    bpm: float = 66.0,
    seed: int = 7,
    sample_rate: int = SAMPLE_RATE,
    level: float = MUSIC_LEVEL,
) -> list[float]:
    """一段氛围底乐：低频持续音 + 三音和声垫 + 每两拍的轻微脉冲。

    听感目标是"有推进但不抢戏"——知识类视频里配乐的作用是托住旁白，不是表现自己。
    """
    total = int(duration * sample_rate)
    samples = [0.0] * total
    rng = _Lcg(seed)
    beat = 60.0 / bpm

    # 和声：A 小调味道的三个音（A2 / C3 / E3），每个加一点失谐，避免"电子味"
    root = 110.0
    chord = [root, root * 1.1892, root * 1.4983]
    detune = [rng.between(-0.4, 0.4) for _ in chord]

    # 低频持续音：整片都在，给"沉"的感觉
    for index in range(total):
        t = index / sample_rate
        swell = 0.55 + 0.45 * math.sin(TWO_PI * t / max(1.0, duration) - math.pi / 2)
        value = 0.5 * math.sin(TWO_PI * (root / 2.0) * t)
        value += 0.18 * math.sin(TWO_PI * (root / 2.0) * 1.5 * t)
        samples[index] = value * swell * level

    # 和声垫：每个音 8 秒一轮进出，轮流出场，避免一直同一块
    for voice, (freq, shift) in enumerate(zip(chord, detune)):
        cycle = 8.0
        offset = voice * cycle / 3.0
        step = TWO_PI * (freq + shift) / sample_rate
        for index in range(total):
            t = index / sample_rate
            local = (t + offset) % cycle
            fade = math.sin(math.pi * (local / cycle)) ** 2
            samples[index] += 0.22 * math.sin(step * index) * fade * level * 1.6

    # 每两拍一次的低频脉冲：给节奏感，但不做成鼓点
    pulse_t = 0.0
    while pulse_t < duration:
        start = int(pulse_t * sample_rate)
        length = int(0.42 * sample_rate)
        for offset in range(length):
            index = start + offset
            if index >= total:
                break
            t = offset / sample_rate
            envelope = math.exp(-t * 9.0)
            samples[index] += 0.5 * math.sin(TWO_PI * 55.0 * t) * envelope * level * 1.2
        pulse_t += beat * 2.0

    # 极轻的底噪，掩盖纯合成音的"死寂"（幅度压到听不出沙沙声）
    noise = _Lcg(seed + 31)
    for index in range(0, total, 3):
        samples[index] += (noise.next_float() - 0.5) * 0.004

    return _soft_limit(samples)


def _soft_limit(samples: list[float], ceiling: float = 0.98) -> list[float]:
    return [max(-ceiling, min(ceiling, value)) for value in samples]


def sfx_samples(kind: str, *, sample_rate: int = SAMPLE_RATE, level: float = SFX_LEVEL) -> list[float]:
    """一小段音效。``kind`` 决定形状，不认识就报错——不要静默给一段静音。"""
    if kind == "whoosh":
        duration = 0.62
        total = int(duration * sample_rate)
        samples = [0.0] * total
        rng = _Lcg(11)
        previous = 0.0
        for index in range(total):
            t = index / sample_rate
            progress = t / duration
            # 一阶低通，截止频率随时间上扬 = 风声扫过
            cutoff = 0.02 + 0.5 * progress
            white = rng.next_float() - 0.5
            previous = previous + cutoff * (white - previous)
            envelope = math.sin(math.pi * progress) ** 2
            samples[index] = previous * envelope * level * 1.4
        return samples

    if kind == "impact":
        duration = 0.85
        total = int(duration * sample_rate)
        samples = [0.0] * total
        rng = _Lcg(23)
        for index in range(total):
            t = index / sample_rate
            envelope = math.exp(-t * 7.5)
            sweep = 120.0 * math.exp(-t * 4.0) + 42.0
            body = math.sin(TWO_PI * sweep * t)
            click = (rng.next_float() - 0.5) * math.exp(-t * 40.0)
            samples[index] = (body * 0.85 + click * 0.5) * envelope * level
        return samples

    if kind == "riser":
        duration = 1.35
        total = int(duration * sample_rate)
        samples = [0.0] * total
        for index in range(total):
            t = index / sample_rate
            progress = t / duration
            freq = 180.0 * (1.0 + 5.0 * progress ** 1.6)
            envelope = progress ** 1.2 * math.exp(-max(0.0, t - duration * 0.92) * 22.0)
            samples[index] = math.sin(TWO_PI * freq * t) * envelope * level * 0.55
        return samples

    if kind == "tick":
        duration = 0.09
        total = int(duration * sample_rate)
        samples = [0.0] * total
        for index in range(total):
            t = index / sample_rate
            samples[index] = math.sin(TWO_PI * 1800.0 * t) * math.exp(-t * 60.0) * level * 0.5
        return samples

    raise ValueError(f"不认识的音效类型：{kind}")


def place_cues(duration: float, cues: Sequence[tuple[float, str]], *, sample_rate: int = SAMPLE_RATE) -> list[float]:
    """把若干音效按时间点摆进一条轨里，返回整轨采样。"""
    total = int(duration * sample_rate)
    track = [0.0] * total
    for at, kind in cues:
        clip = sfx_samples(kind, sample_rate=sample_rate)
        start = int(max(0.0, at) * sample_rate)
        for offset, value in enumerate(clip):
            index = start + offset
            if index >= total:
                break
            track[index] += value
    return _soft_limit(track)


def write_wav(path: Path | str, samples: Iterable[float], *, sample_rate: int = SAMPLE_RATE, channels: int = 2) -> Path:
    """写 16 bit PCM WAV。单声道数据可以直接复制成立体声。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = list(samples)
    frames = bytearray()
    for value in data:
        clipped = max(-1.0, min(1.0, value))
        packed = struct.pack("<h", int(clipped * 32767))
        for _ in range(channels):
            frames += packed
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return target


__all__ = [
    "MUSIC_LEVEL",
    "SAMPLE_RATE",
    "SFX_LEVEL",
    "music_samples",
    "place_cues",
    "sfx_samples",
    "write_wav",
]
