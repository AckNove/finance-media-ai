"""从研究报告建立视频任务目录（研究 → 视频的交接点）。

这个模块存在的理由，是让"从报告建视频任务"变成**可追溯的文件交接**，而不是一句
"点一下就开始生成"。具体做法：

* 把报告正文、证据行、来源清单一并写进任务目录的 `research/`，每份都带 SHA-256。
  这样后面写脚本的人（或 Agent）手上有的是**带出处的材料**，而不是一段凭空的摘要。
* 脚本与分镜目录**留空**。空是诚实的：没有模型（或有模型但价目未配）时，
  我们无法生成符合 AK 风格的脚本，更不能塞一段模板文字冒充。
* `job.json` 明写 `stage` / `next_stage` / `blocked` / `blocked_reason`，
  以及继续推进需要什么。人打开目录就知道卡在哪。
* 同一个报告重复建任务不会覆盖已有目录（`job_id` 已存在则报错），避免把人工已经
  改了的分镜覆盖掉。

不做什么：不抓新资料、不调模型、不生成分镜、不渲染。这些都是后续阶段的事，而且
必须由人看过报告之后再决定。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.app.core.config import PROJECT_ROOT

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
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


def _report_markdown(report: Any) -> str:
    lines = [
        f"# {report.title}",
        "",
        f"- 报告 ID：{report.id}",
        f"- 研究任务 ID：{getattr(report, 'research_task_id', None)}",
        f"- 核查状态：{_value(report.verification_status)}",
        f"- 生成方式：{report.author_agent or '规则汇编'}",
        "",
        "## 摘要",
        "",
        report.summary or "（报告没有摘要）",
        "",
        "## 要点",
        "",
    ]
    findings = report.findings or []
    if not findings:
        lines.append("（报告没有列要点——写脚本前必须先补研究）")
    for item in findings:
        if isinstance(item, dict):
            text = item.get("claim") or item.get("text") or json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
        lines.append(f"- {text}")
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


def create_video_job(
    report: Any,
    *,
    evidence: Iterable[Any] = (),
    sources: Iterable[Any] = (),
    job_id: str | None = None,
    root: Path | None = None,
    now: dt.datetime | None = None,
) -> VideoJobScaffold:
    """把一份报告交接成一个视频任务目录（不生成脚本、不渲染）。"""
    base = root or JOBS_ROOT
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%d")
    identifier = job_id or f"v5_r{report.id}_{stamp}_{_slug(report.title)}"
    identifier = _slug(identifier, limit=64)
    directory = base / identifier
    if directory.exists():
        raise VideoHandoffError("job_exists", f"任务目录已存在：{directory}")

    evidence_rows = list(evidence)
    source_rows = list(sources)

    directory.mkdir(parents=True, exist_ok=False)
    for name in JOB_DIRS:
        (directory / name).mkdir(exist_ok=True)

    report_md = _report_markdown(report)
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
    blocked = not ready
    blocked_reason = "" if ready else provider_note
    next_step = (
        "按 research/report.md 写 script/script.md 与 storyboard/storyboard.csv，"
        "素材登记进 assets_manifest.csv 后走 Phase 2 的渲染链"
        if ready
        else "先配好模型（.env 的 API Key + config/models.yaml 的 enabled 与价目），"
        "再让脚本阶段生成 script/script.md；本轮不生成任何脚本，避免用模板文字冒充"
    )

    manifest = {
        "job_id": identifier,
        "created_at": (now or dt.datetime.now(dt.UTC)).isoformat(),
        "stage": "research_imported",
        "next_stage": "content_plan",
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "model_provider": provider_note,
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
        },
        "narrative_style": "AK（accessible-explainer + investigative-storytelling + ak-signature）",
        "not_done": [
            "script/script.md（需要模型或人工撰写）",
            "storyboard/storyboard.csv（禁止未经脚本直接生成）",
            "assets_manifest.csv（素材必须先登记授权）",
            "audio/ 与 output/（渲染属后续阶段）",
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
        stage=manifest["stage"],
        next_stage=manifest["next_stage"],
        blocked=blocked,
        blocked_reason=blocked_reason,
        next_step=next_step,
        report_id=int(report.id),
        evidence_count=len(evidence_rows),
        source_count=len(source_rows),
        files=tuple(files),
    )


__all__ = [
    "JOB_DIRS",
    "JOBS_ROOT",
    "VideoHandoffError",
    "VideoJobScaffold",
    "create_video_job",
]
