"""路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes import health, knowledge, research_reports, sources, topics


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
    return router
