"""排期里的那条片子，到底能不能发。

排期表只记"我打算哪天发什么"，但真到发的那天要回答的是三个更硬的问题：
片子渲染出来了吗？它依据的那些结论现在还作数吗？导出过没有？

这三个问题的答案都不在数据库里，而在 ``content/jobs/<job_id>/`` 这个目录里：

* ``job.json`` 的 ``draft_gate.usable`` 为 ``false`` 时，说明这条片子引用的结论
  后来被撤回了确认——**片子还在，但它的结论已经不作数了**，拿去发就是拿旧结论
  冒充当前结论（真事：``v6_accept_industry`` 就是这种状态）。
* ``output/*.mp4`` 才是真渲染完的证据，``job.json`` 的 ``stage`` 只说明交接做完。

所以这里做的是"读目录事实"，不做任何推断；读不到就如实说读不到。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.core.config import PROJECT_ROOT

__all__ = [
    "DEFAULT_JOBS_ROOT",
    "READINESS_LABELS",
    "job_readiness",
]

DEFAULT_JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"

#: 状态 → 人话。前端直接显示这个，不再各写一份文案。
READINESS_LABELS: dict[str, str] = {
    "no_job": "还没排视频",
    "job_missing": "任务目录不在了",
    "no_gate": "片子在，但查不到成稿闸门记录",
    "film_missing": "还没渲染出片子",
    "draft_invalid": "结论确认被撤回，片子已失效",
    "ready": "片子就绪",
}

_NEXT_STEP: dict[str, str] = {
    "no_job": "先给它建一个视频任务（研究任务页 → 从报告创建），渲染完再回来填任务号。",
    "job_missing": "这个 job_id 在 content/jobs 下找不到：确认是不是换了机器或删过目录。",
    "no_gate": "这个任务目录里没有 job.json：查不到它依据的结论当时是否被人工确认过。"
               "要么翻记录人工核对一遍，要么重新交接、重新渲染一次。",
    "film_missing": "到视频任务里跑一次渲染（render_job），出片后再回来看这条。",
    "draft_invalid": "有结论被撤回了确认：重新确认结论后重跑渲染，别拿旧片子发。",
    "ready": "可以导出或去平台发布；发完回来填链接，事实由你登记。",
}


def _film_of(job_dir: Path) -> Path | None:
    """优先 ``output/final.mp4``，其余按修改时间取最新的一个。"""
    output = job_dir / "output"
    if not output.is_dir():
        return None
    preferred = output / "final.mp4"
    if preferred.is_file():
        return preferred
    candidates = [path for path in output.glob("*.mp4") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _render_run(job_dir: Path) -> dict[str, Any]:
    """渲染回执（``qc/render_run.json``）。没有就返回空字典，不猜。"""
    path = job_dir / "qc" / "render_run.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "status": str(payload.get("status") or ""),
        "stage": str(payload.get("stage") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
    }


def job_readiness(video_job_id: str | None, *, jobs_root: Path | None = None) -> dict[str, Any]:
    """读磁盘事实，判断这条排期背后的片子能不能发。

    返回值里的 ``status`` 只会是 :data:`READINESS_LABELS` 的键之一；
    ``usable`` 只有 ``ready`` 才是 ``True``（可以导出/发布）。
    """
    root = Path(jobs_root) if jobs_root is not None else DEFAULT_JOBS_ROOT
    cleaned = (video_job_id or "").strip()

    if not cleaned:
        return _result("no_job", job_id="")

    # 目录名来自接口参数，这里再守一道：不允许穿越
    if any(ch in cleaned for ch in ("/", "\\", "..")):
        return _result("job_missing", job_id=cleaned)

    job_dir = root / cleaned
    config = job_dir / "job.json"
    if not job_dir.is_dir():
        return _result("job_missing", job_id=cleaned, path=str(job_dir))

    film = _film_of(job_dir)
    film_info: dict[str, Any] = {}
    if film is not None:
        stat = film.stat()
        film_info = {
            "path": str(film),
            "relative_path": str(film.relative_to(job_dir)).replace("\\", "/"),
            "byte_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }

    # 目录在、片子也在，却没有 job.json：说明它是在成稿闸门机制之前产的，或者
    # 交接记录被删过。这时**查不到它引用的结论当时是否被确认**——不能当成就绪，
    # 也不能说成"任务目录不在了"（那是另一回事，会把人引到错的地方去找）。
    if not config.is_file():
        return _result("no_gate", job_id=cleaned, path=str(job_dir), film=film_info)

    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    gate = payload.get("draft_gate") or {}
    gate_usable = gate.get("usable")

    # 顺序有讲究：先看结论还作不作数，再看片子。片子存在但结论被撤回，
    # 是**最该拦住**的那种状态，不能因为文件在就说"就绪"。
    if gate_usable is False:
        return _result(
            "draft_invalid",
            job_id=cleaned,
            path=str(job_dir),
            film=film_info,
            invalidated_at=str(gate.get("invalidated_at") or ""),
            invalidated_reason=str(gate.get("invalidated_reason") or ""),
            stage=str(payload.get("stage") or ""),
            render=_render_run(job_dir),
        )

    if film is None:
        return _result(
            "film_missing",
            job_id=cleaned,
            path=str(job_dir),
            stage=str(payload.get("stage") or ""),
            render=_render_run(job_dir),
        )

    return _result(
        "ready",
        job_id=cleaned,
        path=str(job_dir),
        film=film_info,
        stage=str(payload.get("stage") or ""),
        render=_render_run(job_dir),
        outline_source=str((payload.get("outline") or {}).get("source") or ""),
    )


def _result(status: str, **extra: Any) -> dict[str, Any]:
    label = READINESS_LABELS[status]
    result: dict[str, Any] = {
        "status": status,
        "label": label,
        "usable": status == "ready",
        "next_step": _NEXT_STEP[status],
        "job_id": "",
        "path": "",
        "film": {},
        "stage": "",
        "render": {},
        "outline_source": "",
        "invalidated_at": "",
        "invalidated_reason": "",
    }
    result.update(extra)
    return result
