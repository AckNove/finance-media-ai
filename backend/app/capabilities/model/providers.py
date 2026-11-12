"""模型 Provider 的解析：把 ``config/models.yaml`` 的声明变成可调用的 HTTP 端点。

设计约束（V5.0）：

1. **密钥只来自环境变量**。本模块从不写、从不打印密钥；``ResolvedProvider``
   只保留掩码后的形式用于日志与监控页面。
2. **没配置就是没配置**。缺 base_url 或（需要密钥的 provider）缺 api_key 时抛
   ``ProviderError``，调用方必须把它变成 ``BLOCKED`` 状态，**不许回退到 Fake 模型**。
3. **OpenAI 兼容协议**是唯一实现：DeepSeek、OpenAI、vLLM、Ollama、LM Studio
   都提供 ``POST {base_url}/chat/completions``。差异只在 base_url 与是否需要密钥。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping

from backend.app.core.config import get_models_config
from backend.app.core.config_schema import ModelsConfig

#: 未在 YAML 里给 base_url_env 明确的默认值（仅作为 env 缺失时的兜底）
DEFAULT_BASE_URLS: Mapping[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "local": "http://127.0.0.1:11434/v1",
}

#: 这些 provider 允许没有 api_key（本地推理服务）
KEYLESS_PROVIDERS: frozenset[str] = frozenset({"local"})

ProviderStatus = Literal["ready", "missing_base_url", "missing_api_key", "disabled", "unknown"]


class ProviderError(RuntimeError):
    """Provider 不可用。``reason`` 是稳定错误码，供 API 与前端展示。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


def mask_secret(value: str | None) -> str:
    """把密钥变成可安全展示的形式。

    只保留长度和最多 3 位前缀（够判断"是不是配错了类型的密钥"），
    **不保留尾部字符**：尾部和前缀合起来足以让人在小圈子里对号入座，
    而展示密钥的任何一段对排查都没有帮助。
    """
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}***（len={len(value)}）"


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """一个可用 provider 的解析结果。"""

    name: str
    display_name: str
    base_url: str
    api_key: str | None
    timeout_seconds: float = 120.0

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def needs_key(self) -> bool:
        return self.name not in KEYLESS_PROVIDERS

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "api_key": mask_secret(self.api_key),
            "needs_key": self.needs_key,
            "timeout_seconds": self.timeout_seconds,
        }


def _env(environ: Mapping[str, str] | None, key: str | None) -> str | None:
    source = os.environ if environ is None else environ
    if not key:
        return None
    value = source.get(key)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def resolve_provider(
    name: str,
    *,
    models_config: ModelsConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedProvider:
    """解析 provider；不可用时抛 ``ProviderError``（绝不返回半可用对象）。"""
    config = models_config or get_models_config()
    settings = config.providers.get(name)
    if settings is None:
        raise ProviderError("unknown_provider", f"provider {name} 未在 config/models.yaml 登记")
    if not settings.enabled:
        raise ProviderError(
            "provider_disabled",
            f"provider {name} 未启用（config/models.yaml 的 providers.{name}.enabled=false）",
        )
    base_url = _env(environ, settings.base_url_env) or DEFAULT_BASE_URLS.get(name)
    if not base_url:
        raise ProviderError(
            "missing_base_url",
            f"provider {name} 缺少 base_url：请设置环境变量 {settings.base_url_env or '（未声明）'}",
        )
    api_key = _env(environ, settings.api_key_env) if settings.api_key_env else None
    if api_key is None and name not in KEYLESS_PROVIDERS:
        raise ProviderError(
            "missing_api_key",
            f"provider {name} 缺少密钥：请设置环境变量 {settings.api_key_env or '（未声明）'}",
        )
    return ResolvedProvider(
        name=name,
        display_name=settings.display_name,
        base_url=base_url,
        api_key=api_key,
    )


def provider_status(
    *,
    models_config: ModelsConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    """给监控页面用的 provider 状态快照（不暴露密钥明文）。"""
    config = models_config or get_models_config()
    report: dict[str, dict[str, object]] = {}
    for name, settings in config.providers.items():
        entry: dict[str, object] = {
            "display_name": settings.display_name,
            "enabled": settings.enabled,
            "api_key_env": settings.api_key_env,
            "base_url_env": settings.base_url_env,
        }
        try:
            resolved = resolve_provider(name, models_config=config, environ=environ)
        except ProviderError as error:
            entry["status"] = error.reason
            entry["message"] = error.message
        else:
            entry["status"] = "ready"
            entry["message"] = ""
            entry["base_url"] = resolved.base_url
            entry["api_key"] = mask_secret(resolved.api_key)
        report[name] = entry
    return report


__all__ = [
    "DEFAULT_BASE_URLS",
    "KEYLESS_PROVIDERS",
    "ProviderError",
    "ProviderStatus",
    "ResolvedProvider",
    "mask_secret",
    "provider_status",
    "resolve_provider",
]
