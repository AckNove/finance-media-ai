"""task_type 注册表（M2.0 T05）。

**唯一权威在 ``config/agent_routing.yaml``**；本模块是它的 Python 影子，
用于类型安全与"三处一致"校验：

1. ``config/agent_routing.yaml`` 的 ``task_types`` 键集合；
2. 本模块的 ``TaskType`` 枚举值集合；
3. 各 ``agents/<role>/PROMPT.md`` 中声明的"注册表 task_type"行。

三者必须精确相等（前两者）或子集关系（第三者），否则拒绝启动。
数据库侧刻意**不做**枚举 CHECK：``research_subtasks.task_type`` 只校验
"形如 ``domain.action``"的格式。理由是每加一种 task_type 就要迁移一次库，
而注册表本来就应该是可配置的；真正的把关点在启动校验与运行期白名单。
"""

from __future__ import annotations

from enum import Enum

from backend.app.core.errors import DomainValidationError


class TaskType(str, Enum):
    """十种任务类型（九种研究/制作 + 选题发现）。"""

    TOPIC_DISCOVER = "topic.discover"
    RESEARCH_PLAN = "research.plan"
    RESEARCH_COLLECT = "research.collect"
    RESEARCH_EXTRACT = "research.extract"
    RESEARCH_ANALYZE = "research.analyze"
    RESEARCH_SYNTHESIZE = "research.synthesize"
    VERIFY_CLAIMS = "verify.claims"
    CONTENT_SCRIPT = "content.script"
    CONTENT_VISUAL = "content.visual"
    CONTENT_PRODUCE = "content.produce"


#: 注册表键集合（与 YAML 逐项相等）
TASK_TYPES: frozenset[str] = frozenset(member.value for member in TaskType)

#: 需要人工闸门的 task_type（与 agent_routing.yaml 的 human_gate 保持一致）
HUMAN_GATE_TASK_TYPES: frozenset[str] = frozenset(
    {
        TaskType.VERIFY_CLAIMS.value,
        TaskType.CONTENT_SCRIPT.value,
        TaskType.CONTENT_PRODUCE.value,
    }
)


class UnknownTaskTypeError(DomainValidationError):
    """未登记的 task_type（运行期白名单拒绝）。"""

    default_message = "未知的 task_type"

    def __init__(self, task_type: str) -> None:
        super().__init__(
            f"task_type={task_type!r} 未在 config/agent_routing.yaml 登记",
            details={"reason": "unknown_task_type", "task_type": task_type},
        )


def is_registered(task_type: str) -> bool:
    return task_type in TASK_TYPES


def coerce(task_type: str) -> TaskType:
    """把字符串转成枚举；未登记则抛 UnknownTaskTypeError。"""
    try:
        return TaskType(task_type)
    except ValueError as exc:
        raise UnknownTaskTypeError(task_type) from exc


def is_human_gate(task_type: str) -> bool:
    return task_type in HUMAN_GATE_TASK_TYPES


__all__ = [
    "HUMAN_GATE_TASK_TYPES",
    "TASK_TYPES",
    "TaskType",
    "UnknownTaskTypeError",
    "coerce",
    "is_human_gate",
    "is_registered",
]
