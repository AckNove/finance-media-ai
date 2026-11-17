"""给素材缺口补料：`python -m backend.scripts.fill_material_gaps --job <任务号>`

做什么
------
读 `storyboard/materials_plan.json`，把缺素材的镜头（`fallback == "manual"`，可选 `animation`）
挑出来，用计划里的**英文检索词**去 **Pexels** 找候选（用户已授权抓取本站素材），挑一条最像的下载到
`assets/real/_staging_online/`，按 11 字段写进 `assets/real/source_register.csv`，
最后**按口播原句重新打分**，不够分就丢弃。

为什么用英文检索词
------------------
Pexels 的索引是英文的：实测 `search_photos("数据中心 机房")` 返回杭州天际线、`"变电站"` 返回北京扶梯。
所以计划里必须带 `queries_en`（`script_materials.PLAN_PROMPT` 已经要求模型给）。

不做什么
--------
- 不把"下载成功"当成"语义匹配"：能不能用由 `score_asset()` 重新打分决定，低于 `MIN_MATCH_SCORE` 直接删文件。
- 不伪造授权：Pexels License 原样写进登记表，并保留作者与页面地址。
- 不并发轰炸：`pexels_scrape` 内部有 2 秒礼貌间隔。
- 中文检索词只在 Pexels 失败后退到 Openverse 时用。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from backend.app.capabilities.media import openverse as ov
from backend.app.capabilities.media import pexels_scrape as px
from backend.app.video import script_materials as sm

JOBS_ROOT = Path(__file__).resolve().parents[2] / "content" / "jobs"
STAGING = Path("assets") / "real" / "_staging_online"
REGISTER_FALLBACK = Path("assets") / "real" / "source_register.csv"

REGISTER_COLUMNS = [
    "asset_id", "original_file", "source_url", "publisher_or_subject", "license",
    "commercial_use", "modification_allowed", "attribution_required", "used_in_shot",
    "downloaded_at", "sha256", "local_path", "note",
]


_WORD = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.split(str(text).lower()) if len(word) > 2}


_STOP = {
    "the", "and", "for", "with", "from", "into", "onto", "over", "under", "two", "three",
    "one", "a", "an", "of", "on", "in", "at", "to", "by", "is", "are", "view", "shot",
}


def _content_tokens(text: str) -> set[str]:
    return {word for word in _tokens(text) if word not in _STOP}


def _english_hit(queries_en, title: str, tags=()) -> float:
    """逐条检索词算命中率，取最高的那条（0–1）。

    要点：把 "two/on/the" 这类词当噪声去掉；一条 3 词的检索词命中 2 个就算 0.67，
    比"把三条检索词并成一个集合再算比例"更贴近人眼判断。
    """

    have = _tokens(title)
    for tag in tags or ():
        have |= _tokens(str(tag))
    best = 0.0
    for query in queries_en or ():
        want = _content_tokens(query)
        if not want:
            continue
        best = max(best, len(want & have) / len(want))
    return best


def _resolve_register(job: Path) -> Path:
    """沿用任务里已有的登记表（remaster_v2 下那一份有 17 条），没有才新建。"""

    existing = sorted(job.rglob("source_register.csv"), key=lambda p: len(str(p)))
    return existing[0] if existing else job / REGISTER_FALLBACK


def _row(*, asset_id: str, local_path: str, page_url: str, download_url: str, creator: str,
         license_name: str, attribution: bool, shot_index: int, retrieved_at: str,
         sha256: str, note: str) -> dict:
    return {
        "asset_id": asset_id,
        "original_file": Path(local_path).name,
        "source_url": page_url or download_url,
        "publisher_or_subject": creator,
        "license": license_name,
        "commercial_use": "allowed",
        "modification_allowed": "allowed",
        "attribution_required": "yes" if attribution else "no",
        "used_in_shot": f"S{shot_index:02d}",
        "downloaded_at": retrieved_at,
        "sha256": sha256,
        "local_path": local_path,
        "note": note,
    }


def _registered_ids(register: Path) -> set[str]:
    if not register.is_file():
        return set()
    with register.open("r", encoding="utf-8", newline="") as handle:
        return {str(row.get("asset_id") or "") for row in csv.DictReader(handle)}


def _append_register(register: Path, row: dict) -> None:
    register.parent.mkdir(parents=True, exist_ok=True)
    new_file = not register.is_file()
    with register.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _pick(assets: list, queries: list[str]) -> object | None:
    """挑一条：优先竖屏且够大，其次标题命中英文检索词多的。"""

    def rank(asset) -> tuple[float, int, int, int]:
        portrait = 1 if asset.height >= asset.width else 0
        big = 1 if min(asset.width, asset.height) >= 900 else 0
        hit = _english_hit(queries, asset.title, getattr(asset, "tags", ()) or ())
        return (round(hit, 3), portrait + big, asset.width * asset.height, 0)

    ranked = sorted(assets, key=rank, reverse=True)
    return ranked[0] if ranked else None


def _gate(shot: dict, *, title: str, tags: tuple[str, ...], kind: str) -> tuple[float, str]:
    """下载后再判一次相关性：用英文检索词与候选标题/标签比。"""

    line = sm.ScriptLine(
        index=int(shot.get("index") or 1),
        text=str(shot.get("text", "")),
        seconds=float(shot.get("seconds") or 0.0),
    )
    intent = shot.get("intent") or {}
    probe = sm.VisualIntent(
        index=line.index,
        kind=str(intent.get("kind") or "real_image"),
        visual_goal=str(intent.get("visual_goal") or ""),
        queries=tuple(str(q) for q in (intent.get("queries_en") or []) if str(q).strip())
        or tuple(str(q) for q in (intent.get("queries") or []) if str(q).strip()),
        source="rules",
    )
    if not probe.queries:  # 没有检索词就没法判，按不通过处理
        return 0.0, "没有可用检索词，无法判定相关性"
    english = tuple(str(q) for q in (intent.get("queries_en") or []) if str(q).strip())
    if english:
        hit = _english_hit(english, title, tags)
        # 英文素材站：只看检索词是否真的落在标题/标签里，不拿中文原句去比英文标题
        return hit, "（按英文检索词命中率判定）"
    asset = sm.LibraryAsset(
        asset_id="probe",
        kind=kind,
        title=title,
        tags=tags,
        local_path="",
    )
    return sm.score_asset(probe, line, asset), ""


def fill(job: Path, *, per_shot: int = 6, limit_shots: int = 4, dry_run: bool = False,
         include_animation: bool = False) -> dict:
    plan_path = job / "storyboard" / "materials_plan.json"
    if not plan_path.is_file():
        raise SystemExit(f"没有素材计划：{plan_path}（先跑 python -m backend.scripts.plan_materials）")

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = payload.get("shots") or []
    wanted = {"manual"} | ({"animation"} if include_animation else set())
    gaps = [s for s in shots if str(s.get("fallback") or "") in wanted][:limit_shots]

    register = _resolve_register(job)
    already = _registered_ids(register)
    staging = job / STAGING
    added: list[dict] = []
    failures: list[dict] = []

    for shot in gaps:
        intent = shot.get("intent") or {}
        queries_en = [str(q) for q in (intent.get("queries_en") or []) if str(q).strip()][:3]
        queries_zh = [str(q) for q in (intent.get("queries") or []) if str(q).strip()][:2]
        kind_wanted = str(intent.get("kind") or "real_image")

        chosen = None
        used_query = ""
        used_source = ""

        # ---- 1. Pexels（英文词，站点内部接口） ----
        if queries_en:
            for query in queries_en:
                for order in ((px.search_videos, px.search_photos) if kind_wanted == "real_video"
                              else (px.search_photos, px.search_videos)):
                    try:
                        found = order(query, per_page=per_shot)
                    except px.PexelsScrapeError as error:
                        failures.append({"index": shot.get("index"), "query": query,
                                         "reason": error.reason, "message": str(error)})
                        continue
                    if not found:
                        continue
                    score, _ = _gate(shot, title=found[0].title, tags=found[0].tags, kind=found[0].kind)
                    best = _pick(found, queries_en)
                    if best is not None and score >= 0:
                        chosen, used_query, used_source = best, query, "pexels"
                        break
                if chosen is not None:
                    break

        # ---- 2. Openverse 兜底（中文词；中文财经题材覆盖薄，但它是可核验许可的第二选择） ----
        if chosen is None and queries_zh:
            pool: list = []
            for query in queries_zh:
                try:
                    pool.extend(ov.search_images(query, limit=per_shot))
                except ov.OpenverseError as error:
                    failures.append({"index": shot.get("index"), "query": query,
                                     "reason": error.reason, "message": str(error)})
            if pool:
                chosen, used_query, used_source = _pick(pool, queries_zh), queries_zh[0], "openverse"

        if chosen is None:
            failures.append({
                "index": shot.get("index"),
                "query": (queries_en or queries_zh or [""])[0],
                "reason": "no_candidate",
                "message": "Pexels 与 Openverse 都没有合适的候选",
            })
            continue

        if dry_run:
            added.append({"index": shot.get("index"), "asset_id": chosen.asset_id,
                          "title": chosen.title, "source": used_source, "dry_run": True})
            continue

        try:
            if used_source == "pexels":
                chosen = px.download(chosen, staging)
            else:
                chosen = ov.download(chosen, staging)
        except (px.PexelsScrapeError, ov.OpenverseError) as error:
            failures.append({"index": shot.get("index"), "query": used_query,
                             "reason": error.reason, "message": str(error)})
            continue

        chosen_kind = getattr(chosen, "kind", "image")
        score, note = _gate(shot, title=chosen.title,
                            tags=tuple(getattr(chosen, "tags", ()) or ()), kind=chosen_kind)
        if score < sm.MIN_MATCH_SCORE:
            Path(chosen.local_path).unlink(missing_ok=True)
            failures.append({
                "index": shot.get("index"), "query": used_query, "reason": "rejected_low_score",
                "message": f"搜到「{chosen.title[:36]}」但和这句不相关（{score:.2f} < {sm.MIN_MATCH_SCORE}）{note}，已丢弃",
            })
            continue

        if chosen.asset_id in already:
            failures.append({"index": shot.get("index"), "query": used_query,
                             "reason": "already_registered",
                             "message": f"{chosen.asset_id} 已经在登记表里，跳过（不重复写）"})
            continue

        creator = getattr(chosen, "photographer", "") or getattr(chosen, "creator", "") or f"{used_source} 作者"
        _append_register(register, _row(
            asset_id=chosen.asset_id,
            local_path=chosen.local_path,
            page_url=chosen.page_url,
            download_url=chosen.download_url,
            creator=creator,
            license_name=chosen.license_name,
            attribution=True,
            shot_index=int(shot.get("index") or 0),
            retrieved_at=chosen.retrieved_at,
            sha256=chosen.sha256,
            note=f"query={used_query}; source={used_source}; score={score:.2f}",
        ))
        already.add(chosen.asset_id)
        added.append({
            "index": shot.get("index"),
            "asset_id": chosen.asset_id,
            "title": chosen.title,
            "kind": chosen_kind,
            "license": chosen.license_name,
            "creator": creator,
            "score": round(score, 3),
            "source": used_source,
            "local_path": chosen.local_path,
            "sha256": chosen.sha256[:12],
        })

    return {"gaps": len(gaps), "added": added, "failures": failures, "register": str(register)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用 Pexels（站点接口）补素材缺口，Openverse 兜底")
    parser.add_argument("--job", required=True)
    parser.add_argument("--jobs-root", default=str(JOBS_ROOT))
    parser.add_argument("--per-shot", type=int, default=6, help="每个检索词取多少条候选")
    parser.add_argument("--limit-shots", type=int, default=4, help="这一轮最多补几个镜头")
    parser.add_argument("--include-animation", action="store_true",
                        help="连计划里判成 animation 的镜头也先试着找真实素材")
    parser.add_argument("--dry-run", action="store_true", help="只选不下载")
    args = parser.parse_args(argv)

    job = Path(args.jobs_root) / args.job
    if not job.is_dir():
        print(f"任务目录不存在：{job}", file=sys.stderr)
        return 2

    result = fill(job, per_shot=args.per_shot, limit_shots=args.limit_shots,
                  dry_run=args.dry_run, include_animation=args.include_animation)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["added"] or not result["gaps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
