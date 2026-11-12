"""编排器：把一个研究目标翻译成"带依赖的子任务 DAG"，交给运行时去跑。

它**不含任何创作逻辑**（不写提示词、不编结论），只负责三件事：

1. 读 ``config/pipelines.yaml`` 里某个流水线的阶段定义（顺序、task_type、执行者）；
2. 把每个 ``kind: task`` 的阶段落成一条子任务，按顺序串成依赖链；
3. 把 ``kind: gate`` 的阶段记录成"必须在某阶段之后停下来等人"的标记——
   落到前一阶段的 ``input_payload["require_human_review"]``，由 worker 在完成后
   真的把任务置为 paused，**不是写在文档里假装有人审过**。

这一层与执行层分离，好处是同样的 DAG 可以：接真实模型跑、接假执行器做回归、
或者只做"如果我这么拆，会发生什么"的推演。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from backend.app.core.config import get_agent_routing, get_pipelines_config
from backend.app.execution.runtime import (
    SubtaskSnapshot,
    TaskRuntime,
    TaskSnapshot,
    TaskRuntimeError,
)
from backend.app.execution.task_types import coerce as coerce_task_type

#: 采集/抽取阶段从调用方拿输入的方式（其余阶段靠上游产物）
STAGE_INPUT_KEYS: dict[str, str] = {
    "research.collect": "queries",
    "research.extract": "files",
}


@dataclass(slots=True)
class PlannedStage:
    id: str
    name: str
    task_type: str
    agent: str
    subtask_id: int | None = None
    gate_after: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "agent": self.agent,
            "subtask_id": self.subtask_id,
            "gate_after": self.gate_after,
        }


@dataclass(slots=True)
class PlanResult:
    task: TaskSnapshot
    stages: list[PlannedStage] = field(default_factory=list)
    human_gates: list[dict[str, str]] = field(default_factory=list)
    skipped_stages: list[str] = field(default_factory=list)

    @property
    def subtask_ids(self) -> list[int]:
        return [stage.subtask_id for stage in self.stages if stage.subtask_id is not None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task.id,
            "status": self.task.status,
            "stages": [stage.as_dict() for stage in self.stages],
            "human_gates": list(self.human_gates),
            "skipped_stages": list(self.skipped_stages),
        }


class Orchestrator:
    """按流水线定义编排任务。"""

    def __init__(
        self,
        runtime: TaskRuntime | None = None,
        *,
        pipelines: Any | None = None,
        routing: Any | None = None,
    ) -> None:
        self.runtime = runtime or TaskRuntime()
        self._pipelines = pipelines
        self._routing = routing

    # ------------------------------------------------------------------ 读配置

    @property
    def pipelines(self) -> Any:
        if self._pipelines is None:
            self._pipelines = get_pipelines_config()
        return self._pipelines

    @property
    def routing(self) -> Any:
        if self._routing is None:
            self._routing = get_agent_routing()
        return self._routing

    def pipeline_stages(self, pipeline: str) -> list[Any]:
        spec = self.pipelines.pipelines.get(pipeline)
        if spec is None:
            raise TaskRuntimeError(
                "unknown_pipeline",
                f"流水线 {pipeline} 未在 config/pipelines.yaml 中登记",
            )
        return list(spec.stages)

    # ------------------------------------------------------------------ 编排

    def plan_task(
        self,
        *,
        title: str,
        objective: str = "",
        pipeline: str = "research_to_script",
        topic_id: int | None = None,
        queries: list[str] | None = None,
        files: list[str] | None = None,
        budget_max_calls: int | None = None,
        budget_max_cost_cny: Decimal | float | None = None,
        max_attempts: int = 2,
        requested_by: str = "human",
        skip: Sequence[str] = (),
        skip_reason: str = "",
        start: bool = True,
    ) -> PlanResult:
        """建任务 + 按流水线落子任务 + 记人工闸门。

        ``skip`` 是**显式跳过**某些阶段 id（不是自动降级）：例如当前没有模型密钥，
        就跳过 ``topic.discover``/``research.plan``，先跑不需要模型的采集阶段。
        跳过会写进计划里并带上 ``skip_reason``，事后能看出"这一跑少了什么、为什么"。

        ``start=False`` 时只建计划不启动，便于人先看计划再放行。
        """
        stages = self.pipeline_stages(pipeline)
        task = self.runtime.create_task(
            title=title,
            objective=objective or title,
            topic_id=topic_id,
            planner="pipeline:" + pipeline,
            requested_by=requested_by,
            budget_max_calls=budget_max_calls,
            budget_max_cost_cny=budget_max_cost_cny,
        )

        planned: list[PlannedStage] = []
        gates: list[dict[str, str]] = []
        skipped: list[str] = []
        skipped_ids = {str(item) for item in skip}
        unknown = skipped_ids - {str(getattr(stage, "id", "")) for stage in stages}
        if unknown:
            raise TaskRuntimeError(
                "unknown_stage",
                f"流水线 {pipeline} 没有这些阶段：{', '.join(sorted(unknown))}",
            )
        # 先把"每个 gate 挂在哪个 task 阶段**之后**"算出来，再建子任务。
        # 语义：gate 是"看完成果再放行"，所以标记落在前一个 task 阶段上，
        # worker 在它完成之后把任务置为 paused，等人审批再 resume。
        # 另外，阶段自己写了 human_gate: true（如 verify.claims）也算一道门。
        gate_after: dict[str, dict[str, str]] = {}
        counters_pre: dict[str, int] = {}
        last_key: str | None = None
        for stage in stages:
            kind = str(getattr(stage, "kind", "task"))
            if kind == "gate":
                gate = {"id": stage.id, "name": getattr(stage, "name", stage.id)}
                gates.append(gate)
                if last_key is not None:
                    gate_after[last_key] = gate
                continue
            counters_pre[stage.task_type] = counters_pre.get(stage.task_type, 0) + 1
            last_key = f"{stage.task_type}#{counters_pre[stage.task_type]}"
            if bool(getattr(stage, "human_gate", False)):
                gate = {"id": f"{stage.id}_gate", "name": f"{getattr(stage, 'name', stage.id)}（人工确认）"}
                gates.append(gate)
                gate_after[last_key] = gate

        previous_id: int | None = None
        counter: dict[str, int] = {}
        skipped_gates: list[dict[str, str]] = []
        for stage in stages:
            kind = str(getattr(stage, "kind", "task"))
            if kind == "gate":
                continue
            task_type = str(stage.task_type)
            if str(stage.id) in skipped_ids:
                # 显式跳过：不建子任务，也不占用 depends_on 链
                skipped.append(str(stage.id))
                coerce_task_type(task_type)  # 跳过的阶段也要是合法 task_type
                counter[task_type] = counter.get(task_type, 0) + 1
                gate = gate_after.get(f"{task_type}#{counter[task_type]}")
                if gate is not None:
                    skipped_gates.append(gate)
                continue
            coerce_task_type(task_type)  # 未登记的 task_type 立刻拒绝
            counter[task_type] = counter.get(task_type, 0) + 1
            key = f"{task_type}#{counter[task_type]}"
            payload: dict[str, Any] = {
                "stage_id": stage.id,
                "stage_name": getattr(stage, "name", stage.id),
                "pipeline": pipeline,
            }
            input_key = STAGE_INPUT_KEYS.get(task_type)
            if input_key == "queries" and queries:
                payload["queries"] = list(queries)
            if input_key == "files" and files:
                payload["files"] = list(files)
            gate = gate_after.get(key)
            if gate is not None:
                payload["require_human_review"] = True
                payload["human_gate"] = gate
            assignee = self._assignee_for(stage)
            if assignee is None:
                skipped.append(stage.id)
                continue
            payload["assignee_source"] = "config/agent_routing.yaml"
            subtask: SubtaskSnapshot = self.runtime.add_subtask(
                task.id,
                sequence=len(planned) + 1,
                question=self._question(stage, objective or title),
                task_type=task_type,
                assignee=assignee,
                input_payload=payload,
                depends_on=[previous_id] if previous_id is not None else (),
                max_attempts=max_attempts,
            )
            previous_id = subtask.id
            planned.append(
                PlannedStage(
                    id=stage.id,
                    name=getattr(stage, "name", stage.id),
                    task_type=task_type,
                    agent=assignee,
                    subtask_id=subtask.id,
                    gate_after=gate is not None,
                )
            )

        plan = {
            "pipeline": pipeline,
            "title": task.title,
            "objective": task.objective,
            "stages": [stage.as_dict() for stage in planned],
            "human_gates": gates,
            "skipped_stages": skipped,
            "skipped_gates": skipped_gates,
            "skip_reason": skip_reason,
            "input": {"queries": queries or [], "files": files or []},
            "note": (
                "阶段顺序来自 config/pipelines.yaml；人工闸门以 require_human_review "
                "标记落在前一阶段上，由 worker 在完成后把任务置为 paused"
            ),
        }
        updated = self.runtime.set_plan(task.id, plan, planner="pipeline:" + pipeline)
        if start:
            updated = self.runtime.start_task(task.id)
        return PlanResult(
            task=updated, stages=planned, human_gates=gates, skipped_stages=skipped
        )

    # ------------------------------------------------------------------ 小工具

    def _assignee_for(self, stage: Any) -> str | None:
        """取配置里该 task_type 允许的第一个 agent（与路由表同源）。"""
        declared = getattr(stage, "agent", None)
        try:
            allowed = list(self.routing.allowed_agents_for(str(stage.task_type)))
        except Exception:  # noqa: BLE001 - 配置异常时退回流水线声明
            return declared
        if declared and (not allowed or declared in allowed):
            return declared
        return allowed[0] if allowed else declared

    @staticmethod
    def _question(stage: Any, objective: str) -> str:
        name = getattr(stage, "name", getattr(stage, "id", "阶段"))
        return f"{name}：{objective}"


__all__ = ["Orchestrator", "PlanResult", "PlannedStage", "STAGE_INPUT_KEYS"]
