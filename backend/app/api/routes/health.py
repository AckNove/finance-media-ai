"""健康检查与系统状态路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.api.deps import AppSettings, require_caller
from backend.app.core.caller import CallerKind
from backend.app.schemas.system import HealthResponse, SystemStatus
from backend.app.services.system_service import build_health, build_system_status

# 根级存活探针：不挂在 /api 前缀下，便于外部监控直接探测；公开端点，不要求身份
health_router = APIRouter(tags=["system"])

# 业务前缀下的系统信息（只对人工操作者开放）
system_router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)


@health_router.get("/health", response_model=HealthResponse, summary="存活探针")
def health(settings: AppSettings) -> HealthResponse:
    """返回进程存活状态。不做数据库访问，避免把探针变成依赖检查。"""
    return build_health(settings)


@system_router.get("/status", response_model=SystemStatus, summary="系统运行状态")
def system_status(settings: AppSettings) -> SystemStatus:
    """返回数据库连通性、迁移版本、能力开关与 Agent 名册等真实运行信息。"""
    return build_system_status(settings)
