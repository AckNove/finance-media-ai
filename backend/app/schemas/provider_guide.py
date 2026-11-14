"""服务商设置页：缺哪个变量、写在哪、写完怎么验。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProviderGuideOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str = ""
    enabled: bool = False
    status: str = ""
    note: str = ""
    key_env: str = ""
    base_url_env: str = ""
    base_url: str = ""
    key_in_process: bool = False
    key_in_env_file: bool = False
    env_file: str = ""
    env_file_exists: bool = False
    models: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    verify_command: str = ""
    ready: bool = False


class ProvidersGuideOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env_file: str = ""
    env_file_exists: bool = False
    example_file: str = ""
    models_file: str = ""
    restart_hint: str = ""
    note: str = ""
    items: list[ProviderGuideOut] = Field(default_factory=list)


__all__ = ["ProviderGuideOut", "ProvidersGuideOut"]
