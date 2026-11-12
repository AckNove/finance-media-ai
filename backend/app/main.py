"""FastAPI 应用入口。

启动：uvicorn backend.app.main:app --reload
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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

- 模型网关已接入 OpenAI 兼容协议，但没有密钥/价目时**拒绝调用**并如实报 blocked；
- 网页搜索与抓取已实现（搜狗/360/bing 降级 + SSRF/robots 闸门），真实联网可用；
- 视频渲染走 FFmpeg 8.1.2 与 OpenKatana sidecar，分镜/时间轴/质检链路可用；
- 内容发布必须人工执行。
"""

TAGS_METADATA = [
    {"name": "system", "description": "健康检查、系统状态与运行监控"},
    {"name": "topics", "description": "选题：内容生产的起点"},
    {"name": "knowledge", "description": "知识条目、检索与知识关联"},
    {"name": "sources", "description": "原始资料来源登记"},
    {"name": "evidence", "description": "事实证据：把主张追溯到来源"},
    {"name": "research", "description": "研究报告"},
    {"name": "research-tasks", "description": "研究任务编排、执行与人工闸门"},
    {"name": "monitoring", "description": "成本统计、模型服务商与 Agent 名册"},
    {"name": "workspace", "description": "工作台：真实搜索、视频任务产物、配置快照"},
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


def _mount_web_console(app: FastAPI) -> None:
    """把前端工作台挂到 /app（纯静态，无需 Node 构建）。

    前端只调用 ``/api/*``，不直接读文件；因此这里挂载的只是壳与脚本，
    数据一律来自真实接口。目录不存在就跳过，不让后端因为缺前端而起不来。
    """
    from fastapi.staticfiles import StaticFiles

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.is_dir():
        app.mount("/app", StaticFiles(directory=web_dir, html=True), name="web")


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
    _mount_web_console(app)

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
