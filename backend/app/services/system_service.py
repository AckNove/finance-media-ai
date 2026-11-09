"""系统状态服务：健康检查需要用真实探测，而不是硬编码字符串。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from backend.app import __version__
from backend.app.core.config import Settings, get_app_config, get_settings
from backend.app.core.utils import utcnow
from backend.app.database.session import get_engine
from backend.app.schemas.system import (
    AgentRosterInfo,
    DatabaseStatus,
    FeatureFlags,
    HealthResponse,
    SystemStatus,
)
from backend.app.services.agent_registry import load_roster

MASKED = "***"


def _masked_url(settings: Settings) -> str:
    """返回脱敏连接串：绝不把密码写进接口响应。"""
    url = settings.resolved_database_url()
    try:
        parsed = make_url(url)
    except Exception:  # pragma: no cover - 非法 URL 时退回文件名
        return "<unparsable-database-url>"
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database or ""
        return f"sqlite:///{Path(database).name}" if database else "sqlite://"
    return parsed.render_as_string(hide_password=True)


def build_health(settings: Settings | None = None) -> HealthResponse:
    settings = settings or get_settings()
    return HealthResponse(status="ok", app=settings.app_name, version=__version__)


def build_system_status(settings: Settings | None = None) -> SystemStatus:
    settings = settings or get_settings()
    app_config = get_app_config()
    integrations = app_config.get("integrations", {}) if isinstance(app_config, dict) else {}

    database = DatabaseStatus(
        dialect="unknown", url=_masked_url(settings), reachable=False, error=None
    )
    engine = get_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            inspector = inspect(connection)
            tables = inspector.get_table_names()
            database.dialect = connection.dialect.name
            database.reachable = True
            database.table_count = len(tables)
            if "alembic_version" in tables:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                database.migration_revision = revision
    except Exception as exc:  # 数据库不可用不应让状态接口 500
        database.reachable = False
        database.error = f"{type(exc).__name__}: {exc}"

    roster = load_roster(settings.agents_dir)

    return SystemStatus(
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        debug=settings.app_debug,
        api_prefix=settings.api_prefix,
        server_time=utcnow(),
        database=database,
        features=FeatureFlags(
            llm_providers=bool(integrations.get("llm", {}).get("enabled", False)),
            web_search=bool(integrations.get("web_search", {}).get("enabled", False)),
            vector_store=bool(integrations.get("vector_store", {}).get("enabled", False)),
            video_rendering=bool(app_config.get("video", {}).get("rendering_enabled", False)),
        ),
        agents=AgentRosterInfo(
            discovered=len(roster),
            roles=[spec.key for spec in roster],
            agents_dir=str(settings.agents_dir),
        ),
    )
