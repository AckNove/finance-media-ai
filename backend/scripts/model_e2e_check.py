"""真实模型端到端验收：建任务 → 真搜索/抓取 → 真模型分析 → 成本落账。

只操作临时库与临时目录，**不碰 data/finance_media.db**。
用真实 DeepSeek（.env 里的 key）跑，所以会花钱——预算约 2~4 分钱。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 8397
BASE = f"http://127.0.0.1:{PORT}"

workdir = Path(tempfile.mkdtemp(prefix="fmai_model_e2e_"))
db_path = workdir / "flow.db"
DB_URL = f"sqlite:///{db_path.as_posix()}"
logs: list[str] = []


def log(line: str) -> None:
    print(line, flush=True)
    logs.append(line)


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, {"raw": body[:400]}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"raw": body[:400]}


def env_for_app() -> dict[str, str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = DB_URL
    env["APP_ENV"] = "development"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# 1) 建库（迁移到 head）
log("== 1) 迁移临时库 ==")
result = subprocess.run(
    [PY, "-m", "alembic", "-x", f"db_url={DB_URL}", "upgrade", "head"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
log(f"alembic rc={result.returncode} tail={result.stdout.strip().splitlines()[-1:]}")
if result.returncode != 0:
    log(result.stderr[-1500:])
    sys.exit(1)

# 2) 起服务
log("== 2) 启动 uvicorn ==")
server = subprocess.Popen(
    [PY, "-m", "uvicorn", "backend.app.main:app", "--port", str(PORT), "--log-level", "warning"],
    cwd=ROOT,
    env=env_for_app(),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    for _ in range(60):
        try:
            status, body = call("GET", "/health")
            if status == 200:
                log(f"/health -> {status} {body.get('status')}")
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.5)
    else:
        log("服务没起来")
        sys.exit(1)

    # 3) 建研究任务
    log("== 3) 建研究任务 ==")
    status, body = call(
        "POST",
        "/api/research-tasks",
        {
            "title": "商业银行是如何赚钱的",
            "objective": "商业银行是如何赚钱的？",
            "budget_max_calls": 8,
            "budget_max_cost_cny": 0.5,
        },
    )
    log(f"POST /api/research-tasks -> {status} task_id={body.get('id')} subtasks={len(body.get('subtasks', []))}")
    task_id = body.get("id")
    if not task_id:
        log(json.dumps(body, ensure_ascii=False)[:600])
        sys.exit(1)

    # 4) 跑 worker（真实搜索/抓取/模型）
    log("== 4) worker 推进（真实搜索 + 抓取 + 模型）==")
    gates_cleared = 0
    rounds = 0
    for attempt in range(1, 21):
        result = subprocess.run(
            [
                PY,
                "-m",
                "backend.scripts.run_worker",
                "--task-id",
                str(task_id),
                "--worker",
                "e2e-worker",
                "--max-iterations",
                "3",
                "--poll-seconds",
                "1",
                "--max-idle-minutes",
                "1",
            ],
            cwd=ROOT,
            env=env_for_app(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        rounds += 1
        tail = [line for line in (result.stdout or "").strip().splitlines() if line.strip()][-3:]
        log(f"  worker#{attempt} rc={result.returncode} :: " + " | ".join(tail)[:300])
        status, snapshot = call("GET", f"/api/research-tasks/{task_id}")
        counts: dict[str, int] = {}
        for subtask in snapshot.get("subtasks", []):
            counts[subtask["status"]] = counts.get(subtask["status"], 0) + 1
        state = snapshot.get("status")
        log(f"  状态 {state} 计数 {counts}")
        if state == "paused":
            # 人工闸门：这里扮演"人"，点一次放行。
            gates_cleared += 1
            log(f"  → 闸门放行第 {gates_cleared} 次（POST /resume）")
            code, after = call("POST", f"/api/research-tasks/{task_id}/resume")
            log(f"    resume -> {code} {after.get('status')}")
            if code >= 400:
                break
            continue
        if state in {"completed", "failed", "cancelled"}:
            break
    _, events = call("GET", f"/api/research-tasks/{task_id}/events?limit=100")
    kinds = [item.get("event_type") for item in events.get("items", [])]
    log(f"  事件总数={events.get('total')} 类型计数："
        f"{ {kind: kinds.count(kind) for kind in sorted(set(kinds))} }")

    # 5) 逐步看输出
    log("== 5) 子任务执行结果 ==")
    status, snapshot = call("GET", f"/api/research-tasks/{task_id}")
    for subtask in snapshot.get("subtasks", []):
        output = subtask.get("output") or {}
        keys = ", ".join(sorted(output)[:8])
        log(
            f"  #{subtask['sequence']} {subtask['task_type']:<20} {subtask['status']:<9} "
            f"gen={output.get('generated_by', '-')} keys=[{keys}]"
        )

    # 6) 数据核对
    log("== 6) 库里的真实数据 ==")
    _, sources = call("GET", "/api/sources?limit=50")
    log(f"GET /api/sources -> total={sources.get('total')}")
    _, reports = call("GET", "/api/research-reports?limit=10")
    log(f"GET /api/research-reports -> total={reports.get('total')}")
    report_id = None
    if reports.get("items"):
        first = reports["items"][0]
        report_id = first.get("id")
        log(f"  report#{report_id} summary={str(first.get('summary'))[:120]}")
        log(f"  findings={json.dumps(first.get('findings'), ensure_ascii=False)[:300]}")
        log(f"  limitations={json.dumps(first.get('limitations'), ensure_ascii=False)[:300]}")
        log(f"  verification_status={first.get('verification_status')}")
    if report_id:
        _, evidence = call("GET", f"/api/evidence?research_report_id={report_id}")
        log(f"GET /api/evidence -> total={evidence.get('total')}")
    _, costs = call("GET", "/api/system/costs")
    log(f"GET /api/system/costs -> {json.dumps(costs, ensure_ascii=False)[:400]}")

    # 7) 落库明文核对（直接读临时库）
    log("== 7) 直接读临时库 ==")
    import sqlite3

    with sqlite3.connect(db_path) as connection:
        for table in ("sources", "research_reports", "evidence", "model_calls", "research_tasks"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log(f"  {table}: {count} 行")
        rows = connection.execute(
            "SELECT provider, model, purpose, prompt_tokens, completion_tokens, cost_estimate, status "
            "FROM model_calls ORDER BY id"
        ).fetchall()
        for row in rows:
            log(f"  model_calls: {row}")
        total = connection.execute("SELECT COALESCE(SUM(cost_estimate),0) FROM model_calls").fetchone()[0]
        log(f"  model_calls 合计成本 = {total} 元")
        secret = connection.execute(
            "SELECT COUNT(*) FROM model_calls WHERE prompt_preview LIKE '%sk-%'"
        ).fetchone()[0]
        log(f"  prompt_preview 里含 sk- 的行数 = {secret}（必须为 0）")

    log(f"== 完成 ==\n临时库：{db_path}")
    log("LOGS_JSON=" + json.dumps(logs, ensure_ascii=False))
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    shutil.rmtree(workdir, ignore_errors=True)
