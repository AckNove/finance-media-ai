"""SAPI 离线后端：Windows 自带语音，零成本、零网络、**没有词级时间**。

用途只有两个：

1. edge-tts 不可用（无网络、接口变动）时的降级通道，让视频流水线仍能出片；
2. 单测与 CI 里的确定性音频来源（不花费用、不依赖外部服务）。

诚实边界：SAPI 拿不到每个词的起止时间，所以本后端返回的字幕时间是
:func:`backend.app.capabilities.tts.provider.estimate_word_timings` 的**估算值**，
``estimated=True``，``meta["timing_source"]="estimated"``。字幕对得上大致节奏，
但对不上字——正式发布必须用带真实词级时间的 provider，或人工校对时间轴。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Sequence

from backend.app.capabilities.tts.provider import (
    TtsError,
    TtsRequest,
    TtsResult,
    TtsVoice,
    estimate_word_timings,
)

PREFERRED_VOICES = (
    "Microsoft Huihui Desktop",
    "Microsoft Yaoyao Desktop",
    "Microsoft Kangkang Desktop",
    "Microsoft Hanhan Desktop",
)

_SCRIPT = """
param([string]$Text, [string]$Out, [string]$VoiceName)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($VoiceName -and $VoiceName -ne '') {
    try { $synth.SelectVoice($VoiceName) } catch { }
}
$synth.Rate = 0
$synth.SetOutputToWaveFile($Out)
$synth.Speak($Text)
$synth.Dispose()
"""

_LIST_SCRIPT = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $synth.GetInstalledVoices()) { if ($v.Enabled) { $v.VoiceInfo.Name } }
"""


def _powershell() -> str:
    return "powershell.exe"


def _run_ps(script: str, *args: str, timeout: float = 120.0) -> str:
    try:
        completed = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TtsError("not_available", "找不到 powershell.exe，SAPI 不可用") from exc
    except subprocess.TimeoutExpired as exc:
        raise TtsError("timeout", f"SAPI 合成超时（{timeout}s）") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise TtsError("synthesis_failed", f"SAPI 失败：{detail[:400]}")
    return completed.stdout or ""


def _probe_duration(path: Path) -> float:
    try:
        from backend.app.capabilities.media.ffmpeg import (  # noqa: PLC0415
            MediaToolError,
            resolve_toolchain,
            run_ffprobe,
        )
    except Exception:  # pragma: no cover
        return 0.0
    try:
        toolchain = resolve_toolchain()
        stdout = run_ffprobe(
            toolchain,
            ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            timeout=20,
        )
    except (MediaToolError, OSError, subprocess.SubprocessError):
        return 0.0
    lines = (stdout or "").strip().splitlines()
    if not lines:
        return 0.0
    try:
        return float(lines[0])
    except ValueError:
        return 0.0


class SapiTtsProvider:
    """Windows SAPI 实现。``available()`` 会真的问一次系统。"""

    name = "sapi"

    def available(self) -> bool:
        try:
            return bool(self.list_voices())
        except TtsError:
            return False

    def list_voices(self, *, locale_prefix: str = "zh") -> Sequence[TtsVoice]:
        output = _run_ps(_LIST_SCRIPT, timeout=60.0)
        voices = []
        for line in output.splitlines():
            name = line.strip()
            if not name:
                continue
            # 桌面中文声音的中文本地化名称里没有 zh 字样，这里按白名单与中文标记判断
            looks_chinese = (
                name in PREFERRED_VOICES
                or "Huihui" in name
                or "Chinese" in name
                or "中文" in name
                or "\u4e2d\u6587" in name
            )
            if locale_prefix and not looks_chinese:
                continue
            voices.append(TtsVoice(name=name, locale="zh-CN", gender="", provider=self.name))
        return tuple(voices)

    def synthesize(self, request: TtsRequest, out_path: Path) -> TtsResult:
        out_path = Path(out_path)
        if out_path.suffix.lower() != ".wav":
            raise TtsError("bad_output_path", "SAPI 只能直接写 .wav，请让上层再转码")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        voice = request.voice
        installed = [item.name for item in self.list_voices()]
        if voice not in installed:
            voice = installed[0] if installed else ""
        _run_ps(_SCRIPT, "-Text", request.text, "-Out", str(out_path), "-VoiceName", voice)
        if not out_path.exists() or out_path.stat().st_size <= 44:
            raise TtsError("empty_audio", "SAPI 没有产出有效音频")

        duration = _probe_duration(out_path)
        words = estimate_word_timings(request.text, duration)
        return TtsResult(
            path=str(out_path),
            provider=self.name,
            voice=voice,
            text=request.text,
            duration_seconds=duration,
            byte_size=out_path.stat().st_size,
            sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
            words=words,
            sample_rate=0,
            estimated=True,
            meta={"timing_source": "estimated", "note": "SAPI 无词级时间，字幕为估算"},
        )


__all__ = ["PREFERRED_VOICES", "SapiTtsProvider"]
