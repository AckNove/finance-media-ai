"""应用配置：环境变量 + YAML 配置文件的统一入口。

分层原则：
- 敏感信息（密钥、密码、连接串中的凭据）只允许来自环境变量 / .env；
- 非敏感的应用与业务配置来自 config/*.yaml；
- 所有路径统一基于项目根目录解析，避免受当前工作目录影响。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] == 项目根目录
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR: Path = PROJECT_ROOT / "config"


class Settings(BaseSettings):
    """环境变量配置（前缀无强制要求，字段名即环境变量名，大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- 运行环境 ---
    app_name: str = "finance-media-ai"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = True
    log_level: str = "INFO"

    # --- 数据库 ---
    database_url: str = "sqlite:///./data/finance_media.db"
    database_echo: bool = False

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_prefix: str = "/api"

    # --- 路径（默认基于项目根目录） ---
    config_dir: Path = DEFAULT_CONFIG_DIR
    data_dir: Path = PROJECT_ROOT / "data"
    agents_dir: Path = PROJECT_ROOT / "agents"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    content_dir: Path = PROJECT_ROOT / "content"
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    video_engine_dir: Path = PROJECT_ROOT / "video_engine"

    # --- 分页 ---
    page_default_limit: int = Field(default=20, ge=1, le=200)
    page_max_limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalise_env(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url().startswith("sqlite")

    def resolved_database_url(self) -> str:
        """把 sqlite 相对路径转换为绝对路径，保证与工作目录无关。"""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw = url[len(prefix) :]
            if raw in ("", ":memory:") or Path(raw).is_absolute():
                return url
            return f"{prefix}{(PROJECT_ROOT / raw).resolve().as_posix()}"
        return url

    def sqlite_file_path(self) -> Path | None:
        """返回 SQLite 数据库文件路径；非 SQLite 或内存库返回 None。"""
        url = self.resolved_database_url()
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            return None
        raw = url[len(prefix) :]
        if raw in ("", ":memory:"):
            return None
        return Path(raw)

    def ensure_runtime_dirs(self) -> None:
        """确保运行期需要的目录存在（数据库文件所在目录等）。"""
        db_path = self.sqlite_file_path()
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    """测试用：清空配置与 YAML 缓存。"""
    get_settings.cache_clear()
    load_yaml_config.cache_clear()


def resolve_project_path(value: str | Path) -> Path:
    """把可能是相对路径的值解析为项目根目录下的绝对路径。"""
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@functools.lru_cache(maxsize=16)
def load_yaml_config(filename: str) -> dict[str, Any]:
    """读取 config/ 下的 YAML 配置文件；缺失或格式错误时抛出明确异常。"""
    path = get_settings().config_dir / filename
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是映射结构: {path}")
    return data


def get_app_config() -> dict[str, Any]:
    """config/settings.yaml"""
    return load_yaml_config("settings.yaml")


def get_brand_config() -> dict[str, Any]:
    """config/brand.yaml"""
    return load_yaml_config("brand.yaml")


def get_content_rules() -> dict[str, Any]:
    """config/content_rules.yaml"""
    return load_yaml_config("content_rules.yaml")


def get_research_sources() -> dict[str, Any]:
    """config/research_sources.yaml"""
    return load_yaml_config("research_sources.yaml")
