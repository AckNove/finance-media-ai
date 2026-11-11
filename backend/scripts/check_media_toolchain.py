"""V2.2 Phase 1：音视频工具链检测与版本校验（只读，不产生任何媒体）。

输出 JSON，供人看、也供后续脚本判断：

    python -m backend.scripts.check_media_toolchain            # 自动解析
    python -m backend.scripts.check_media_toolchain --text     # 人类可读
    python -m backend.scripts.check_media_toolchain --require-project   # 必须用项目独立构建

退出码：0 = 达标（可用、版本达标、必需能力齐全）；1 = 不达标。
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.app.capabilities.media import ffmpeg as media
from backend.app.core.config import get_app_config


def build_report(*, require_project: bool = False) -> tuple[dict[str, object], bool]:
    toolchain = media.resolve_toolchain(app_config=get_app_config())
    report = media.probe_capabilities(toolchain)
    payload = report.describe()
    payload["require_project"] = require_project
    ok = report.ok
    if require_project and toolchain.source not in {"config", "project", "explicit"}:
        payload["project_build_required_but_missing"] = True
        ok = False
    # 系统里同时存在多份 ffmpeg 时，把另外两份显式列出来，避免"以为用的是新的"
    payload["other_interpreters_on_machine"] = _other_interpreters(toolchain)
    return payload, ok


def _other_interpreters(active: media.MediaToolchain) -> list[dict[str, object]]:
    import shutil
    from pathlib import Path

    found: list[dict[str, object]] = []
    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        candidate = Path(path_ffmpeg)
        if not active.ffmpeg or candidate.resolve() != active.ffmpeg.resolve():
            line, version = media.read_version(candidate)
            found.append(
                {
                    "source": "path",
                    "ffmpeg": str(candidate),
                    "version_line": line,
                    "version": ".".join(str(part) for part in version) if version else None,
                    "note": "PATH 上的 ffmpeg，仅供对照；本模块不会用它",
                }
            )
    return found


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文能力名会变成乱码或被管道解码成替换字符。
    # 这里显式把标准输出切到 UTF-8（只影响本进程的输出编码，不改系统设置）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="检测音视频工具链与能力")
    parser.add_argument("--text", action="store_true", help="人类可读输出")
    parser.add_argument(
        "--require-project",
        action="store_true",
        help="要求使用项目独立构建（tools/ffmpeg），不允许回退到系统 PATH",
    )
    args = parser.parse_args(argv)

    payload, ok = build_report(require_project=args.require_project)
    if args.text:
        for line in _text_lines(payload):
            print(line)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _text_lines(payload: dict[str, object]) -> list[str]:
    toolchain = payload["toolchain"]
    lines = [
        f"toolchain source : {toolchain['source']}",
        f"ffmpeg           : {toolchain['ffmpeg']}",
        f"ffprobe          : {toolchain['ffprobe']}",
        f"version          : {toolchain['version']} (min {toolchain['minimum_version']})",
        f"satisfies min    : {toolchain['satisfies_minimum']}",
        f"overall ok       : {payload['ok']}",
    ]
    if payload.get("missing_required"):
        lines.append(f"missing required : {', '.join(payload['missing_required'])}")
    if payload.get("missing_recommended"):
        lines.append(f"missing optional : {', '.join(payload['missing_recommended'])}")
    if payload.get("other_interpreters_on_machine"):
        for item in payload["other_interpreters_on_machine"]:
            lines.append(f"also on machine  : {item['ffmpeg']} ({item['version']})")
    lines.append("")
    lines.append("capabilities:")
    for item in payload["capabilities"]:
        flag = "OK  " if item["present"] else ("MISS" if item["required"] else "warn")
        lines.append(f"  [{flag}] {item['key']:18s} {item['label']}")
    return lines


if __name__ == "__main__":
    sys.exit(main())
