"""`python -m backend.scripts.daily_check` —— 今天的系统体检（一条命令看六件事）。

为什么要这个
------------
操作手册里写"每天该看三处"，但那是三处不同的页面/命令拼起来的。日常真正要回答的只有一句话：
**今天的系统还健康吗、有没有卡住等人、花了多少钱、备份是不是新的。**

这个脚本只读，不改任何东西，也不联网、不调模型：

1. 任务：各状态计数、暂停（等人）的任务、窗口内失败的任务；
2. 子任务：窗口内失败的子任务（带原因）；
3. 人工确认：还没人确认的语义核查条数（这是最长见的"卡住"来源——`content.script` 会等你确认）；
4. 视频任务：被人工撤回确认而失效的任务（`job.json` 里 `draft_gate.usable` 为假）；
5. 花费：今天/窗口内的模型调用次数、Token、金额（来自 `model_calls` 审计行，不是估算）；
6. 备份：最近一份备份有多旧；
7. 磁盘：项目所在盘的剩余空间。

退出码：`0` 一切正常；`1` 有需要处理的事项（见输出的"需要处理"一节）；`3` 连不上数据库。
参数错误（`argparse`）照旧返回 2。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import PROJECT_ROOT, get_settings
from backend.app.database.session import build_engine
from backend.app.models.execution import ResearchSubtask, ResearchTask
from backend.app.models.metering import ModelCall
from backend.app.models.research import FindingCheck

DEFAULT_JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
DEFAULT_BACKUPS = PROJECT_ROOT / "data" / "backups"
BACKUP_WARN_DAYS = 7
PROTECTED_DB_SUFFIX = "data/finance_media.db"


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


def _task_section(session, *, since: datetime) -> dict[str, Any]:
    rows = session.execute(
        select(ResearchTask.status, func.count()).group_by(ResearchTask.status)
    ).all()
    by_status = {_status_key(status): int(count) for status, count in rows}

    paused = session.execute(
        select(ResearchTask)
        .where(ResearchTask.status == "paused")
        .order_by(ResearchTask.updated_at.desc())
        .limit(20)
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
        .limit(20)
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


def _subtask_section(session, *, since: datetime) -> dict[str, Any]:
    failed = session.execute(
        select(ResearchSubtask)
        .where(ResearchSubtask.status == "failed", ResearchSubtask.updated_at >= since)
        .order_by(ResearchSubtask.updated_at.desc())
        .limit(20)
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


def _check_section(session) -> dict[str, Any]:
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


def _job_section(jobs_root: Path) -> dict[str, Any]:
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


def _cost_section(session, *, today: datetime, since: datetime) -> dict[str, Any]:
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


def _backup_section(backups: Path, *, now: datetime) -> dict[str, Any]:
    if not backups.is_dir():
        return {"dir": str(backups), "exists": False, "newest": "", "age_days": None}
    files = sorted(backups.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {"dir": str(backups), "exists": True, "newest": "", "age_days": None}
    newest = files[0]
    age = (now - datetime.fromtimestamp(newest.stat().st_mtime, UTC)).total_seconds() / 86400
    return {
        "dir": str(backups),
        "exists": True,
        "newest": newest.name,
        "age_days": round(age, 1),
        "count": len(files),
    }


def _disk_section() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
    except OSError as error:  # pragma: no cover - 只有在盘不可读时才会走到
        return {"ok": False, "error": str(error)}
    return {
        "ok": True,
        "free_gb": round(usage.free / 1024**3, 1),
        "total_gb": round(usage.total / 1024**3, 1),
    }


def _attention(report: dict[str, Any]) -> list[str]:
    items: list[str] = []
    unavailable = [name for name, value in report["sections_unavailable"].items() if value]
    if unavailable:
        items.append(
            f"这几块查不了：{', '.join(unavailable)}（这个库还没迁移到当前版本，"
            "先跑 `python -m alembic -x db_url=… upgrade head`；只读诊断不会替你改它）"
        )
    if report["tasks"].get("failed_in_window"):
        items.append(f"窗口内有 {len(report['tasks']['failed_in_window'])} 个任务失败")
    if report["subtasks"].get("count"):
        items.append(f"窗口内有 {report['subtasks']['count']} 个子任务失败")
    if report["checks"].get("pending"):
        items.append(
            f"{report['checks']['pending']} 条语义核查还没人工确认"
            f"（报告 {report['checks']['pending_reports']}）——`content.script` 会一直等着"
        )
    if report["jobs"].get("invalidated_count"):
        items.append(f"{report['jobs']['invalidated_count']} 个视频任务的确认被撤回过（已标记失效）")
    age = report["backup"].get("age_days")
    if age is None:
        items.append("data/backups 里还没有任何备份")
    elif age > BACKUP_WARN_DAYS:
        items.append(f"最近一份备份已经 {age} 天（建议 python -m backend.scripts.backup_db）")
    if report["disk"].get("ok") and report["disk"]["free_gb"] < 5:
        items.append(f"磁盘只剩 {report['disk']['free_gb']} GB")
    if report["tasks"].get("by_status", {}).get("paused"):
        items.append(f"{report['tasks']['by_status']['paused']} 个任务暂停在等人放行")
    return items


class DailyCheckError(RuntimeError):
    """连不上库属于"体检做不了"，不是"体检结果不好"。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _ensure_sqlite_exists(url: str) -> None:
    """SQLite 会"顺手"新建一个空库，把路径写错就永远查不出东西——先自己判一下。"""

    if not url.startswith("sqlite"):
        return
    parsed = make_url(url)
    path = parsed.database or ""
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    target = Path(path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if not target.exists():
        raise DailyCheckError(
            "database_missing", f"库文件不存在：{target}（先确认 --database-url 写对了）"
        )


def _tolerant(name: str, fn, *args, register: dict[str, str], **kwargs):
    """查不了就记一笔，不让整份体检崩掉。

    真实开发库目前停在 `de8148733a9b`（只有 19 张表），`research_tasks` 这些表根本不存在；
    诊断工具对着它应该如实说"这块查不了"，而不是抛 `OperationalError`。
    """

    try:
        return fn(*args, **kwargs)
    except OperationalError as error:  # pragma: no cover - 只在库结构落后时触发
        register[name] = str(error.orig or error).splitlines()[0][:200]
        return {"unavailable": True, "reason": register[name]}


def collect(
    *,
    database_url: str = "",
    jobs_root: Path | str = DEFAULT_JOBS_ROOT,
    backups: Path | str = DEFAULT_BACKUPS,
    days: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """只读汇总，返回可 JSON 化的字典（测试也走这个入口）。"""

    moment = now or datetime.now(UTC)
    since = _naive_utc(moment - timedelta(days=max(1, days)))
    today = _naive_utc(moment.replace(hour=0, minute=0, second=0, microsecond=0))

    url = database_url or get_settings().database_url
    _ensure_sqlite_exists(url)
    engine = build_engine(get_settings().model_copy(update={"database_url": url}))
    unavailable: dict[str, str] = {}
    try:
        factory = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        with factory() as session:
            tasks = _tolerant("任务", _task_section, session, since=since, register=unavailable)
            subtasks = _tolerant(
                "子任务", _subtask_section, session, since=since, register=unavailable
            )
            checks = _tolerant("语义核查", _check_section, session, register=unavailable)
            cost = _tolerant(
                "花费",
                _cost_section,
                session,
                today=today,
                since=since,
                register=unavailable,
            )
    finally:
        engine.dispose()

    report: dict[str, Any] = {
        "generated_at": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "database_url": url,
        "protected_database": str(url).replace("\\", "/").endswith(PROTECTED_DB_SUFFIX),
        "window_days": max(1, days),
        "tasks": tasks,
        "subtasks": subtasks,
        "checks": checks,
        "jobs": _job_section(Path(jobs_root)),
        "cost": cost,
        "backup": _backup_section(Path(backups), now=moment),
        "disk": _disk_section(),
        "sections_unavailable": unavailable,
    }
    items = _attention(report)
    report["attention"] = items
    report["ok"] = not items
    return report


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"体检时间：{report['generated_at']}（UTC）")
    target = "真实开发库（只读）" if report["protected_database"] else report["database_url"]
    lines.append(f"数据库：{target}")
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
        lines.append(f"最近备份：{backup['newest']}（{backup['age_days']} 天前，共 {backup.get('count', 1)} 份）")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="今天的系统体检（只读，不改任何东西）")
    parser.add_argument("--database-url", default="", help="默认用 config/settings.yaml 里的库")
    parser.add_argument("--jobs-root", default=str(DEFAULT_JOBS_ROOT))
    parser.add_argument("--backups", default=str(DEFAULT_BACKUPS))
    parser.add_argument("--days", type=int, default=1, help="窗口天数（默认 1 天）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    try:
        report = collect(
            database_url=args.database_url,
            jobs_root=args.jobs_root,
            backups=args.backups,
            days=args.days,
        )
    except Exception as error:  # noqa: BLE001 - 连不上库就直接说清楚
        reason = getattr(error, "reason", "database_unavailable")
        payload = {"ok": False, "blocked": True, "reason": reason, "message": str(error)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
        return 3

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    else:
        print(render_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
