"""研究任务路由：建任务、看进度、推进执行、人工闸门。

设计取舍：
- **任务只由编排器拆解**。HTTP 层不提供"手工加子任务"的接口，否则流水线定义就形同虚设，
  库里也会出现无法复现的临时结构。需要跳过阶段就用 ``skip``，并写清 ``skip_reason``。
- ``/run`` 是**同步推进**（一次最多几格）：本机单用户场景下，用户点一下就能看到结果，
  不需要常驻 worker 与队列。真正的后台常驻由 ``backend/scripts/research_task.py run`` 承担。
- 所有写操作都要求 human 身份（见 ``router.ROUTE_CALLERS``）；agent 只能经执行层与工具写库。
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy import select

from backend.app.api.deps import COMMON_RESPONSES, DbSession, Pagination, require_caller
from backend.app.core.caller import CallerKind
from backend.app.core.errors import ConflictError, DomainValidationError, NotFoundError
from backend.app.database.session import get_session_factory
from backend.app.execution.executor import AgentExecutor
from backend.app.execution.orchestrator import Orchestrator
from backend.app.execution.runtime import TaskRuntime, TaskRuntimeError
from backend.app.execution.task_types import TaskType
from backend.app.execution.worker import Worker
from backend.app.models.execution import ResearchSubtask, ResearchTask, TaskEvent
from backend.app.schemas.common import Page
from backend.app.schemas.research_task import (
    PlanningOut,
    ResearchTaskCreate,
    ResearchTaskDetailOut,
    ResearchTaskOut,
    RunReportOut,
    RunRequest,
    SubtaskOut,
    TaskEventOut,
)

router = APIRouter(
    prefix="/research-tasks",
    tags=["research-tasks"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)

#: 运行时的稳定原因码 -> HTTP 语义。``TaskRuntimeError`` 是领域层异常，
#: 不继承 ``AppError``（它同时被 worker/CLI 使用），所以在这里显式翻译，
#: 保证前端拿到的是 404/409/422 而不是 500。
_NOT_FOUND_REASONS = frozenset({"unknown_task", "unknown_subtask"})
_CONFLICT_REASONS = frozenset(
    {
        "task_closed",
        "not_paused",
        "already_started",
        "subtask_finished",
        "stale_attempt",
        "lease_held",
    }
)


def _http_error(error: TaskRuntimeError) -> NoReturn:
    """把运行时拒绝翻成 HTTP 错误，``reason`` 原样放进 details。"""
    details = {"reason": error.reason}
    if error.reason in _NOT_FOUND_REASONS:
        raise NotFoundError(error.message, details=details)
    if error.reason in _CONFLICT_REASONS:
        raise ConflictError(error.message, details=details)
    raise DomainValidationError(error.message, details=details)


def _runtime() -> TaskRuntime:
    return TaskRuntime(get_session_factory())


def _plan_of(db: DbSession, task_id: int) -> dict[str, object] | None:
    plan = db.execute(
        select(ResearchTask.plan).where(ResearchTask.id == task_id)
    ).scalar_one_or_none()
    return plan or None


def _planning_of(db: DbSession, task_id: int) -> PlanningOut | None:
    """从 ``research.plan`` 子任务的产出里读"到底是谁做的规划"。

    不猜、不从配置推断：这一段直接读执行器写进去的 ``planner`` / ``model_used`` /
    ``degraded_reason``，所以"标着 model 其实是规则"这种事在接口层面就藏不住。
    """
    output = db.execute(
        select(ResearchSubtask.output_payload)
        .where(
            ResearchSubtask.research_task_id == task_id,
            ResearchSubtask.task_type == TaskType.RESEARCH_PLAN.value,
        )
        .order_by(ResearchSubtask.sequence)
        .limit(1)
    ).scalar_one_or_none()
    if not isinstance(output, dict):
        return None
    return PlanningOut.model_validate(output)


def _subtask_out(item: Any) -> SubtaskOut:
    """把子任务行转成接口结构。

    注意 ``output`` 不是 ORM 上的列名（列叫 ``output_payload``），直接
    ``model_validate(item)`` 会让每一格的 ``output`` 恒为 None——前端就永远看不到
    某一阶段到底产出了什么。这里显式补一次。
    """
    base = SubtaskOut.model_validate(item)
    return base.model_copy(update={"output": item.output_payload})


def _detail(db: DbSession, task_id: int) -> ResearchTaskDetailOut:
    try:
        snapshot = _runtime().task(task_id)
    except TaskRuntimeError as error:
        _http_error(error)
    subtasks = db.execute(
        select(ResearchSubtask)
        .where(ResearchSubtask.research_task_id == task_id)
        .order_by(ResearchSubtask.sequence)
    ).scalars().all()
    # 先在**基类**上验证，再补三个派生字段：``counts`` 在快照上是方法、
    # ``plan`` 不在快照里，直接对快照 validate 会把绑定方法当字段值。
    payload = ResearchTaskOut.model_validate(snapshot).model_dump()
    payload["subtasks"] = [_subtask_out(item) for item in subtasks]
    payload["counts"] = snapshot.counts()
    payload["plan"] = _plan_of(db, task_id)
    payload["planning"] = _planning_of(db, task_id)
    return ResearchTaskDetailOut.model_validate(payload)


@router.post(
    "",
    response_model=ResearchTaskDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="建研究任务（按流水线拆解）",
)
def create_research_task(payload: ResearchTaskCreate, db: DbSession) -> ResearchTaskDetailOut:
    """按 ``config/pipelines.yaml`` 把研究目标拆成带依赖的子任务，并把计划写进任务。"""
    orchestrator = Orchestrator(_runtime())
    try:
        result = orchestrator.plan_task(
            title=payload.title,
            objective=payload.objective,
            pipeline=payload.pipeline,
            topic_id=payload.topic_id,
            queries=payload.queries,
            files=payload.files,
            skip=payload.skip,
            skip_reason=payload.skip_reason,
            budget_max_calls=payload.budget_max_calls,
            budget_max_cost_cny=payload.budget_max_cost_cny,
            max_attempts=payload.max_attempts,
            start=payload.start,
            requested_by="human",
        )
    except TaskRuntimeError as error:
        _http_error(error)
    return _detail(db, result.task.id)


@router.get("", response_model=Page[ResearchTaskOut], summary="研究任务列表")
def list_research_tasks(
    db: DbSession,
    page: Pagination,
    status_filter: str | None = None,
) -> Page[ResearchTaskOut]:
    runtime = _runtime()
    try:
        snapshots = runtime.list_tasks(status=status_filter, limit=page.offset + page.limit)
    except ValueError as error:  # 非法状态值
        raise DomainValidationError(f"未知任务状态：{status_filter}") from error
    window = snapshots[page.offset : page.offset + page.limit]
    stats = runtime.stats()
    return Page[ResearchTaskOut](
        items=[ResearchTaskOut.model_validate(item) for item in window],
        total=sum(stats.tasks.values()),
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{task_id}", response_model=ResearchTaskDetailOut, summary="研究任务详情")
def get_research_task(db: DbSession, task_id: int = Path(ge=1)) -> ResearchTaskDetailOut:
    return _detail(db, task_id)


@router.get(
    "/{task_id}/events",
    response_model=Page[TaskEventOut],
    summary="任务事件流（审计）",
)
def list_task_events(
    db: DbSession,
    page: Pagination,
    task_id: int = Path(ge=1),
) -> Page[TaskEventOut]:
    try:
        _runtime().task(task_id)
    except TaskRuntimeError as error:
        _http_error(error)
    statement = (
        select(TaskEvent)
        .where(TaskEvent.research_task_id == task_id)
        .order_by(TaskEvent.event_seq)
    )
    total = len(db.execute(statement).scalars().all())
    events = db.execute(statement.limit(page.limit).offset(page.offset)).scalars().all()
    return Page[TaskEventOut](
        items=[TaskEventOut.model_validate(item) for item in events],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/{task_id}/run", response_model=RunReportOut, summary="同步推进执行（最多几格）")
def run_research_task(
    db: DbSession,
    payload: RunRequest,
    task_id: int = Path(ge=1),
) -> RunReportOut:
    """真实执行：真搜索、真抓取、真落库。没有模型密钥时阶段会 blocked 而不是伪造结果。"""
    runtime = _runtime()
    worker = Worker(
        runtime,
        AgentExecutor(session_factory=get_session_factory()),
        poll_seconds=payload.poll_seconds,
    )
    try:
        report = worker.run_until_idle(
            max_iterations=payload.max_iterations, task_id=task_id
        )
    except TaskRuntimeError as error:
        _http_error(error)
    return RunReportOut(
        worker=report.worker,
        iterations=report.iterations,
        completed=list(report.completed),
        blocked=list(report.blocked),
        retried=list(report.retried),
        failed=list(report.failed),
        reaped=list(report.reaped),
        added_subtasks=list(report.added_subtasks),
        idle=report.idle,
        stopped_reason=report.stopped_reason,
        task=_detail(db, task_id),
    )


def _control(db: DbSession, task_id: int, action: str) -> ResearchTaskDetailOut:
    runtime = _runtime()
    try:
        if action == "pause":
            runtime.pause(task_id, reason="human_pause")
        elif action == "resume":
            runtime.resume(task_id)
        else:
            runtime.cancel(task_id, reason="human_cancel")
    except TaskRuntimeError as error:
        _http_error(error)
    return _detail(db, task_id)


@router.post("/{task_id}/pause", response_model=ResearchTaskDetailOut, summary="暂停任务")
def pause_research_task(db: DbSession, task_id: int = Path(ge=1)) -> ResearchTaskDetailOut:
    return _control(db, task_id, "pause")


@router.post("/{task_id}/resume", response_model=ResearchTaskDetailOut, summary="恢复任务")
def resume_research_task(db: DbSession, task_id: int = Path(ge=1)) -> ResearchTaskDetailOut:
    return _control(db, task_id, "resume")


@router.post("/{task_id}/cancel", response_model=ResearchTaskDetailOut, summary="取消任务")
def cancel_research_task(db: DbSession, task_id: int = Path(ge=1)) -> ResearchTaskDetailOut:
    return _control(db, task_id, "cancel")
