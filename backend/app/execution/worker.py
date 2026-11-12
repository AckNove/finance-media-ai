"""Worker：反复「收租约 → 抢一个子任务 → 执行 → 回写结果」，直到没有可做的活。

为什么要一个循环而不是一跑到底：真实任务会跨分钟（搜索、抓取、模型调用），
进程随时可能被杀（Windows 重启、手动 Ctrl-C）。循环 + 库内租约让"重启后接着跑"
成为默认行为，而不是特例。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.app.execution.executor import AgentExecutor
from backend.app.execution.runtime import Claim, TaskRuntime, TaskRuntimeError

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 2.0


@dataclass(slots=True)
class WorkerReport:
    """一轮循环里发生了什么（供 CLI 与 API 展示，不做聚合判断）。"""

    worker: str
    iterations: int = 0
    completed: list[int] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    retried: list[int] = field(default_factory=list)
    reaped: list[int] = field(default_factory=list)
    idle: bool = False
    stopped_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "iterations": self.iterations,
            "completed": self.completed,
            "blocked": self.blocked,
            "failed": self.failed,
            "retried": self.retried,
            "reaped": self.reaped,
            "idle": self.idle,
            "stopped_reason": self.stopped_reason,
        }


class Worker:
    """单线程 worker。默认并发 1，与 ``execution.max_concurrency`` 的默认值一致。"""

    def __init__(
        self,
        runtime: TaskRuntime,
        executor: AgentExecutor | None = None,
        *,
        name: str = "worker-1",
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runtime = runtime
        self.executor = executor or AgentExecutor()
        self.name = name
        self.poll_seconds = poll_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ 一轮

    def run_once(self, *, task_id: int | None = None) -> WorkerReport:
        """抢一个子任务并执行；没有可做的活返回 ``idle=True``。"""
        report = WorkerReport(worker=self.name)
        report.reaped = self.runtime.reap_expired_leases(worker=self.name)

        claim = self.runtime.claim_next(worker=self.name, task_id=task_id)
        if claim is None:
            report.idle = True
            report.stopped_reason = "no_ready_subtask"
            return report

        report.iterations = 1
        self._apply(report, claim)
        return report

    def run_until_idle(
        self, *, max_iterations: int = 50, task_id: int | None = None
    ) -> WorkerReport:
        """连续干活直到没有可抢的子任务（或到达迭代上限）。"""
        total = WorkerReport(worker=self.name)
        for _ in range(max_iterations):
            report = self.run_once(task_id=task_id)
            total.iterations += report.iterations
            total.completed.extend(report.completed)
            total.blocked.extend(report.blocked)
            total.failed.extend(report.failed)
            total.retried.extend(report.retried)
            total.reaped.extend(report.reaped)
            if report.idle:
                total.idle = True
                total.stopped_reason = report.stopped_reason
                return total
        total.stopped_reason = "max_iterations"
        return total

    # ------------------------------------------------------------------ 回写

    def _apply(self, report: WorkerReport, claim: Claim) -> None:
        outcome = self.executor.execute(claim, worker=self.name)
        try:
            if outcome.status == "completed":
                self.runtime.complete(
                    claim,
                    output=outcome.output,
                    calls=outcome.calls,
                    tokens=outcome.tokens,
                    cost_cny=outcome.cost_cny,
                    agent_run_id=outcome.agent_run_id,
                )
                report.completed.append(claim.subtask_id)
                self._pause_for_gate(claim, report)
            elif outcome.status == "blocked":
                # 阻塞不是失败：挂起来等人处理（补密钥、看脚本），任务进入 paused
                self.runtime.request_human_review(claim, reason=outcome.reason or "blocked")
                report.blocked.append(claim.subtask_id)
                logger.warning(
                    "subtask %s blocked: %s", claim.subtask_id, outcome.reason
                )
            elif outcome.status == "retry":
                self.runtime.fail(claim, reason=outcome.reason or "retry", retryable=True)
                report.retried.append(claim.subtask_id)
            else:
                self.runtime.fail(claim, reason=outcome.reason or "failed", retryable=False)
                report.failed.append(claim.subtask_id)
        except TaskRuntimeError as error:
            # 令牌失效（别的 worker 接手了）：这一轮结果作废，不覆盖别人的写入
            logger.warning("drop stale result for subtask %s: %s", claim.subtask_id, error.reason)
            report.stopped_reason = f"stale_attempt:{claim.subtask_id}"

    def _pause_for_gate(self, claim: Claim, report: WorkerReport) -> None:
        """阶段声明了 ``require_human_review``：完成之后把任务停下来等人。

        闸门由编排器写进 ``input_payload``，所以"这道门到底有没有真的挡住"
        可以直接查库确认，而不是只看配置里写了 ``human_gate: true``。
        """
        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        if not payload.get("require_human_review"):
            return
        gate = payload.get("human_gate") or {}
        gate_id = str(gate.get("id", "")) if isinstance(gate, dict) else ""
        self.runtime.pause(claim.task_id, reason=f"human_gate:{gate_id or 'unnamed'}")
        logger.info("task %s paused for human gate %s", claim.task_id, gate_id)


__all__ = ["DEFAULT_POLL_SECONDS", "Worker", "WorkerReport"]
