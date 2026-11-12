"""模型能力包：provider 解析、token 估算与统一网关（V5.0 M2）。

对外只暴露三样东西：
- ``providers``：密钥只来自环境变量，缺失即 ``not_configured``；
- ``tokens``：中文按 1.5 token/字、ASCII 按 3.5 字符/token 估算；
- ``gateway``：唯一真实调用入口，价目未知一律拒绝，绝不 Fake。

导入顺序说明：``gateway`` 内部对数据库与模型层是**延迟导入**的，避免
``capabilities.model`` ↔ ``models`` ↔ ``database`` 的循环引用。
"""

from __future__ import annotations

from backend.app.capabilities.model.gateway import (
    BudgetContext,
    CallRecord,
    GatewayStatus,
    LocalRateLimitError,
    ModelGateway,
    ModelReply,
    ModelRequest,
    get_model_gateway,
    reset_model_gateway,
)
from backend.app.capabilities.model.providers import (
    DEFAULT_BASE_URLS,
    KEYLESS_PROVIDERS,
    ProviderError,
    ResolvedProvider,
    mask_secret,
    provider_status,
    resolve_provider,
)
from backend.app.capabilities.model.tokens import (
    ASCII_CHARS_PER_TOKEN,
    CJK_TOKENS_PER_CHAR,
    cost_cny,
    estimate_prompt_tokens,
    estimate_tokens,
)

__all__ = [
    "ASCII_CHARS_PER_TOKEN",
    "BudgetContext",
    "CJK_TOKENS_PER_CHAR",
    "CallRecord",
    "DEFAULT_BASE_URLS",
    "GatewayStatus",
    "LocalRateLimitError",
    "KEYLESS_PROVIDERS",
    "ModelGateway",
    "ModelReply",
    "ModelRequest",
    "ProviderError",
    "ResolvedProvider",
    "cost_cny",
    "estimate_prompt_tokens",
    "estimate_tokens",
    "get_model_gateway",
    "mask_secret",
    "provider_status",
    "reset_model_gateway",
    "resolve_provider",
]
