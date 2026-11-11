"""FastAPI 应用入口。

启动：uvicorn backend.app.main:app --reload
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app import __version__
from backend.app.api.middleware import install_middleware
from backend.app.api.router import build_api_router, build_root_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import install_exception_handlers
from backend.app.core.logging import configure_logging, get_logger
from backend.app.core.startup_checks import assert_startup_ok, run_startup_checks
from backend.app.database.session import dispose_engine

logger = get_logger(__name__)

DESCRIPTION = """
finance-media-ai 第一阶段后端服务。

以个人 IP 为核心的金融知识自媒体研究与内容生产平台，提供：

- 选题管理（topics）
- 知识条目与知识关联（knowledge）
- 资料来源与事实证据（sources / evidence）
- 研究报告（research-reports）
- 系统状态与健康检查（system）

第一阶段能力边界（明确未实现，不伪装）：

- 未接入任何大模型 / 网页搜索 API；
- 未实现语义（向量）检索，知识检索为关键词匹配；
- 未实现视频渲染、配音、字幕生成；
- 内容发布必须人工执行。
"""

TAGS_METADATA = [
    {"name": "system", "description": "健康检查与系统状态"},
    {"name": "topics", "description": "选题：内容生产的起点"},
    {"name": "knowledge", "description": "知识条目、检索与知识关联"},
    {"name": "sources", "description": "原始资料来源登记"},
    {"name": "evidence", "description": "事实证据：把主张追溯到来源"},
    {"name": "research", "description": "研究报告"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    settings.ensure_runtime_dirs()
    logger.info(
        "starting %s v%s (env=%s, database=%s)",
        settings.app_name,
        __version__,
        settings.app_env,
        settings.resolved_database_url(),
    )
    # 启动自检：配置、注册表、端点权限声明不一致时直接拒绝启动
    report = run_startup_checks(app, settings)
    logger.info("启动自检：%s", report.summary())
    app.state.startup_report = report
    assert_startup_ok(report)
    try:
        yield
    finally:
        dispose_engine()
        logger.info("shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )
    app.state.settings = settings

    install_middleware(app)
    install_exception_handlers(app)
    app.include_router(build_root_router())
    app.include_router(build_api_router(settings.api_prefix))

    # 以 /health 为例，方便运维脚本一眼看到服务身份
    @app.get("/", include_in_schema=False)
    def index() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "backend.app.main:app",
        host=_settings.api_host,
        port=_settings.api_port,
        reload=_settings.app_debug,
        log_level=_settings.log_level.lower(),
    )
