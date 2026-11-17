"""给素材缺口补料：`python -m backend.scripts.fill_material_gaps --job <任务号>`

做什么
------
读 `storyboard/materials_plan.json`，把 `fallback == "manual"`（缺素材）的镜头挑出来，
用模型给出的检索词去 **Openverse**（无需 Key、许可可核验）找候选，挑一条最像的下载到
`assets/real/_staging_online/`，按 11 字段写进 `assets/real/source_register.csv`，
然后**重新跑一遍素材匹配**，把结果写回计划。

不做什么
--------
- 不去抓 Pexels / Pixabay 的网页：那些站点要求走官方 API，抓页面既是绕过限制，也拿不到可核验的授权字段。
- 不把"下载成功"当成"语义匹配"：下载只是补库，能不能用由 `script_materials.match_shots()` 重新打分决定。
- 不伪造授权：只收 cc0 / pdm / cc-by，且把许可与作者原样写进登记表。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from backend.app.capabilities.media import openverse as ov
from backend.app.video import script_materials as sm

JOBS_ROOT = Path(__file__).resolve().parents[2] / "content" / "jobs"
STAGING = Path("assets") / "real" / "_staging_online"
REGISTER_FALLBACK = Path("assets") / "real" / "source_register.csv"


def _resolve_register(job: Path) -> Path:
    """沿用任务里已有的登记表（remaster_v2 下那一份有 17 条），没有才新建。"""

    existing = sorted(job.rglob("source_register.csv"), key=lambda p: len(str(p)))
    return existing[0] if existing else job / REGISTER_FALLBACK

REGISTER_COLUMNS = [
    "asset_id", "original_file", "source_url", "publisher_or_subject", "license",
    "commercial_use", "modification_allowed", "attribution_required", "used_in_shot",
    "downloaded_at", "sha256", "local_path", "note",
]


def _append_register(register: Path, asset: ov.OpenverseAsset, *, shot_index: int) -> None:
    register.parent.mkdir(parents=True, exist_ok=True)
    new_file = not register.is_file()
    with register.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow({
            "asset_id": asset.asset_id,
            "original_file": Path(asset.local_path).name,
            "source_url": asset.page_url or asset.download_url,
            "publisher_or_subject": asset.creator or "Openverse 作者",
            "license": asset.license_name,
            "commercial_use": "allowed",
            "modification_allowed": "allowed",
            "attribution_required": "yes" if asset.license_name.upper().startswith("CC BY") else "no",
            "used_in_shot": f"S{shot_index:02d}",
            "downloaded_at": asset.retrieved_at,
            "sha256": asset.sha256,
            "local_path": asset.local_path,
            "note": f"keywords={' '.join(asset.tags) or asset.query}; source=openverse",
        })


def _pick(candidates: list[ov.OpenverseAsset], queries: list[str]) -> ov.OpenverseAsset | None:
    """挑一条：优先竖图且够大，其次按标题命中检索词的多少。"""

    def rank(asset: ov.OpenverseAsset) -> tuple[int, int, int]:
        portrait = 1 if asset.height >= asset.width else 0
        big = 1 if min(asset.width, asset.height) >= 900 else 0
        title = asset.title.lower()
        hits = sum(1 for word in queries if word and word.lower() in title)
        return (portrait + big, hits, asset.width * asset.height)

    ranked = sorted(candidates, key=rank, reverse=True)
    return ranked[0] if ranked else None


def fill(job: Path, *, per_shot: int = 6, limit_shots: int = 4, dry_run: bool = False) -> dict:
    plan_path = job / "storyboard" / "materials_plan.json"
    if not plan_path.is_file():
        raise SystemExit(f"没有素材计划：{plan_path}（先跑 python -m backend.scripts.plan_materials）")

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = payload.get("shots") or []
    gaps = [s for s in shots if str(s.get("fallback") or "") == "manual"][:limit_shots]

    register = _resolve_register(job)
    staging = job / STAGING
    added: list[dict] = []
    failures: list[dict] = []

    for shot in gaps:
        intent = shot.get("intent") or {}
        queries = [str(q) for q in (intent.get("queries") or []) if str(q).strip()][:3]
        if not queries:
            queries = [str(shot.get("text", ""))[:24]]
        candidates: list[ov.OpenverseAsset] = []
        for query in queries:
            try:
                candidates.extend(ov.search_images(query, limit=per_shot))
            except ov.OpenverseError as error:
                failures.append({"index": shot.get("index"), "query": query, "reason": error.reason, "message": str(error)})
        chosen = _pick(candidates, queries)
        if chosen is None:
            failures.append({"index": shot.get("index"), "query": queries[0], "reason": "no_candidate", "message": "Openverse 没有合适的候选"})
            continue
        if dry_run:
            added.append({"index": shot.get("index"), "asset_id": chosen.asset_id, "title": chosen.title, "dry_run": True})
            continue
        try:
            chosen = ov.download(chosen, staging)
        except ov.OpenverseError as error:
            failures.append({"index": shot.get("index"), "query": chosen.query, "reason": error.reason, "message": str(error)})
            continue
        chosen.tags = tuple(queries)

        # 关键闸门：下载成功 ≠ 能用。按口播原句重新打分，分数不够就退掉，
        # 绝不把"搜到的东西"直接塞时间轴（这是最容易骗过自己的地方）。
        line = sm.ScriptLine(
            index=int(shot.get("index") or 1),
            text=str(shot.get("text", "")),
            seconds=float(shot.get("seconds") or 0.0),
        )
        intent = sm.rule_intent(line)
        scored = sm.score_asset(
            intent,
            line,
            sm.LibraryAsset(
                asset_id=chosen.asset_id,
                kind="image",
                title=chosen.title,
                tags=chosen.tags,
                local_path=chosen.local_path,
                page_url=chosen.page_url,
                credit=chosen.creator,
                license_name=chosen.license_name,
            ),
        )
        if scored < sm.MIN_MATCH_SCORE:
            Path(chosen.local_path).unlink(missing_ok=True)
            failures.append({
                "index": shot.get("index"),
                "query": chosen.query,
                "reason": "rejected_low_score",
                "message": f"搜到「{chosen.title[:24]}」但和这句不相关（{scored:.2f} < {sm.MIN_MATCH_SCORE}），已丢弃",
            })
            continue

        _append_register(register, chosen, shot_index=int(shot.get("index") or 0))
        added.append({
            "index": shot.get("index"),
            "asset_id": chosen.asset_id,
            "title": chosen.title,
            "license": chosen.license_name,
            "creator": chosen.creator,
            "local_path": chosen.local_path,
            "sha256": chosen.sha256[:12],
        })

    return {"gaps": len(gaps), "added": added, "failures": failures, "register": str(register)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用 Openverse 补素材缺口（无需 Key）")
    parser.add_argument("--job", required=True)
    parser.add_argument("--jobs-root", default=str(JOBS_ROOT))
    parser.add_argument("--per-shot", type=int, default=6, help="每个检索词取多少条候选")
    parser.add_argument("--limit-shots", type=int, default=4, help="这一轮最多补几个镜头")
    parser.add_argument("--dry-run", action="store_true", help="只选不下载")
    args = parser.parse_args(argv)

    job = Path(args.jobs_root) / args.job
    if not job.is_dir():
        print(f"任务目录不存在：{job}", file=sys.stderr)
        return 2

    result = fill(job, per_shot=args.per_shot, limit_shots=args.limit_shots, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["added"] or not result["gaps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
