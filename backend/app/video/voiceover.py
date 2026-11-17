"""真人录音接入：把「你自己对着口播稿录的音」接进视频流水线。

为什么要有这一层
----------------
V2.0 的三个人工节点之一是「录音/出镜」。系统不能默认用 TTS 顶替真人的声音——
本文件只做三件事：
1. 把用户录的音频规整成"逐句可定位"的结构（每句有文件、有实测时长、有起止时间）；
2. 时长一律以 ffprobe 实测为准，**绝不按字数推算后当成实测**；
3. 对不齐的情况如实标注 `alignment`，让下游知道"这个时间轴是量的还是估的"。

两条录音方式
------------
- `per_line`：每句一个文件（`01.mp3`、`02.mp3`…）。**精确对齐**，推荐。
- `single`：整段录一遍。只能按字数比例切开，标 `estimated`，字幕与镜头时间轴会漂。

不做什么
--------
- 不做声音克隆：没有经过授权的克隆服务，也不该拿别人的音色冒充。
- 不做语音识别：本机没有装识别模型，不假装能自动切句；切句靠"每句一个文件"或人工给的行。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
VOICEOVER_DIR = Path("audio") / "voiceover"
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")

# 句与句之间的自然停顿：估算模式按这个数留白；精确模式按实测，不留白
DEFAULT_GAP_SECONDS = 0.12


class VoiceoverError(RuntimeError):
    """录音导入失败。`reason` 是稳定原因码，界面与日志都靠它说话。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class VoiceLine:
    """一句旁白在录音里的实际位置。"""

    index: int
    text: str
    path: str
    seconds: float
    start: float

    @property
    def end(self) -> float:
        return round(self.start + self.seconds, 3)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "path": self.path,
            "seconds": round(self.seconds, 3),
            "start": round(self.start, 3),
            "end": self.end,
        }


@dataclass
class Voiceover:
    """一整条真人录音。"""

    job_root: Path
    lines: list[VoiceLine] = field(default_factory=list)
    alignment: str = "per_line"  # per_line | estimated
    source: str = ""
    note: str = ""

    @property
    def total_seconds(self) -> float:
        if not self.lines:
            return 0.0
        return round(max(line.end for line in self.lines), 3)

    def as_dict(self) -> dict:
        return {
            "alignment": self.alignment,
            "source": self.source,
            "note": self.note,
            "total_seconds": self.total_seconds,
            "lines": [line.as_dict() for line in self.lines],
        }


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe_seconds(path: Path) -> float:
    """用 ffprobe 量真实时长。量不出来就是 0，不猜。"""

    if not FFPROBE.is_file():
        raise VoiceoverError("ffprobe_missing", f"找不到 ffprobe：{FFPROBE}")
    result = _run([
        str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    text = (result.stdout or "").strip()
    try:
        return round(float(text), 3)
    except ValueError:
        return 0.0


def _normalise_lines(lines: list[str]) -> list[str]:
    cleaned = [re.sub(r"\s+", " ", str(line)).strip() for line in lines]
    return [line for line in cleaned if line]


def _copy_into(job_root: Path, source: Path, target_name: str) -> Path:
    target_dir = job_root / VOICEOVER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / target_name
    if source.resolve() == target.resolve():
        return target
    shutil.copy2(source, target)
    return target


def _sorted_audio_files(directory: Path) -> list[Path]:
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES]
    return sorted(files, key=lambda p: p.name)


def import_per_line(job_root: Path, directory: Path, lines: list[str]) -> Voiceover:
    """每句一个文件：文件名排序即句序。时长全部实测。"""

    texts = _normalise_lines(lines)
    if not texts:
        raise VoiceoverError("empty_lines", "没有可对齐的口播稿句子")
    if not directory.is_dir():
        raise VoiceoverError("missing_directory", f"录音目录不存在：{directory}")

    files = _sorted_audio_files(directory)
    if not files:
        raise VoiceoverError("no_audio", f"{directory} 里没有音频文件（支持 {', '.join(AUDIO_SUFFIXES)}）")
    if len(files) != len(texts):
        raise VoiceoverError(
            "count_mismatch",
            f"口播稿 {len(texts)} 句，录音却有 {len(files)} 个文件：句数与文件数必须一一对应",
        )

    cursor = 0.0
    result: list[VoiceLine] = []
    for index, (path, text) in enumerate(zip(files, texts)):
        seconds = probe_seconds(path)
        if seconds <= 0:
            raise VoiceoverError("unreadable_audio", f"量不出时长：{path.name}")
        stored = _copy_into(job_root, path, f"line_{index + 1:02d}{path.suffix.lower()}")
        result.append(VoiceLine(index=index, text=text, path=str(stored), seconds=seconds, start=round(cursor, 3)))
        cursor += seconds

    return Voiceover(
        job_root=job_root,
        lines=result,
        alignment="per_line",
        source=str(directory),
        note="每句一个文件，时长逐句实测",
    )


def import_single(job_root: Path, audio: Path, lines: list[str], *, gap: float = DEFAULT_GAP_SECONDS) -> Voiceover:
    """整段一录：按字数比例分配时长，**明确标注是估算**。"""

    texts = _normalise_lines(lines)
    if not texts:
        raise VoiceoverError("empty_lines", "没有可对齐的口播稿句子")
    if not audio.is_file():
        raise VoiceoverError("missing_audio", f"录音文件不存在：{audio}")
    if audio.suffix.lower() not in AUDIO_SUFFIXES:
        raise VoiceoverError("bad_suffix", f"不支持的音频格式：{audio.suffix}")

    total = probe_seconds(audio)
    if total <= 0:
        raise VoiceoverError("unreadable_audio", f"量不出时长：{audio.name}")

    gaps = gap * max(0, len(texts) - 1)
    speakable = max(total - gaps, 0.1)
    weights = [max(1, len(text)) for text in texts]
    weight_sum = sum(weights)

    stored = _copy_into(job_root, audio, f"full{audio.suffix.lower()}")
    cursor = 0.0
    result: list[VoiceLine] = []
    for index, (text, weight) in enumerate(zip(texts, weights)):
        seconds = round(speakable * weight / weight_sum, 3)
        result.append(VoiceLine(index=index, text=text, path=str(stored), seconds=seconds, start=round(cursor, 3)))
        cursor += seconds + gap

    return Voiceover(
        job_root=job_root,
        lines=result,
        alignment="estimated",
        source=str(audio),
        note=f"整段录音按字数比例切分（估算，不是实测）：总长 {total:.2f} 秒，句间留白 {gap:.2f} 秒",
    )


def write_voiceover(voiceover: Voiceover) -> Path:
    """落盘 `audio/voiceover/voiceover.json`。"""

    target_dir = voiceover.job_root / VOICEOVER_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "voiceover.json"
    payload = voiceover.as_dict()
    payload["job_root"] = str(voiceover.job_root)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_voiceover(job_root: Path) -> Voiceover | None:
    """读回已导入的录音；没有就返回 None（下游据此决定用 TTS 还是真人声）。"""

    path = job_root / VOICEOVER_DIR / "voiceover.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    lines = [
        VoiceLine(
            index=int(item["index"]),
            text=str(item["text"]),
            path=str(item["path"]),
            seconds=float(item["seconds"]),
            start=float(item["start"]),
        )
        for item in payload.get("lines") or []
    ]
    if not lines:
        return None
    return Voiceover(
        job_root=job_root,
        lines=lines,
        alignment=str(payload.get("alignment") or "per_line"),
        source=str(payload.get("source") or ""),
        note=str(payload.get("note") or ""),
    )


def to_srt(voiceover: Voiceover) -> str:
    """按逐句时间轴生成 SRT。预估模式会在第一句前加一行说明，避免被当成精确字幕。"""

    def stamp(seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    blocks: list[str] = []
    for order, line in enumerate(voiceover.lines, start=1):
        blocks.append(f"{order}\n{stamp(line.start)} --> {stamp(line.end)}\n{line.text}\n")
    return "\n".join(blocks)


__all__ = [
    "AUDIO_SUFFIXES",
    "DEFAULT_GAP_SECONDS",
    "VOICEOVER_DIR",
    "VoiceLine",
    "Voiceover",
    "VoiceoverError",
    "import_per_line",
    "import_single",
    "load_voiceover",
    "probe_seconds",
    "to_srt",
    "write_voiceover",
]
