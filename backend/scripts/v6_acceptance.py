"""V6.0 三主题真实验收：行业 / 产业链 / 上市公司各跑一遍完整研究。

跑的是真的：真 uvicorn、真搜索、真抓取、真 DeepSeek、真临时库。
所以**会花钱**——每题按 `--budget` 给的预算上限（默认 0.4 元）卡住，
超了模型网关会直接拒绝请求，不会偷偷继续烧。

三题跑完再做一条内容链路：对第一份报告点一条「人工确认」，
然后建视频任务并真渲染，验证"只有确认过的结论能进成稿"。

用法：
    python -m backend.scripts.v6_acceptance                 # 三题 + 渲染
    python -m backend.scripts.v6_acceptance --no-render     # 只跑研究
    python -m backend.scripts.v6_acceptance --only chain    # 只跑一题

临时库与日志放在 %TEMP%\\fmai_v6_accept_*\\，不会碰 data/finance_media.db。
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
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 8399
BASE = f"http://127.0.0.1:{PORT}"

#: V6.0 第五部分要求的三类主题。每条都是新主题，不复用旧研究结果。
TOPICS: list[dict[str, str]] = [
    {
        "key": "industry",
        "kind": "行业研究",
        "title": "储能行业2025年为什么从爆发转向价格战",
        "objective": (
            "2025 年储能装机仍在增长，但电芯与系统价格持续下行、部分企业毛利转负。"
            "请研究：这个行业当前处在什么阶段，市场规模与增速、供需结构、竞争格局、"
            "驱动因素与政策变化分别是什么，主要风险在哪里？请给可核对的公开数据。"
        ),
    },
    {
        "key": "chain",
        "kind": "产业链研究",
        "title": "光伏产业链利润为什么会从中游流向上下游两端",
        "objective": (
            "请沿光伏产业链拆解上游硅料、中游硅片电池组件、下游电站与辅材设备的"
            "成本结构与价值分配：各环节的钱是怎么赚的、当前瓶颈在哪、"
            "利润为什么会在不同环节之间迁移？请给出可核对的公开数据与来源。"
        ),
    },
    {
        "key": "company",
        "kind": "上市公司研究",
        "title": "隆基绿能2024年财报里的亏损来自哪里",
        "objective": (
            "请基于公开财报与公告研究隆基绿能 2024 年的经营情况：主营业务与商业模式、"
            "收入与利润结构变化、盈利能力与现金流、资产负债与存货减值、"
            "与同行的差异、以及主要风险。请标注数据口径与时点。"
        ),
    },
]


def _flag(name: str, default: str = "") -> str:
    for index, item in enumerate(sys.argv):
        if item == f"--{name}" and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if item.startswith(f"--{name}="):
            return item.split("=", 1)[1]
    return default


BUDGET = float(_flag("budget", "0.4"))
ONLY = _flag("only", "")
NO_RENDER = "--no-render" in sys.argv

workdir = Path(tempfile.mkdtemp(prefix="fmai_v6_accept_"))
db_path = workdir / "accept.db"
DB_URL = f"sqlite:///{db_path.as_posix()}"
report: dict[str, object] = {"started_at": datetime.now(UTC).isoformat(), "topics": []}


def log(line: str) -> None:
    print(line, flush=True)


def call(method: str, path: str, payload: dict | None = None, *, timeout: int = 180) -> tuple[int, dict]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def run_worker(task_id: int, attempt: int) -> str:
    result = subprocess.run(
        [
            PY, "-m", "backend.scripts.run_worker",
            "--task-id", str(task_id),
            "--worker", "v6-accept",
            "--max-iterations", "3",
            "--poll-seconds", "1",
            "--max-idle-minutes", "1",
        ],
        cwd=ROOT,
        env=env_for_app(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
    )
    tail = [line for line in (result.stdout or "").strip().splitlines() if line.strip()][-2:]
    return f"worker#{attempt} rc={result.returncode} :: " + " | ".join(tail)[:240]


def drive_task(task_id: int) -> dict:
    """推进到终态，遇到人工闸门就放行（这里扮演人）。"""
    gates = 0
    rounds = 0
    for attempt in range(1, 25):
        log("  " + run_worker(task_id, attempt))
        rounds = attempt
        _, snapshot = call("GET", f"/api/research-tasks/{task_id}")
        counts: dict[str, int] = {}
        for subtask in snapshot.get("subtasks", []):
            counts[subtask["status"]] = counts.get(subtask["status"], 0) + 1
        state = snapshot.get("status")
        log(f"  状态 {state} 计数 {counts}")
        if state == "paused":
            gates += 1
            code, after = call("POST", f"/api/research-tasks/{task_id}/resume")
            log(f"  → 闸门放行 #{gates} -> {code} {after.get('status')}")
            if code >= 400:
                break
            continue
        if state in {"completed", "failed", "cancelled"}:
            break
    return {"rounds": rounds, "gates_cleared": gates}


def research_one(topic: dict[str, str]) -> dict:
    log(f"\n===== {topic['kind']}：{topic['title']} =====")
    code, body = call(
        "POST",
        "/api/research-tasks",
        {
            "title": topic["title"],
            "objective": topic["objective"],
            "budget_max_calls": 8,
            "budget_max_cost_cny": BUDGET,
        },
    )
    task_id = body.get("id")
    log(f"POST /api/research-tasks -> {code} task_id={task_id} subtasks={len(body.get('subtasks', []))}")
    if code >= 400 or not task_id:
        return {"key": topic["key"], "kind": topic["kind"], "ok": False, "error": body}

    outcome = drive_task(task_id)
    _, detail = call("GET", f"/api/research-tasks/{task_id}")
    planning = detail.get("planning") or {}
    log(
        f"  planning planner={planning.get('planner')} model_used={planning.get('model_used')} "
        f"queries={planning.get('query_count')} subtasks={planning.get('subtask_count')} "
        f"degraded={planning.get('degraded_reason') or '-'}"
    )
    subtasks = [
        {
            "sequence": item["sequence"],
            "task_type": item["task_type"],
            "status": item["status"],
            "generated_by": (item.get("output") or {}).get("generated_by"),
        }
        for item in detail.get("subtasks", [])
    ]

    _, sources = call("GET", "/api/sources?limit=1")
    _, reports = call("GET", "/api/research-reports?limit=20")
    report_id = None
    report_row: dict = {}
    for item in reports.get("items", []):
        if item.get("research_task_id") == task_id:
            report_id = item["id"]
            report_row = item
            break
    evidence_total = 0
    checks_summary: dict = {}
    checks: list[dict] = []
    if report_id:
        _, evidence = call("GET", f"/api/evidence?research_report_id={report_id}&limit=1")
        evidence_total = int(evidence.get("total") or 0)
        _, checks_summary = call("GET", f"/api/research-reports/{report_id}/checks/summary")
        _, listing = call("GET", f"/api/research-reports/{report_id}/checks?limit=100")
        checks = listing.get("items") or []
    log(
        f"  数据：sources={sources.get('total')} evidence={evidence_total} "
        f"report={report_id} checks={len(checks)} "
        f"verdicts={(checks_summary or {}).get('by_verdict')}"
    )

    return {
        "key": topic["key"],
        "kind": topic["kind"],
        "title": topic["title"],
        "ok": report_id is not None,
        "task_id": task_id,
        "task_status": detail.get("status"),
        "sources": int(sources.get("total") or 0),
        "evidence": evidence_total,
        "report_id": report_id,
        "report_summary": str(report_row.get("summary") or "")[:400],
        "findings": len(report_row.get("findings") or []),
        "limitations": str(report_row.get("limitations") or "")[:300],
        "planning": {
            "planner": planning.get("planner"),
            "model_used": planning.get("model_used"),
            "query_count": planning.get("query_count"),
            "subtask_count": planning.get("subtask_count"),
            "degraded_reason": planning.get("degraded_reason"),
        },
        "checks_summary": checks_summary,
        "checks_sample": [
            {
                "id": item["id"],
                "verdict": item["verdict"],
                "claim": str(item["claim"])[:160],
                "supporting": (item.get("supporting") or [])[:2],
                "human": item.get("human_review_status"),
            }
            for item in checks[:6]
        ],
        "subtasks": subtasks,
        **outcome,
    }


def content_chain(first: dict) -> dict:
    """从第一份报告走出来：人工确认一条 → 建视频任务 → 真渲染。"""
    report_id = first.get("report_id")
    checks = first.get("checks_sample") or []
    if not report_id or not checks:
        return {"ok": False, "reason": "no_report_or_checks"}

    check_id = checks[0]["id"]
    log(f"\n===== 内容链路：确认核查 #{check_id}（报告 {report_id}）=====")
    code, body = call(
        "POST",
        f"/api/research-reports/{report_id}/checks/{check_id}/review",
        {"status": "confirmed", "note": "V6.0 验收：原文逐字核对通过，口径与来源一致"},
    )
    log(f"  review -> {code}")
    if code >= 400:
        return {"ok": False, "reason": "review_failed", "detail": body}

    _, readiness = call("GET", f"/api/research-reports/{report_id}/draft-readiness")
    log(f"  draft-readiness -> allowed={readiness.get('allowed')} confirmed={readiness.get('confirmed')}")

    job_id = f"v6_accept_{first['key']}"
    code, body = call("POST", f"/api/research-reports/{report_id}/video-job", {"job_id": job_id})
    log(f"  video-job -> {code} {json.dumps(body, ensure_ascii=False)[:200]}")
    if code >= 400:
        return {"ok": False, "reason": "video_job_failed", "detail": body, "readiness": readiness}

    result = {
        "ok": True,
        "job_id": job_id,
        "check_id": check_id,
        "readiness": readiness,
        "job": body,
    }
    if NO_RENDER:
        return result

    log("  真渲染（分镜 → 素材 → 时间轴 → 引擎 → QC）…")
    render = subprocess.run(
        [
            PY, "-m", "backend.scripts.render_job",
            "--job", job_id,
            "--narration",
            "--output", "output/final.mp4",
        ],
        cwd=ROOT,
        env=env_for_app(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    tail = [line for line in (render.stdout or "").strip().splitlines() if line.strip()][-4:]
    log(f"  render rc={render.returncode} :: " + " | ".join(tail)[:400])
    # 产物是相对于任务目录写的，补成绝对路径，别把相对路径写进验收报告
    run_json = ROOT / "content" / "jobs" / job_id / "qc" / "render_run.json"
    if run_json.exists():
        payload = json.loads(run_json.read_text(encoding="utf-8"))
        output = (payload.get("report") or {}).get("output")
        result["render"] = {
            "status": payload.get("status"),
            "stage": payload.get("stage"),
            "output": str((ROOT / "content" / "jobs" / job_id / output).resolve())
            if output
            else None,
            "qc_failed": [
                key
                for key, value in ((payload.get("report") or {}).get("qc") or {}).get("checks", {}).items()
                if not value
            ],
        }
    result["render_rc"] = render.returncode
    return result


def main() -> int:
    log(f"工作目录：{workdir}")
    result = subprocess.run(
        [PY, "-m", "alembic", "-x", f"db_url={DB_URL}", "upgrade", "head"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log(f"迁移 rc={result.returncode}")
    if result.returncode != 0:
        log(result.stderr[-1200:])
        return 1

    server = subprocess.Popen(
        [PY, "-m", "uvicorn", "backend.app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env_for_app(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            try:
                code, body = call("GET", "/health")
                if code == 200:
                    log(f"/health -> {code} {body.get('status')}")
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.5)
        else:
            log("服务没起来")
            return 1

        selected = [t for t in TOPICS if not ONLY or t["key"] == ONLY]
        for topic in selected:
            try:
                report["topics"].append(research_one(topic))
            except Exception as error:  # noqa: BLE001 - 验收脚本要留下失败证据
                log(f"  !! 出错了：{error!r}")
                report["topics"].append({"key": topic["key"], "ok": False, "error": repr(error)})

        first_ok = next((t for t in report["topics"] if t.get("ok")), None)
        if first_ok:
            report["content"] = content_chain(first_ok)

        code, costs = call("GET", "/api/system/costs")
        report["costs"] = costs

        import sqlite3

        with sqlite3.connect(db_path) as connection:
            tables = {
                name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in (
                    "sources", "evidence", "research_reports", "finding_checks",
                    "model_calls", "research_tasks", "research_subtasks", "task_events",
                )
            }
            rows = connection.execute(
                "SELECT provider, model, purpose, prompt_tokens, completion_tokens, cost_estimate "
                "FROM model_calls ORDER BY id"
            ).fetchall()
            total = connection.execute(
                "SELECT COALESCE(SUM(cost_estimate),0) FROM model_calls"
            ).fetchone()[0]
            leaked = connection.execute(
                "SELECT COUNT(*) FROM model_calls WHERE prompt_preview LIKE '%sk-%'"
            ).fetchone()[0]
            task_total = connection.execute(
                "SELECT COALESCE(SUM(cost_spent),0) FROM research_tasks"
            ).fetchone()[0]
        # 账要对得上：账本合计 == 任务累计 == 接口报的 task_cost_cny。
        # 允许 1e-9 的浮点误差，对不上就说明有调用的钱没记进任务（预算闸门会低估花销）。
        api_task_cost = str((costs or {}).get("task_cost_cny") or "0")
        accounting_ok = (
            abs(float(total) - float(task_total)) < 1e-9
            and abs(float(total) - float(api_task_cost)) < 1e-9
        )
        report["database"] = {
            "tables": tables,
            "model_calls": [list(row) for row in rows],
            "model_cost_total_cny": str(total),
            "task_cost_spent_cny": str(task_total),
            "api_task_cost_cny": api_task_cost,
            "accounting_ok": accounting_ok,
            "secret_leaks": leaked,
        }
        log(
            f"\n花费合计 {total} 元（任务账 {task_total} / 接口 {api_task_cost}，"
            f"对得上={accounting_ok}），调用 {len(rows)} 次，密钥泄漏行 {leaked}"
        )

        out_dir = ROOT / "content" / "acceptance" / "v6"
        out_dir.mkdir(parents=True, exist_ok=True)
        report["finished_at"] = datetime.now(UTC).isoformat()
        target = out_dir / os.environ.get("FM_ACCEPT_OUT", "acceptance.json")
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        log(f"验收结果写入 {target}")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        if not os.environ.get("FM_KEEP_ACCEPT_DB"):
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
