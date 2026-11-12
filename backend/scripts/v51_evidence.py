"""V5.1 验收证据导出（只读临时库，不碰真实开发库）。

用法：python -m backend.scripts._v51_evidence [--db <路径>]
不给 --db 时自动挑 %TEMP% 下最新的一次 e2e 临时库。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import tempfile


def newest_temp_db() -> pathlib.Path:
    root = pathlib.Path(tempfile.gettempdir())
    candidates = sorted(
        root.glob("fmai_model_e2e_*/flow.db"), key=lambda p: p.stat().st_mtime
    )
    if not candidates:
        raise SystemExit("没找到临时库，先跑一次 model_e2e_check")
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)
    db = pathlib.Path(args.db) if args.db else newest_temp_db()
    print(f"db = {db}")
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    task = dict(con.execute("SELECT * FROM research_tasks").fetchone())
    keep = {
        k: task[k]
        for k in (
            "id",
            "title",
            "status",
            "calls_spent",
            "tokens_spent",
            "cost_spent",
            "budget_max_calls",
            "budget_max_cost",
            "failure_reason",
        )
        if k in task
    }
    print("task =", json.dumps(keep, ensure_ascii=False))

    print("subtasks =")
    for row in con.execute(
        "SELECT sequence, task_type, status, attempts, failure_reason "
        "FROM research_subtasks ORDER BY sequence"
    ):
        print("  ", dict(row))

    plan_row = con.execute(
        "SELECT output_payload FROM research_subtasks WHERE task_type='research.plan'"
    ).fetchone()
    if plan_row:
        plan = json.loads(plan_row[0] or "{}")
        print(
            "plan =",
            json.dumps(
                {
                    "planner": plan.get("planner"),
                    "model_used": plan.get("model_used"),
                    "degraded_reason": plan.get("degraded_reason"),
                    "objective": plan.get("objective"),
                    "core_questions": len(plan.get("core_questions") or []),
                    "subtasks": len(plan.get("subtasks") or []),
                    "queries": len(plan.get("queries") or []),
                    "risks": len(plan.get("risks") or []),
                    "gaps": len(plan.get("gaps") or []),
                    "proposed_subtasks": len(plan.get("proposed_subtasks") or []),
                },
                ensure_ascii=False,
            ),
        )

    print("finding_checks =")
    rows = list(
        con.execute(
            "SELECT finding_index, verdict, human_review_status, claim, reason, "
            "supporting, contradicting, missing, issues, evidence_ids, source_ids, "
            "provider, model, model_call_id, prompt_tokens, completion_tokens, "
            "cost_cny, checked_at FROM finding_checks ORDER BY finding_index"
        )
    )
    for row in rows:
        print(
            "  ",
            json.dumps(
                {k: row[k] for k in row.keys() if k not in ("supporting", "issues")},
                ensure_ascii=False,
            ),
        )
        print("      supporting =", row["supporting"])
        print("      issues =", row["issues"])
    print(f"checks total = {len(rows)}")

    print("model_calls =")
    total = 0.0
    for row in con.execute(
        "SELECT purpose, provider, model, prompt_tokens, completion_tokens, "
        "cost_estimate, pricing_unknown, status, prompt_preview "
        "FROM model_calls ORDER BY id"
    ):
        total += float(row["cost_estimate"] or 0)
        print(
            "  ",
            row["purpose"],
            row["provider"],
            row["model"],
            f"{row['prompt_tokens']}/{row['completion_tokens']}",
            f"{float(row['cost_estimate'] or 0):.6f}",
            row["status"],
            "preview_has_key=",
            "sk-" in (row["prompt_preview"] or ""),
        )
    print(f"model cost total = {total:.6f} 元")

    for table in ("sources", "evidence", "research_reports", "finding_checks"):
        print(f"{table} rows =", con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
