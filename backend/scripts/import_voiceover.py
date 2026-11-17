"""导入真人录音：`python -m backend.scripts.import_voiceover --job <任务号> ...`

两种用法
--------
1. 每句一个文件（推荐，时间轴精确）：
       python -m backend.scripts.import_voiceover --job v6_power_first_film --dir D:\\录音\\电力

2. 整段一录（按字数估算切分，标 estimated）：
       python -m backend.scripts.import_voiceover --job v6_power_first_film --audio D:\\录音\\电力整段.m4a

口播稿从哪来
------------
默认按顺序找：`audio/narration/narration.json` 的逐句文本 → `script/SCRIPT.md` 的旁白段。
也可以用 `--lines-file` 指定一个每行一句的纯文本文件（改完稿先存一份，最稳）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.video import voiceover as vo


JOBS_ROOT = Path(__file__).resolve().parents[2] / "content" / "jobs"


def _load_lines(job_root: Path, lines_file: Path | None) -> list[str]:
    if lines_file is not None:
        text = lines_file.read_text(encoding="utf-8")
        return [line.strip() for line in text.splitlines() if line.strip()]

    narration = job_root / "audio" / "narration" / "narration.json"
    if narration.is_file():
        payload = json.loads(narration.read_text(encoding="utf-8"))
        texts = [str(item.get("text", "")).strip() for item in payload.get("clips") or []]
        texts = [text for text in texts if text]
        if texts:
            return texts

    script = job_root / "script" / "SCRIPT.md"
    if script.is_file():
        rows = script.read_text(encoding="utf-8").splitlines()
        picked = []
        for row in rows:
            stripped = row.strip()
            if not stripped or stripped.startswith(("#", "-", ">", "|", "```")):
                continue
            picked.append(stripped)
        if picked:
            return picked

    raise vo.VoiceoverError(
        "no_script",
        f"没找到口播稿：{job_root} 下既没有 audio/narration/narration.json，也没有可用的 script/SCRIPT.md",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导入真人录音并按句对齐")
    parser.add_argument("--job", required=True, help="任务号，对应 content/jobs/<任务号>")
    parser.add_argument("--jobs-root", default=str(JOBS_ROOT), help="任务根目录")
    parser.add_argument("--dir", dest="directory", help="每句一个文件的目录（推荐）")
    parser.add_argument("--audio", help="整段录音文件")
    parser.add_argument("--lines-file", help="每行一句的口播稿纯文本")
    parser.add_argument("--gap", type=float, default=vo.DEFAULT_GAP_SECONDS, help="整段模式下的句间留白（秒）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    job_root = Path(args.jobs_root) / args.job
    if not job_root.is_dir():
        print(f"任务目录不存在：{job_root}", file=sys.stderr)
        return 2
    if not args.directory and not args.audio:
        print("必须给 --dir（每句一个文件）或 --audio（整段录音）", file=sys.stderr)
        return 2
    if args.directory and args.audio:
        print("--dir 与 --audio 只能选一个", file=sys.stderr)
        return 2

    try:
        lines = _load_lines(job_root, Path(args.lines_file) if args.lines_file else None)
        if args.directory:
            result = vo.import_per_line(job_root, Path(args.directory), lines)
        else:
            result = vo.import_single(job_root, Path(args.audio), lines, gap=args.gap)
        target = vo.write_voiceover(result)
    except vo.VoiceoverError as error:
        print(f"导入失败（{error.reason}）：{error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"对齐方式：{result.alignment}（{result.note}）")
        print(f"共 {len(result.lines)} 句，总长 {result.total_seconds:.2f} 秒")
        for line in result.lines:
            print(f"  {line.index + 1:>2}. {line.start:6.2f}–{line.end:6.2f}s  {line.text[:34]}")
        print(f"写入：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
