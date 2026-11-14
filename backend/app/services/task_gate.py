"""人工闸门："任务为什么停在这儿、人该看什么、看完点哪里"。

闸门本身由编排器写进子任务的 ``input_payload``（``require_human_review`` +
``human_gate``），worker 照它把任务 ``pause``。所以这里**不猜**——只读那两条字段，
再配一句人话。以前界面上只显示"任务 paused"，人得自己去想是哪个门、看什么。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 闸门 id -> 一句"看完点哪里"。没登记的 id 会退回通用话术。
GATE_STEPS: dict[str, str] = {
    "topic_approval": (
        "看完「这一步产出了什么」里的选题拆解，觉得题目立得住，就点「继续跑」；"
        "觉得不行就改选题或取消任务。"
    ),
    "fact_check_gate": (
        "这一步之后要逐条核对结论与原文。看完「这一步产出了什么」，"
        "再去「语义核查」页逐条确认，确认之后才点「继续跑」。"
    ),
    "verify_claims": (
        "看完「语义核查」页的逐条判定与引用片段，逐条确认或退回；"
        "确认之后才点「继续跑」，成稿闸门只认人工确认过的结论。"
    ),
    "script_approval": "看完「脚本编辑」页的稿子，改成你要的样子再放行。",
    "content_review": "看完片子再放行；不满意就回分镜页改，别在这里点继续。",
}

#: 通用话术：闸门 id 没登记时用
GENERIC_STEP = "看完产物再点「继续跑」；不想继续就取消任务。"


@dataclass(frozen=True)
class GateInfo:
    """一道挡住任务的人工闸门。"""

    gate_id: str
    label: str = ""
    subtask_id: int | None = None
    after_sequence: int | None = None
    next_step: str = field(default=GENERIC_STEP)

    def as_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "label": self.label,
            "subtask_id": self.subtask_id,
            "after_sequence": self.after_sequence,
            "next_step": self.next_step,
        }


def _stage_labels() -> dict[str, str]:
    """``config/pipelines.yaml`` 里闸门阶段的 id → 名字（人工立项 / 结论核查……）。"""

    try:
        from backend.app.core.config import get_pipelines_config
    except Exception:  # pragma: no cover - 配置层坏掉时不影响任务详情
        return {}
    try:
        pipelines = get_pipelines_config()
    except Exception:  # pragma: no cover - 同上
        return {}
    labels: dict[str, str] = {}
    for pipeline in pipelines.pipelines.values():
        for stage in pipeline.stages:
            if stage.kind == "gate":
                labels.setdefault(stage.id, stage.name)
    return labels


def gate_of(subtasks, *, task_status: str = "") -> GateInfo | None:
    """找出**刚刚挡住任务的那道门**。

    编排器把闸门标记写在**被闸门挡住的那一格**上（做完这一格 → 停下来等人），
    不是写在下一格上。所以找"已成功、带 ``require_human_review``、序号最大"的那一格。
    任务没停在 paused 时一律返回 ``None``——不然会拿一道早就过去的门当现状。
    """

    if str(task_status) != "paused":
        return None

    waiting = None
    for subtask in subtasks:
        payload = subtask.input_payload if isinstance(subtask.input_payload, dict) else {}
        if not payload.get("require_human_review"):
            continue
        status_text = str(getattr(subtask.status, "value", subtask.status))
        if status_text != "succeeded":
            continue
        sequence = int(getattr(subtask, "sequence", 0) or 0)
        if waiting is None or sequence > waiting[0]:
            waiting = (sequence, subtask, payload)

    if waiting is None:
        return None

    _, subtask, payload = waiting
    gate = payload.get("human_gate") or {}
    gate_id = str(gate.get("id", "")) if isinstance(gate, dict) else ""
    gate_name = str(gate.get("name", "")) if isinstance(gate, dict) else ""
    if not gate_id:
        gate_id = "unnamed"
    label = gate_name or _stage_labels().get(gate_id, "")
    return GateInfo(
        gate_id=gate_id,
        label=label,
        subtask_id=int(getattr(subtask, "id", 0) or 0) or None,
        after_sequence=int(getattr(subtask, "sequence", 0) or 0) or None,
        next_step=GATE_STEPS.get(gate_id, GENERIC_STEP),
    )


__all__ = ["GATE_STEPS", "GENERIC_STEP", "GateInfo", "gate_of"]
