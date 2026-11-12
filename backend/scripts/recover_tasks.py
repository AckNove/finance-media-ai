"""启动恢复：机器重启、进程被杀之后，把任务队列恢复到「能接着跑」的状态。

做三件事，每件都只写真实状态、不猜：

1. **回收过期租约**：worker 崩了以后子任务一直挂在 ``running``、租约到期也没人管。
   这里把所有租约已过期的子任务退回 ``ready``，并记 ``retried`` 事件。
2. **报告阻塞项**：把 ``paused``（等人）和 ``blocked``（等外部条件）的任务列出来，
   连同它们的失败原因，让人一眼看出「卡在哪、要配什么」。
3. **给出下一步命令**：每条都打印可直接复制的命令。

默认只报告（``--apply`` 才真的回收租约），因为「自动改写别人的任务状态」是有副作用的动作。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _summarise(task) -> dict[str, object]:
    counts = task.counts()
    open_subtasks = [
        {
            "sequence": item.sequence,
            "task_type": item.task_type,
            "status": item.status,
            "attempts": f"{item.attempts}/{item.max_attempts}",
            "reason": item.failure_reason or "",
        }
        for item in task.subtasks
        if item.status in {"pending", "ready", "running", "blocked", "failed"}
    ]
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "counts": counts,
        "open_subtasks": open_subtasks,
    }


def collect(runtime, *, statuses: tuple[str, ...] = ("paused", "running", "created", "failed")) -> list[dict]:
    found: list[dict] = []
    for status in statuses:
        for task in runtime.list_tasks(status=status, limit=100):
            found.append(_summarise(task))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复未完成的研究任务")
    parser.add_argument("--database-url", default=None, help="默认用 DATABASE_URL / 配置里的库")
    parser.add_argument("--apply", action="store_true", help="真的回收过期租约（默认只看不改）")
    parser.add_argument("--worker", default="recovery", help="回收时记在谁名下")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    if arguments.database_url:
        import os

        os.environ["DATABASE_URL"] = arguments.database_url

    from backend.app.core.config import clear_settings_cache
    from backend.app.database.session import dispose_engine
    from backend.app.execution.runtime import TaskRuntime

    clear_settings_cache()
    dispose_engine()

    runtime = TaskRuntime()
    if arguments.apply:
        reaped = runtime.reap_expired_leases(worker=arguments.worker)
    else:
        reaped = []

    tasks = collect(runtime)
    payload = {
        "reaped_subtasks": reaped,
        "applied": bool(arguments.apply),
        "tasks": tasks,
        "hints": [
            "paused 的任务在等人工或外部条件：看 open_subtasks 里的 reason，"
            "补好密钥/看过闸门结果后，用 POST /api/research-tasks/{id}/resume 继续",
            "blocked 的子任务多半是 model_not_configured：配 .env 的 DEEPSEEK_API_KEY 或启用 local",
            "running 但租约过期：加 --apply 再跑一次就会退回 ready",
        ],
    }

    if arguments.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "已回收" if arguments.apply else "只看不改（加 --apply 才动）"
        print(f"恢复检查：{mode}")
        if reaped:
            print(f"回收过期租约的子任务：{reaped}")
        if not tasks:
            print("没有需要恢复的任务。")
        for task in tasks:
            print(f"\n#{task['id']} [{task['status']}] {task['title']}")
            print(f"  子任务统计：{task['counts']}")
            for item in task["open_subtasks"]:
                reason = f"  原因：{item['reason']}" if item["reason"] else ""
                print(f"  - #{item['sequence']} {item['task_type']} [{item['status']}] 尝试 {item['attempts']}{reason}")
        print("\n提示：")
        for hint in payload["hints"]:
            print(f"  - {hint}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
