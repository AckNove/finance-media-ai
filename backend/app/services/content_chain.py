"""一条报告从研究到发布，中间到底走到哪一步了。

为什么单独一个模块：这条链上有七段，分别存在七张表/七个目录里——

1. 研究任务（`research_tasks`）
2. 报告本身（`research_reports`）
3. 语义核查 + 人工确认（`finding_checks`）
4. 脚本（`scripts`）
5. 视频任务（`content/jobs/<job_id>/job.json`）
6. 排期（`content_calendar`）
7. 导出包（同表的 `export_path`）

页面上它们散在「研究任务」「语义核查」「脚本编辑」「视频预览」「内容日历」五个页面里，
人想问的其实只有一句：**这份报告现在卡在哪、下一步该点哪儿**。

三条口径在这里写死，别在页面里再算一遍：

- 状态只有四种：`done`（做完）、`current`（现在该做这个）、`blocked`（等人工或有硬问题）、`missing`（还没有）。
- `current` 只给**第一段没做完的**，不给人一排"都可以点"（那是没结论）。
- 每段的 `detail` 只写事实（几条、几版、什么状态），`next_step` 才写动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.execution import ResearchTask
from backend.app.models.operations import ContentCalendar
from backend.app.models.research import ResearchReport
from backend.app.research.draft_gate import readiness_of
from backend.app.services.calendar_readiness import DEFAULT_JOBS_ROOT, job_readiness
from backend.app.services.report_progress import _job_index
from backend.app.services.script_service import ScriptService

STAGE_KEYS = ("research", "report", "checks", "script", "film", "schedule", "export")
STAGE_LABELS = {
    "research": "研究任务",
    "report": "研究报告",
    "checks": "语义核查与人工确认",
    "script": "脚本",
    "film": "视频成片",
    "schedule": "发布排期",
    "export": "导出交付包",
}
STATUS_ORDER = {"blocked": 0, "current": 1, "missing": 2, "done": 3}


class ChainError(RuntimeError):
    """这条链问不出来（比如报告不存在）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ChainStage:
    key: str
    label: str
    status: str
    detail: str
    next_step: str = ""
    link: str = ""

    @property
    def done(self) -> bool:
        return self.status == "done"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "next_step": self.next_step,
            "link": self.link,
        }


@dataclass(frozen=True)
class ContentChain:
    report_id: int
    title: str
    stages: tuple[ChainStage, ...] = field(default_factory=tuple)
    current: str = ""
    finished: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "title": self.title,
            "stages": [stage.as_dict() for stage in self.stages],
            "current": self.current,
            "finished": self.finished,
        }


def _status_of(task: ResearchTask | None) -> str:
    if task is None:
        return ""
    return str(getattr(task.status, "value", task.status) or "")


def _research_stage(task: ResearchTask | None) -> ChainStage:
    label = STAGE_LABELS["research"]
    if task is None:
        # 不算"卡住"：这类报告不是研究流水线产出的（早期数据、手工建的），
        # 后面几步照样能走。写成 done 而不是 missing，是为了别把 current 指到一段
        # 根本没有前置要求的地方去。
        return ChainStage(
            key="research",
            label=label,
            status="done",
            detail="没有关联研究任务（这份报告不是研究流水线产出的，不影响后面几步）",
        )
    status = _status_of(task)
    link = f"#/tasks/{task.id}"
    if status in ("completed",):
        return ChainStage(
            key="research",
            label=label,
            status="done",
            detail=f"任务 #{task.id} 已跑完",
            link=link,
        )
    if status in ("failed", "cancelled"):
        return ChainStage(
            key="research",
            label=label,
            status="blocked",
            detail=f"任务 #{task.id} 是 {status}：{task.failure_reason or '没有记录原因'}",
            next_step="先看任务详情里哪一格失败，处理完再决定是重跑还是重开任务",
            link=link,
        )
    if status == "paused":
        return ChainStage(
            key="research",
            label=label,
            status="blocked",
            detail=f"任务 #{task.id} 停在人工闸门，等人放行",
            next_step="到任务详情点「推进执行」；卡在闸门上时先看「这一步产出了什么」",
            link=link,
        )
    return ChainStage(
        key="research",
        label=label,
        status="current",
        detail=f"任务 #{task.id} 还在跑（{status}）",
        next_step="到任务详情看进度，必要时点「推进执行」",
        link=link,
    )


def _report_stage(report: ResearchReport) -> ChainStage:
    verified = str(getattr(report.verification_status, "value", report.verification_status) or "")
    detail = f"报告 #{report.id}（v{report.version}，核查状态 {verified or '未记录'}）"
    return ChainStage(
        key="report",
        label=STAGE_LABELS["report"],
        status="done",
        detail=detail,
        link=f"#/reports?report={report.id}",
    )


def _checks_stage(readiness: Any) -> ChainStage:
    label = STAGE_LABELS["checks"]
    total = int(getattr(readiness, "total_checks", 0) or 0)
    confirmed = int(getattr(readiness, "confirmed", 0) or 0)
    if total == 0:
        return ChainStage(
            key="checks",
            label=label,
            status="missing",
            detail="这条报告还没有语义核查记录",
            next_step="到「语义核查」页点「加载核查结果」，让模型逐条判定",
            link="#/checks",
        )
    if confirmed == 0:
        return ChainStage(
            key="checks",
            label=label,
            status="blocked",
            detail=f"核查 {total} 条，一条都没人工确认——脚本阶段会直接拒绝",
            next_step="到「语义核查」页逐条看，确认那些你亲自核对过原文的结论",
            link="#/checks",
        )
    if confirmed < total:
        return ChainStage(
            key="checks",
            label=label,
            status="current",
            detail=f"核查 {total} 条，已人工确认 {confirmed} 条",
            next_step="可以先建脚本（只用确认过的那几条）；想更完整就继续确认",
            link="#/checks",
        )
    return ChainStage(
        key="checks",
        label=label,
        status="done",
        detail=f"核查 {total} 条，全部人工确认",
        link="#/checks",
    )


def _script_stage(script: Any | None) -> ChainStage:
    label = STAGE_LABELS["script"]
    if script is None:
        return ChainStage(
            key="script",
            label=label,
            status="missing",
            detail="还没有脚本",
            next_step="核查确认之后，用 research_to_script 产线跑 content.script，或在报告页直接建视频任务",
            link="#/scripts",
        )
    status = str(getattr(script.status, "value", script.status) or "")
    detail = f"脚本 #{script.id}（v{script.version}，状态 {status}）"
    if status in ("approved", "storyboard_ready", "produced", "published"):
        return ChainStage(
            key="script",
            label=label,
            status="done",
            detail=detail,
            link="#/scripts",
        )
    if status in ("draft", "in_review"):
        return ChainStage(
            key="script",
            label=label,
            status="current",
            detail=detail,
            next_step="到「脚本编辑」页过一遍再送审（批准后才能直接开片子）",
            link="#/scripts",
        )
    return ChainStage(
        key="script",
        label=label,
        status="blocked",
        detail=f"{detail}——这一版被驳回了",
        next_step="看驳回理由改一稿，或者重跑 content.script",
        link="#/scripts",
    )


def _same_report(entry: dict[str, Any], title: str) -> bool:
    """任务目录里的报告标题，和手上这份报告对得上吗？

    `content/jobs/<job_id>/job.json` 里记的是"跑这条片子时那个库"的 report_id，
    而 id 只在一个库内唯一——演示库的 #1 与工作库的 #1 会互相冒充。
    对不上标题的一律不算这条链的成片（宁可不认，也不能拿别人的片子说"能发"）。
    """

    stored = str(entry.get("title") or "").strip()
    if not stored:
        return True  # 没记标题的老目录：无从判断，按对得上处理（后面还有成稿闸门把关）
    return stored == title.strip()


def _film_stage(jobs: list[dict[str, Any]], *, title: str = "") -> ChainStage:
    label = STAGE_LABELS["film"]
    mine = [entry for entry in jobs if _same_report(entry, title)]
    foreign = [entry for entry in jobs if not _same_report(entry, title)]
    note = f"（另有 {len(foreign)} 个同号任务是别的库的，标题对不上，不计入）" if foreign else ""
    if not mine:
        return ChainStage(
            key="film",
            label=label,
            status="missing",
            detail=f"还没有视频任务{note}",
            next_step="在报告页点「建视频任务」；脚本已批准的话会用脚本排分镜",
            link="#/video",
        )
    usable = [entry for entry in mine if entry.get("usable")]
    if usable:
        entry = usable[0]
        return ChainStage(
            key="film",
            label=label,
            status="done",
            detail=f"{len(usable)} 个能发的成片（如 {entry['job_id']}）{note}",
            link=f"#/video?job={entry['job_id']}",
        )
    entry = mine[0]
    status = str(entry.get("readiness_status") or "")
    reason = str(entry.get("readiness_label") or "不可用")
    return ChainStage(
        key="film",
        label=label,
        status="blocked",
        detail=f"{len(mine)} 个视频任务都不能发（最近一条 {entry['job_id']}：{reason}）{note}",
        next_step=str(entry.get("next_step") or "回「语义核查」确认结论后重新建任务或重渲"),
        link=f"#/video?job={entry['job_id']}" if status else "#/video",
    )


def _schedule_stage(entries: list[ContentCalendar]) -> ChainStage:
    label = STAGE_LABELS["schedule"]
    if not entries:
        return ChainStage(
            key="schedule",
            label=label,
            status="missing",
            detail="还没排进内容日历",
            next_step="在「视频预览」页点「排进日历」，或在「内容日历」页手建一条",
            link="#/operations",
        )
    published = [entry for entry in entries if str(getattr(entry.publish_state, "value", entry.publish_state)) == "published"]
    if published:
        return ChainStage(
            key="schedule",
            label=label,
            status="done",
            detail=f"{len(entries)} 条排期，其中 {len(published)} 条已发布",
            link="#/operations",
        )
    return ChainStage(
        key="schedule",
        label=label,
        status="current",
        detail=f"{len(entries)} 条排期，都还没标已发布",
        next_step="发出去之后回「内容日历」把链接填上（系统不会自己发布）",
        link="#/operations",
    )


def _export_stage(entries: list[ContentCalendar]) -> ChainStage:
    label = STAGE_LABELS["export"]
    exported = [entry for entry in entries if (entry.export_path or "").strip()]
    if not entries:
        return ChainStage(
            key="export",
            label=label,
            status="missing",
            detail="还没排期，所以也谈不上导出",
            next_step="先排期，再在「内容日历」点「导出」",
            link="#/operations",
        )
    if not exported:
        return ChainStage(
            key="export",
            label=label,
            status="current",
            detail="有排期，但还没导出过交付包",
            next_step="在「内容日历」点「导出」：会拿到文案 + 标签 + 片子 + 发布清单",
            link="#/operations",
        )
    entry = exported[0]
    return ChainStage(
        key="export",
        label=label,
        status="done",
        detail=f"{len(exported)} 条排期有导出包（如 {Path(entry.export_path).name}）",
        link="#/operations",
    )


def chain_of(
    session: Session,
    report_id: int,
    *,
    jobs_root: Path | None = None,
) -> ContentChain:
    """把一份报告的七段拼成一条链。"""

    report = session.get(ResearchReport, report_id)
    if report is None:
        raise ChainError("unknown_report", f"报告 #{report_id} 不存在")

    task = session.get(ResearchTask, report.research_task_id) if report.research_task_id else None
    readiness = readiness_of(session, report)
    script = ScriptService(session).latest_for_report(report_id)

    root = Path(jobs_root) if jobs_root is not None else DEFAULT_JOBS_ROOT
    index = _job_index(root)
    raw_jobs = index.get(report_id, [])
    jobs: list[dict[str, Any]] = []
    for entry in raw_jobs:
        info = job_readiness(entry["job_id"], jobs_root=root)
        jobs.append({**entry, **info, "job_id": entry["job_id"]})

    job_ids = [entry["job_id"] for entry in jobs]
    statement = select(ContentCalendar).where(ContentCalendar.research_report_id == report_id)
    entries = list(session.execute(statement).scalars())
    if job_ids:
        extra = select(ContentCalendar).where(ContentCalendar.video_job_id.in_(job_ids))
        seen = {entry.id for entry in entries}
        entries.extend(entry for entry in session.execute(extra).scalars() if entry.id not in seen)
    entries.sort(key=lambda entry: entry.id)

    stages = (
        _research_stage(task),
        _report_stage(report),
        _checks_stage(readiness),
        _script_stage(script),
        _film_stage(jobs, title=report.title),
        _schedule_stage(entries),
        _export_stage(entries),
    )

    current = next((stage.key for stage in stages if not stage.done), "")
    return ContentChain(
        report_id=report.id,
        title=report.title,
        stages=stages,
        current=current,
        finished=current == "",
    )
