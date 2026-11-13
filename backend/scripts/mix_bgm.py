"""把背景音乐混进已有成片。

为什么要单独一个脚本、而不是让渲染流程顺手加背景音乐：**授权校验必须先于混音**。
这里第一件事是 ``require_usable``——授权不在白名单里就退出，一个字节都不生成。

用法：

    python -m backend.scripts.mix_bgm --video content/jobs/<job>/output/final.mp4 \
        --track calm-01 --out content/jobs/<job>/output/final_bgm.mp4 --volume 0.3

只用本机 ffmpeg（``tools/ffmpeg/bin`` 优先），不上传、不联网、不改原文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.video.bgm import BgmError, BgmTrack, load_library, pick, require_usable
from backend.app.capabilities.media.ffmpeg import (
    MediaToolError,
    require_toolchain,
    run_ffmpeg,
)

#: 背景音乐默认音量（相对人声）。0.3 已经能听出来，又不至于盖住口播。
DEFAULT_VOLUME = 0.3


class MixError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def build_args(video: Path, track: Path, out: Path, *, volume: float) -> list[str]:
    """拼 ffmpeg 参数。视频流直接 copy（不重编码，不损画质）。"""
    return [
        "-y",
        "-i",
        str(video),
        "-stream_loop",
        "-1",
        "-i",
        str(track),
        "-filter_complex",
        f"[1:a]volume={volume:.3f}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out),
    ]


def mix(*, video: Path, track: BgmTrack, out: Path, volume: float = DEFAULT_VOLUME, root: Path | None = None) -> int:
    """真正的混音。返回进程退出码（0 = 成功）。"""
    require_usable(track, root=root)  # 授权不过，这里就抛了，不产生文件
    if not video.is_file():
        raise MixError("missing_video", f"没有这个成片：{video}")
    out.parent.mkdir(parents=True, exist_ok=True)
    toolchain = require_toolchain()
    args = build_args(video, track.path(root), out, volume=volume)
    run_ffmpeg(toolchain, args)
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="给成片混入背景音乐（先验授权）")
    parser.add_argument("--video", required=True, help="已有成片路径")
    parser.add_argument("--track", required=True, help="曲库里登记过的曲目 id")
    parser.add_argument("--out", default="", help="输出路径（默认 <video>_bgm.mp4）")
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME, help="背景音乐音量 0~1")
    parser.add_argument("--library", default=None, help="曲库 YAML 路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    video = Path(args.video).resolve()
    out = Path(args.out).resolve() if args.out else video.with_name(video.stem + "_bgm" + video.suffix)
    try:
        library = load_library(Path(args.library) if args.library else None)
        track = pick(args.track, library=library)
        mix(video=video, track=track, out=out, volume=max(0.0, min(1.0, args.volume)), root=library.root)
    except BgmError as error:
        payload = {"ok": False, "reason": error.reason, "message": error.message}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"拒绝混音：{error.message}")
        return 2
    except MixError as error:
        payload = {"ok": False, "reason": error.reason, "message": error.message}
        print(json.dumps(payload, ensure_ascii=False) if args.json else error.message)
        return 1
    except MediaToolError as error:
        payload = {"ok": False, "reason": "ffmpeg_failed", "message": error.describe()}
        print(json.dumps(payload, ensure_ascii=False) if args.json else error.describe())
        return 1

    payload = {
        "ok": True,
        "video": str(video),
        "track": track.as_dict(library.root),
        "output": str(out),
        "volume": args.volume,
        "attribution": track.attribution,
        "note": "成片里用了这首 BGM；发布时按授权填写署名字段。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"已混音：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
