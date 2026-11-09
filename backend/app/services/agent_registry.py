"""Agent 名册与统一任务协议（第一阶段：调度接口，不接任何模型）。

设计原则（避免"假装已接通"）：
- 本模块只做三件事：读取角色定义、构造统一协议任务、把任务落库为 AgentRun 记录。
- 不包含任何 LLM / 搜索 / 抓取客户端调用；`RecordingDispatcher` 只登记任务，
  状态停在 pending，输出为空，等下一阶段接入真实执行器。
- 未来接入真实模型时，新增一个 `TaskDispatcher` 子类并在 `get_dispatcher()` 中返回它，
  协议与调用点不变。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DependencyUnavailableError
from backend.app.core.utils import new_task_id, utcnow
from backend.app.models.agent import AgentRun
from backend.app.models.enums import AgentStatus


@dataclass(frozen=True)
class AgentRoleSpec:
    """一个 Agent 角色的静态定义（来自 agents/roster.yaml）。"""

    key: str
    display_name: str
    role: str
    prompt_file: str
    responsibilities: tuple[str, ...] = ()

    @property
    def prompt_path(self) -> Path:
        return Path(self.prompt_file)


@dataclass
class AgentTaskEnvelope:
    """Agent 统一输入/输出协议。

    字段与 AgentRun 表一一对应，便于"协议 → 落库"无损映射。
    """

    task_id: str
    agent_name: str
    task_type: str
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] | None = None
    status: AgentStatus = AgentStatus.PENDING
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "task_type": self.task_type,
            "input": self.input,
            "context": self.context,
            "sources": self.sources,
            "evidence": self.evidence,
            "output": self.output,
            "status": self.status.value,
            "errors": self.errors,
        }


def load_roster(agents_dir: Path | None = None) -> list[AgentRoleSpec]:
    """读取 agents/roster.yaml，并校验每个角色的提示词文件确实存在。"""
    root = agents_dir or get_settings().agents_dir
    roster_path = root / "roster.yaml"
    if not roster_path.is_file():
        return []
    raw = yaml.safe_load(roster_path.read_text(encoding="utf-8")) or {}
    specs: list[AgentRoleSpec] = []
    for entry in raw.get("agents", []):
        prompt_file = entry.get("prompt_file", "")
        specs.append(
            AgentRoleSpec(
                key=entry["key"],
                display_name=entry.get("display_name", entry["key"]),
                role=entry.get("role", ""),
                prompt_file=str(root / prompt_file) if prompt_file else "",
                responsibilities=tuple(entry.get("responsibilities", []) or ()),
            )
        )
    return specs


def roster_keys_missing_prompts(agents_dir: Path | None = None) -> list[str]:
    """返回提示词文件缺失的角色 key（用于健康检查与测试）。"""
    missing: list[str] = []
    for spec in load_roster(agents_dir):
        if not spec.prompt_file or not Path(spec.prompt_file).is_file():
            missing.append(spec.key)
    return missing


class TaskDispatcher(ABC):
    """调度器接口：把统一协议任务交给某个执行器。"""

    @abstractmethod
    def dispatch(self, task: AgentTaskEnvelope) -> AgentTaskEnvelope:  # pragma: no cover - 接口
        raise NotImplementedError


class RecordingDispatcher(TaskDispatcher):
    """第一阶段实现：只登记任务，不执行。

    它会真实写入 agent_runs 记录（status=pending、output=NULL），
    便于后续接入执行器后按 task_id 回填结果；不会编造任何输出。
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def dispatch(self, task: AgentTaskEnvelope) -> AgentTaskEnvelope:
        if not task.task_id:
            task.task_id = new_task_id()
        run = AgentRun(
            task_id=task.task_id,
            agent_name=task.agent_name,
            task_type=task.task_type,
            status=AgentStatus.PENDING,
            input_payload=task.input,
            context_payload=task.context,
            sources=task.sources,
            evidence=task.evidence,
            error_payload=task.errors or None,
            requested_at=utcnow(),
        )
        self.db.add(run)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        task.status = AgentStatus.PENDING
        return task


class UnavailableDispatcher(TaskDispatcher):
    """未配置执行器时使用：显式失败，绝不返回伪造结果。"""

    def __init__(self, reason: str = "第一阶段未接入任何模型或搜索服务") -> None:
        self.reason = reason

    def dispatch(self, task: AgentTaskEnvelope) -> AgentTaskEnvelope:  # pragma: no cover
        raise DependencyUnavailableError(
            f"Agent 执行器不可用：{self.reason}",
            details={"agent_name": task.agent_name, "task_type": task.task_type},
        )


def get_dispatcher(db: Session, settings: Settings | None = None) -> TaskDispatcher:
    """返回当前可用的调度器。

    第一阶段固定返回 RecordingDispatcher：真实落库、不真实执行。
    """
    _ = settings or get_settings()
    return RecordingDispatcher(db)
