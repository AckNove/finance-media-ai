"""整条链路的出参（见 `backend/app/services/content_chain.py`）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChainStatus = Literal["done", "current", "blocked", "missing"]


class ChainStageOut(BaseModel):
    """一段：做完没做完、为什么、下一步点哪儿。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    status: ChainStatus
    detail: str = ""
    #: 只写动作，不写"应该会……"：看得见的一步才配当 next_step。
    next_step: str = ""
    #: 前端深链（`#/xxx`），空串表示这一步没有独立页面。
    link: str = ""


class ContentChainOut(BaseModel):
    """一份报告从研究到发布的七段。"""

    model_config = ConfigDict(extra="forbid")

    report_id: int
    title: str
    stages: list[ChainStageOut] = Field(default_factory=list)
    #: 第一段没做完的 key；全做完为空串。
    current: str = ""
    finished: bool = False
