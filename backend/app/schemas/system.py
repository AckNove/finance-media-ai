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
    # M2.0 T03：SQLite 运行参数（非 SQLite 或无连接时为 None）
    journal_mode: str | None = Field(default=None, description="实测 PRAGMA journal_mode")
    busy_timeout: str | None = Field(default=None, description="实测 PRAGMA busy_timeout（毫秒）")


class FeatureFlags(BaseModel):
    """能力开关：全部为 false 时明确表示"未接入"。

    前四个由 integrations/video 配置派生；后三个是 M2.0 新增的执行层开关。
    """

    llm_providers: bool
    web_search: bool
    vector_store: bool
    video_rendering: bool
    # M2.0 新增（T02/T03）
    model: bool = Field(default=False, description="模型适配层是否可用（2.0 恒为 false）")
    search: bool = Field(default=False, description="搜索/抓取工具是否可用（2.0 恒为 false）")
    execution: bool = Field(default=False, description="执行引擎是否启用（2.0 恒为 false）")


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
