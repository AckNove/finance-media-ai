"""edge-tts 后端：免费、无需密钥、能给真实词级时间的在线合成。

为什么不直接用微软的 REST 接口而用 edge-tts：它复刻了 Edge 浏览器的"大声朗读"
WebSocket 协议，能拿到每个词的精确起止时间（``WordBoundary`` 事件），
这正是字幕对齐与音画同步需要的；自己实现要维护 DRM 与鉴权细节。

诚实边界：

* 这是**微软未公开文档**的接口，随时可能变；因此上层必须通过
  :class:`backend.app.capabilities.tts.provider.TtsProvider` 抽象调用，
  失败时能退到离线 SAPI（见 :mod:`sapi`），不能把可用性写死。
* ``boundary`` 必须显式传 ``"WordBoundary"``：默认的 ``"SentenceBoundary"``
  在中文文本上只会给出整句，拿不到词级时间（本项目实测 0 个事件）。
* 合成需要联网。无网络时抛 :class:`TtsError`（``reason="offline"``），不静默降级。
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Sequence

from backend.app.capabilities.tts.provider import (
    TtsError,
    TtsRequest,
    TtsResult,
    TtsVoice,
    WordTiming,
)

DEFAULT_VOICE = "zh-CN-YunxiNeural"
PROBE_TIMEOUT_SECONDS = 20.0


def _import_edge_tts():
    try:
        import edge_tts  # noqa: PLC0415 - 延迟导入，缺依赖时给出可读错误
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise TtsError(
            "not_installed",
            "edge-tts 未安装：pip install \"edge-tts==7.3.1\"",
        ) from exc
    return edge_tts


def _run(coro):
    """在同步接口里跑协程；已在事件循环中时用独立线程避免嵌套循环报错。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _duration_seconds(path: Path) -> float:
    """用 ffprobe 读容器时长；读不到就返回 0，由调用方决定是否接受。"""
    try:
        from backend.app.capabilities.media.ffmpeg import (  # noqa: PLC0415
            MediaToolError,
            resolve_toolchain,
            run_ffprobe,
        )
    except Exception:  # pragma: no cover - 媒体模块不可用时不影响合成
        return 0.0
    try:
        toolchain = resolve_toolchain()
        stdout = run_ffprobe(
            toolchain,
            ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            timeout=int(PROBE_TIMEOUT_SECONDS),
        )
    except (MediaToolError, OSError, subprocess.SubprocessError):
        return 0.0
    raw = (stdout or "").strip().splitlines()
    if not raw:
        return 0.0
    try:
        return float(raw[0])
    except ValueError:
        return 0.0


class EdgeTtsProvider:
    """edge-tts 实现。``available()`` 只检查依赖，不联网。"""

    name = "edge"

    def __init__(self, *, boundary: str = "WordBoundary") -> None:
        self.boundary = boundary

    def available(self) -> bool:
        try:
            _import_edge_tts()
        except TtsError:
            return False
        return True

    def list_voices(self, *, locale_prefix: str = "zh") -> Sequence[TtsVoice]:
        edge_tts = _import_edge_tts()

        async def _list() -> list[TtsVoice]:
            raw = await edge_tts.list_voices()
            voices = []
            for item in raw:
                locale = str(item.get("Locale", ""))
                if locale_prefix and not locale.lower().startswith(locale_prefix.lower()):
                    continue
                voices.append(
                    TtsVoice(
                        name=str(item.get("ShortName", "")),
                        locale=locale,
                        gender=str(item.get("Gender", "")),
                        provider=self.name,
                    )
                )
            return voices

        try:
            return tuple(_run(_list()))
        except Exception as exc:  # pragma: no cover - 网络异常路径
            raise TtsError("network_error", f"无法获取声音列表：{exc}") from exc

    def synthesize(self, request: TtsRequest, out_path: Path) -> TtsResult:
        edge_tts = _import_edge_tts()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        async def _speak() -> tuple[list[WordTiming], int]:
            communicate = edge_tts.Communicate(
                request.text,
                request.voice,
                rate=request.rate,
                volume=request.volume,
                pitch=request.pitch,
                boundary=self.boundary,
            )
            words: list[WordTiming] = []
            audio_bytes = 0
            with out_path.open("wb") as handle:
                async for chunk in communicate.stream():
                    kind = chunk.get("type")
                    if kind == "audio":
                        data = chunk.get("data") or b""
                        handle.write(data)
                        audio_bytes += len(data)
                    elif kind == "WordBoundary":
                        start = float(chunk.get("offset", 0)) / 1e7
                        span = float(chunk.get("duration", 0)) / 1e7
                        words.append(
                            WordTiming(
                                text=str(chunk.get("text", "")),
                                start=start,
                                end=start + span,
                            )
                        )
            return words, audio_bytes

        try:
            words, audio_bytes = _run(_speak())
        except TtsError:
            raise
        except Exception as exc:
            raise TtsError("synthesis_failed", f"合成失败：{exc}") from exc

        if audio_bytes <= 0 or not out_path.exists():
            raise TtsError("empty_audio", "合成没有产出音频数据（检查网络或声音名）")

        digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
        measured = _duration_seconds(out_path)
        word_end = max((word.end for word in words), default=0.0)
        duration = measured or word_end
        meta: dict[str, Any] = {
            "boundary": self.boundary,
            "measured_by": "ffprobe" if measured else "word_boundary",
            "audio_bytes": audio_bytes,
        }
        return TtsResult(
            path=str(out_path),
            provider=self.name,
            voice=request.voice,
            text=request.text,
            duration_seconds=duration,
            byte_size=out_path.stat().st_size,
            sha256=digest,
            words=tuple(words),
            sample_rate=0,
            estimated=False,
            meta=meta,
        )


__all__ = ["DEFAULT_VOICE", "EdgeTtsProvider"]
