"""命令行入口：把「建任务 → 编排 → 跑 worker → 看结果」变成一条命令。

用法（在仓库根目录）::

    python -m backend.scripts.research_task plan --title "商业银行是如何赚钱的"
    python -m backend.scripts.research_task run --task-id 1
    python -m backend.scripts.research_task status --task-id 1
    python -m backend.scripts.research_task events --task-id 1

设计原则：CLI 只做参数解析与打印，所有逻辑都在 runtime/orchestrator/worker 里，
这样 API、前端、定时任务调用的是同一套代码，不会出现"命令行能跑、接口不能跑"。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from backend.app.execution.executor import AgentExecutor
from backend.app.execution.orchestrator import Orchestrator
from backend.app.execution.runtime import TaskRuntime, TaskRuntimeError
from backend.app.execution.worker import Worker


def _print(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK 会乱码
    print(text)


def _plan(args: argparse.Namespace) -> int:
    orchestrator = Orchestrator(TaskRuntime())
    result = orchestrator.plan_task(
        title=args.title,
        objective=args.objective or args.title,
        pipeline=args.pipeline,
        queries=args.query or None,
        files=args.file or None,
        budget_max_calls=args.max_calls,
        budget_max_cost_cny=args.max_cost,
    )
    _print(result.as_dict())
    return 0


def _run(args: argparse.Namespace) -> int:
    runtime = TaskRuntime()
    worker = Worker(runtime, AgentExecutor(), name=args.worker)
    try:
        report = worker.run_until_idle(max_iterations=args.max_iterations,
                                       task_id=args.task_id)
    except TaskRuntimeError as error:
        _print({"error": error.reason, "message": str(error)})
        return 2
    _print(report.as_dict())
    if args.show_task and args.task_id is not None:
        _print(runtime.task(args.task_id).as_dict())
    return 0


def _status(args: argparse.Namespace) -> int:
    runtime = TaskRuntime()
    if args.task_id is None:
        _print([task.as_dict() for task in runtime.list_tasks(limit=args.limit)])
        return 0
    try:
        _print(runtime.task(args.task_id).as_dict())
    except TaskRuntimeError as error:
        _print({"error": error.reason, "message": str(error)})
        return 2
    return 0


def _events(args: argparse.Namespace) -> int:
    runtime = TaskRuntime()
    _print(runtime.events(args.task_id, limit=args.limit))
    return 0


def _stats(_: argparse.Namespace) -> int:
    _print(TaskRuntime().stats().as_dict())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="研究任务：编排、执行、查看")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="按流水线编排一个新任务")
    plan.add_argument("--title", required=True)
    plan.add_argument("--objective", default="")
    plan.add_argument("--pipeline", default="research_to_script")
    plan.add_argument("--query", action="append", help="采集阶段的检索词，可重复")
    plan.add_argument("--file", action="append", help="抽取阶段的本地文件，可重复")
    plan.add_argument("--max-calls", type=int, default=None)
    plan.add_argument("--max-cost", type=float, default=None)
    plan.set_defaults(func=_plan)

    run = sub.add_parser("run", help="跑 worker 直到没有可做的活")
    run.add_argument("--task-id", type=int, default=None)
    run.add_argument("--worker", default="cli-worker")
    run.add_argument("--max-iterations", type=int, default=20)
    run.add_argument("--show-task", action="store_true")
    run.set_defaults(func=_run)

    status = sub.add_parser("status", help="看任务快照")
    status.add_argument("--task-id", type=int, default=None)
    status.add_argument("--limit", type=int, default=10)
    status.set_defaults(func=_status)

    events = sub.add_parser("events", help="看任务事件流")
    events.add_argument("--task-id", type=int, required=True)
    events.add_argument("--limit", type=int, default=50)
    events.set_defaults(func=_events)

    stats = sub.add_parser("stats", help="看运行时统计")
    stats.set_defaults(func=_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - 手工运行入口
    raise SystemExit(main())
