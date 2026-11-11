"""V2.2 Phase 1：安装项目独立的完整 FFmpeg 构建（不碰系统 PATH、不覆盖系统 FFmpeg）。

用法：

    # 1) 用已经下载好的压缩包安装（推荐：先人工核验来源）
    python -m backend.scripts.install_ffmpeg --archive tools/_staging/ffmpeg-8.1.2-full_build.7z

    # 2) 让脚本自己下载（URL 与 SHA256 都写死在 PINNED_BUILD 里）
    python -m backend.scripts.install_ffmpeg --download

    # 3) 只校验已安装的版本（CI / 启动前自检可复用）
    python -m backend.scripts.install_ffmpeg --verify

安全边界（与任务书一致）：

- 目标目录固定为仓库内的 `tools/ffmpeg`，**不接受任意路径**。
- 归档 SHA256 必须与 `PINNED_BUILD` 记录一致，否则拒绝安装。
- 已存在安装时**不覆盖、不删除**：只重新写版本记录并提示。
- 只用 `tar`（Windows 自带的 bsdtar/libarchive，能读 7z）解包，不引入新依赖。
- 本脚本不修改 PATH、不注册系统服务、不需要管理员权限。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

TARGET_DIR = PROJECT_ROOT / "tools" / "ffmpeg"


@dataclass(frozen=True)
class PinnedBuild:
    version: str
    ffmpeg_line_prefix: str
    upstream_commit: str
    license_name: str
    license_url: str
    archive_name: str
    archive_url: str
    archive_bytes: int
    archive_sha256: str
    inner_dir: str
    built_with: str
    source_page: str


#: 固定构建：**不用 "latest" 浮动版本**。换版本时必须同时改 archive_sha256。
PINNED_BUILD = PinnedBuild(
    version="8.1.2-full_build-www.gyan.dev",
    ffmpeg_line_prefix="ffmpeg version 8.1.2-full_build-www.gyan.dev",
    upstream_commit="38b88335f9",
    license_name="GPL-3.0-or-later",
    license_url="https://www.gnu.org/licenses/gpl-3.0.txt",
    archive_name="ffmpeg-8.1.2-full_build.7z",
    archive_url="https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-full_build.7z",
    archive_bytes=166_721_853,
    archive_sha256="0fff188997a499b5382e0f66e845d4556c48c54f0113ebed4853d556dbdd7059",
    inner_dir="ffmpeg-8.1.2-full_build",
    built_with="gcc 16.1.0 (Rev2, Built by MSYS2 project)",
    source_page="https://www.gyan.dev/ffmpeg/builds/",
)

KEEP_ENTRIES = ("bin", "doc", "presets", "LICENSE", "README.txt")


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def installed_binaries() -> tuple[Path, Path]:
    return TARGET_DIR / "bin" / "ffmpeg.exe", TARGET_DIR / "bin" / "ffprobe.exe"


def version_line(binary: Path) -> str:
    done = subprocess.run(
        [str(binary), "-hide_banner", "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if done.returncode != 0:
        raise SystemExit(f"无法执行 {binary}: {done.returncode}")
    return (done.stdout or done.stderr or "").splitlines()[0].strip()


def verify_install() -> dict[str, object]:
    ffmpeg, ffprobe = installed_binaries()
    result: dict[str, object] = {
        "target": str(TARGET_DIR),
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
        "expected_version": PINNED_BUILD.version,
        "ok": False,
    }
    if not (ffmpeg.is_file() and ffprobe.is_file()):
        result["reason"] = "binaries_missing"
        return result
    ffmpeg_line = version_line(ffmpeg)
    ffprobe_line = version_line(ffprobe)
    result["ffmpeg_version_line"] = ffmpeg_line
    result["ffprobe_version_line"] = ffprobe_line
    version_ok = ffmpeg_line.startswith(PINNED_BUILD.ffmpeg_line_prefix) and ffprobe_line.startswith(
        PINNED_BUILD.ffmpeg_line_prefix.replace("ffmpeg version", "ffprobe version")
    )
    result["version_matches_pin"] = version_ok
    record_path = TARGET_DIR / "VERSION.json"
    result["record_present"] = record_path.is_file()
    result["ok"] = bool(version_ok and record_path.is_file())
    if not result["ok"]:
        result["reason"] = "version_mismatch" if not version_ok else "record_missing"
    return result


def write_records(archive_sha256: str, archive_bytes: int, archive_path: str | None) -> None:
    ffmpeg, ffprobe = installed_binaries()
    ffmpeg_line = version_line(ffmpeg)
    ffprobe_line = version_line(ffprobe)
    installed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "tool": "ffmpeg",
        "kind": "project-local full build (not on PATH, does not replace system ffmpeg)",
        "version": PINNED_BUILD.version,
        "ffmpeg_version_line": ffmpeg_line,
        "ffprobe_version_line": ffprobe_line,
        "upstream_commit": PINNED_BUILD.upstream_commit,
        "built_with": PINNED_BUILD.built_with,
        "license": PINNED_BUILD.license_name,
        "license_url": PINNED_BUILD.license_url,
        "license_note": (
            "该构建启用 --enable-gpl --enable-version3，因此整体按 GPLv3 分发。"
            "本项目只把它当作独立可执行文件调用（不链接其库），"
            "分发本仓库时不需要附带该二进制；如需分发，必须同时满足 GPLv3 条款。"
        ),
        "source_page": PINNED_BUILD.source_page,
        "archive": {
            "name": PINNED_BUILD.archive_name,
            "url": PINNED_BUILD.archive_url,
            "bytes": archive_bytes,
            "sha256": archive_sha256,
        },
        "archive_local_copy": archive_path,
        "target_dir": str(TARGET_DIR),
        "declared_bytes": PINNED_BUILD.archive_bytes,
        "declared_sha256": PINNED_BUILD.archive_sha256,
        "hash_matches_declared": archive_sha256 == PINNED_BUILD.archive_sha256,
        "installed_at": installed_at,
        "kept_entries": list(KEEP_ENTRIES),
        "not_on_path": True,
        "system_ffmpeg_untouched": True,
    }
    (TARGET_DIR / "VERSION.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    source_md = f"""# FFmpeg 项目独立构建（V2.2 Phase 1）

本目录是**项目内**的完整 FFmpeg 构建，与系统上已安装的 FFmpeg **互不影响**：

- 不修改系统 PATH，不注册全局命令；
- 不覆盖、不删除 `D:\\DeliveryOptimization\\miniconda\\Library\\bin\\ffmpeg.exe`（4.2.2）等既有安装；
- 项目代码按「配置 > 本目录 > PATH」的顺序解析工具链（见 `backend/app/capabilities/media/ffmpeg.py`）。

## 来源与版本

| 项 | 值 |
| --- | --- |
| 版本 | `{PINNED_BUILD.version}` |
| 发行来源 | {PINNED_BUILD.source_page} |
| 归档 | `{PINNED_BUILD.archive_name}`（{archive_bytes:,} 字节） |
| 归档 SHA-256 | `{archive_sha256}` |
| 上游 FFmpeg 提交 | `{PINNED_BUILD.upstream_commit}` |
| 构建工具链 | {PINNED_BUILD.built_with} |
| 许可证 | {PINNED_BUILD.license_name}（{PINNED_BUILD.license_url}） |
| 安装时间 | {installed_at} |

`ffmpeg -version` 首行：

```
{ffmpeg_line}
```

## 许可证说明

该构建启用 `--enable-gpl --enable-version3`，按 **GPLv3** 分发（`LICENSE` 随包提供）。

本项目把它当作**独立可执行文件**调用（`subprocess`），不链接其库，因此本仓库源码不受 GPL 传染；
但**不要把 `bin/` 下的二进制提交进仓库**——一旦随仓库分发，就必须同时履行 GPLv3 义务。
`bin/`、`doc/`、`presets/` 已在 `.gitignore` 中排除，本目录只保留来源与版本记录。

## 为什么选 8.1.2 而不是 7.x

任务书建议目标版本为 7.x。实际操作时发行方（gyan.dev）已不再提供 7.x 的版本化安装包，
只提供当前稳定版 8.1.2 与 git 快照。选择 **8.1.2 稳定版**（而不是 git 快照）的理由：

1. 它是**带版本号的稳定发行**，符合"不用浮动版本"的要求（git 快照会每天变化）；
2. 8.1.2 是 7.x 的超集，OpenKatana 0.1.3 需要的 `-fps_mode`（5.1+）在其中可用（已实测）；
3. 完整构建包含 libass / libx264 / AAC 等本项目渲染链路必需组件（已逐项实测，见报告）。

## 重新安装

```powershell
python -m backend.scripts.install_ffmpeg --download      # 按 PINNED_BUILD 下载并校验
python -m backend.scripts.install_ffmpeg --verify        # 只校验
```
"""
    (TARGET_DIR / "SOURCE.md").write_text(source_md, encoding="utf-8")


def extract_archive(archive: Path, target: Path) -> None:
    if target.exists():
        raise SystemExit(f"目标已存在，拒绝覆盖：{target}")
    staging = Path(tempfile.mkdtemp(prefix="ffmpeg-extract-"))
    try:
        done = subprocess.run(
            ["tar", "-xf", str(archive), "-C", str(staging)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if done.returncode != 0:
            raise SystemExit(f"解包失败：{done.stderr.strip()[:400]}")
        inner = staging / PINNED_BUILD.inner_dir
        if not inner.is_dir():
            raise SystemExit(f"归档结构与预期不符，找不到 {PINNED_BUILD.inner_dir}")
        target.parent.mkdir(parents=True, exist_ok=True)
        inner.rename(target)
        unused = [item.name for item in staging.iterdir() if item.name != PINNED_BUILD.inner_dir]
        if unused:
            print(f"提示：归档内还有未使用条目 {unused}", file=sys.stderr)
    finally:
        if staging.exists():
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(staging)], check=False)


def download_archive(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"下载 {PINNED_BUILD.archive_url} → {destination}")
    with urllib.request.urlopen(PINNED_BUILD.archive_url, timeout=120) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装/校验项目独立 FFmpeg 构建")
    parser.add_argument("--archive", type=Path, help="已有的压缩包路径")
    parser.add_argument("--download", action="store_true", help="按 PINNED_BUILD 下载压缩包")
    parser.add_argument("--verify", action="store_true", help="只校验已安装版本")
    args = parser.parse_args(argv)

    if args.verify:
        report = verify_install()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if args.archive is None and not args.download:
        parser.error("需要 --archive、--download 或 --verify 之一")

    archive = args.archive
    if archive is None:
        archive = download_archive(Path(tempfile.gettempdir()) / PINNED_BUILD.archive_name)
    if not archive.is_file():
        raise SystemExit(f"压缩包不存在：{archive}")

    actual_bytes = archive.stat().st_size
    actual_sha = sha256_of(archive)
    print(f"archive={archive} bytes={actual_bytes} sha256={actual_sha}")
    if actual_bytes != PINNED_BUILD.archive_bytes or actual_sha != PINNED_BUILD.archive_sha256:
        raise SystemExit(
            "归档与固定记录不一致，拒绝安装（请核对来源/版本，切勿直接放过）：\n"
            f"  期望 {PINNED_BUILD.archive_bytes} / {PINNED_BUILD.archive_sha256}\n"
            f"  实际 {actual_bytes} / {actual_sha}"
        )

    ffmpeg, _ = installed_binaries()
    if ffmpeg.is_file():
        print(f"检测到已安装（{TARGET_DIR}）：不覆盖，只重写版本记录")
        write_records(actual_sha, actual_bytes, str(archive))
    else:
        extract_archive(archive, TARGET_DIR)
        write_records(actual_sha, actual_bytes, str(archive))

    report = verify_install()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
