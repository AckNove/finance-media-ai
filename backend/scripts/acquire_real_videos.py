"""从维基共享资源取**动态**实拍素材（视频），只收可商用授权。

为什么不用 Pexels / Pixabay：它们的接口要 API Key，而任务书禁止购买/申请任何付费服务；
维基共享资源开放接口不需要 Key，且许可证写在文件元数据里，能逐条核实。

规则（比图片更严，因为视频更容易踩"素材站说免费、实际不能商用"的坑）：
- 只认 CC0 / Public domain / CC BY 系列（不含 SA / NC / ND）；
- 只下**转码后的 webm/mp4 衍生品**（原片动辄几百 MB，而且多为 ogv）；
- 每个槽位记录：文件页、发布者、许可、时长、分辨率、下载时间、SHA-256、准备用在哪一镜；
- 下载后用 ffmpeg 抽 3 张静帧，方便人眼确认"画面对不对题"。

用法：
    python -m backend.scripts.acquire_real_videos --plan
    python -m backend.scripts.acquire_real_videos --only wind_farm
    python -m backend.scripts.acquire_real_videos --title File:Xxx.webm --slot wind_farm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REM = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film" / "remaster_v2"
STAGING = REM / "assets" / "real" / "_staging_video"
STILLS = REM / "assets" / "real" / "_stills"
CANDIDATES = REM / "assets" / "real" / "video_candidates.json"
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "AKVideoPipeline/1.0 (local production use)"}

# 只收可商用；SA/NC/ND 一律不要（SA 会传染、NC 不能商用、ND 不能剪辑）
LICENSE_OK = ("cc0", "public domain", "pd", "cc by 4.0", "cc by 3.0", "cc by 2.5", "cc by 2.0")
LICENSE_BAD = ("sa", "nc", "nd", "fair use", "non-free")

# 槽位 → 检索词（按分镜需要排的）
PLAN: dict[str, tuple[str, ...]] = {
    "wind_farm": ("wind turbines farm", "wind farm aerial"),
    "transmission_gallop": ("transmission line conductor", "power line gallop"),
    "substation_live": ("electrical substation", "power transformer station"),
    "power_plant_smoke": ("power station cooling tower", "thermal power plant"),
    "industry_night": ("factory night lights", "steel mill night"),
    "city_power": ("city skyline night", "street lights city"),
}


@dataclass
class VideoCandidate:
    slot: str
    title: str
    page_url: str
    file_url: str
    derivative: str
    license_short: str
    artist: str
    credit: str
    width: int
    height: int
    duration_s: float
    bytes_expected: int
    local_path: str = ""
    sha256: str = ""
    downloaded_at: str = ""
    note: str = ""
    stills: list[str] = field(default_factory=list)


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # 网络抖动很常见，退避重试
            last_error = error
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"接口失败：{last_error}")


def _verdict(license_short: str) -> str:
    text = (license_short or "").strip().lower()
    if not text:
        return "unknown"
    if any(bad in text for bad in LICENSE_BAD):
        return "forbidden"
    if any(ok in text for ok in LICENSE_OK):
        return "allowed"
    return "unknown"


def _pick_derivative(info: dict) -> dict | None:
    """优先 1080p，其次 480p；只要 webm/mp4（ogv 不好进流水线）。"""
    derivatives = [d for d in (info.get("derivatives") or []) if d.get("src")]
    if not derivatives:
        return None
    wanted = ("1080p", "720p", "480p", "360p")
    for size in wanted:
        for item in derivatives:
            key = str(item.get("transcodekey") or "")
            if key.startswith(size) and key.endswith(("webm", "mp4")):
                return item
    for item in derivatives:
        if str(item.get("transcodekey") or "").endswith(("webm", "mp4")):
            return item
    return None


def _candidates_from(pages: dict, slot: str, limit: int = 3) -> list[VideoCandidate]:
    found: list[VideoCandidate] = []
    for page in (pages or {}).values():
        info = (page.get("videoinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        license_short = (meta.get("LicenseShortName") or {}).get("value", "") or ""
        if _verdict(license_short) != "allowed":
            continue
        derivative = _pick_derivative(info)
        if derivative is None:
            continue
        artist = (meta.get("Artist") or {}).get("value", "") or ""
        credit = (meta.get("Credit") or {}).get("value", "") or ""
        duration = 0.0
        try:
            duration = float((meta.get("Length") or {}).get("value", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0
        found.append(
            VideoCandidate(
                slot=slot,
                title=page.get("title", ""),
                page_url=info.get("descriptionurl", ""),
                file_url=info.get("url", ""),
                derivative=derivative.get("src", ""),
                license_short=license_short,
                artist=artist[:200],
                credit=credit[:200],
                width=int(derivative.get("width") or 0),
                height=int(derivative.get("height") or 0),
                duration_s=round(duration, 2),
                bytes_expected=int(info.get("size") or 0),
                note=f"transcode={derivative.get('transcodekey', '')}",
            )
        )
        if len(found) >= limit:
            break
    return found


def search_slot(slot: str, queries: tuple[str, ...], limit: int = 3) -> list[VideoCandidate]:
    for query in queries:
        data = _get(
            {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:video {query}",
                "gsrnamespace": 6,
                "gsrlimit": 12,
                "prop": "videoinfo",
                "viprop": "url|size|derivatives|extmetadata",
            }
        )
        pages = (data.get("query") or {}).get("pages") or {}
        found = _candidates_from(pages, slot, limit=limit)
        if found:
            return found
    return []


def fetch_by_title(title: str, slot: str) -> list[VideoCandidate]:
    data = _get(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "videoinfo",
            "viprop": "url|size|derivatives|extmetadata",
        }
    )
    pages = (data.get("query") or {}).get("pages") or {}
    return _candidates_from(pages, slot, limit=1)


def download(candidate: VideoCandidate) -> VideoCandidate:
    STAGING.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(candidate.derivative).path).suffix or ".webm"
    name = f"{candidate.slot}_{Path(candidate.title).stem[:40]}{suffix}".replace(" ", "_").replace(":", "_")
    target = STAGING / name
    if not target.exists():
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                request = urllib.request.Request(candidate.derivative, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=180) as response:
                    target.write_bytes(response.read())
                break
            except Exception as error:
                last_error = error
                time.sleep(3.0 * (attempt + 1))
        else:
            candidate.note = (candidate.note + " download_failed").strip()
            candidate.local_path = ""
            return candidate
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    candidate.local_path = str(target.relative_to(PROJECT_ROOT))
    candidate.sha256 = digest
    candidate.downloaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    candidate.stills = extract_stills(target, candidate.slot)
    return candidate


def extract_stills(path: Path, slot: str, count: int = 3) -> list[str]:
    STILLS.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(path)
    paths: list[str] = []
    for index in range(count):
        if duration <= 0:
            break
        at = duration * (0.2 + 0.3 * index)
        out = STILLS / f"{slot}_{index + 1}.png"
        subprocess.run(
            [
                str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=640:-1", str(out),
            ],
            capture_output=True,
        )
        if out.exists():
            paths.append(str(out.relative_to(PROJECT_ROOT)))
    return paths


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def probe_size(path: Path) -> tuple[int, int, float]:
    proc = subprocess.run(
        [str(FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        width, height = [int(part) for part in proc.stdout.strip().split(",")[:2]]
    except ValueError:
        width, height = 0, 0
    return width, height, probe_duration(path)


def write_registry(candidates: dict[str, VideoCandidate]) -> None:
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(
        json.dumps({k: asdict(v) for k, v in candidates.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_registry() -> dict[str, VideoCandidate]:
    if not CANDIDATES.exists():
        return {}
    data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    return {k: VideoCandidate(**v) for k, v in data.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="取可商用的真实动态素材")
    parser.add_argument("--plan", action="store_true", help="按预设槽位全量搜")
    parser.add_argument("--only", nargs="*", default=[], help="只搜这些槽位")
    parser.add_argument("--title", action="append", default=[], help="slot=File:xxx.webm 精确取件")
    parser.add_argument("--dry-run", action="store_true", help="只搜不下")
    args = parser.parse_args(argv)

    registry = load_registry()
    report: dict = {"slots": {}, "downloaded": [], "no_asset": []}

    if args.title:
        for item in args.title:
            slot, _, title = item.partition("=")
            found = fetch_by_title(title.strip(), slot.strip())
            if not found:
                report["no_asset"].append(slot)
                continue
            candidate = found[0]
            if not args.dry_run:
                candidate = download(candidate)
                registry[candidate.slot] = candidate
            report["slots"][slot] = asdict(candidate)
        write_registry(registry)
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    slots = PLAN.items()
    if args.only:
        slots = [(slot, PLAN[slot]) for slot in args.only if slot in PLAN]
    elif not args.plan:
        parser.error("要么 --plan，要么 --only，要么 --title")

    for slot, queries in slots:
        found = search_slot(slot, queries)
        if not found:
            report["no_asset"].append(slot)
            continue
        candidate = found[0]
        if not args.dry_run:
            candidate = download(candidate)
            registry[slot] = candidate
            if candidate.local_path:
                width, height, duration = probe_size(STAGING / Path(candidate.local_path).name)
                candidate.width, candidate.height = width, height
                candidate.duration_s = round(duration, 2)
                registry[slot] = candidate
                report["downloaded"].append(slot)
        report["slots"][slot] = asdict(candidate)

    write_registry(registry)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
