"""路由聚合与权限清单（M2.0 T04）。

``ROUTE_CALLERS`` 是每个 HTTP 操作的**权限清单**：``(方法, 路径模板) -> 允许的调用者集合``。
空集合表示公开端点（如存活探针）。启动自检会把它与 ``app.openapi()`` 的实际操作集合
逐项比对：多一个（漏声明权限）、少一个（清单过期）都会拒绝启动。

为什么要清单 + 声明两份：声明（``dependencies=[Depends(require_caller(...))]``）
是运行时真正生效的强制点；清单是"能被静态核对"的契约。少了清单，新增端点可能忘记
声明权限而静默变成公开；少了清单比对，清单会腐烂成装饰品。
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes import (
    health,
    knowledge,
    monitoring,
    research_reports,
    research_tasks,
    sources,
    topics,
    workspace,
)
from backend.app.core.caller import CallerKind

HUMAN_ONLY = frozenset({CallerKind.HUMAN})
PUBLIC: frozenset[CallerKind] = frozenset()

#: 真实存在但**不写入 OpenAPI**的端点。它们同样不碰数据库、不接受任何写操作，
#: 因此显式登记为豁免，而不是靠"OpenAPI 里看不见"来蒙混过关。
#: 新增这类端点必须在这里登记，否则启动自检会报"未声明调用者"。
NON_API_ROUTES: frozenset[tuple[str, str]] = frozenset({("GET", "/")})

#: 每个 HTTP 操作的允许调用者。路径使用 FastAPI 的模板写法（含 {param}）。
ROUTE_CALLERS: dict[tuple[str, str], frozenset[CallerKind]] = {
    ("GET", "/health"): PUBLIC,
    ("GET", "/api/system/status"): HUMAN_ONLY,
    ("POST", "/api/topics"): HUMAN_ONLY,
    ("GET", "/api/topics"): HUMAN_ONLY,
    ("GET", "/api/topics/columns"): HUMAN_ONLY,
    ("GET", "/api/topics/{topic_id}"): HUMAN_ONLY,
    ("PATCH", "/api/topics/{topic_id}"): HUMAN_ONLY,
    ("POST", "/api/knowledge"): HUMAN_ONLY,
    ("GET", "/api/knowledge"): HUMAN_ONLY,
    ("GET", "/api/knowledge/search"): HUMAN_ONLY,
    ("POST", "/api/knowledge/relations"): HUMAN_ONLY,
    ("GET", "/api/knowledge/{item_id}"): HUMAN_ONLY,
    ("PATCH", "/api/knowledge/{item_id}"): HUMAN_ONLY,
    ("GET", "/api/knowledge/{item_id}/relations"): HUMAN_ONLY,
    ("POST", "/api/sources"): HUMAN_ONLY,
    ("GET", "/api/sources"): HUMAN_ONLY,
    ("POST", "/api/evidence"): HUMAN_ONLY,
    ("GET", "/api/evidence"): HUMAN_ONLY,
    ("POST", "/api/research-reports"): HUMAN_ONLY,
    ("GET", "/api/research-reports"): HUMAN_ONLY,
    ("GET", "/api/research-reports/{report_id}"): HUMAN_ONLY,
    ("POST", "/api/research-reports/{report_id}/video-job"): HUMAN_ONLY,
    # M8：研究任务编排/执行（人工建任务，人工推进，人工闸门）
    ("POST", "/api/research-tasks"): HUMAN_ONLY,
    ("GET", "/api/research-tasks"): HUMAN_ONLY,
    ("GET", "/api/research-tasks/{task_id}"): HUMAN_ONLY,
    ("GET", "/api/research-tasks/{task_id}/events"): HUMAN_ONLY,
    ("POST", "/api/research-tasks/{task_id}/run"): HUMAN_ONLY,
    ("POST", "/api/research-tasks/{task_id}/pause"): HUMAN_ONLY,
    ("POST", "/api/research-tasks/{task_id}/resume"): HUMAN_ONLY,
    ("POST", "/api/research-tasks/{task_id}/cancel"): HUMAN_ONLY,
    # M9：运行监控（成本/服务商/名册）
    ("GET", "/api/system/costs"): HUMAN_ONLY,
    ("GET", "/api/system/providers"): HUMAN_ONLY,
    ("GET", "/api/system/agents"): HUMAN_ONLY,
    # M9：工作台（真实搜索、视频任务产物只读、配置快照）
    ("GET", "/api/workspace/search"): HUMAN_ONLY,
    ("GET", "/api/workspace/jobs"): HUMAN_ONLY,
    ("GET", "/api/workspace/jobs/{job_id}"): HUMAN_ONLY,
    ("POST", "/api/workspace/jobs/{job_id}/pipeline"): HUMAN_ONLY,
    ("POST", "/api/workspace/jobs/{job_id}/render"): HUMAN_ONLY,
    ("GET", "/api/workspace/jobs/{job_id}/render"): HUMAN_ONLY,
    ("GET", "/api/workspace/jobs/{job_id}/file"): HUMAN_ONLY,
    ("GET", "/api/workspace/config"): HUMAN_ONLY,
}


def build_root_router() -> APIRouter:
    """根级路由（不带 /api 前缀）：存活探针。"""
    router = APIRouter()
    router.include_router(health.health_router)
    return router


def build_api_router(prefix: str) -> APIRouter:
    """业务路由，统一挂在 /api 前缀下。"""
    router = APIRouter(prefix=prefix)
    router.include_router(health.system_router)
    router.include_router(topics.router)
    router.include_router(knowledge.router)
    router.include_router(sources.sources_router)
    router.include_router(sources.evidence_router)
    router.include_router(research_reports.router)
    router.include_router(research_tasks.router)
    router.include_router(monitoring.router)
    router.include_router(workspace.router)
    return router


__all__ = [
    "HUMAN_ONLY",
    "NON_API_ROUTES",
    "PUBLIC",
    "ROUTE_CALLERS",
    "build_api_router",
    "build_root_router",
]
