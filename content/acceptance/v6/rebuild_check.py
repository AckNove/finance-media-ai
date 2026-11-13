"""V6.0 收尾自检：用保留的验收库重建一份视频任务，验证可追溯性与内容质检。

只读打开验收临时库，重建 job（不再调模型、不花钱），跑 local TTS + 渲染 + 内容质检，
把结论打到 stdout。真实库 data/finance_media.db 一律拒绝。
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\25803\finance-media-ai")
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app.models.evidence import Evidence  # noqa: E402
from backend.app.models.research import FindingCheck, ResearchReport  # noqa: E402
from backend.app.models.source import Source  # noqa: E402
from backend.app.research.draft_gate import confirmed_findings, readiness_of  # noqa: E402
from backend.app.video.content_qc import review_and_write  # noqa: E402
from backend.app.video.handoff import create_video_job  # noqa: E402

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def pick_db() -> Path:
    best = None
    for path in glob.glob(os.path.expandvars(r"%TEMP%\fmai_v6_accept_*\accept.db")):
        db = Path(path)
        if "finance_media.db" in str(db):
            continue
        import sqlite3

        try:
            with sqlite3.connect(db) as conn:
                confirmed = conn.execute(
                    "SELECT COUNT(*) FROM finding_checks WHERE human_review_status='confirmed'"
                ).fetchone()[0]
                reports = conn.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0]
        except Exception:
            continue
        if confirmed and reports:
            best = db
    return best  # type: ignore[return-value]


def main() -> int:
    db_path = pick_db()
    if db_path is None:
        print(json.dumps({"ok": False, "reason": "no_acceptance_db_with_confirmed_check"}, ensure_ascii=False))
        return 1
    print(json.dumps({"db": str(db_path)}, ensure_ascii=False))

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        report = session.query(ResearchReport).order_by(ResearchReport.id).first()
        checks = (
            session.query(FindingCheck)
            .filter(FindingCheck.research_report_id == report.id)
            .all()
        )
        gate = readiness_of(session, report)
        confirmed = confirmed_findings(session, report)
        evidence = list(
            session.scalars(select(Evidence).where(Evidence.research_report_id == report.id))
        )
        source_ids = {item.source_id for item in evidence}
        sources = (
            list(session.scalars(select(Source).where(Source.id.in_(sorted(source_ids)))))
            if source_ids
            else []
        )
        print(json.dumps({
            "report_id": report.id,
            "checks": len(checks),
            "gate": gate.as_dict(),
            "evidence": len(evidence),
            "sources": len(sources),
            "confirmed": [item["text"][:40] for item in confirmed],
        }, ensure_ascii=False))
        job = create_video_job(
            report,
            evidence=evidence,
            sources=sources,
            job_id="v6_accept_company_v2",
            confirmed=confirmed,
        )
        print(json.dumps({"job": {k: str(v) for k, v in job.as_dict().items()}}, ensure_ascii=False))

    # 目录一律用 create_video_job 返回的 scaffold.path：写死目录名会在改 job_id 时
    # 渲染错任务（上一版就是这样报 unknown_job 的）。
    job_root = getattr(job, "path", None) or getattr(getattr(job, "scaffold", None), "path", None)
    if job_root is None:
        raise SystemExit(f"拿不到任务目录：{job.as_dict()}")
    job_dir = Path(job_root).resolve()
    render = subprocess.run(
        [PY, "-m", "backend.scripts.render_job", "--job", job_dir.name, "--narration",
         "--output", "output/final.mp4"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print("render rc", render.returncode, (render.stdout or "").strip().splitlines()[-1:])

    qc, paths = review_and_write(job_dir.name, root=ROOT / "content" / "jobs")
    print(
        "content_qc",
        json.dumps(
            {
                "ok": qc.ok,
                "errors": [item.check for item in qc.errors],
                "warnings": [item.check for item in qc.warnings],
                "stats": qc.stats,
                "files": paths,
            },
            ensure_ascii=False,
        ),
    )
    engine.dispose()
    return 0 if qc.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
