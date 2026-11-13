"""背景音乐曲库自检工具。

用法：

    python -m backend.scripts.bgm_check --list
    python -m backend.scripts.bgm_check --check calm-piano-01
    python -m backend.scripts.bgm_check --library content/assets/bgm/library.yaml --json

只读：不下载、不改文件、不联网。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.video.bgm import (
    BgmError,
    check_library,
    licence_summary,
    load_library,
    pick,
)


def _render_text(library_note: str, items: list[dict[str, object]]) -> str:
    lines = ["背景音乐曲库", f"  {library_note}"]
    if not items:
        return "\n".join(lines)
    lines.append(f"  共 {len(items)} 首：")
    for item in items:
        mark = "OK  " if item["usable"] else "拒绝"
        attr = "（需署名）" if item["needs_attribution"] else ""
        exists = "" if item["exists"] else "  ⚠ 文件不存在"
        lines.append(
            f"  [{mark}] {item['id']}｜{item['title']}｜{item['licence']}{attr}"
            f"{exists}"
        )
        if not item["usable"]:
            lines.append(f"        原因：{item['message']}")
    summary = licence_summary([]) if not items else {}
    del summary
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="背景音乐曲库自检（只读）")
    parser.add_argument("--library", default=None, help="曲库 YAML 路径（默认 content/assets/bgm/library.yaml）")
    parser.add_argument("--list", action="store_true", help="列出全部曲目与可用性")
    parser.add_argument("--check", default="", help="只验一首曲目 id")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    library_path = Path(args.library) if args.library else None
    try:
        library = load_library(library_path)
    except BgmError as error:
        payload = {"ok": False, "reason": error.reason, "message": error.message}
        print(json.dumps(payload, ensure_ascii=False) if args.json else error.message)
        return 1

    if args.check:
        try:
            track = pick(args.check, library=library)
        except BgmError as error:
            payload = {"ok": False, "reason": error.reason, "message": error.message}
            print(json.dumps(payload, ensure_ascii=False) if args.json else error.message)
            return 1
        payload = {"ok": True, "track": track.as_dict(library.root)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"可用：{track.id}｜{track.title}｜{track.licence}")
        return 0

    items = check_library(library)
    summary = licence_summary(library.tracks)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "root": str(library.root),
                    "count": len(items),
                    "usable": sum(1 for item in items if item["usable"]),
                    "licences": summary,
                    "items": items,
                    "note": library.default_note(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(_render_text(library.default_note(), items))
    if items:
        print(f"  按授权统计：{summary}")
    print(f"  可用 {sum(1 for item in items if item['usable'])} / {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
