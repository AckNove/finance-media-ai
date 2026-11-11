"""L2 执行层（M2.0 只落骨架：task_type 注册表与路由校验）。

本阶段**没有**真正的执行器：`TaskRuntime` / `Orchestrator` / `AgentExecutor`
计划在 2.4 落地。现在这个包里能用的只有两件事：

1. `task_types`：十种 task_type 的枚举与人工闸门集合，启动自检与路由校验的权威之一；
2. `routing`：把 `config/agent_routing.yaml` 读成路由表，并校验三处（YAML / 枚举 /
   各角色 `PROMPT.md` 声明）互相一致。

刻意不导出任何"看起来能跑"的东西：没有 `run_task()`、没有队列、没有 worker。
"""

from backend.app.execution import routing, task_types
from backend.app.execution.task_types import (
    HUMAN_GATE_TASK_TYPES,
    TASK_TYPES,
    TaskType,
    UnknownTaskTypeError,
    coerce,
    is_human_gate,
    is_registered,
)

__all__ = [
    "HUMAN_GATE_TASK_TYPES",
    "TASK_TYPES",
    "TaskType",
    "UnknownTaskTypeError",
    "coerce",
    "is_human_gate",
    "is_registered",
    "routing",
    "task_types",
]
