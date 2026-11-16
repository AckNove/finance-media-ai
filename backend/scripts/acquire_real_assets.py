"""从维基共享资源（Wikimedia Commons）取真实拍摄素材，并把授权信息一起记下来。

为什么是 Commons：它的每一张图/每一段视频都带机器可读的授权元数据
（`extmetadata.LicenseShortName` / `License` / `Artist` / `Credit`），
不需要 API Key、不需要注册、不需要付费，而且允许程序批量核对"到底能不能商用"。

本脚本只做三件事：
1. 按查询词搜 `imageinfo`，把候选的授权、作者、来源页、原始文件地址全部取回；
2. **只挑允许商业使用、且允许改编的授权**（CC0 / 公有领域 / CC BY），
   **一律跳过 CC BY-SA 与 NC**——BY-SA 有传染性，NC 不能用于变现内容；
3. 下载到 `assets/real/_staging/`，算 sha256，写一份 candidates.json。

它不判断"这张图好不好看"，也不替人做最终授权决定——那一步要人看登记表。

用法：
    python -m backend.scripts.acquire_real_assets --query "power transmission tower China"
    python -m backend.scripts.acquire_real_assets --plan      # 按预设清单批量取
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOB_ROOT = PROJECT_ROOT / "content" / "jobs" / "v6_power_first_film"
STAGING = JOB_ROOT / "remaster_v2" / "assets" / "real" / "_staging"
REGISTRY_JSON = JOB_ROOT / "remaster_v2" / "assets" / "real" / "candidates.json"

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "AKFinanceMediaBot/1.0 (local research project; contact: repo owner) "
    "python-urllib"
)
TIMEOUT = 45

# 允许商用的授权白名单（短名，Commons 的 LicenseShortName 字段）
COMMERCIAL_OK = ("CC0", "Public domain", "PD", "CC BY 4.0", "CC BY 3.0", "CC BY 2.0", "CC BY 2.5")
# 明确拒绝的授权（出现即跳过）
FORBIDDEN = ("SA", "NC", "ND", "Fair use", "Non-free")

# 本轮要覆盖的真实场景（对应任务书第一层"目标场景"）
PLAN: tuple[tuple[str, str], ...] = (
    ("coal_power_plant", "coal power station"),
    ("substation_transformer", "electrical substation"),
    ("grid_control_room", "power grid control room"),
    ("wind_farm", "wind turbines farm"),
    ("industrial_power_use", "steel mill factory"),
    ("city_night_power", "city night skyline"),
    ("pylon_line_sunset", "power lines sunset"),
    ("nrel_power_plant", "NREL power plant"),
)


@dataclass
class Candidate:
    slot: str
    title: str
    page_url: str
    file_url: str
    mime: str
    width: int
    height: int
    size_bytes: int
    license_short: str
    license_name: str
    artist: str
    credit: str
    commercial_ok: bool
    reasons: list[str] = field(default_factory=list)
    local_path: str = ""
    sha256: str = ""
    retrieved_at: str = ""


def _get(url: str, *, attempts: int = 4) -> bytes:
    """取件带退避重试：公共 API 会偶发 429/连接重置，一次失败不代表拿不到。"""
    last: Exception | None = None
    for index in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                return response.read()
        except Exception as error:  # noqa: BLE001 - 网络层什么都可能抛
            last = error
            time.sleep(2.0 * (index + 1))
    raise RuntimeError(f"取件失败（重试 {attempts} 次）：{last}")


def _api(params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    return json.loads(_get(f"{API}?{query}").decode("utf-8"))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _license_verdict(short: str, name: str) -> tuple[bool, list[str]]:
    """能不能商用：白名单命中→可以；命中禁用词→不可以；都不命中→不可以（宁可错杀）。"""
    text = f"{short} {name}"
    for word in FORBIDDEN:
        if word.lower() in text.lower():
            return False, [f"授权含 {word}，不能用于变现内容"]
    for allowed in COMMERCIAL_OK:
        if allowed.lower() in text.lower():
            return True, []
    return False, [f"授权「{short or name or '未知'}」不在允许商用的白名单里"]


def fetch_by_title(title: str) -> list[Candidate]:
    """按 Commons 文件标题精确取一条；标题写错就是空列表，不会悄悄给个别的东西。"""
    params = {
        "action": "query",
        "titles": title if title.startswith("File:") else f"File:{title}",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1920",
    }
    payload = _api(params)
    return _candidates_from(payload)


def search(query: str, *, limit: int = 12, kinds: str = "image") -> list[Candidate]:
    """按关键词搜真实拍摄素材；只回带 imageinfo 的条目。"""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query} filetype:{kinds}",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1920",
    }
    payload = _api(params)
    return _candidates_from(payload)


def _candidates_from(payload: dict) -> list[Candidate]:
    pages = payload.get("query", {}).get("pages", [])
    out: list[Candidate] = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}

        def field_of(key: str) -> str:
            return _strip_html(str((meta.get(key) or {}).get("value", "")))

        short = field_of("LicenseShortName")
        name = field_of("License")
        ok, reasons = _license_verdict(short, name)
        out.append(
            Candidate(
                slot="",
                title=page.get("title", ""),
                page_url=info.get("descriptionurl", ""),
                # Commons 对原图直链会限流（HTTP 429），官方建议取按需尺寸的缩略图
                file_url=info.get("thumburl") or info.get("url", ""),
                mime=info.get("mime", ""),
                width=int(info.get("width", 0) or 0),
                height=int(info.get("height", 0) or 0),
                size_bytes=int(info.get("size", 0) or 0),
                license_short=short,
                license_name=name,
                artist=field_of("Artist"),
                credit=field_of("Credit"),
                commercial_ok=ok,
                reasons=reasons,
            )
        )
    return out


def download(candidate: Candidate, *, dest_dir: Path) -> Candidate:
    """下载并算哈希；文件名用标题的稳定部分，避免中文与冒号进路径。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(candidate.file_url).path).suffix or ".bin"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", candidate.title.split(":", 1)[-1]).strip("_")[:60]
    target = dest_dir / f"{candidate.slot}_{stem}{suffix}"
    if not target.exists():
        target.write_bytes(_get(candidate.file_url))
    candidate.local_path = str(target.relative_to(PROJECT_ROOT)).replace("\\", "/")
    candidate.sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    candidate.retrieved_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return candidate


def pick(candidates: list[Candidate], *, min_width: int = 1200) -> Candidate | None:
    """挑一张：先看能不能商用，再看分辨率够不够 1080 竖屏裁切。"""
    usable = [c for c in candidates if c.commercial_ok and c.width >= min_width]
    usable.sort(key=lambda c: (c.width * c.height), reverse=True)
    return usable[0] if usable else None


def acquire(slot: str, query: str, *, limit: int = 12, dry_run: bool = False) -> dict:
    found = search(query, limit=limit)
    chosen = pick(found)
    record: dict = {
        "slot": slot,
        "query": query,
        "found": len(found),
        "usable": sum(1 for c in found if c.commercial_ok),
        "chosen": None,
        "rejected_sample": [
            {"title": c.title, "license": c.license_short, "why": "; ".join(c.reasons)}
            for c in found
            if not c.commercial_ok
        ][:5],
    }
    if chosen is None:
        record["status"] = "no_commercially_usable_asset"
        return record
    chosen.slot = slot
    if not dry_run:
        try:
            chosen = download(chosen, dest_dir=STAGING)
        except Exception as error:  # noqa: BLE001 - 单张失败不该拖垮整批
            record["status"] = "download_failed"
            record["error"] = f"{type(error).__name__}: {error}"
            record["chosen"] = asdict(chosen)
            return record
        time.sleep(2.0)  # 对公共 API 客气一点
    record["chosen"] = asdict(chosen)
    record["status"] = "dry_run" if dry_run else "downloaded"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="取真实拍摄素材并登记授权")
    parser.add_argument("--query", help="单个查询词")
    parser.add_argument("--slot", default="adhoc", help="给单次查询起的槽位名")
    parser.add_argument("--plan", action="store_true", help="按预设清单批量取")
    parser.add_argument("--dry-run", action="store_true", help="只查授权不下载")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--title", action="append", default=[],
        help="按 Commons 文件标题精确取（可重复，格式 slot=File:xxx.jpg）",
    )
    args = parser.parse_args(argv)

    if not args.query and not args.plan and not args.title:
        parser.error("要么给 --query、--title，要么用 --plan")

    if args.title:
        results = []
        for item in args.title:
            slot, _, title = item.partition("=")
            found = fetch_by_title(title)
            chosen = pick(found)
            record = {"slot": slot, "query": title, "found": len(found),
                      "usable": sum(1 for c in found if c.commercial_ok), "chosen": None}
            if chosen is None:
                record["status"] = "no_commercially_usable_asset"
            else:
                chosen.slot = slot
                try:
                    chosen = download(chosen, dest_dir=STAGING)
                    record["status"] = "downloaded"
                except Exception as error:  # noqa: BLE001
                    record["status"] = "download_failed"
                    record["error"] = f"{type(error).__name__}: {error}"
                record["chosen"] = asdict(chosen)
            results.append(record)
    else:
        jobs = PLAN if args.plan else ((args.slot, args.query),)
        results = [acquire(slot, query, limit=args.limit, dry_run=args.dry_run) for slot, query in jobs]

    # 合并而不是覆盖：分几次取素材时，前面的登记不能因为再跑一次就消失
    merged: dict[str, dict] = {}
    if REGISTRY_JSON.is_file():
        try:
            previous = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
            for item in previous.get("results", []):
                merged[item.get("slot", "")] = item
        except json.JSONDecodeError:
            pass
    for item in results:
        merged[item.get("slot", "")] = item
    results = list(merged.values())

    REGISTRY_JSON.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_JSON.write_text(
        json.dumps(
            {
                "source": "Wikimedia Commons",
                "api": API,
                "policy": {
                    "accepted": list(COMMERCIAL_OK),
                    "rejected_keywords": list(FORBIDDEN),
                    "note": "只收允许商用且允许改编的授权；CC BY-SA 与 NC 一律不收。",
                },
                "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "results": len(results),
        "downloaded": sum(1 for r in results if r["status"] == "downloaded"),
        "no_asset": [r["slot"] for r in results if r["status"] == "no_commercially_usable_asset"],
        "registry": str(REGISTRY_JSON.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
