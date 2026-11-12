"""把「渲染一条片子」做成可以后台跑的独立进程。

工作台点「渲染成片」如果直接在请求里同步跑，浏览器得挂十几分钟，中间断网就前功尽弃。
所以真实做法是：接口只负责**起进程**并立刻返回，进程把每一步写进 ``qc/render_run.json``，
页面轮询那个文件看进度。

用法::

    python -m backend.scripts.render_job --job phase2_media_engine_demo
    python -m backend.scripts.render_job --job my_job --no-render      # 只生成时间轴
    python -m backend.scripts.render_job --job my_job --output output/final.mp4

状态文件结构（页面直接读它）::

    {"job_id": ..., "status": "running|done|failed", "stage": ..., "started_at": ...,
     "finished_at": ..., "log": ["..."], "report": {...} | null, "error": "..." }

``status`` 只会是这三种：没有中间态谎言——进程被杀就是 ``running`` 停在最后一次写入，
``started_at`` 与 ``updated_at`` 能看出它什么时候不动了。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.video.render import RenderError, render_job  # noqa: E402

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
STATUS_NAME = "render_run.json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # 原子替换：页面永远读不到写了一半的 JSON


def status_path(job_dir: Path) -> Path:
    return job_dir / "qc" / STATUS_NAME


def read_status(job_dir: Path) -> dict | None:
    path = status_path(job_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在独立进程里渲染一条视频任务")
    parser.add_argument("--job", required=True, help="任务目录名（content/jobs/<job>）")
    parser.add_argument("--output", default="output/final.mp4")
    parser.add_argument("--no-render", action="store_true", help="只生成并校验时间轴，不真渲染")
    parser.add_argument("--no-qc", action="store_true", help="跳过成片质检（一般不用）")
    parser.add_argument("--narration", action="store_true",
                        help="按分镜的 narration 列合成旁白音轨（真实 TTS，默认关闭）")
    parser.add_argument("--narration-provider", default="auto", choices=["auto", "edge", "sapi"])
    parser.add_argument("--narration-voice", default=None)
    parser.add_argument("--jobs-root", default=None, help="默认 content/jobs")
    args = parser.parse_args(argv)

    jobs_root = Path(args.jobs_root) if args.jobs_root else JOBS_ROOT
    job_dir = jobs_root / args.job
    if not job_dir.is_dir():
        print(json.dumps({"error": "unknown_job", "job": args.job}, ensure_ascii=False))
        return 2

    payload = {
        "job_id": args.job,
        "status": "running",
        "stage": "starting",
        "started_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "render": not args.no_render,
        "narration": bool(args.narration),
        "output_name": args.output,
        "log": ["进程已启动，正在读分镜与素材清单"],
        "report": None,
        "error": "",
    }
    _write(status_path(job_dir), payload)

    try:
        report = render_job(
            job_dir,
            do_render=not args.no_render,
            do_qc=not args.no_qc,
            output_name=args.output,
            narration=bool(args.narration),
            narration_provider=args.narration_provider,
            narration_voice=args.narration_voice,
        )
    except RenderError as error:
        payload.update(
            status="failed",
            stage="failed",
            finished_at=_now(),
            updated_at=_now(),
            error=f"{error.reason}: {error}",
        )
        payload["log"].append(f"失败：{error.reason}")
        _write(status_path(job_dir), payload)
        print(json.dumps({"status": "failed", "reason": error.reason, "message": str(error)},
                         ensure_ascii=False))
        return 3
    except Exception as error:  # noqa: BLE001 - 后台进程要留下死因，而不是静默退出
        payload.update(status="failed", stage="failed", finished_at=_now(), updated_at=_now(),
                       error=f"{type(error).__name__}: {error}")
        _write(status_path(job_dir), payload)
        print(json.dumps({"status": "failed", "error": type(error).__name__}, ensure_ascii=False))
        return 3

    data = report.as_dict()
    payload.update(
        status="done",
        stage=data.get("stage", ""),
        finished_at=_now(),
        updated_at=_now(),
        report=data,
    )
    payload["log"].append(f"完成：{data.get('stage')}")
    _write(status_path(job_dir), payload)
    print(json.dumps({"status": "done", "stage": data.get("stage"),
                      "output": data.get("output_path")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工/后台运行入口
    raise SystemExit(main())
