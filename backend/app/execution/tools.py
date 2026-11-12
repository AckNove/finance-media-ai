"""编排类工具：`task_update`（推进自己这一格）与 `handoff`（向平台提请求）。

为什么这两个工具必须走 `TaskRuntime` 而不是直接写表：

* 状态机在 runtime 里，里面有租约、令牌、事件序号、下游唤醒。工具层要是自己
  `UPDATE research_subtasks SET status=...`，就等于绕开了租约校验——过期的 worker
  可以把别人正在跑的格子标成完成。
* 因此工具只拿 `CallerContext` 里那份身份（`task_id` / `subtask_id` /
  `attempt_token`），先 `runtime.claim_for(...)` 把这次抢占重建出来（令牌不符即
  `stale_attempt`），再调用 runtime 的公开方法。工具层不发明新的状态迁移。

`task_update` 的动作白名单刻意只包含"子任务自己能决定的事"：续租、完成、失败、
主动释放、请求人工复核。**没有** `pause` / `cancel` / `resume`——那些是人对整条
任务的决定，Agent 不该有。

`handoff` 的五型交接里，只有 `need_human` 会真的改状态（转人工复核，任务置为
paused）；其余四种（`request_subtask` / `request_revision` /
`insufficient_evidence` / `request_asset`）写一条 `direction_changed` 事件并把
请求内容记进事件 payload——平台侧（人或调度器）读取后决定怎么办。这样"Agent 提了
一个请求"和"请求被批准执行"在数据上是两件事，不会被混成一件。
"""

from __future__ import annotations

from typing import Any

from backend.app.capabilities.tools import ToolResult
from backend.app.core.caller import CallerContext, CallerKind
from backend.app.execution.runtime import Claim, TaskRuntime, TaskRuntimeError

#: 子任务自己能做的动作。
TASK_ACTIONS: tuple[str, ...] = (
    "heartbeat",
    "complete",
    "fail",
    "release",
    "request_human_review",
)

#: 五型交接。
HANDOFF_KINDS: tuple[str, ...] = (
    "request_subtask",
    "request_revision",
    "need_human",
    "insufficient_evidence",
    "request_asset",
)

#: 允许 Agent 请求新建的子任务类型必须已在 task_type 注册表里（下面运行时再校验）。
_MAX_OUTPUT_CHARS = 4000


def _error(code: str, message: str, **extra: Any) -> ToolResult:
    data = {"reason": code}
    if extra:
        data.update(extra)
    return ToolResult(status="error", error_code=code, error_message=message)


def _require_agent(caller: CallerContext) -> ToolResult | None:
    if caller.kind != CallerKind.AGENT:
        return ToolResult(
            status="denied",
            error_code="agent_only",
            error_message="只有 Agent 身份可以推进任务状态",
        )
    if not caller.task_id or not caller.attempt_token:
        return ToolResult(
            status="denied",
            error_code="missing_attempt",
            error_message="身份里没有 task_id / attempt_token，无法定位这次执行",
        )
    return None


def _status_text(snapshot: Any) -> str:
    """TaskSnapshot.status 在不同路径下可能是枚举或字符串，统一成字符串。"""
    status = snapshot.status
    return getattr(status, "value", str(status))


def _runtime_error(error: TaskRuntimeError) -> ToolResult:
    status = "error"
    if error.reason in {"stale_attempt", "unknown_task", "unknown_subtask", "missing_token"}:
        status = "error"
    return ToolResult(
        status=status,
        error_code=error.reason,
        error_message=error.message,
        data={"reason": error.reason},
    )


def make_task_update(*, runtime: TaskRuntime | None = None) -> Any:
    """构造 `task_update` 的提供方。"""
    shared = runtime or TaskRuntime()

    def task_update(
        caller: CallerContext,
        *,
        action: str,
        reason: str = "",
        output: Any = None,
        calls: int = 0,
        tokens: int = 0,
        cost_cny: float = 0.0,
        retryable: bool = True,
    ) -> ToolResult:
        denied = _require_agent(caller)
        if denied is not None:
            return denied
        if action not in TASK_ACTIONS:
            return _error(
                "bad_arguments",
                f"未知动作：{action}",
                allowed=list(TASK_ACTIONS),
            )

        try:
            claim = shared.claim_for(
                int(caller.task_id),  # type: ignore[arg-type]
                int(caller.subtask_id or 0),
                caller.attempt_token or "",
            )
        except (TaskRuntimeError, ValueError) as error:
            if isinstance(error, TaskRuntimeError):
                return _runtime_error(error)
            return _error("bad_arguments", f"任务号/子任务号不是整数：{error}")

        try:
            if action == "heartbeat":
                refreshed = shared.heartbeat(claim, worker=caller.name)
                return ToolResult(
                    status="ok",
                    data={
                        "action": "heartbeat",
                        "lease_until": refreshed.lease_until.isoformat()
                        if refreshed.lease_until
                        else None,
                        "attempt_token": refreshed.attempt_token,
                    },
                )
            if action == "complete":
                snapshot = shared.complete(
                    claim,
                    output=output,
                    calls=int(calls),
                    tokens=int(tokens),
                    cost_cny=float(cost_cny),
                )
                return ToolResult(
                    status="ok",
                    data={
                        "action": "complete",
                        "task_status": _status_text(snapshot),
                        "counts": snapshot.counts(),
                    },
                )
            if action == "fail":
                if not reason.strip():
                    return _error("bad_arguments", "失败必须写 reason")
                snapshot = shared.fail(
                    claim,
                    reason=reason.strip(),
                    retryable=bool(retryable),
                    cost_cny=float(cost_cny),
                )
                return ToolResult(
                    status="ok",
                    data={
                        "action": "fail",
                        "task_status": _status_text(snapshot),
                        "retryable": bool(retryable),
                    },
                )
            if action == "release":
                snapshot = shared.release(claim, reason=reason.strip() or "released")
                return ToolResult(
                    status="ok",
                    data={"action": "release", "task_status": _status_text(snapshot)},
                )
            if action == "request_human_review":
                if not reason.strip():
                    return _error("bad_arguments", "请求人工复核必须写 reason")
                snapshot = shared.request_human_review(
                    claim, reason=reason.strip(), output=output
                )
                return ToolResult(
                    status="ok",
                    data={"action": "request_human_review", "task_status": _status_text(snapshot)},
                )
        except TaskRuntimeError as error:
            return _runtime_error(error)
        except ValueError as error:
            return _error("bad_arguments", str(error))

        return _error("bad_arguments", f"未处理的动作：{action}")

    return task_update


def make_handoff(*, runtime: TaskRuntime | None = None) -> Any:
    """构造 `handoff` 的提供方。"""
    shared = runtime or TaskRuntime()

    def handoff(
        caller: CallerContext,
        *,
        kind: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        question: str = "",
        task_type: str = "",
        assignee: str | None = None,
        max_attempts: int = 2,
    ) -> ToolResult:
        denied = _require_agent(caller)
        if denied is not None:
            return denied
        if kind not in HANDOFF_KINDS:
            return _error(
                "bad_arguments", f"未知交接类型：{kind}", allowed=list(HANDOFF_KINDS)
            )
        if not reason.strip():
            return _error("bad_arguments", "交接必须写 reason（为什么发起这次请求）")

        request = dict(payload or {})
        if len(str(request)) > _MAX_OUTPUT_CHARS:
            return _error("bad_arguments", "payload 太大（超过 4000 字符）")

        try:
            claim = shared.claim_for(
                int(caller.task_id),  # type: ignore[arg-type]
                int(caller.subtask_id or 0),
                caller.attempt_token or "",
            )
        except (TaskRuntimeError, ValueError) as error:
            if isinstance(error, TaskRuntimeError):
                return _runtime_error(error)
            return _error("bad_arguments", f"任务号/子任务号不是整数：{error}")

        try:
            if kind == "need_human":
                snapshot = shared.request_human_review(
                    claim,
                    reason=reason.strip(),
                    output={"handoff": {"kind": kind, "request": request}},
                )
                return ToolResult(
                    status="ok",
                    data={
                        "kind": kind,
                        "action": "request_human_review",
                        "task_status": _status_text(snapshot),
                    },
                )

            if kind == "request_subtask":
                if not question.strip():
                    return _error("bad_arguments", "request_subtask 需要 question")
                if not task_type.strip():
                    return _error("bad_arguments", "request_subtask 需要 task_type")
                sequence = _next_sequence(shared, claim.task_id)
                shared.add_subtask(
                    claim.task_id,
                    sequence=sequence,
                    question=question.strip(),
                    task_type=task_type.strip(),
                    assignee=assignee,
                    input_payload={"requested_by_subtask": claim.subtask_id, **request},
                    depends_on=(claim.subtask_id,),
                    max_attempts=int(max_attempts),
                )
                recorded = shared.record_handoff(
                    claim,
                    kind=kind,
                    reason=reason.strip(),
                    payload={"question": question.strip(), "task_type": task_type.strip(), **request},
                )
                return ToolResult(
                    status="ok",
                    data={
                        "kind": kind,
                        "created_sequence": sequence,
                        "task_type": task_type.strip(),
                        "event_seq": recorded["event_seq"],
                    },
                )

            recorded = shared.record_handoff(
                claim, kind=kind, reason=reason.strip(), payload=request
            )
            return ToolResult(
                status="ok",
                data={
                    "kind": kind,
                    "recorded": True,
                    "event_seq": recorded["event_seq"],
                    "event_type": recorded["event_type"],
                },
            )
        except TaskRuntimeError as error:
            return _runtime_error(error)
        except ValueError as error:
            return _error("bad_arguments", str(error))

    return handoff


def _next_sequence(runtime: TaskRuntime, task_id: int) -> int:
    snapshot = runtime.task(task_id)
    return max((item.sequence for item in snapshot.subtasks), default=0) + 1


def register_execution_adapters(registry: Any, *, runtime: TaskRuntime | None = None) -> None:
    """把 `task_update` / `handoff` 注册进工具注册表。"""
    registry.register("task_update", make_task_update(runtime=runtime))
    registry.register("handoff", make_handoff(runtime=runtime))


__all__ = [
    "HANDOFF_KINDS",
    "TASK_ACTIONS",
    "make_handoff",
    "make_task_update",
    "register_execution_adapters",
]
