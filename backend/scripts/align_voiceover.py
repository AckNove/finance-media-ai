"""把真人录音对齐成"一句一行"的配音轨。

输入：
- `content/jobs/<job>/voiceover/raw/takeN.m4a`（用户原始录音）
- `content/jobs/<job>/voiceover/clean/takeN.wav`（已降噪、已归一响度）
- `content/jobs/<job>/voiceover/transcript.json`（whisper 逐段转写，带词级时间）

输出：
- `voiceover/lines.json`：每句的文本、属于哪个 take、在 take 里的起止、时长
- `voiceover/voiceover.wav`：按句拼好的整条配音轨（句间留 GAP 秒）
- `voiceover/subtitles.srt`：字幕（以录音里真正说出的话为准）

设计要点：
1. **以录音为准**。用户加了口播稿里没有的开场、结束语和语气词，字幕必须跟着录音走，
   不能拿脚本硬套。
2. **只做"同音纠错"**。转写会把「五大」听成「武大」、「现货」听成「限货」，这类
   只改字面、不改意思的才纠；任何可能改变事实的（数字、主体、时间）一律不动，
   交回人工确认。
3. 断句优先用标点，其次按停顿；一行太长（超过 MAX_CHARS）再按逗号切开。
"""

from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

FFMPEG = Path("tools/ffmpeg/bin/ffmpeg.exe")

# 句与句之间留的静默（秒）。真人念稿本来就有停顿，这里只补一点点，避免粘在一起。
GAP = 0.18
# 单行字幕的最大字数（中文按字算），超出就按逗号切。
MAX_CHARS = 26

# 同音／近音误写纠正：只改字面，不改意思。左边是转写结果，右边是原意。
CORRECTIONS: tuple[tuple[str, str], ...] = (
    ("武大发电集团", "五大发电集团"),
    ("同理下降", "同比下降"),
    ("经理人增长", "净利润增长"),
    ("供弄事业", "公用事业"),
    ("没电就多了一份收入表", "煤电就多了一张收入表"),
    ("每一电容量电价机制", "煤电容量电价机制"),
    ("每电改型两部织电价", "煤电改行两部制电价"),
    ("累电装机", "累计装机"),
    ("光幅", "光伏"),
    ("装机结构一遍", "装机结构一变"),
    ("谁负的顶峰", "谁负责顶峰"),
    ("谁负着兜底", "谁负责兜底"),
    ("砍天吃饭", "看天吃饭"),
    ("随时顶伤", "随时顶上"),
    ("电力限货市场", "电力现货市场"),
    ("限货市典", "现货试点"),
    ("限货市场", "现货市场"),
    ("分时金价", "分时竞价"),
    ("巴克地区", "八个地区"),
    ("零下", "宁夏"),
    ("大唐中卫基地", "大唐中卫云基地"),
    ("数据中心圆区", "数据中心园区"),
    ("七连三倍", "七点三倍"),
    ("资产规模排到全国第七", "智算规模排到全国第七"),
    ("多了以来买家", "多了一类买家"),
    ("一家方式", "议价方式"),
    ("但先不要记着下结论", "但先不要急着下结论"),
    ("火电乘压", "火电承压"),
    ("发电量的降", "发电量在降"),
    ("店家要涨", "电价要涨"),
    ("容量店家比例", "容量电价比例"),
    ("限货价差", "现货价差"),
    ("国家等院局", "国家能源局"),
    ("启动市运行", "启动试运行"),
    ("累电装机", "累计装机"),
    ("大唐中卫基地", "大唐中卫云基地"),
    ("这下逻辑", "这套逻辑"),
    ("我们要定三件事", "我们要盯三件事"),
    ("卖了多少读电", "卖了多少度电"),
    ("以前一张表", "以前只有一张表"),
)


@dataclass
class Line:
    index: int
    text: str
    take: str
    start: float
    end: float
    seconds: float
    raw: str = ""
    hard: bool = False

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "take": self.take,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "seconds": round(self.seconds, 3),
            "raw": self.raw,
            "hard": self.hard,
        }


@dataclass
class Take:
    name: str
    segments: list[dict] = field(default_factory=list)


def _fix(text: str) -> str:
    fixed = text
    for wrong, right in CORRECTIONS:
        fixed = fixed.replace(wrong, right)
    return fixed


def _clean(text: str) -> str:
    """去掉尾部句号（字幕不要句号），把英文逗号换成中文。"""

    tidy = text.strip().replace(",", "，").replace("?", "？").replace("!", "！").replace(" ", "")
    return tidy.rstrip("。，、；：")


def _split_long(text: str, limit: int = MAX_CHARS) -> list[str]:
    """太长就按逗号切；实在没逗号可切，就先放宽到 1.35 倍再找位置硬切。"""

    if len(text) <= limit:
        return [text]
    if "，" not in text[:limit] and len(text) <= int(limit * 1.35):
        return [text]
    parts = [part for part in text.split("，") if part]
    chunks: list[str] = []
    buffer = ""
    for part in parts:
        candidate = f"{buffer}，{part}" if buffer else part
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
        while len(part) > limit:
            cut = limit
            # 别把数字/百分比切开：往前退到非数字的位置
            while cut > 1 and (part[cut - 1].isdigit() or part[cut - 1] in ".%" or part[cut].isdigit()):
                cut -= 1
            chunks.append(part[:cut])
            part = part[cut:]
        buffer = part
    if buffer:
        chunks.append(buffer)
    return chunks


def _sentence_times(segment: dict) -> list[tuple[str, float, float]]:
    """把一段（可能含多句）按标点切开，用词级时间按字数比例分配。"""

    text = str(segment.get("text", "")).strip()
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    if not text:
        return []
    pieces = [piece for piece in text.replace("?", "？").replace("!", "！").replace(";", "；").split("。") if piece]
    # 问号/叹号/分号也算断点：先按句末标点切，再保留各自标点
    expanded: list[str] = []
    for piece in pieces:
        buffer = ""
        for char in piece:
            buffer += char
            if char in "？！；":
                expanded.append(buffer)
                buffer = ""
        if buffer:
            expanded.append(buffer)
    expanded = [item for item in expanded if item.strip()]
    total = sum(len(_clean(item)) for item in expanded) or 1
    span = max(0.05, end - start)
    rows: list[tuple[str, float, float]] = []
    cursor = start
    for item in expanded:
        weight = len(_clean(item)) / total
        piece_end = min(end, cursor + span * weight)
        rows.append((item, cursor, piece_end))
        cursor = piece_end
    if rows:
        rows[-1] = (rows[-1][0], rows[-1][1], end)
    return rows


def merge_fragments(lines: list[Line], *, min_seconds: float = 1.2, min_chars: int = 8, max_chars: int = MAX_CHARS) -> list[Line]:
    """把过短的碎片并回相邻句。

    whisper 的分段边界不一定落在标点上，会出现「…7940 亿千瓦」+「时，同比下降 2.1%」
    这种半句；按"时长够长且字数够"合并后再重新编号，字幕才读得顺。
    只在同一个 take 内合并，跨 take 不合并（那是两段不同的录音）。
    """

    merged: list[Line] = []
    pending: Line | None = None
    for line in lines:
        if pending is None:
            pending = Line(line.index, line.text, line.take, line.start, line.end, line.seconds, line.raw, line.hard)
            continue
        same_take = line.take == pending.take
        # 判据用"上一句有没有说完"：whisper 的分段边界常在句子中间，
        # 只要上一段没以句末标点收尾，就说明这句话还没说完，必须接上
        too_long = len(pending.text) + len(line.text) > max_chars * 2
        if same_take and not pending.hard and not too_long:
            pending.text = f"{pending.text}{line.text}"
            pending.end = line.end
            pending.seconds = round(pending.end - pending.start, 3)
            pending.raw = f"{pending.raw} {line.raw}".strip()
            pending.hard = line.hard
            continue
        merged.append(pending)
        pending = Line(line.index, line.text, line.take, line.start, line.end, line.seconds, line.raw, line.hard)
    if pending is not None:
        merged.append(pending)

    # 合并后可能又太长：按逗号切回去（时间按字数比例分配）
    result: list[Line] = []
    for line in merged:
        chunks = _split_long(line.text, MAX_CHARS)
        if len(chunks) == 1:
            result.append(line)
            continue
        cursor = line.start
        for chunk in chunks:
            share = len(chunk) / max(1, len(line.text))
            span = line.seconds * share
            result.append(
                Line(0, chunk, line.take, cursor, min(line.end, cursor + span), round(span, 3), line.raw, line.hard)
            )
            cursor = min(line.end, cursor + span)

    for position, line in enumerate(result, start=1):
        line.index = position
    return result


def build_lines(transcript: dict, *, max_chars: int = MAX_CHARS) -> list[Line]:
    """transcript: {take_name: [segment, ...]} → 逐句 Line 列表。

    直接拿 whisper 的分段当句子：它本来就是按静音切的，一段就是一两句完整的话。
    只在单段超过 `max_chars` 时按逗号切开（时间按字数比例分配），
    这样既不会把一句话拆成碎片，也不会出现一屏塞不下的长句。
    """

    lines: list[Line] = []
    index = 0
    for take_name in sorted(transcript, key=lambda name: int(name[4:]) if name[4:].isdigit() else 0):
        for segment in transcript[take_name]:
            raw_text = str(segment.get("text", "")).strip()
            if not raw_text:
                continue
            text = _clean(_fix(raw_text))
            if not text:
                continue
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            span = max(0.1, end - start)
            chunks = _split_long(text, max_chars)
            cursor = start
            for position, chunk in enumerate(chunks):
                share = len(chunk) / max(1, len(text))
                piece = span * share if len(chunks) > 1 else span
                piece_end = end if position == len(chunks) - 1 else min(end, cursor + piece)
                index += 1
                lines.append(
                    Line(
                        index=index,
                        text=chunk,
                        take=take_name,
                        start=cursor,
                        end=piece_end,
                        seconds=round(max(0.12, piece_end - cursor), 3),
                        raw=raw_text,
                        hard=str(raw_text).strip().endswith(("。", "？", "！", "；")),
                    )
                )
                cursor = piece_end
    return lines


BROKEN_TAILS = ("服", "瓦", "本", "话", "家", "变", "千", "亿", "分", "时", "的", "在", "和", "与", "千瓦时")


def _glue_broken(lines: list[Line]) -> list[Line]:
    """把 whisper 在词中间断开的相邻行粘回去（只在同一 take、且上一行结尾不像句末时）。"""

    result: list[Line] = []
    for line in lines:
        if result:
            previous = result[-1]
            tail = previous.text[-1:] or ""
            if previous.take == line.take and tail and (tail in BROKEN_TAILS or tail.isdigit()):
                previous.text = f"{previous.text}{line.text}"
                previous.end = line.end
                previous.seconds = round(previous.end - previous.start, 3)
                continue
        result.append(line)
    for position, item in enumerate(result, start=1):
        item.index = position
    return result


def _resplit_long(lines: list[Line], *, limit: int = MAX_CHARS) -> list[Line]:
    """粘回后可能又变成超长行，按逗号再拆一次（时间按字数比例）。"""

    result: list[Line] = []
    for line in lines:
        chunks = _split_long(line.text, limit)
        if len(chunks) == 1:
            result.append(line)
            continue
        cursor = line.start
        for position, chunk in enumerate(chunks):
            share = len(chunk) / max(1, len(line.text))
            piece_end = line.end if position == len(chunks) - 1 else min(line.end, cursor + line.seconds * share)
            result.append(
                Line(0, chunk, line.take, cursor, piece_end, round(max(0.12, piece_end - cursor), 3), line.raw, line.hard)
            )
            cursor = piece_end
    for position, item in enumerate(result, start=1):
        item.index = position
    return result


def _slice_wav(source: Path, target: Path, start: float, end: float) -> None:
    duration = max(0.05, end - start)
    subprocess.run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
            "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(target),
        ],
        check=True,
    )


def write_voiceover(job_root: Path, lines: list[Line], *, gap: float = GAP) -> Path:
    """按句切干净录音并拼接成整条配音轨，返回 wav 路径。

    拼接直接在 PCM 层做（所有片段都是 48k 单声道 16bit），不依赖 ffmpeg 的 concat，
    省掉 Windows 下路径转义这一堆坑，也保证"字幕时间轴 = 音频时间轴"。
    """

    voice_dir = job_root / "voiceover"
    pieces_dir = voice_dir / "pieces"
    pieces_dir.mkdir(parents=True, exist_ok=True)

    gap_frames = int(round(gap * 48000))
    silence = b"\x00\x00" * gap_frames

    payload = bytearray()
    for line in lines:
        source = voice_dir / "clean" / f"{line.take}.wav"
        target = pieces_dir / f"line{line.index:03d}.wav"
        _slice_wav(source, target, line.start, line.end)
        with wave.open(str(target), "rb") as handle:
            payload.extend(handle.readframes(handle.getnframes()))
        payload.extend(silence)

    out = voice_dir / "voiceover.wav"
    with wave.open(str(out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(bytes(payload))
    return out


def to_srt(lines: list[Line], *, gap: float = GAP, lead: float = 0.0) -> str:
    """生成字幕：时间轴与拼好的配音轨一致。"""

    rows: list[str] = []
    cursor = lead
    for line in lines:
        start = cursor
        end = cursor + line.seconds
        rows.append(f"{line.index}\n{_srt_time(start)} --> {_srt_time(end)}\n{line.text}\n")
        cursor = end + gap
    return "\n".join(rows)


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 入口
    import argparse

    parser = argparse.ArgumentParser(description="把真人录音对齐成逐句配音轨与字幕")
    parser.add_argument("--job", required=True, help="任务号，例如 v6_power_first_film")
    parser.add_argument("--gap", type=float, default=GAP, help="句间静默秒数")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS, help="单行字幕最大字数")
    parser.add_argument("--jobs-root", default="content/jobs")
    args = parser.parse_args(argv)

    job_root = Path(args.jobs_root) / args.job
    voice_dir = job_root / "voiceover"
    transcript = json.loads((voice_dir / "transcript.json").read_text(encoding="utf-8"))
    lines = _resplit_long(_glue_broken(merge_fragments(build_lines(transcript, max_chars=args.max_chars), max_chars=args.max_chars)), limit=args.max_chars)
    if not lines:
        raise SystemExit("转写结果里没有任何可用句子，先检查 transcript.json")

    wav = write_voiceover(job_root, lines, gap=args.gap)
    (voice_dir / "lines.json").write_text(
        json.dumps([line.as_dict() for line in lines], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (voice_dir / "subtitles.srt").write_text(to_srt(lines, gap=args.gap), encoding="utf-8")

    total = sum(line.seconds for line in lines) + args.gap * max(0, len(lines) - 1)
    print(
        json.dumps(
            {
                "lines": len(lines),
                "audio": str(wav),
                "seconds": round(total, 3),
                "takes": sorted({line.take for line in lines}),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
