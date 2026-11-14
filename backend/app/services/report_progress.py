"""一份报告往下走到哪一步：脚本有没有、视频任务建了几个、最近那条成片还能不能发。

为什么要单独一层：报告列表页原本只显示"核验状态 + 成稿闸门"，
用户看不出这份报告是不是已经生成过脚本、是不是已经建过视频任务。
于是出现两种真实浪费：

1. 明明脚本已经写好（`scripts` 表里有 v2），又去点一次「建视频任务」，
   拿到却是按规则排的分镜——因为"有没有脚本"这件事没人替你看；
2. 或者反过来，片子早就渲好了、已经排进日历，却还在想"这份报告下一步干什么"。

判定只读、只讲事实，不替人做决定：
- 脚本数来自 `scripts` 表（`research_report_id` 关联）；
- 视频任务来自磁盘 `content/jobs/*/job.json` 的 `report.report_id`
  （目录在、`job.json` 不在的历史任务不会凭空出现，这里也如实忽略）；
- 能不能发沿用 `backend/app/services/calendar_readiness.py` 那套口径，
  同一个问题绝不出现两个答案。

整页只做一次脚本分组查询 + 一次目录扫描，不做 N+1。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import PROJECT_ROOT
from backend.app.models.script import Script
from backend.app.schemas.report_progress import JobProgressOut, ReportProgressOut
from backend.app.services.calendar_readiness import job_readiness

logger = logging.getLogger(__name__)

DEFAULT_JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"


def _script_facts(session: Session, report_ids: list[int]) -> dict[int, dict[str, Any]]:
    """一次分组查询取回每份报告的脚本数与最新版本。"""

    if not report_ids:
        return {}
    rows = session.execute(
        select(
            Script.research_report_id,
            func.count(Script.id),
            func.max(Script.version),
            func.max(Script.id),
        )
        .where(Script.research_report_id.in_(report_ids))
        .group_by(Script.research_report_id)
    ).all()
    facts: dict[int, dict[str, Any]] = {}
    for report_id, count, version, script_id in rows:
        if report_id is None:
            continue
        facts[int(report_id)] = {
            "script_count": int(count or 0),
            "latest_script_version": int(version) if version is not None else None,
            "latest_script_id": int(script_id) if script_id is not None else None,
        }
    return facts


def _job_index(jobs_root: Path | None = None) -> dict[int, list[dict[str, Any]]]:
    """扫一遍任务目录，按 `job.json` 里的 `report.report_id` 归堆。

    读坏的 `job.json` 记一条 warning 继续，不让一个坏文件把整页拖垮。
    """

    root = Path(jobs_root) if jobs_root is not None else DEFAULT_JOBS_ROOT
    index: dict[int, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return index
    for job_dir in sorted(root.iterdir()):
        job_file = job_dir / "job.json"
        if not job_dir.is_dir() or not job_file.is_file():
            continue
        try:
            payload = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("视频任务 %s 的 job.json 读不出来：%s", job_dir.name, error)
            continue
        report = payload.get("report") or {}
        report_id = report.get("report_id")
        if not isinstance(report_id, int):
            continue
        index.setdefault(report_id, []).append(
            {"job_id": job_dir.name, "title": str(report.get("title") or "")}
        )
    return index


def _next_step(
    *, script_count: int, latest_version: int | None, jobs: list[JobProgressOut]
) -> str:
    """按事实给一句"下一步"，不说客套话。"""

    usable = [job for job in jobs if job.usable]
    if usable:
        latest = usable[-1]
        return f"已有能发的成片（{latest.job_id}）：去「视频预览」看片，然后排进内容日历。"
    if jobs:
        broken = jobs[-1]
        reason = broken.readiness_label or "片子不可用"
        return (
            f"建过 {len(jobs)} 个视频任务，但都不能发（最近一条：{reason}）："
            "先回「语义核查」确认结论，再重新建任务或重渲。"
        )
    if script_count:
        version = f" v{latest_version}" if latest_version else ""
        return f"脚本已就绪{version}：可以「建视频任务」，分镜会按这份脚本排，不用再重写。"
    return "还没有脚本：先让 content.script 跑出脚本（或先确认语义核查），再建视频任务。"


def progress_map(
    session: Session,
    report_ids: list[int],
    *,
    jobs_root: Path | None = None,
) -> dict[int, ReportProgressOut]:
    """一整页报告的进度：一次脚本分组查询 + 一次目录扫描。"""

    wanted = [int(report_id) for report_id in report_ids]
    # 目录根只解析一次，并且**显式传给** job_readiness：
    # 否则 monkeypatch 这一层的 DEFAULT_JOBS_ROOT 不生效，判定会跑去扫仓库里的
    # content/jobs，得出"任务目录不存在"的错答案。
    root = Path(jobs_root) if jobs_root is not None else DEFAULT_JOBS_ROOT
    index = _job_index(root)
    facts = _script_facts(session, wanted)
    result: dict[int, ReportProgressOut] = {}
    for report_id in wanted:
        facts_of = facts.get(report_id, {})
        jobs: list[JobProgressOut] = []
        for entry in index.get(report_id, []):
            readiness = job_readiness(entry["job_id"], jobs_root=root)
            film = readiness.get("film") or {}
            jobs.append(
                JobProgressOut(
                    job_id=entry["job_id"],
                    title=entry["title"],
                    readiness_status=readiness.get("status"),
                    readiness_label=readiness.get("label"),
                    usable=bool(readiness.get("usable")),
                    film_path=str(film.get("relative_path") or ""),
                    next_step=str(readiness.get("next_step") or ""),
                )
            )
        script_count = int(facts_of.get("script_count") or 0)
        latest_version = facts_of.get("latest_script_version")
        result[report_id] = ReportProgressOut(
            report_id=report_id,
            script_count=script_count,
            latest_script_version=latest_version,
            latest_script_id=facts_of.get("latest_script_id"),
            job_count=len(jobs),
            jobs=jobs,
            next_step=_next_step(
                script_count=script_count, latest_version=latest_version, jobs=jobs
            ),
        )
    return result


def progress_of(
    session: Session,
    report_id: int,
    *,
    jobs_root: Path | None = None,
) -> ReportProgressOut:
    """单份报告的进度（复用 `progress_map`，一行判定逻辑只写一次）。"""

    return progress_map(session, [report_id], jobs_root=jobs_root)[int(report_id)]


__all__ = [
    "DEFAULT_JOBS_ROOT",
    "progress_map",
    "progress_of",
]
