"""配置文件的结构校验（M2.0 T02）。

设计原则：
- YAML 是业务配置的唯一权威，环境变量只做有限覆盖（见 ``Settings.cost_daily_cap_cny``）；
- 这里是"结构 + 取值范围"校验，不含业务逻辑；
- 全部模型 ``extra="forbid"``：拼错的键会立刻失败，而不是被静默忽略。

为什么不用 Settings 承载这些配置：
Settings 来自环境变量，适合部署差异；模型价目、任务类型、流水线属于业务配置，
要能被 code review 与版本管理，所以放在 ``config/*.yaml``。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRICT = ConfigDict(extra="forbid")

PricingPolicy = Literal["reject_unknown", "allow_with_upper_bound"]


# --------------------------------------------------------------------------
# config/settings.yaml 的三段（cost / execution / features）
# --------------------------------------------------------------------------


class CostSettings(BaseModel):
    """成本闸门配置。所有金额单位与 ``currency`` 一致。"""

    model_config = STRICT

    currency: str = Field(default="CNY", min_length=3, max_length=3)
    daily_cap_cny: float = Field(gt=0, description="当日总预算上限")
    default_task_calls: int = Field(ge=1, description="单任务默认调用次数上限")
    default_task_cost_cny: float = Field(gt=0, description="单任务默认金额上限")
    unknown_price_upper_bound_cny: float = Field(gt=0, description="价目未知时的单次预留上界")
    pricing_policy: PricingPolicy = "reject_unknown"

    @model_validator(mode="after")
    def _check_policy_consistency(self) -> CostSettings:
        # Q8=B：默认拒绝；若有人把策略改成 allow_with_upper_bound，
        # 必须同时确认上界存在（否则等于"0 元硬跑"）。
        if self.pricing_policy == "allow_with_upper_bound" and not (
            self.unknown_price_upper_bound_cny > 0
        ):  # pragma: no cover - 由 Field(gt=0) 兜底
            raise ValueError("allow_with_upper_bound 需要 positive unknown_price_upper_bound_cny")
        if self.default_task_cost_cny > self.daily_cap_cny:
            raise ValueError("default_task_cost_cny 不得大于 daily_cap_cny")
        return self


class ExecutionSettings(BaseModel):
    """执行层参数（DB 队列 + 租约）。"""

    model_config = STRICT

    enabled: bool = False
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    max_concurrency: int = Field(default=1, ge=1, le=1, description="本地 SQLite 固定串行")
    lease_seconds: int = Field(default=300, ge=30)
    heartbeat_seconds: int = Field(default=100, ge=10)
    dev_tick_enabled: bool = False

    @model_validator(mode="after")
    def _check_heartbeat(self) -> ExecutionSettings:
        if self.heartbeat_seconds * 2 > self.lease_seconds:
            raise ValueError("heartbeat_seconds 必须小于 lease_seconds 的一半，否则会误判失效")
        return self


class FeaturesSettings(BaseModel):
    """M2.0 新增的三个能力开关（其余四个由 integrations/video 段派生）。"""

    model_config = STRICT

    model: bool = False
    search: bool = False
    execution: bool = False


# --------------------------------------------------------------------------
# config/models.yaml
# --------------------------------------------------------------------------


class ApprovedModelException(BaseModel):
    """价目未知但经人工批准可调用的例外（Q8=B 的唯一入口）。"""

    model_config = STRICT

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    max_cost_per_call_cny: float = Field(gt=0)
    total_budget_cny: float = Field(gt=0)
    approved_by: str = Field(min_length=1)
    approved_at: str = Field(min_length=1)
    expires_at: str | None = None


class ModelPricingSettings(BaseModel):
    """models.yaml 的 cost 段。"""

    model_config = STRICT

    currency: str = Field(default="CNY", min_length=3, max_length=3)
    pricing_policy: PricingPolicy = "reject_unknown"
    approved_exceptions: list[ApprovedModelException] = Field(default_factory=list)
    cache_ttl_hours: int = Field(default=24, ge=0)


class ProviderSettings(BaseModel):
    model_config = STRICT

    display_name: str = Field(min_length=1)
    base_url_env: str | None = None
    api_key_env: str | None = None
    enabled: bool = False


class ModelSettings(BaseModel):
    """单个模型的价目条目。价格为空表示"未知"，不得当作 0。"""

    model_config = STRICT

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    display_name: str | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    input_price_per_1k_cny: float | None = Field(default=None, ge=0)
    output_price_per_1k_cny: float | None = Field(default=None, ge=0)
    enabled: bool = False
    # 价目出处：谁核的、什么时候核的、按什么口径换算的。
    # 这三项不是装饰——价格会变，没有出处的数字等于没核过（报告里要能追）。
    price_source: str | None = None
    price_checked_at: str | None = None
    price_basis: str | None = None
    # 本地限流：官方并发上限之外，自己在进程内先摁住节奏，避免 429 才回头重试。
    requests_per_minute: int | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, ge=1)
    note: str | None = None

    @property
    def pricing_status(self) -> Literal["known", "missing"]:
        if self.input_price_per_1k_cny is None or self.output_price_per_1k_cny is None:
            return "missing"
        return "known"

    @property
    def price_provenance(self) -> dict[str, str]:
        """价目出处摘要；缺项写空串，由调用方决定是否提示"未复核"。"""
        return {
            "source": self.price_source or "",
            "checked_at": self.price_checked_at or "",
            "basis": self.price_basis or "",
        }


class ModelsConfig(BaseModel):
    model_config = STRICT

    version: int = Field(ge=1)
    updated_at: str
    cost: ModelPricingSettings
    providers: dict[str, ProviderSettings] = Field(min_length=1)
    models: list[ModelSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_model_providers(self) -> ModelsConfig:
        seen: set[tuple[str, str]] = set()
        for entry in self.models:
            if entry.provider not in self.providers:
                raise ValueError(f"模型 {entry.provider}/{entry.model} 的 provider 未在 providers 中登记")
            key = (entry.provider, entry.model)
            if key in seen:
                raise ValueError(f"模型条目重复: {entry.provider}/{entry.model}")
            seen.add(key)
        for exception in self.cost.approved_exceptions:
            if (exception.provider, exception.model) not in seen:
                raise ValueError(
                    f"approved_exceptions 指向未登记模型: {exception.provider}/{exception.model}"
                )
        return self

    def find_model(self, provider: str, model: str) -> ModelSettings | None:
        for entry in self.models:
            if entry.provider == provider and entry.model == model:
                return entry
        return None


# --------------------------------------------------------------------------
# config/agent_routing.yaml
# --------------------------------------------------------------------------


class TaskTypeSpec(BaseModel):
    """单个 task_type 的登记项。"""

    model_config = STRICT

    display_name: str = Field(min_length=1)
    description: str | None = None
    allowed_agents: list[str] = Field(min_length=1)
    tools: list[str] = Field(min_length=1)
    writes: list[str] = Field(default_factory=list)
    human_gate: bool = False


class AgentToolSpec(BaseModel):
    """单个 Agent 的工具白名单与数据域。"""

    model_config = STRICT

    tools: list[str] = Field(min_length=1)
    db_scopes: list[str] = Field(default_factory=list)


class AgentRoutingConfig(BaseModel):
    model_config = STRICT

    version: int = Field(ge=1)
    updated_at: str
    default_deny: bool = True
    task_types: dict[str, TaskTypeSpec] = Field(min_length=1)
    agents: dict[str, AgentToolSpec] = Field(min_length=1)

    def allowed_agents_for(self, task_type: str) -> frozenset[str]:
        spec = self.task_types.get(task_type)
        return frozenset(spec.allowed_agents) if spec else frozenset()

    def tools_for(self, agent: str) -> frozenset[str]:
        spec = self.agents.get(agent)
        return frozenset(spec.tools) if spec else frozenset()

    def db_scopes_for(self, agent: str) -> frozenset[str]:
        spec = self.agents.get(agent)
        return frozenset(spec.db_scopes) if spec else frozenset()


# --------------------------------------------------------------------------
# config/pipelines.yaml
# --------------------------------------------------------------------------


class PipelineStage(BaseModel):
    model_config = STRICT

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["task", "gate"]
    task_type: str | None = None
    agent: str | None = None
    produces: list[str] = Field(default_factory=list)
    human_gate: bool = False

    @model_validator(mode="after")
    def _check_kind_fields(self) -> PipelineStage:
        if self.kind == "task":
            if not self.task_type:
                raise ValueError(f"任务阶段 {self.id} 必须声明 task_type")
            # 任务阶段允许 human_gate=true：表示"该任务的产物必须经人工确认才能流转"，
            # 例如 fact_check（Agent 执行 + 专人复核）。纯人工闸门请用 kind: gate。
        else:
            if not self.human_gate:
                raise ValueError(f"闸门阶段 {self.id} 必须设置 human_gate: true")
            if self.task_type:
                raise ValueError(f"闸门阶段 {self.id} 不应声明 task_type")
        return self


class PipelineSpec(BaseModel):
    model_config = STRICT

    display_name: str = Field(min_length=1)
    description: str | None = None
    stages: list[PipelineStage] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_stage_ids(self) -> PipelineSpec:
        ids = [stage.id for stage in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError("同一流水线内 stage id 必须唯一")
        return self


class PipelinesConfig(BaseModel):
    model_config = STRICT

    version: int = Field(ge=1)
    updated_at: str
    pipelines: dict[str, PipelineSpec] = Field(min_length=1)


__all__ = [
    "AgentRoutingConfig",
    "AgentToolSpec",
    "ApprovedModelException",
    "CostSettings",
    "ExecutionSettings",
    "FeaturesSettings",
    "ModelPricingSettings",
    "ModelSettings",
    "ModelsConfig",
    "PipelineSpec",
    "PipelineStage",
    "PipelinesConfig",
    "PricingPolicy",
    "ProviderSettings",
    "TaskTypeSpec",
]
