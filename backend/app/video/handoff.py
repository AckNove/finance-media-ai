"""从研究报告建立视频任务目录（研究 → 视频的交接点）。

这个模块存在的理由，是让"从报告建视频任务"变成**可追溯的文件交接**，而不是一句
"点一下就开始生成"。具体做法：

* 把报告正文、证据行、来源清单一并写进任务目录的 `research/`，每份都带 SHA-256。
  这样后面写脚本的人（或 Agent）手上有的是**带出处的材料**，而不是一段凭空的摘要。
* 脚本目录**留空**：没有模型（或有模型但价目未配）时，我们无法生成符合 AK 风格的
  脚本，更不能塞一段模板文字冒充。
* 分镜由**纯规则**排一份草稿（`backend/app/video/outline.py`）：每镜口播稿就是底稿里
  的原文，逐镜写明来源，产物旁边放 `storyboard/OUTLINE.md` 说明它没有调用模型。
  有至少两个来源且证据非空才排；排不出来就把原因写进 `job.json`，不硬凑。
* `job.json` 明写 `stage` / `next_stage` / `blocked` / `blocked_reason`，
  以及继续推进需要什么。人打开目录就知道卡在哪。
* 同一个报告重复建任务不会覆盖已有目录（`job_id` 已存在则报错），避免把人工已经
  改了的分镜覆盖掉。

不做什么：不抓新资料、不调模型、不渲染。这几件事必须由人看过报告之后再决定。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.app.core.config import PROJECT_ROOT

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
INVALIDATION_FILE = "INVALIDATED.md"
JOB_DIRS: tuple[str, ...] = (
    "research",
    "script",
    "storyboard",
    "assets",
    "audio",
    "output",
    "qc",
)
_SAFE = re.compile(r"[^0-9a-zA-Z_\-]+")


class VideoHandoffError(RuntimeError):
    """建任务目录失败（目录已存在、报告为空等）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class VideoJobScaffold:
    """一次交接的结果（都是可 JSON 化的裸数据）。"""

    job_id: str
    path: str
    stage: str
    next_stage: str
    blocked: bool
    blocked_reason: str
    next_step: str
    report_id: int
    evidence_count: int
    source_count: int
    files: tuple[str, ...] = field(default_factory=tuple)
    outline: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "path": str(self.path),
            "stage": self.stage,
            "next_stage": self.next_stage,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "next_step": self.next_step,
            "report_id": self.report_id,
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "files": list(self.files),
            "outline": dict(self.outline),
        }


def _value(item: Any) -> Any:
    """枚举或普通值都取成可 JSON 化的裸值。"""
    return getattr(item, "value", item)


def _slug(text: str, *, limit: int = 24) -> str:
    cleaned = _SAFE.sub("_", text or "").strip("_")
    return cleaned[:limit] or "job"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def _report_markdown(report: Any, confirmed: Sequence[Mapping[str, Any]] = ()) -> str:
    """交接给视频任务的报告副本。

    只有**人工确认过**的结论写在「可进成稿」一节里；其余结论照样列出来，但明确标注
    「不得进成稿」并带上当前复核状态——既不隐藏，也不许下游拿去当定稿。
    """
    confirmed_by_index = {
        int(item.get("finding_index", -1)): item for item in confirmed
    }
    lines = [
        f"# {report.title}",
        "",
        f"- 报告 ID：{report.id}",
        f"- 研究任务 ID：{getattr(report, 'research_task_id', None)}",
        f"- 核查状态：{_value(report.verification_status)}",
        f"- 生成方式：{report.author_agent or '规则汇编'}",
        f"- 人工已确认结论：{len(confirmed_by_index)} 条（只有这些可以进成稿）",
        "",
        "## 摘要",
        "",
        report.summary or "（报告没有摘要）",
        "",
        "## 可进成稿的结论（人工已确认）",
        "",
    ]
    if not confirmed_by_index:
        lines.append("（没有已确认的结论——按口径不得成稿）")
    findings = list(report.findings or [])

    def _text_of(item: Any) -> str:
        if isinstance(item, dict):
            return str(
                item.get("claim")
                or item.get("text")
                or item.get("statement")
                or json.dumps(item, ensure_ascii=False)
            )
        return str(item)

    for index, item in enumerate(findings):
        entry = confirmed_by_index.get(index)
        if entry is None:
            continue
        lines.append(
            f"- 结论 #{index}：{_text_of(item)}"
            f"（核查记录 #{entry.get('check_id')}｜模型判定 {entry.get('verdict') or 'unknown'}"
            f"｜人工已确认）"
        )
    pending = [
        (index, item)
        for index, item in enumerate(findings)
        if index not in confirmed_by_index
    ]
    if pending:
        lines += ["", "## 尚未人工确认（不得进成稿）", ""]
        for index, item in pending:
            lines.append(f"- 结论 #{index}（待人工复核）：{_text_of(item)}")
    lines += ["", "## 局限", "", report.limitations or "（未填写）", ""]
    return "\n".join(lines)


def _provider_ready() -> tuple[bool, str]:
    """有没有可用的模型服务（决定能不能进脚本阶段）。"""
    from backend.app.capabilities.model.providers import provider_status

    try:
        statuses = provider_status()
    except Exception as error:  # noqa: BLE001 - 配置坏了也要给出诚实结论
        return False, f"provider_status_failed:{type(error).__name__}"
    ready = [name for name, entry in statuses.items() if entry.get("status") == "ready"]
    if ready:
        return True, ",".join(sorted(ready))
    return False, "model_not_configured"


def _digest_from_rows(report: Any, evidence_rows: list[Any], source_rows: list[Any]) -> Any:
    """把库里的报告 + 证据 + 来源拼成规则分镜需要的底稿对象。"""
    from backend.app.research.digest import DataPoint, Digest, SourceRef

    labels: dict[Any, str] = {}
    refs: list[SourceRef] = []
    for index, source in enumerate(source_rows, start=1):
        label = f"S{index:02d}"
        labels[getattr(source, "id", None)] = label
        refs.append(
            SourceRef(
                source_id=label,
                title=str(getattr(source, "title", "") or ""),
                url=str(getattr(source, "url", "") or ""),
                site_name=str(getattr(source, "publisher", "") or ""),
                author="",
                published_at=str(getattr(source, "published_at", "") or ""),
                sha256=str(getattr(source, "content_hash", "") or ""),
                snapshot_path="",
                char_count=0,
                query="",
            )
        )
    points = []
    for item in evidence_rows:
        label = labels.get(getattr(item, "source_id", None))
        claim = str(getattr(item, "claim", "") or "").strip()
        if label and claim:
            points.append(DataPoint(source_id=label, text=claim))
    return Digest(
        title=str(getattr(report, "title", "") or ""),
        objective=str(getattr(report, "summary", "") or "")[:200],
        sources=tuple(refs),
        data_points=tuple(points),
    )


def _try_outline(report: Any, evidence_rows: list[Any], source_rows: list[Any],
                 directory: Path, identifier: str) -> dict[str, Any]:
    """试着排一份规则分镜；排不出来就如实记原因，不让整个交接失败。"""
    from backend.app.video.outline import OutlineError, build_outline

    digest = _digest_from_rows(report, evidence_rows, source_rows)
    try:
        result = build_outline(digest, job_root=directory, job_id=identifier)
    except OutlineError as error:
        return {"ok": False, "reason": error.reason, "message": str(error)}
    except Exception as error:  # noqa: BLE001 - 交接不能因为排版问题整体失败
        return {"ok": False, "reason": "outline_failed", "message": f"{type(error).__name__}: {error}"}
    payload = result.as_dict()
    payload["ok"] = True
    payload["chain"] = _try_chain_shot(report, directory)
    return payload


def _try_script_storyboard(
    report: Any, script: Any, directory: Path, script_id: int | None
) -> dict[str, Any]:
    """有脚本就按脚本排分镜（模型写过的那份稿子终于能进片子了）。

    脚本排不出来时**不退化成规则分镜**：规则分镜的句子跟脚本站的不是一回事，
    悄悄换掉等于让人以为"片子就是脚本那条"，所以只如实记原因。
    """
    from backend.app.video.script_board import ScriptStoryboardError, script_storyboard

    try:
        result = script_storyboard(script, job_root=directory)
    except ScriptStoryboardError as error:
        return {
            "ok": False,
            "source": "script",
            "script_id": script_id,
            "reason": error.reason,
            "message": str(error),
        }
    except Exception as error:  # noqa: BLE001 - 交接不能因为排版问题整体失败
        return {
            "ok": False,
            "source": "script",
            "script_id": script_id,
            "reason": "storyboard_failed",
            "message": f"{type(error).__name__}: {error}",
        }
    payload = dict(result)
    payload["ok"] = True
    payload["script_id"] = script_id
    payload["chain"] = _try_chain_shot(report, directory)
    return payload


def _try_chain_shot(report: Any, directory: Path) -> dict[str, Any]:
    """结论里有产业链结构时，自动生成示意图并插进分镜（开场之后）。

    只在挂到上游/中游/下游的结论**至少覆盖两段**时才插：只讲到一环的稿子，
    画三段会有一段全是"（本环缺证据）"，反而误导观众。
    """
    from backend.app.video.chain import insert_chain_shot, nodes_from_findings

    findings = getattr(report, "findings", None) or []
    stages = nodes_from_findings(
        item for item in findings if isinstance(item, dict)
    )
    filled = [key for key, nodes in stages.items() if nodes]
    if len(filled) < 2:
        return {
            "ok": False,
            "reason": "not_a_chain_story",
            "message": f"结论覆盖的产业链环节只有 {len(filled)} 段（至少 2 段才画示意图）",
        }
    try:
        result = insert_chain_shot(
            directory,
            title=str(getattr(report, "title", "") or "")[:24] or "产业链结构",
            stages=stages,
        )
    except Exception as error:  # noqa: BLE001 - 示意图插不进去不该毁掉整个交接
        return {"ok": False, "reason": "chain_failed", "message": f"{type(error).__name__}: {error}"}
    result["ok"] = True
    return result


def create_video_job(
    report: Any,
    *,
    evidence: Iterable[Any] = (),
    sources: Iterable[Any] = (),
    job_id: str | None = None,
    root: Path | None = None,
    now: dt.datetime | None = None,
    outline: bool = True,
    confirmed: Sequence[Mapping[str, Any]] = (),
    script: Any | None = None,
    script_id: int | None = None,
) -> VideoJobScaffold:
    """把一份报告交接成一个视频任务目录（不生成脚本、不渲染）。

    `confirmed` 是**人工已确认**的结论（来自 `backend/app/research/draft_gate.py`）。
    按用户定的口径：没有确认过的结论就不许成稿——所以这里空着就直接拒绝，
    并且只把确认结论背后的证据交给分镜阶段（未确认的结论照样写进报告副本并标注）。

    `script` 是 `scripts` 表里的那一行（`content.script` 阶段写出来的稿子）。
    给了它，分镜就按**脚本**排（一句一镜、带数字的句子做数据卡），而不是按报告
    数据点套规则模板；没给则退回规则分镜，并在 `outline.source` 里如实标明。
    """
    if not confirmed:
        raise VideoHandoffError(
            "draft_not_confirmed",
            "这份报告还没有人工确认过的结论：先到「语义核查」页逐条确认，再来建视频任务",
        )
    base = root or JOBS_ROOT
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%d")
    identifier = job_id or f"v5_r{report.id}_{stamp}_{_slug(report.title)}"
    identifier = _slug(identifier, limit=64)
    directory = base / identifier
    if directory.exists():
        raise VideoHandoffError("job_exists", f"任务目录已存在：{directory}")

    evidence_rows = list(evidence)
    source_rows = list(sources)

    # 分镜只能吃"确认结论背后的证据"：先按证据 id 圈定，再按来源标签兜底。
    allowed_evidence_ids = {
        int(value)
        for item in confirmed
        for value in (item.get("evidence_ids") or ())
        if isinstance(value, int)
    }
    allowed_source_labels = {
        str(value) for item in confirmed for value in (item.get("source_ids") or ())
    }
    if allowed_evidence_ids:
        draft_evidence = [
            item for item in evidence_rows if getattr(item, "id", None) in allowed_evidence_ids
        ]
    else:
        draft_evidence = []
    if not draft_evidence and allowed_source_labels:
        label_by_db_id = {
            getattr(source, "id", None): f"S{index:02d}"
            for index, source in enumerate(source_rows, start=1)
        }
        draft_evidence = [
            item
            for item in evidence_rows
            if label_by_db_id.get(getattr(item, "source_id", None)) in allowed_source_labels
        ]

    directory.mkdir(parents=True, exist_ok=False)
    for name in JOB_DIRS:
        (directory / name).mkdir(exist_ok=True)

    report_md = _report_markdown(report, confirmed)
    files = [
        _write(directory / "research" / "report.md", report_md),
        _write(
            directory / "research" / "evidence.json",
            json.dumps(
                [
                    {
                        "evidence_id": getattr(item, "id", None),
                        "source_id": getattr(item, "source_id", None),
                        "claim": getattr(item, "claim", ""),
                        "excerpt": (getattr(item, "original_excerpt", "") or "")[:1000],
                        "locator": getattr(item, "quote_locator", None),
                        "verification_status": _value(
                            getattr(item, "verification_status", None)
                        ),
                    }
                    for item in evidence_rows
                ],
                ensure_ascii=False,
                indent=1,
            ),
        ),
        _write(
            directory / "research" / "sources.json",
            json.dumps(
                [
                    {
                        "source_id": getattr(item, "id", None),
                        "title": getattr(item, "title", ""),
                        "url": getattr(item, "url", None),
                        "publisher": getattr(item, "publisher", None),
                        "published_at": str(getattr(item, "published_at", "") or ""),
                        "content_hash": getattr(item, "content_hash", None),
                        "credibility": _value(
                            getattr(item, "credibility_level", None)
                        ),
                    }
                    for item in source_rows
                ],
                ensure_ascii=False,
                indent=1,
            ),
        ),
    ]

    ready, provider_note = _provider_ready()
    if script is not None:
        outline_result = _try_script_storyboard(report, script, directory, script_id)
    elif outline:
        outline_result = _try_outline(report, draft_evidence, source_rows, directory, identifier)
    else:
        outline_result = {"ok": False, "reason": "outline_disabled", "source": "rules"}
    outline_ready = bool(outline_result.get("ok"))
    blocked = not ready and not outline_ready
    if outline_ready:
        blocked_reason = ""
        stage = "storyboard_ready"
        next_stage = "render"
        if outline_result.get("source") == "script":
            next_step = (
                "分镜已按**脚本**排好（storyboard/storyboard.csv，逐镜来源见 "
                "storyboard/FROM_SCRIPT.md）：每句一拍、带数字的句子做成了数据卡。"
                "先人工看一遍分镜，然后跑 python -m backend.scripts.render_job "
                f"--job {identifier} --narration 出片。"
            )
        else:
            next_step = (
                "分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。"
                "先人工看一遍分镜，然后跑 python -m backend.scripts.render_job "
                f"--job {identifier} --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。"
            )
    elif ready:
        blocked_reason = ""
        stage = "research_imported"
        next_stage = "content_plan"
        next_step = (
            "按 research/report.md 写 script/script.md 与 storyboard/storyboard.csv，"
            "素材登记进 assets_manifest.csv 后走 Phase 2 的渲染链"
        )
    else:
        # 分镜没排出来时，先说清"是资料不够"还是"是没模型"——两者的处理办法不一样，
        # 混成一个 model_not_configured 会让人以为配上密钥就能出片。
        data_reason = outline_result.get("reason") or ""
        if data_reason in {"insufficient_sources", "no_data_points"}:
            blocked_reason = data_reason
        else:
            blocked_reason = provider_note
        stage = "research_imported"
        next_stage = "content_plan"
        if blocked_reason in {"insufficient_sources", "no_data_points"}:
            next_step = (
                "证据链里的可用来源太少，规则分镜排不出来；先补抓资料（另一个来源、"
                "或同一来源的更完整原文），再重新交接。不硬凑、不重复引用同一来源充数"
            )
        else:
            next_step = (
                "先配好模型（.env 的 API Key + config/models.yaml 的 enabled 与价目），"
                "再让脚本阶段生成 script/script.md；本轮不生成任何脚本，避免用模板文字冒充"
            )

    manifest = {
        "job_id": identifier,
        "created_at": (now or dt.datetime.now(dt.UTC)).isoformat(),
        "stage": stage,
        "next_stage": next_stage,
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "model_provider": provider_note,
        "outline": outline_result,
        "report": {
            "report_id": report.id,
            "research_task_id": getattr(report, "research_task_id", None),
            "title": report.title,
            "verification_status": _value(
                getattr(report, "verification_status", None)
            ),
            "sha256": _sha256_text(report_md),
        },
        "counts": {
            "evidence": len(evidence_rows),
            "sources": len(source_rows),
            "confirmed_findings": len(confirmed),
            "draft_evidence": len(draft_evidence),
        },
        "draft_gate": {
            "rule": "只有 human_review_status=confirmed 的结论可以进入成稿",
            "confirmed_findings": [item.get("finding_index") for item in confirmed],
            "check_ids": [item.get("check_id") for item in confirmed],
        },
        "narrative_style": "AK（accessible-explainer + investigative-storytelling + ak-signature）",
        "not_done": [
            "script/script.md（需要模型或人工撰写；规则分镜里的口播稿是原文摘引，不是成稿）"
            if not outline_ready
            else "script/script.md（分镜已就位，但正式脚本仍需模型或人工）",
            "assets_manifest.csv（素材必须先登记授权；纯色底镜头不需要）",
            "audio/ 与 output/（跑 render_job 才会生成）",
        ],
    }
    files.append(
        _write(
            directory / "job.json",
            json.dumps(manifest, ensure_ascii=False, indent=1),
        )
    )
    files.append(
        _write(
            directory / "HANDOFF.md",
            "\n".join(
                [
                    f"# 视频任务交接：{report.title}",
                    "",
                    "## 这个目录是什么",
                    "",
                    "从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，",
                    "脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。",
                    "",
                    "## 已经就位",
                    "",
                    f"- `research/report.md`（sha256 `{manifest['report']['sha256']}`）",
                    f"- `research/evidence.json`（{len(evidence_rows)} 条证据）",
                    f"- `research/sources.json`（{len(source_rows)} 个来源）",
                    *(
                        [
                            f"- `storyboard/storyboard.csv`（{outline_result.get('shot_count', 0)} 镜，"
                            f"{outline_result.get('total_seconds', 0)} 秒）",
                            "- `storyboard/OUTLINE.md`（逐镜来源，说明它是规则草稿）",
                        ]
                        if outline_ready
                        else [
                            "- 分镜：**没有排出来**"
                            f"（原因 `{outline_result.get('reason')}`："
                            f"{outline_result.get('message', '')}）"
                        ]
                    ),
                    "",
                    "## 还缺什么",
                    "",
                    *[f"- {item}" for item in manifest["not_done"]],
                    "",
                    "## 当前状态",
                    "",
                    f"- stage：`{manifest['stage']}` → 下一步 `{manifest['next_stage']}`",
                    f"- blocked：`{blocked}`"
                    + (f"（原因 `{blocked_reason}`）" if blocked else ""),
                    f"- 模型：`{provider_note}`",
                    "",
                    "## 下一步",
                    "",
                    next_step,
                    "",
                ]
            ),
        )
    )

    return VideoJobScaffold(
        job_id=identifier,
        path=str(directory),
        stage=stage,
        next_stage=next_stage,
        blocked=blocked,
        blocked_reason=blocked_reason,
        next_step=next_step,
        report_id=int(report.id),
        evidence_count=len(evidence_rows),
        source_count=len(source_rows),
        files=tuple(files),
        outline=dict(outline_result),
    )


def _load_job_manifest(job_json: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(job_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def jobs_using_checks(
    check_ids: Iterable[int], *, root: Path | None = None
) -> list[Path]:
    """找出 `job.json` 里 `draft_gate.check_ids` 命中给定核查记录的任务目录。"""

    wanted = {int(item) for item in check_ids if item is not None}
    if not wanted:
        return []
    base = root or JOBS_ROOT
    if not base.is_dir():
        return []
    hits: list[Path] = []
    for job_json in sorted(base.glob("*/job.json")):
        payload = _load_job_manifest(job_json)
        if payload is None:
            continue
        gate = payload.get("draft_gate")
        if not isinstance(gate, dict):
            continue
        ids = {
            int(item)
            for item in (gate.get("check_ids") or [])
            if isinstance(item, (int, str)) and str(item).isdigit()
        }
        if ids & wanted:
            hits.append(job_json.parent)
    return hits


def invalidate_jobs(
    check_ids: Iterable[int],
    *,
    reason: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """人工撤回确认后，把用到这些核查结果的视频任务标记为失效。

    只在任务目录里写标记（`job.json` 的 `draft_gate.invalidated_*` + `INVALIDATED.md`），
    **不删除任何产物**：片子还在，但它已经不能当作"经人工确认的成稿"使用。
    """

    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    affected: list[dict[str, Any]] = []
    for directory in jobs_using_checks(check_ids, root=root):
        job_json = directory / "job.json"
        payload = _load_job_manifest(job_json)
        if payload is None:  # pragma: no cover - 上面刚读过
            continue
        gate = payload.setdefault("draft_gate", {})
        hit_ids = sorted(
            {
                int(item)
                for item in (gate.get("check_ids") or [])
                if str(item).isdigit() and int(item) in {int(x) for x in check_ids}
            }
        )
        gate["invalidated_at"] = stamp
        gate["invalidated_reason"] = reason
        gate["invalidated_check_ids"] = hit_ids
        gate["usable"] = False
        job_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (directory / INVALIDATION_FILE).write_text(
            "\n".join(
                [
                    "# 这个视频任务已经失效",
                    "",
                    f"- 时间：{stamp}",
                    f"- 原因：{reason}",
                    f"- 涉及的核查记录：{'、'.join(str(item) for item in hit_ids) or '无'}",
                    "",
                    "人工把对应结论的确认状态改回去了（rejected / needs_more_evidence / pending），",
                    "所以这条片子不能再当作「经人工确认的成稿」使用。",
                    "产物文件没有被删除：要恢复使用，先回到「语义核查」页重新确认结论，",
                    "再从报告重新开一个视频任务。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        affected.append(
            {
                "job_id": directory.name,
                "path": str(directory),
                "check_ids": hit_ids,
                "invalidated_at": stamp,
            }
        )
    return affected


__all__ = [
    "INVALIDATION_FILE",
    "JOB_DIRS",
    "JOBS_ROOT",
    "VideoHandoffError",
    "VideoJobScaffold",
    "create_video_job",
    "invalidate_jobs",
    "jobs_using_checks",
]
