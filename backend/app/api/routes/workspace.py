"""工作台接口：真实搜索、视频任务产物（磁盘只读）、配置快照。

为什么这些要放在 API 里而不是让前端读文件：
1. **浏览器读不到文件系统**。分镜 CSV、素材清单、成片 MP4 必须由服务端提供。
2. **路径必须收口**。``content/jobs/<job_id>/`` 之外的路径一律拒绝，避免变成任意文件读取。
3. **只读**。这些端点不生成、不修改产物——产物由 ``build_phase2.py`` 之类的构建脚本产出，
   API 只负责把"已经存在的事实"呈现出来。没有产物就如实返回空，不造数据。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Path as PathParam, Query
from fastapi.responses import FileResponse

from backend.app.api.deps import COMMON_RESPONSES, require_caller
from backend.app.capabilities.search.engine import SearchQuery, SearchUnavailable, search
from backend.app.core.caller import CallerKind
from backend.app.core.config import (
    PROJECT_ROOT,
    get_cost_settings,
    get_execution_settings,
    get_features_settings,
    get_models_config,
)
from backend.app.core.errors import DomainValidationError, NotFoundError

router = APIRouter(
    prefix="/workspace",
    tags=["workspace"],
    responses=COMMON_RESPONSES,
    dependencies=[Depends(require_caller(CallerKind.HUMAN))],
)

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4a", ".wav", ".png", ".jpg", ".csv", ".md", ".json"}
MAX_PREVIEW_ROWS = 200


def _resolve_inside(candidate: Path, root: Path) -> Path:
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise DomainValidationError("路径越界，已拒绝", details={"reason": "path_escape"})
    return resolved


def _read_csv(path: Path, limit: int = MAX_PREVIEW_ROWS) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows[:limit],
        "total": len(rows),
        "truncated": len(rows) > limit,
    }


def _job_summary(job_dir: Path) -> dict[str, Any]:
    output_dir = job_dir / "output"
    videos = (
        sorted(
            (item for item in output_dir.glob("*") if item.suffix.lower() == ".mp4"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if output_dir.is_dir()
        else []
    )
    return {
        "job_id": job_dir.name,
        "has_output": bool(videos),
        "outputs": [
            {"name": item.name, "byte_size": item.stat().st_size} for item in videos
        ],
        "artifacts": {
            name: (job_dir / name).exists()
            for name in (
                "storyboard/storyboard.csv",
                "assets_manifest.csv",
                "source_register.csv",
                "visual_direction.md",
                "qc/qc_report.md",
                "script/script.md",
                "project/timeline.json",
            )
        },
    }


@router.get("/jobs", summary="视频任务目录（只读枚举 content/jobs）")
def list_jobs() -> dict[str, Any]:
    if not JOBS_ROOT.is_dir():
        return {"root": "content/jobs", "jobs": []}
    jobs = [
        _job_summary(child)
        for child in sorted(JOBS_ROOT.iterdir())
        if child.is_dir() and not child.name.startswith(".")
    ]
    return {"root": "content/jobs", "jobs": jobs}


@router.get("/jobs/{job_id}", summary="视频任务详情（分镜/素材/质检/脚本）")
def get_job(job_id: str = PathParam(min_length=1, max_length=80)) -> dict[str, Any]:
    job_dir = _resolve_inside(JOBS_ROOT / job_id, JOBS_ROOT)
    if not job_dir.is_dir():
        raise NotFoundError(f"任务目录不存在：{job_id}", details={"reason": "unknown_job"})
    summary = _job_summary(job_dir)
    payload: dict[str, Any] = {"summary": summary}

    storyboard = job_dir / "storyboard" / "storyboard.csv"
    if storyboard.is_file():
        payload["storyboard"] = _read_csv(storyboard)
    manifest = job_dir / "assets_manifest.csv"
    if manifest.is_file():
        payload["assets"] = _read_csv(manifest)
    register = job_dir / "source_register.csv"
    if register.is_file():
        payload["sources"] = _read_csv(register)
    for key, relative in (
        ("script", "script/script.md"),
        ("visual_direction", "visual_direction.md"),
        ("qc_report", "qc/qc_report.md"),
    ):
        path = job_dir / relative
        if path.is_file():
            payload[key] = path.read_text(encoding="utf-8", errors="replace")[:20000]
    timeline = job_dir / "project" / "timeline.json"
    if timeline.is_file():
        payload["timeline_bytes"] = timeline.stat().st_size
    return payload


@router.get("/jobs/{job_id}/file", summary="读取任务产物文件（?path=output/xxx.mp4）")
def get_job_file(
    job_id: str = PathParam(min_length=1, max_length=80),
    path: str = Query(min_length=1, max_length=400, description="任务目录内的相对路径"),
) -> FileResponse:
    job_dir = _resolve_inside(JOBS_ROOT / job_id, JOBS_ROOT)
    target = _resolve_inside(job_dir / path, job_dir)
    if not target.is_file():
        raise NotFoundError("文件不存在", details={"reason": "missing_file"})
    if target.suffix.lower() not in VIDEO_SUFFIXES:
        raise DomainValidationError(
            "该类型文件不提供预览", details={"reason": "unsupported_preview"}
        )
    return FileResponse(target)


@router.get("/search", summary="真实网络搜索（搜狗/360/bing 降级）")
def workspace_search(
    q: str = Query(min_length=1, max_length=200, description="检索词"),
    site: str = Query(default="", max_length=100, description="限定站点，如 pbc.gov.cn"),
    limit: int = Query(default=10, ge=1, le=20),
) -> dict[str, Any]:
    """真实搜索。失败时如实返回错误原因，不返回假结果。

    受 ``features.search`` 开关约束：关着就明确拒绝并说明怎么打开，
    而不是"页面上有个搜索框但行为与配置不一致"。
    """
    if not get_features_settings().search:
        return {
            "ok": False,
            "message": "搜索开关未启用：把 config/settings.yaml 的 features.search 改为 true 再试",
            "reasons": ["feature_disabled:search"],
            "hits": [],
        }
    query = SearchQuery(keywords=q, site=site, limit=limit)
    try:
        report = search(query)
    except SearchUnavailable as error:
        return {
            "ok": False,
            "message": str(error),
            "reasons": list(error.reasons),
            "hits": [],
        }
    return {"ok": True, "message": "", "reasons": [], **report.as_dict()}


@router.get("/config", summary="配置快照（只读，供设置页展示）")
def get_config_snapshot() -> dict[str, Any]:
    features = get_features_settings()
    cost = get_cost_settings()
    execution = get_execution_settings()
    models = get_models_config()
    return {
        "features": {
            "model": features.model,
            "search": features.search,
            "execution": features.execution,
        },
        "cost": {
            "currency": cost.currency,
            "daily_cap_cny": cost.daily_cap_cny,
            "default_task_calls": cost.default_task_calls,
            "default_task_cost_cny": cost.default_task_cost_cny,
            "pricing_policy": cost.pricing_policy,
        },
        "execution": {
            "enabled": execution.enabled,
            "max_concurrency": execution.max_concurrency,
            "lease_seconds": execution.lease_seconds,
            "heartbeat_seconds": execution.heartbeat_seconds,
            "dev_tick_enabled": execution.dev_tick_enabled,
        },
        "models": [
            {
                "provider": item.provider,
                "model": item.model,
                "display_name": item.display_name,
                "enabled": item.enabled,
                "input_price_per_1k_cny": item.input_price_per_1k_cny,
                "output_price_per_1k_cny": item.output_price_per_1k_cny,
                "pricing_status": item.pricing_status,
                "note": item.note,
            }
            for item in models.models
        ],
        "editable_by": "config/models.yaml 与 config/settings.yaml（本页只读）",
    }
