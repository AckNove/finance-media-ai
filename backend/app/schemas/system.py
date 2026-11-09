"""系统级 Schema：健康检查与运行状态。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """存活探针：只回答"进程是否活着"，不触碰数据库。"""

    status: str = Field(description="ok 表示进程存活")
    app: str
    version: str


class DatabaseStatus(BaseModel):
    dialect: str
    url: str = Field(description="已脱敏的连接串（不含密码）")
    reachable: bool
    table_count: int | None = None
    migration_revision: str | None = None
    error: str | None = None


class FeatureFlags(BaseModel):
    """第一阶段的能力开关：全部为 false，明确表示"未接入"。"""

    llm_providers: bool
    web_search: bool
    vector_store: bool
    video_rendering: bool


class AgentRosterInfo(BaseModel):
    discovered: int
    roles: list[str]
    agents_dir: str


class SystemStatus(BaseModel):
    app: str
    version: str
    environment: str
    debug: bool
    api_prefix: str
    server_time: datetime
    database: DatabaseStatus
    features: FeatureFlags
    agents: AgentRosterInfo
