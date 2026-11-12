"""常驻 worker：让任务队列在没人敲命令的时候也能往前走。

``research_task run`` 是「跑一批就退出」，适合手动推进一格；
这个脚本是「一直挂着」，适合日常使用和开机自启：

    python -m backend.scripts.run_worker
    python -m backend.scripts.run_worker --max-idle-minutes 30 --task-id 3

设计原则（与仓库其它脚本一致）：

1. **只写真实状态**：每一轮做的事都会落进 ``task_events``，控制台打 JSON 行，
   便于重定向到日志后按行排查。
2. **不无限空转**：连续 ``--max-idle-minutes`` 分钟没有活干就退出（默认 15 分钟），
   避免一个没人管的进程永远占着租约和连接；``--forever`` 才是真常驻。
3. **不在只读开发库上跑**：``data/finance_media.db`` 是只读开发库，这里直接拒绝，
   与 ``scripts/start.ps1`` 的口径一致。
4. **模型没配也能跑**：需要模型的阶段会被执行器标成 ``blocked`` 并转人工审核，
   不会伪造输出，也不会把任务算成成功。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import get_execution_settings, get_settings  # noqa: E402
from backend.app.execution.executor import AgentExecutor  # noqa: E402
from backend.app.execution.runtime import TaskRuntime, TaskRuntimeError  # noqa: E402
from backend.app.execution.worker import Worker  # noqa: E402

PROTECTED_DB_SUFFIX = "data/finance_media.db"


def _print(payload: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK 会乱码
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def _is_protected(database_url: str) -> bool:
    normalised = database_url.replace("\\", "/").rstrip("/")
    return normalised.endswith(PROTECTED_DB_SUFFIX)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="常驻 worker：轮询并推进研究任务")
    parser.add_argument("--task-id", type=int, default=None, help="只推进某个任务")
    parser.add_argument("--worker", default="worker-daemon", help="记在 task_events.actor 里的名字")
    parser.add_argument("--poll-seconds", type=float, default=None,
                        help="空闲时的轮询间隔，默认取 config/settings.yaml 的 execution.worker_poll_seconds")
    parser.add_argument("--max-idle-minutes", type=float, default=15.0,
                        help="连续多久没活干就退出（默认 15 分钟）")
    parser.add_argument("--forever", action="store_true", help="一直挂着，不因空闲退出")
    parser.add_argument("--max-iterations", type=int, default=0,
                        help="最多推进多少轮（0 表示不设上限）")
    args = parser.parse_args(argv)

    settings = get_settings()
    database_url = str(settings.database_url)
    if _is_protected(database_url):
        _print({
            "error": "protected_database",
            "message": "拒绝在 data/finance_media.db 上跑 worker：那是只读开发库。"
                       "用 DATABASE_URL 指向工作库（例如 data/finance_media.work.db）。",
            "database_url": database_url,
        })
        return 2

    execution = get_execution_settings()
    poll_seconds = float(args.poll_seconds or execution.worker_poll_seconds or 2.0)
    runtime = TaskRuntime(
        lease_seconds=execution.lease_seconds,
        heartbeat_seconds=execution.heartbeat_seconds,
    )
    worker = Worker(runtime, AgentExecutor(), name=args.worker, poll_seconds=poll_seconds)

    _print({
        "event": "start",
        "database_url": database_url,
        "worker": args.worker,
        "poll_seconds": poll_seconds,
        "max_idle_minutes": None if args.forever else args.max_idle_minutes,
        "task_id": args.task_id,
    })

    started = time.monotonic()
    last_work = time.monotonic()
    iterations = 0
    totals = {"completed": 0, "blocked": 0, "failed": 0, "retried": 0, "reaped": 0}

    try:
        while True:
            iterations += 1
            try:
                report = worker.run_once(task_id=args.task_id)
            except TaskRuntimeError as error:
                _print({"event": "runtime_error", "reason": error.reason, "message": str(error)})
                return 3
            except Exception as error:  # noqa: BLE001 - 单轮失败不该让常驻进程死掉
                _print({"event": "iteration_failed", "error": type(error).__name__,
                        "message": str(error)})
                time.sleep(poll_seconds)
                continue

            payload = report.as_dict()
            worked = bool(payload.get("completed") or payload.get("blocked")
                          or payload.get("failed") or payload.get("retried") or payload.get("reaped"))
            for key in totals:
                items = payload.get(key) or []
                totals[key] += len(items)
            if worked:
                last_work = time.monotonic()
                _print({"event": "iteration", "iteration": iterations, **payload})
            else:
                if args.max_iterations and iterations >= args.max_iterations:
                    _print({"event": "stop", "reason": "max_iterations", "iterations": iterations,
                            "totals": totals})
                    break
                idle_seconds = time.monotonic() - last_work
                if not args.forever and idle_seconds >= args.max_idle_minutes * 60:
                    _print({"event": "stop", "reason": "idle", "idle_seconds": round(idle_seconds, 1),
                            "iterations": iterations, "totals": totals})
                    break
                time.sleep(poll_seconds)
                continue

            if args.max_iterations and iterations >= args.max_iterations:
                _print({"event": "stop", "reason": "max_iterations", "iterations": iterations,
                        "totals": totals})
                break
    except KeyboardInterrupt:  # pragma: no cover - 手工 Ctrl+C
        _print({"event": "stop", "reason": "keyboard_interrupt", "iterations": iterations,
                "totals": totals, "uptime_seconds": round(time.monotonic() - started, 1)})
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工运行入口
    raise SystemExit(main())
