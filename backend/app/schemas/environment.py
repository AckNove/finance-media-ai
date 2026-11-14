"""系统设置页的环境体检（只读）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnvCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: str = Field(description="ok / warn / bad")
    detail: str = ""
    fix: str = ""


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    counts: dict[str, int] = Field(default_factory=dict)
    database_url: str = ""
    project_root: str = ""
    checks: list[EnvCheckOut] = Field(default_factory=list)
    note: str = ""


__all__ = ["EnvCheckOut", "EnvironmentOut"]
