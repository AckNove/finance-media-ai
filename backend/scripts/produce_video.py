"""一条命令把"已审核的研究报告"变成"可播放的成片"。

真实生产里没人愿意记三条命令：先建视频任务、再渲时间轴、再看出片。这个脚本把它们
串起来，每一步都打印真实产物路径；任何一步缺东西就停在那一步并说明缺什么，不跳过、
不伪造。

用法：
    python -m backend.scripts.produce_video --report-id 1 --narration
    python -m backend.scripts.produce_video --report-id 1 --no-render   # 只排分镜与时间轴
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy.orm import sessionmaker

from backend.app.core.config import PROJECT_ROOT, Settings, get_settings
from backend.app.database.base import Base
from backend.app.database.session import build_engine
from backend.app.models.evidence import Evidence
from backend.app.models.research import ResearchReport
from backend.app.models.source import Source
from backend.app.research.draft_gate import confirmed_findings, readiness_of
from backend.app.video.handoff import VideoHandoffError, create_video_job
from backend.app.video.render import RenderError, render_job

PROTECTED_DB_SUFFIX = "data/finance_media.db"


def _is_protected(database_url: str) -> bool:
    normalised = (database_url or "").replace("\\", "/").rstrip("/")
    return normalised.endswith(PROTECTED_DB_SUFFIX)


def _load_report(session: Any, report_id: int) -> tuple[ResearchReport, list[Any], list[Any]]:
    report = session.get(ResearchReport, report_id)
    if report is None:
        raise LookupError(f"没有 id={report_id} 的研究报告")
    evidence = (
        session.query(Evidence)
        .filter(Evidence.research_report_id == report_id)
        .order_by(Evidence.id)
        .all()
    )
    source_ids = {row.source_id for row in evidence if row.source_id}
    sources = (
        session.query(Source).filter(Source.id.in_(source_ids)).order_by(Source.id).all()
        if source_ids
        else []
    )
    return report, evidence, sources


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="研究报告 → 视频任务 → 成片")
    parser.add_argument("--report-id", type=int, required=True, help="research_reports.id")
    parser.add_argument("--job-id", default=None, help="指定任务目录名，默认按报告 id 生成")
    parser.add_argument("--database-url", default=None, help="默认读 config/settings.yaml")
    parser.add_argument("--allow-development-db", action="store_true",
                        help="显式允许在 data/finance_media.db 上跑（默认拒绝）")
    parser.add_argument("--narration", action="store_true", help="合成旁白音轨（需要能连 TTS）")
    parser.add_argument("--no-render", action="store_true", help="只排分镜与时间轴，不渲染")
    parser.add_argument("--jobs-root", default=None, help="任务根目录，默认 content/jobs")
    args = parser.parse_args(argv)

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    if _is_protected(database_url) and not args.allow_development_db:
        print(json.dumps({"ok": False, "error": "protected_database",
                          "message": f"{database_url} 是开发库，默认拒绝写入；"
                                     "确需执行请显式加 --allow-development-db"},
                         ensure_ascii=False))
        return 2

    engine = build_engine(Settings(database_url=database_url))
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    jobs_root = Path(args.jobs_root) if args.jobs_root else PROJECT_ROOT / "content" / "jobs"

    with session_factory() as session:
        try:
            report, evidence, sources = _load_report(session, args.report_id)
        except LookupError as error:
            print(json.dumps({"ok": False, "stage": "handoff", "error": "unknown_report",
                              "message": str(error)}, ensure_ascii=False))
            return 3
        try:
            scaffold = create_video_job(
                report,
                evidence=evidence,
                sources=sources,
                job_id=args.job_id or f"report_{args.report_id}",
                root=jobs_root,
                confirmed=confirmed_findings(session, report),
            )
        except VideoHandoffError as error:
            payload = {"ok": False, "stage": "handoff", "error": error.reason,
                       "message": str(error)}
            if error.reason == "draft_not_confirmed":
                state = readiness_of(session, report)
                payload["draft_readiness"] = state.as_dict()
                payload["next_step"] = (
                    "先到工作台 #/checks?report=<id> 逐条人工确认结论，"
                    "或者 POST /api/research-reports/<id>/checks/<check_id>/review"
                )
            print(json.dumps(payload, ensure_ascii=False))
            return 3

    print(json.dumps({"ok": True, "stage": "handoff", **scaffold.as_dict()},
                     ensure_ascii=False))

    if scaffold.blocked:
        print(json.dumps({"ok": False, "stage": "storyboard",
                          "reason": scaffold.blocked_reason, "next_step": scaffold.next_step},
                         ensure_ascii=False))
        return 4

    try:
        render_report = render_job(
            Path(scaffold.path),
            narration=args.narration,
            do_render=not args.no_render,
        )
    except RenderError as error:
        print(json.dumps({"ok": False, "stage": "render", "error": error.reason,
                          "message": str(error)}, ensure_ascii=False))
        return 5

    payload = render_report.as_dict()
    # 只排时间轴（--no-render）时没有成片是预期的，不算失败
    ok = bool(render_report.ok) or (args.no_render and render_report.stage == "timeline_ready")
    print(json.dumps({"ok": ok, "stage": render_report.stage,
                      "job_id": render_report.job_id,
                      "output": render_report.output_path,
                      "output_sha256": render_report.output_sha256,
                      "output_byte_size": render_report.output_byte_size,
                      "frames": render_report.frames,
                      "qc": render_report.qc,
                      "notes": payload.get("notes", [])}, ensure_ascii=False))
    return 0 if ok else 6


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
