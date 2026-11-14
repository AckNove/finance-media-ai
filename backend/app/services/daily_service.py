"""每日体检的聚合逻辑（只读，不联网、不调模型）。

为什么放在 `services/` 而不是脚本里
----------------------------------
同一份判断要能被两个入口用：CLI（`python -m backend.scripts.daily_check`，运维每天跑）
和工作台首页的卡片（`GET /api/system/daily`，人打开页面就想看）。两个入口各写一遍，
迟早会变成"命令行说 3 条待办、页面说 5 条"这种没人敢信的东西。

所以：**判断只在这里写一次**，脚本和接口都调 `collect_daily()`。

看的是什么
----------
1. 任务：各状态计数、暂停（等人）的任务、窗口内失败的任务；
2. 子任务：窗口内失败的（带原因与尝试次数）；
3. 人工确认：还没人确认的语义核查条数——这是最常见的"卡住"来源，
   `content.script` 会一直等人工把结论确认掉；
4. 视频任务：被撤回确认而失效的（`job.json` 的 `draft_gate.usable` 为假）；
5. 花费：今天 / 窗口内的调用次数、Token、金额（来自 `model_calls` 审计行，不是估算）；
6. 备份：最近一份有多旧；
7. 磁盘：项目所在盘的余量。

诚实边界
--------
* 只读：不写任何表、不改文件、不动 WAL 状态；
* 老库（缺表）不会抛异常：该块标成"查不了"并说明原因，其余照常；
* 这里算的是"系统有没有卡住/花了多少钱"，**不判断内容好坏**。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.core.config import PROJECT_ROOT
from backend.app.models.execution import ResearchSubtask, ResearchTask
from backend.app.models.metering import ModelCall
from backend.app.models.research import FindingCheck

DEFAULT_JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
DEFAULT_BACKUPS = PROJECT_ROOT / "data" / "backups"
BACKUP_WARN_DAYS = 7
DISK_WARN_GB = 5.0
PROTECTED_DB_SUFFIX = "data/finance_media.db"
ITEM_LIMIT = 20


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _naive_utc(moment: datetime) -> datetime:
    """库里存的是 naive UTC，比较前统一。"""

    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _status_key(value: Any) -> str:
    """枚举列取 `.value`，纯字符串原样用（否则打印出 `ResearchTaskStatus.COMPLETED`）。"""

    return str(getattr(value, "value", value))


def task_section(session: Session, *, since: datetime) -> dict[str, Any]:
    rows = session.execute(
        select(ResearchTask.status, func.count()).group_by(ResearchTask.status)
    ).all()
    by_status = {_status_key(status): int(count) for status, count in rows}

    paused = session.execute(
        select(ResearchTask)
        .where(ResearchTask.status == "paused")
        .order_by(ResearchTask.updated_at.desc())
        .limit(ITEM_LIMIT)
    ).scalars()
    paused_items = [
        {
            "task_id": task.id,
            "title": task.title,
            "updated_at": _iso(task.updated_at),
            "failure_reason": task.failure_reason or "",
        }
        for task in paused
    ]

    failed = session.execute(
        select(ResearchTask)
        .where(ResearchTask.status == "failed", ResearchTask.updated_at >= since)
        .order_by(ResearchTask.updated_at.desc())
        .limit(ITEM_LIMIT)
    ).scalars()
    failed_items = [
        {
            "task_id": task.id,
            "title": task.title,
            "updated_at": _iso(task.updated_at),
            "failure_reason": task.failure_reason or "",
        }
        for task in failed
    ]
    return {"by_status": by_status, "paused": paused_items, "failed_in_window": failed_items}


def subtask_section(session: Session, *, since: datetime) -> dict[str, Any]:
    failed = session.execute(
        select(ResearchSubtask)
        .where(ResearchSubtask.status == "failed", ResearchSubtask.updated_at >= since)
        .order_by(ResearchSubtask.updated_at.desc())
        .limit(ITEM_LIMIT)
    ).scalars()
    items = [
        {
            "subtask_id": row.id,
            "task_id": row.research_task_id,
            "task_type": row.task_type,
            "attempts": row.attempts,
            "failure_reason": row.failure_reason or "",
        }
        for row in failed
    ]
    return {"failed_in_window": items, "count": len(items)}


def check_section(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(FindingCheck.human_review_status, func.count()).group_by(
            FindingCheck.human_review_status
        )
    ).all()
    by_status = {_status_key(status): int(count) for status, count in rows}
    pending = by_status.get("pending", 0)
    report_ids = session.execute(
        select(FindingCheck.research_report_id)
        .where(FindingCheck.human_review_status == "pending")
        .distinct()
    ).scalars()
    return {
        "by_status": by_status,
        "pending": pending,
        "pending_reports": sorted({int(value) for value in report_ids if value is not None}),
    }


def job_section(jobs_root: Path) -> dict[str, Any]:
    total = 0
    invalidated: list[dict[str, str]] = []
    if jobs_root.is_dir():
        for job_json in sorted(jobs_root.glob("*/job.json")):
            total += 1
            try:
                payload = json.loads(job_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            gate = payload.get("draft_gate") or {}
            if gate and gate.get("usable") is False:
                invalidated.append(
                    {
                        "job_id": job_json.parent.name,
                        "invalidated_at": str(gate.get("invalidated_at") or ""),
                        "reason": str(gate.get("invalidated_reason") or ""),
                    }
                )
    return {"jobs": total, "invalidated": invalidated, "invalidated_count": len(invalidated)}


def cost_section(session: Session, *, today: datetime, since: datetime) -> dict[str, Any]:
    def _sum(start: datetime) -> dict[str, Any]:
        row = session.execute(
            select(
                func.count(ModelCall.id),
                func.coalesce(func.sum(ModelCall.total_tokens), 0),
                func.coalesce(func.sum(ModelCall.cost_estimate), 0),
            ).where(ModelCall.created_at >= start)
        ).one()
        return {
            "calls": int(row[0] or 0),
            "tokens": int(row[1] or 0),
            "cost_cny": round(float(row[2] or 0), 6),
        }

    return {"today": _sum(today), "window": _sum(since)}


def backup_section(backups: Path, *, now: datetime) -> dict[str, Any]:
    if not backups.is_dir():
        return {"dir": str(backups), "exists": False, "newest": "", "age_days": None, "verified": None}
    files = sorted(backups.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {"dir": str(backups), "exists": True, "newest": "", "age_days": None, "verified": None}
    newest = files[0]
    age = (now - datetime.fromtimestamp(newest.stat().st_mtime, UTC)).total_seconds() / 86400
    # 备份验证的结论由 backend.scripts.backup_verify 写在备份旁边；
    # 这里只读它，免得体检自己再跑一遍完整性检查（那是几十秒的事）。
    verified = None
    report_path = newest.with_suffix(".verify.json")
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            verified = {
                "ok": bool(payload.get("ok")),
                "checked_at": str(payload.get("checked_at") or ""),
                "problems": list(payload.get("problems") or []),
            }
        except (OSError, json.JSONDecodeError):
            verified = {"ok": False, "checked_at": "", "problems": ["验证记录读不出来"]}
    return {
        "dir": str(backups),
        "exists": True,
        "newest": newest.name,
        "age_days": round(age, 1),
        "count": len(files),
        "verified": verified,
    }


def disk_section() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
    except OSError as error:  # pragma: no cover - 只有在盘不可读时才会走到
        return {"ok": False, "error": str(error)}
    return {
        "ok": True,
        "free_gb": round(usage.free / 1024**3, 1),
        "total_gb": round(usage.total / 1024**3, 1),
    }


def _tolerant(name: str, fn, *args, register: dict[str, str], **kwargs):
    """查不了就记一笔，不让整份体检崩掉。

    真实开发库目前停在 `de8148733a9b`（只有 19 张表），`research_tasks` 这些表根本不存在；
    诊断应该如实说"这块查不了"，而不是抛 `OperationalError`。
    """

    try:
        return fn(*args, **kwargs)
    except OperationalError as error:  # pragma: no cover - 只在库结构落后时触发
        register[name] = str(error.orig or error).splitlines()[0][:200]
        return {"unavailable": True, "reason": register[name]}


def attention_items(report: dict[str, Any]) -> list[str]:
    """把"需要人处理"的事项按重要性排出来（空列表＝今天干净）。"""

    items: list[str] = []
    unavailable = [name for name, value in report.get("sections_unavailable", {}).items() if value]
    if unavailable:
        items.append(
            f"这几块查不了：{', '.join(unavailable)}（这个库还没迁移到当前版本，"
            "先跑 python -m alembic -x db_url=… upgrade head；只读诊断不会替你改它）"
        )
    if report["tasks"].get("failed_in_window"):
        items.append(f"窗口内有 {len(report['tasks']['failed_in_window'])} 个任务失败")
    if report["subtasks"].get("count"):
        items.append(f"窗口内有 {report['subtasks']['count']} 个子任务失败")
    if report["checks"].get("pending"):
        items.append(
            f"{report['checks']['pending']} 条语义核查还没人工确认"
            f"（报告 {report['checks']['pending_reports']}）——content.script 会一直等着"
        )
    if report["jobs"].get("invalidated_count"):
        items.append(f"{report['jobs']['invalidated_count']} 个视频任务的确认被撤回过（已标记失效）")
    age = report["backup"].get("age_days")
    if age is None:
        items.append("data/backups 里还没有任何备份")
    elif age > BACKUP_WARN_DAYS:
        items.append(f"最近一份备份已经 {age} 天（建议 python -m backend.scripts.backup_db）")
    verified = report["backup"].get("verified")
    if age is not None and verified is None:
        items.append(
            "最近一份备份还没验证过能不能恢复"
            "（python -m backend.scripts.backup_verify：只读地打开它、跑完整性检查、和当前库比表与行数）"
        )
    elif verified is not None and not verified.get("ok"):
        reason = "；".join(verified.get("problems") or []) or "原因没记下来"
        items.append(f"最近一份备份验证不通过：{reason}")
    if report["disk"].get("ok") and report["disk"]["free_gb"] < DISK_WARN_GB:
        items.append(f"磁盘只剩 {report['disk']['free_gb']} GB")
    if report["tasks"].get("by_status", {}).get("paused"):
        items.append(f"{report['tasks']['by_status']['paused']} 个任务暂停在等人放行")
    return items


def collect_daily(
    session: Session,
    *,
    jobs_root: Path | str = DEFAULT_JOBS_ROOT,
    backups: Path | str = DEFAULT_BACKUPS,
    days: int = 1,
    now: datetime | None = None,
    database_url: str = "",
) -> dict[str, Any]:
    """在给定会话上做一次只读汇总（CLI 与接口共用这一份判断）。"""

    moment = now or datetime.now(UTC)
    window = max(1, days)
    since = _naive_utc(moment - timedelta(days=window))
    today = _naive_utc(moment.replace(hour=0, minute=0, second=0, microsecond=0))

    unavailable: dict[str, str] = {}
    tasks = _tolerant("任务", task_section, session, since=since, register=unavailable)
    subtasks = _tolerant("子任务", subtask_section, session, since=since, register=unavailable)
    checks = _tolerant("语义核查", check_section, session, register=unavailable)
    cost = _tolerant(
        "花费", cost_section, session, today=today, since=since, register=unavailable
    )

    report: dict[str, Any] = {
        "generated_at": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "database_url": database_url,
        "protected_database": str(database_url).replace("\\", "/").endswith(PROTECTED_DB_SUFFIX),
        "window_days": window,
        "tasks": tasks,
        "subtasks": subtasks,
        "checks": checks,
        "jobs": job_section(Path(jobs_root)),
        "cost": cost,
        "backup": backup_section(Path(backups), now=moment),
        "disk": disk_section(),
        "sections_unavailable": unavailable,
    }
    items = attention_items(report)
    report["attention"] = items
    report["ok"] = not items
    return report


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"体检时间：{report['generated_at']}（UTC）")
    target = "真实开发库（只读）" if report["protected_database"] else report["database_url"]
    lines.append(f"数据库：{target or '（未指定）'}")
    lines.append("")

    tasks = report["tasks"]
    counts = ", ".join(f"{key} {value}" for key, value in sorted(tasks.get("by_status", {}).items()))
    lines.append(f"任务：{counts or '（没有任何任务）'}")
    for item in tasks.get("paused", []):
        reason = f"｜{item['failure_reason']}" if item["failure_reason"] else ""
        lines.append(f"  · 暂停 #{item['task_id']} {item['title']}（{item['updated_at']}）{reason}")
    for item in tasks.get("failed_in_window", []):
        lines.append(
            f"  · 失败 #{item['task_id']} {item['title']}（{item['updated_at']}）"
            f"｜{item['failure_reason']}"
        )

    lines.append("")
    subtasks = report["subtasks"]
    lines.append(f"子任务失败：{subtasks.get('count', 0)} 条（窗口内）")
    for item in subtasks.get("failed_in_window", []):
        lines.append(
            f"  · #{item['subtask_id']} {item['task_type']} 试了 {item['attempts']} 次"
            f"｜{item['failure_reason']}"
        )

    lines.append("")
    checks = report["checks"]
    pending = checks.get("pending", 0)
    lines.append(
        f"语义核查：待人工确认 {pending} 条"
        + (f"（报告 {checks['pending_reports']}）" if checks.get("pending_reports") else "")
    )
    if checks.get("by_status"):
        detail = ", ".join(f"{key} {value}" for key, value in sorted(checks["by_status"].items()))
        lines.append(f"  · 明细：{detail}")

    lines.append("")
    jobs = report["jobs"]
    lines.append(f"视频任务：{jobs['jobs']} 个，其中失效 {jobs['invalidated_count']} 个")
    for item in jobs["invalidated"]:
        lines.append(f"  · {item['job_id']}（{item['invalidated_at']}）｜{item['reason']}")

    lines.append("")
    cost = report["cost"]
    if cost.get("unavailable"):
        lines.append("今天花费：查不了（这个库没有 model_calls 表）")
    else:
        lines.append(
            f"今天花费：{cost['today']['calls']} 次调用 / {cost['today']['tokens']} tokens"
            f" / ¥{cost['today']['cost_cny']}"
        )
        lines.append(
            f"近 {report['window_days']} 天：{cost['window']['calls']} 次调用"
            f" / {cost['window']['tokens']} tokens / ¥{cost['window']['cost_cny']}"
        )

    lines.append("")
    backup = report["backup"]
    if backup.get("newest"):
        lines.append(
            f"最近备份：{backup['newest']}（{backup['age_days']} 天前，共 {backup.get('count', 1)} 份）"
        )
    else:
        lines.append("最近备份：没有")
    disk = report["disk"]
    if disk.get("ok"):
        lines.append(f"磁盘：剩 {disk['free_gb']} GB / 共 {disk['total_gb']} GB")

    lines.append("")
    if report["attention"]:
        lines.append("需要处理：")
        for item in report["attention"]:
            lines.append(f"  ! {item}")
    else:
        lines.append("需要处理：无（今天的系统是干净的）")
    return "\n".join(lines)


__all__ = [
    "BACKUP_WARN_DAYS",
    "DEFAULT_BACKUPS",
    "DEFAULT_JOBS_ROOT",
    "PROTECTED_DB_SUFFIX",
    "attention_items",
    "backup_section",
    "check_section",
    "collect_daily",
    "cost_section",
    "disk_section",
    "job_section",
    "render_text",
    "subtask_section",
    "task_section",
]
