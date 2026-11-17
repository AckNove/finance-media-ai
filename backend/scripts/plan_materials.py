"""把"已确认的口播稿"变成"每一段要什么画面"的素材计划。

用法：

    python -m backend.scripts.plan_materials --job v6_power_first_film
    python -m backend.scripts.plan_materials --job v6_power_first_film --no-model

它做四件事：
1. 从任务目录里读口播稿（优先 `script/SCRIPT.md` 的分段表，其次 `audio/narration/narration.json`
   的 clip 文本），实测时长用旁白音频的 `duration_seconds`；
2. 用本项目统一的模型（deepseek-4.1-Flash）逐段判断"观众该看见什么"、给出检索词；
   没有模型就退回规则，并如实标注 `source=rules`；
3. 在已登记、可商用的素材库里匹配（`source_register.csv`），匹配不上就报缺口；
4. 写出 `materials_plan.json` 并打印摘要（覆盖率、缺口、总时长）。

**不会联网搜图**：Pexels 需要官方 API Key，没配置就是 BLOCKED（见 `/api/system/media-status`）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT
from backend.app.video import script_materials as sm

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
NARRATION = Path("audio") / "narration" / "narration.json"
SCRIPT_MD = Path("script") / "SCRIPT.md"


def _lines_from_narration(job: Path) -> list[dict[str, object]]:
    path = job / NARRATION
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for clip in payload.get("clips") or []:
        text = str(clip.get("text") or "").strip()
        if not text:
            continue
        rows.append({"text": text, "seconds": float(clip.get("duration_seconds") or 0.0)})
    return rows


def _lines_from_script_md(job: Path) -> list[dict[str, object]]:
    """`SCRIPT.md` 里只要有列表或表格形式的分句就取出来；取不到就返回空。"""

    path = job / SCRIPT_MD
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("|") or line.startswith(">"):
            continue
        if line[0] in "-*0123456789" and "，" not in line[:1]:
            cleaned = line.lstrip("-*0123456789. ）)").strip()
            if len(cleaned) >= 8:
                rows.append({"text": cleaned})
    return rows


def _caller():
    from backend.app.core.caller import CallerContext

    return CallerContext.human("plan-materials-cli")


def _model_intents(lines: list[sm.ScriptLine]) -> tuple[list[sm.VisualIntent], str]:
    """真调一次模型；失败就退回规则，并把原因带回去（不静默）。"""

    from backend.app.capabilities.model import ModelRequest, get_model_gateway
    from backend.app.capabilities.model.providers import provider_status
    from backend.app.core.caller import CallerContext

    engine = get_model_gateway()
    ready = list(engine.ready_providers()) if hasattr(engine, "ready_providers") else []
    if not ready:
        ready = [name for name, entry in provider_status().items() if entry.get("status") in {"ready", "ok"}]
    if not ready:
        return [sm.rule_intent(line) for line in lines], "没有就绪的模型服务商，已退回规则"

    # 模型一律从 config/models.yaml 里取"已启用 + 价目已核"的那条，不在脚本里写死
    from backend.app.core.config import get_models_config

    models = get_models_config()
    chosen = None
    for entry in models.models:
        if entry.provider in ready and entry.enabled and entry.input_price_per_1k_cny is not None:
            chosen = entry
            break
    if chosen is None:
        return [sm.rule_intent(line) for line in lines], "没有已启用且价目已核的模型，已退回规则"
    provider, model = chosen.provider, chosen.model

    def complete(*, prompt: str, purpose: str, max_tokens: int, caller=None, budget=None,
                 cacheable: bool = False, response_format: str = "text"):
        request = ModelRequest(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            purpose=purpose,
            max_tokens=max_tokens,
            cacheable=cacheable,
            response_format=response_format,
        )
        return engine.complete(caller or _caller(), request, budget=budget)

    try:
        return sm.model_intents(lines, complete=complete), ""
    except sm.MaterialPlanError as error:
        return [sm.rule_intent(line) for line in lines], f"模型没成功（{error.reason}），已退回规则：{error}"


def build(job_id: str, *, use_model: bool = True, jobs_root: Path = JOBS_ROOT) -> dict[str, object]:
    job = Path(jobs_root) / job_id
    if not job.is_dir():
        raise SystemExit(f"任务目录不存在：{job}")

    rows = _lines_from_narration(job) or _lines_from_script_md(job)
    if not rows:
        raise SystemExit(f"{job} 里没有找到口播稿（{NARRATION} 或 {SCRIPT_MD}）")

    lines = sm.split_narration(rows)
    intents: list[sm.VisualIntent]
    note = ""
    if use_model:
        intents, note = _model_intents(lines)
    else:
        intents = [sm.rule_intent(line) for line in lines]
        note = "按 --no-model 走规则"

    library = sm.load_library([job, PROJECT_ROOT / "content" / "library"])
    shots = sm.match_shots(lines, intents, library)
    summary = sm.summarise(shots, library)
    if note:
        summary["note"] = note

    out_path = job / "storyboard" / "materials_plan.json"
    sm.write_plan(shots, out_path)

    return {
        "job": job_id,
        "plan": str(out_path),
        "model_used": any(intent.source == "model" for intent in intents),
        "summary": summary,
        "shots": [shot.as_dict() for shot in shots],
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="口播稿 → 镜头素材计划")
    parser.add_argument("--job", required=True, help="content/jobs 下的任务目录名")
    parser.add_argument("--no-model", action="store_true", help="只用规则，不调用模型")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)

    payload = build(args.job, use_model=not args.no_model)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    summary = payload["summary"]
    print(f"口播稿：{summary['shots']} 段，合计 {summary['total_seconds']} 秒")
    print(f"素材库：{summary['library_size']} 条；匹配上 {summary['matched']} 段；"
          f"走动画 {summary['animated']} 段；待补素材 {summary['manual_needed']} 段")
    print(f"覆盖率：{summary['coverage']:.0%}；计划写到 {payload['plan']}")
    if summary.get("note"):
        print(f"说明：{summary['note']}")
    for shot in payload["shots"]:
        mark = shot["asset_id"] or f"[{shot['fallback'] or '缺'}]"
        print(f"  {shot['index']:>2}. {shot['seconds']:>5.2f}s  {mark:<10} {shot['intent']['kind']:<10} {shot['text'][:26]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
