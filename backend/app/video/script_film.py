"""从一份**已批准的脚本**直接开一条视频任务。

以前只有一条路：研究报告 → 建视频任务。走那条路意味着把研究与核查重走一遍，
哪怕脚本早就写好了、也早就人工批准了。这一版补上"只做片子"的入口：

- **只认已批准的脚本**：`draft` / `in_review` / `rejected` 一律拒绝。脚本没批就出片，
  等于绕过"人工看过"这道关。
- **脚本背后的结论要是仍然成立**：脚本挂在某份报告上时，去查那份报告的人工确认记录
  （`backend/app/research/draft_gate.py`）。确认被撤回了就拒绝——否则会拿一份
  依据已经作废的稿子去做片子。
- **失效链路照样接得上**：`job.json` 的 `draft_gate.check_ids` 记下这份脚本依据的核查
  记录，之后人工撤回确认时 `handoff.invalidate_jobs()` 能把这条任务标成失效。
- **不悄悄换分镜**：脚本排不出分镜（句子太少、没有可用的量）就如实记原因，
  不退化成"按报告数据点套规则模板"的那套。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.core.config import PROJECT_ROOT

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"

#: 允许开片子的脚本状态。写出来而不是"不等于 draft"，是为了以后加状态时被迫想一遍。
ALLOWED_STATUSES = frozenset({"approved", "storyboard_ready", "produced"})

#: 拒绝时给的稳定原因码 → 给人看的一句话。
_REASON_TEXT = {
    "script_not_approved": "脚本还没批准，不能开片子：先走完审核（状态到 approved 再回来）",
    "draft_not_confirmed": "这份脚本背后的结论现在没有人工确认记录，不能拿它去出片",
    "empty_script": "脚本里没有可用的句子，排不出分镜",
    "job_exists": "同名任务目录已经存在，换个 job_id 再来（不覆盖已有产物）",
    "unknown_report": "脚本挂着的报告已经不在了，先确认报告再出片",
}


class ScriptFilmError(RuntimeError):
    """开不了这条片子。`reason` 是稳定码，`message` 是给人看的话。"""

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        self.message = message or _REASON_TEXT.get(reason, reason)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class ScriptFilm:
    """开好（或开了一半）的任务目录。"""

    job_id: str
    path: str
    script_id: int
    report_id: int | None
    stage: str
    next_stage: str
    blocked: bool
    blocked_reason: str
    next_step: str
    shot_count: int = 0
    total_seconds: float = 0.0
    files: tuple[str, ...] = ()
    storyline: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "path": self.path,
            "script_id": self.script_id,
            "report_id": self.report_id,
            "stage": self.stage,
            "next_stage": self.next_stage,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "next_step": self.next_step,
            "shot_count": self.shot_count,
            "total_seconds": self.total_seconds,
            "files": list(self.files),
            "storyline": dict(self.storyline),
        }


def _status_of(script: Any) -> str:
    raw = getattr(script, "status", "")
    return str(getattr(raw, "value", raw) or "").strip().lower()


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _slug(text: str, *, limit: int = 64) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in str(text)
    ).strip("_")
    return (cleaned or "script")[:limit]


def _script_snapshot(script: Any) -> dict[str, Any]:
    """脚本存档：只留"这条片子依据的是哪一版稿子"。"""

    segments = list(getattr(script, "segments", None) or [])
    return {
        "script_id": int(getattr(script, "id", 0) or 0),
        "version": int(getattr(script, "version", 0) or 0),
        "status": _status_of(script),
        "title": str(getattr(script, "title", "") or ""),
        "hook": str(getattr(script, "hook", "") or ""),
        "body": str(getattr(script, "body", "") or ""),
        "segments": [item for item in segments if isinstance(item, dict)],
        "disclaimer": str(getattr(script, "disclaimer", "") or ""),
        "word_count": getattr(script, "word_count", None),
        "estimated_duration": getattr(script, "estimated_duration", None),
        "research_report_id": getattr(script, "research_report_id", None),
    }


def _script_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        f"# {snapshot['title'] or '（无标题）'}",
        "",
        f"- 脚本 #{snapshot['script_id']} 第 {snapshot['version']} 版，状态 `{snapshot['status']}`",
    ]
    if snapshot.get("research_report_id"):
        lines.append(f"- 来源报告 #{snapshot['research_report_id']}")
    lines.append("")
    if snapshot.get("hook"):
        lines.extend(["## 开场", "", snapshot["hook"], ""])
    lines.extend(["## 正文", "", snapshot.get("body") or "（空）", ""])
    if snapshot.get("disclaimer"):
        lines.extend(["## 免责声明", "", snapshot["disclaimer"], ""])
    return "\n".join(lines)


def create_job_from_script(
    session: Any,
    script_id: int,
    *,
    job_id: str | None = None,
    root: Path | None = None,
    now: dt.datetime | None = None,
) -> ScriptFilm:
    """按脚本开一条视频任务目录（只排分镜，不渲染）。"""

    from backend.app.models.script import Script
    from backend.app.research.draft_gate import confirmed_findings, readiness_of
    from backend.app.video.script_board import (
        ScriptStoryboardError,
        script_storyboard,
    )

    script = session.get(Script, int(script_id))
    if script is None:
        raise ScriptFilmError("unknown_script", f"脚本 id={script_id} 不存在")

    status = _status_of(script)
    if status not in ALLOWED_STATUSES:
        raise ScriptFilmError(
            "script_not_approved", f"脚本现在状态是 `{status}`，还不能开片子：{_REASON_TEXT['script_not_approved']}"
        )

    snapshot = _script_snapshot(script)
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%d")
    identifier = _slug(job_id or f"film_s{script.id}_{stamp}_{script.title or ''}")
    base = root or JOBS_ROOT
    directory = base / identifier
    if directory.exists():
        raise ScriptFilmError("job_exists", f"任务目录已存在：{directory}")

    # 脚本挂在报告上时，脚本依据的结论必须仍然成立。
    report = None
    confirmed: list[dict[str, Any]] = []
    report_id = getattr(script, "research_report_id", None)
    if report_id:
        from backend.app.models.research import ResearchReport

        report = session.get(ResearchReport, int(report_id))
        if report is None:
            raise ScriptFilmError("unknown_report", f"脚本挂着的报告 #{report_id} 已经不在库里")
        readiness = readiness_of(session, report)
        if not readiness.allowed:
            raise ScriptFilmError(
                "draft_not_confirmed",
                f"{_REASON_TEXT['draft_not_confirmed']}（报告 #{report.id}："
                f"已确认 {readiness.confirmed}/{readiness.total_checks}）",
            )
        confirmed = confirmed_findings(session, report)

    files: list[str] = []
    files.append(
        _write(
            directory / "script" / "script.json",
            json.dumps(snapshot, ensure_ascii=False, indent=1),
        )
    )
    files.append(_write(directory / "script" / "SCRIPT.md", _script_markdown(snapshot)))

    try:
        outline: dict[str, Any] = dict(script_storyboard(script, job_root=directory))
        outline["ok"] = True
        outline["source"] = "script"
    except ScriptStoryboardError as error:
        outline = {
            "ok": False,
            "source": "script",
            "reason": error.reason,
            "message": str(error),
        }
    except Exception as error:  # noqa: BLE001 - 排版炸了不该吞掉，但要说清是哪一步
        outline = {
            "ok": False,
            "source": "script",
            "reason": "storyboard_failed",
            "message": f"{type(error).__name__}: {error}",
        }

    ok = bool(outline.get("ok"))
    if ok:
        stage, next_stage, blocked_reason = "storyboard_ready", "render", ""
        next_step = (
            "分镜已按脚本排好（storyboard/storyboard.csv，逐镜来源见 "
            "storyboard/FROM_SCRIPT.md）。先人工看一遍分镜，然后跑 "
            f"python -m backend.scripts.render_job --job {identifier} --narration 出片。"
        )
    else:
        stage, next_stage = "script_imported", "storyboard"
        blocked_reason = str(outline.get("reason") or "storyboard_failed")
        next_step = (
            "脚本排不出分镜（原因 "
            f"`{blocked_reason}`：{outline.get('message', '')}）。"
            "回到「脚本编辑」把句子补足或改短，再重新开一条；不会拿规则模板顶替。"
        )

    manifest = {
        "job_id": identifier,
        "created_at": (now or dt.datetime.now(dt.UTC)).isoformat(),
        "source": "script",
        "stage": stage,
        "next_stage": next_stage,
        "blocked": not ok,
        "blocked_reason": blocked_reason,
        "outline": outline,
        "script": snapshot,
        "report": (
            {
                "report_id": int(report.id),
                "research_task_id": getattr(report, "research_task_id", None),
                "title": str(getattr(report, "title", "") or ""),
                "verification_status": str(
                    getattr(getattr(report, "verification_status", ""), "value", "")
                    or getattr(report, "verification_status", "")
                ),
            }
            if report is not None
            else None
        ),
        "draft_gate": {
            "rule": "只有人工确认过的结论可以进成稿；这条片子按已批准的脚本制作",
            "source": "script",
            "script_id": int(script.id),
            "script_status": status,
            "confirmed_findings": [item.get("finding_index") for item in confirmed],
            "check_ids": [
                item.get("check_id") for item in confirmed if item.get("check_id") is not None
            ],
        },
        "not_done": [
            "assets_manifest.csv（数据卡由分镜阶段登记；外部素材必须先登记授权）",
            "audio/ 与 output/（跑 render_job 才会生成）",
            "正式发布（系统不代发）",
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
                    f"# 视频任务交接（按脚本）：{snapshot['title'] or identifier}",
                    "",
                    "## 这个目录是什么",
                    "",
                    f"从**已批准的脚本 # {script.id}（第 {script.version} 版）**开出来的视频任务。",
                    "研究材料没有搬进来——这条片子只依据这份脚本；要看依据，回脚本页或来源报告。",
                    "",
                    "## 已经就位",
                    "",
                    f"- `script/SCRIPT.md`（脚本原文快照）",
                    f"- `script/script.json`（逐句结构，含来源标注）",
                    (
                        f"- `storyboard/storyboard.csv`（{outline.get('shot_count', 0)} 镜，"
                        f"{outline.get('total_seconds', 0)} 秒）"
                        if ok
                        else f"- 分镜：**没有排出来**（原因 `{blocked_reason}`）"
                    ),
                    "",
                    "## 还缺什么",
                    "",
                    *[f"- {item}" for item in manifest["not_done"]],
                    "",
                    "## 下一步",
                    "",
                    next_step,
                    "",
                ]
            ),
        )
    )

    return ScriptFilm(
        job_id=identifier,
        path=str(directory),
        script_id=int(script.id),
        report_id=int(report_id) if report_id else None,
        stage=stage,
        next_stage=next_stage,
        blocked=not ok,
        blocked_reason=blocked_reason,
        next_step=next_step,
        shot_count=int(outline.get("shot_count", 0) or 0),
        total_seconds=float(outline.get("total_seconds", 0) or 0.0),
        files=tuple(files),
        storyline={
            "ok": ok,
            "reason": outline.get("reason", ""),
            "source": "script",
            "shot_count": int(outline.get("shot_count", 0) or 0),
            "total_seconds": float(outline.get("total_seconds", 0) or 0.0),
        },
    )


__all__ = ["ALLOWED_STATUSES", "JOBS_ROOT", "ScriptFilm", "ScriptFilmError", "create_job_from_script"]
