"""逐帧渲染一个镜头：Chromium 打开镜头页 → 每帧 __seek(t) → 截图 → ffmpeg 编码。

为什么是独立脚本而不是 backend 模块里的函数：渲染这一层只需要 Playwright，
而 Playwright 装在 ``tools/openkatana/.venv`` 里（仓库主 venv 没有，也不该为了它
把整个浏览器栈装进生产环境）。所以这个脚本只 import 标准库 + Playwright，
可以用任意解释器跑：

    tools\\openkatana\\.venv\\Scripts\\python.exe backend\\scripts\\cinema_render.py \
        --scene content\\jobs\\v6_power_first_film\\remaster_v1\\production\\scenes\\p01 \
        --out   %TEMP%\\ak_p01 --frames-from 0 --frames-to 60

参数说明：

``--scene``   镜头目录（里面必须有 index.html，以及它引用的 scene.js / 素材）
``--out``     输出目录；帧写进 ``<out>/frames/%05d.png``
``--fps``     帧率（默认 30）
``--frames``  总帧数；不给就读页面里的 window.__AK.duration × fps
``--mp4``     给了就直接用 ffmpeg 编成无声 MP4（有音频时由上层再合成）
``--png-only`` 只出帧，不编码

输出的 JSON 里带上：帧数、耗时、每帧平均毫秒、页面报错、sha256（编码后）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CHROMIUM = Path(r"D:\playwright_browsers\chromium-1243\chrome-win64\chrome.exe")
DEFAULT_FFMPEG = Path(r"tools\ffmpeg\bin\ffmpeg.exe")


def _find_ffmpeg(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    env = os.environ.get("AK_FFMPEG")
    if env and Path(env).is_file():
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    candidate = root / DEFAULT_FFMPEG
    if candidate.is_file():
        return candidate
    which = shutil.which("ffmpeg")
    return Path(which) if which else None


def _find_chromium(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    env = os.environ.get("AK_CHROMIUM")
    if env and Path(env).is_file():
        return Path(env)
    if DEFAULT_CHROMIUM.is_file():
        return DEFAULT_CHROMIUM
    return None


def render(
    *,
    scene_dir: Path,
    out_dir: Path,
    fps: int,
    frames: int | None,
    frames_from: int,
    frames_to: int | None,
    viewport: tuple[int, int] | None,
    chromium: Path | None,
    mp4: Path | None,
    ffmpeg: Path | None,
    quiet: bool,
) -> dict:
    from playwright.sync_api import sync_playwright  # 只在这里 import，别的用途不需要它

    entry = scene_dir / "index.html"
    if not entry.is_file():
        raise SystemExit(f"镜头目录里没有 index.html：{entry}")

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    errors: list[str] = []
    config: dict = {}
    rendered = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(chromium) if chromium else None,
            args=[
                "--disable-lcd-text",        # 去掉子像素抗锯齿，字形更稳
                "--force-color-profile=srgb",
                "--hide-scrollbars",
                "--mute-audio",
                "--disable-extensions",
            ],
        )
        page = browser.new_page(
            viewport={"width": (viewport or (1080, 1920))[0], "height": (viewport or (1080, 1920))[1]},
            device_scale_factor=1,
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(f"console.{message.type}: {message.text}")
                if message.type in ("error",) else None)

        page.goto(entry.as_uri(), wait_until="load")
        page.wait_for_function("window.__ready === true", timeout=120_000)

        config = page.evaluate("() => window.__AK")
        page_errors = page.evaluate("() => window.__errors || []")
        errors.extend(str(item) for item in page_errors)

        total_frames = frames
        if not total_frames:
            duration = float(config.get("duration") or 0.0)
            total_frames = max(1, int(round(duration * fps)))
        last = min(total_frames, frames_to) if frames_to is not None else total_frames

        stage = page.locator("#stage")
        for index in range(frames_from, last):
            page.evaluate("(t) => window.__seek(t)", index / fps)
            target = frames_dir / f"{index:05d}.png"
            stage.screenshot(path=str(target), animations="disabled", caret="hide")
            rendered += 1

        browser.close()

    elapsed = time.perf_counter() - started
    payload: dict = {
        "scene": str(scene_dir),
        "frames_dir": str(frames_dir),
        "frames": rendered,
        "frames_range": [frames_from, last],
        "fps": fps,
        "seconds": round(rendered / fps, 3),
        "elapsed_seconds": round(elapsed, 2),
        "ms_per_frame": round(elapsed * 1000 / rendered, 1) if rendered else None,
        "errors": errors,
        "config": config,
    }

    if mp4 is not None and rendered:
        if ffmpeg is None:
            payload["encode_error"] = "找不到 ffmpeg，跳过编码"
        else:
            first = frames_dir / f"{frames_from:05d}.png"
            mp4.parent.mkdir(parents=True, exist_ok=True)
            command = [
                str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                "-framerate", str(fps),
                "-start_number", str(frames_from),
                "-i", str(frames_dir / "%05d.png"),
                "-frames:v", str(rendered),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(mp4),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            payload["encode"] = {
                "ok": result.returncode == 0,
                "stderr": result.stderr[-800:] if result.returncode else "",
                "mp4": str(mp4),
                "first_frame": str(first),
            }
            if mp4.is_file():
                payload["mp4_byte_size"] = mp4.stat().st_size
                payload["mp4_sha256"] = hashlib.sha256(mp4.read_bytes()).hexdigest()

    if not quiet:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把一张镜头页逐帧渲成 PNG，可选编成无声 MP4")
    parser.add_argument("--scene", required=True, help="镜头目录（含 index.html）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--frames", type=int, default=None, help="总帧数，默认按 duration×fps 算")
    parser.add_argument("--frames-from", type=int, default=0)
    parser.add_argument("--frames-to", type=int, default=None)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--mp4", default=None, help="顺便编成无声 MP4 的目标路径")
    parser.add_argument("--chromium", default=None)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    scene_dir = Path(args.scene).resolve()
    out_dir = Path(args.out).resolve()
    chromium = _find_chromium(args.chromium)
    if chromium is None:
        raise SystemExit("找不到 Chromium：用 --chromium 指定，或设 AK_CHROMIUM")

    payload = render(
        scene_dir=scene_dir,
        out_dir=out_dir,
        fps=args.fps,
        frames=args.frames,
        frames_from=max(0, args.frames_from),
        frames_to=args.frames_to,
        viewport=(args.width, args.height),
        chromium=chromium,
        mp4=Path(args.mp4).resolve() if args.mp4 else None,
        ffmpeg=_find_ffmpeg(args.ffmpeg),
        quiet=args.quiet,
    )
    if payload.get("errors"):
        print(f"页面报错 {len(payload['errors'])} 条（见 JSON）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
