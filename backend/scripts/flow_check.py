"""§5 前端真实验收：用真 HTTP 服务把「建选题 → 建任务 → 真实搜索抓取 → 资料/证据/报告 →
脚本送审 → 记审核 → 从报告建视频任务 → 成本」整条走一遍，并打印每一步的真实返回码与 ID。

临时脚本，跑完即删。默认用 %TEMP% 下的独立库，绝不碰 data/finance_media.db。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 8395
BASE = f"http://127.0.0.1:{PORT}"


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, (json.loads(body) if body else {})


def main() -> int:
    work = Path(os.environ["TEMP"]) / f"fmai_flow_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    db_path = work / "flow.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    sys.path.insert(0, str(ROOT))
    from backend.app.core.config import Settings  # noqa: PLC0415
    from backend.app.database.session import build_engine  # noqa: PLC0415
    from backend.app.models import Base  # noqa: PLC0415

    engine = build_engine(Settings(database_url=database_url))
    Base.metadata.create_all(engine)
    engine.dispose()

    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    server = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "backend.app.main:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    steps: list[dict] = []
    try:
        for _ in range(60):
            try:
                if call("GET", "/health")[0] == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            print("服务没起来")
            return 1

        status, topic = call("POST", "/api/topics", {
            "title": "商业银行是如何赚钱的？",
            "description": "拆开看净息差、中间业务与信用成本",
        })
        steps.append({"step": "建选题", "status": status, "id": topic.get("id"), "body": topic if status >= 400 else None})

        status, task = call("POST", "/api/research-tasks", {
            "title": "商业银行是如何赚钱的？",
            "objective": "商业银行靠什么赚钱、净息差为什么下降",
            "pipeline": "research_to_script",
            "topic_id": topic.get("id"),
            "skip": ["topic_candidate", "topic_scoring", "topic_approval"],
            "skip_reason": "选题已人工确认，直接从检索开始",
            "queries": ["商业银行 净息差 下降", "商业银行 中间业务收入"],
        })
        steps.append({"step": "建研究任务", "status": status, "id": task.get("id"),
                      "subtasks": len(task.get("subtasks") or []), "body": task if status >= 400 else None})
        task_id = task.get("id")

        for round_index in range(4):
            status, run = call("POST", f"/api/research-tasks/{task_id}/run",
                               {"max_iterations": 3, "poll_seconds": 0})
            steps.append({"step": f"推进第{round_index + 1}轮", "status": status,
                          "completed": run.get("completed"), "blocked": run.get("blocked"),
                          "retried": run.get("retried"), "failed": run.get("failed")})

        status, detail = call("GET", f"/api/research-tasks/{task_id}")
        steps.append({"step": "任务终态", "status": status, "task_status": detail.get("status"),
                      "counts": detail.get("counts")})

        status, sources = call("GET", "/api/sources?limit=5")
        steps.append({"step": "资料库", "status": status, "total": sources.get("total")})

        status, reports = call("GET", "/api/research-reports?limit=5")
        report_id = (reports.get("items") or [{}])[0].get("id")
        steps.append({"step": "报告列表", "status": status, "total": reports.get("total"),
                      "report_id": report_id})

        if report_id:
            status, evidence = call("GET", f"/api/evidence?research_report_id={report_id}&limit=5")
            steps.append({"step": "证据链", "status": status, "total": evidence.get("total")})

        status, script = call("POST", "/api/scripts", {
            "topic_id": topic.get("id"),
            "title": "商业银行赚钱的四条腿",
            "body": "净息差是主腿，中间业务是第二条腿，信用成本决定利润留存。",
            "research_report_id": report_id,
        })
        steps.append({"step": "建脚本", "status": status, "id": script.get("id"),
                      "version": script.get("version"), "state": script.get("status"),
                      "body": script if status >= 400 else None})
        if script.get("id"):
            for target in ("in_review", "approved"):
                status, moved = call("PATCH", f"/api/scripts/{script['id']}/status",
                                     {"status": target, "actor": "验收脚本"})
                steps.append({"step": f"脚本→{target}", "status": status,
                              "state": moved.get("status"), "body": moved if status >= 400 else None})

        status, review = call("POST", "/api/reviews", {
            "target_type": "research_report",
            "target_id": report_id,
            "verdict": "needs_revision",
            "reviewer_name": "验收脚本",
            "stage": "professional",
            "comment": "结论段还没写（缺模型），先退回",
        })
        steps.append({"step": "记审核", "status": status, "id": review.get("id"),
                      "verdict": review.get("verdict")})

        if report_id:
            job_id = "v5_flow_acceptance"
            target = ROOT / "content" / "jobs" / job_id
            if target.exists():
                import shutil  # noqa: PLC0415

                shutil.rmtree(target)
            status, job = call("POST", f"/api/research-reports/{report_id}/video-job",
                               {"job_id": job_id})
            steps.append({"step": "从报告建视频任务", "status": status, "job_id": job.get("job_id"),
                          "stage": job.get("stage"), "blocked": job.get("blocked"),
                          "files": job.get("files"), "body": job if status >= 400 else None})

        status, costs = call("GET", "/api/system/costs")
        steps.append({"step": "成本", "status": status, "model_calls": costs.get("model_calls"),
                      "model_cost_cny": costs.get("model_cost_cny"), "note": costs.get("note")})

        status, providers = call("GET", "/api/system/providers")
        rows = providers if isinstance(providers, list) else providers.get("providers") or []
        deepseek = next((row for row in rows if isinstance(row, dict) and row.get("name") == "deepseek"), {})
        steps.append({"step": "服务商", "status": status, "deepseek": deepseek.get("status")})
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()

    print(json.dumps({"steps": steps, "db": str(db_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
