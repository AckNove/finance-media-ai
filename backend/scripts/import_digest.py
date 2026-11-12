"""把一份已落盘的底稿（`content/research/<task>/digest.json`）导进一个库里。

用途：让评审者不必重跑联网研究，也能从仓库里已有的真实底稿复现"报告 → 成片"这条链。
导入过程只做搬运：底稿里的来源、数据点、印证结果原样写进 sources / research_reports /
evidence 三张表，不改写任何数字，也不补任何来源。

典型用法（独立示例库，绝不碰 data/finance_media.db）：

    python -m backend.scripts.import_digest --digest content/research/task-1/digest.json \
        --database-url sqlite:///./data/demo.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy.orm import sessionmaker

from backend.app.core.config import Settings
from backend.app.database.base import Base
from backend.app.database.session import build_engine
from backend.app.research.digest import DataPoint, Digest, SourceRef
from backend.app.research.persistence import (
    record_evidence,
    record_report,
    record_sources,
)
from backend.app.research.verify import verify_points

PROTECTED_DB_SUFFIX = "data/finance_media.db"


def _is_protected(database_url: str) -> bool:
    normalised = (database_url or "").replace("\\", "/").rstrip("/")
    return normalised.endswith(PROTECTED_DB_SUFFIX)


def digest_from_json(payload: dict[str, Any]) -> Digest:
    """把 digest.json 还原成对象。字段缺了就当缺，不猜。"""
    sources = [
        SourceRef(
            source_id=str(item.get("source_id") or ""),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            site_name=str(item.get("site_name") or ""),
            author=str(item.get("author") or ""),
            published_at=str(item.get("published_at") or ""),
            sha256=str(item.get("sha256") or ""),
            snapshot_path=str(item.get("snapshot_path") or ""),
            char_count=int(item.get("char_count") or 0),
            query=str(item.get("query") or ""),
        )
        for item in payload.get("sources") or []
    ]
    points = [
        DataPoint(
            source_id=str(item.get("source_id") or ""),
            text=str(item.get("text") or ""),
            numbers=tuple(str(number) for number in (item.get("numbers") or ())),
        )
        for item in payload.get("data_points") or []
    ]
    meta = dict(payload.get("meta") or {})
    return Digest(
        title=str(payload.get("title") or ""),
        objective=str(payload.get("objective") or ""),
        sources=sources,
        data_points=points,
        queries=tuple(payload.get("queries") or ()),
        generated_at=str(payload.get("generated_at") or ""),
        generated_by=str(payload.get("generated_by") or "rules"),
        notes=tuple(payload.get("notes") or ()),
        meta=meta,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把底稿导入数据库（来源 + 报告 + 证据）")
    parser.add_argument("--digest", required=True, help="digest.json 路径")
    parser.add_argument("--database-url", required=True, help="目标库；默认拒绝开发库")
    parser.add_argument("--allow-development-db", action="store_true")
    args = parser.parse_args(argv)

    if _is_protected(args.database_url) and not args.allow_development_db:
        print(json.dumps({"ok": False, "error": "protected_database",
                          "message": "拒绝写入 data/finance_media.db；换个库或显式确认"},
                         ensure_ascii=False))
        return 2

    path = Path(args.digest)
    if not path.is_file():
        print(json.dumps({"ok": False, "error": "missing_digest", "message": str(path)},
                         ensure_ascii=False))
        return 3
    digest = digest_from_json(json.loads(path.read_text(encoding="utf-8")))

    engine = build_engine(Settings(database_url=args.database_url))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        recorded = record_sources(session, digest.sources)
        report = record_report(session, digest=digest)
        verification = verify_points(digest)
        evidence_rows = record_evidence(
            session, report=report, digest=digest,
            source_ids=recorded.mapping, verification=verification,
        )
        session.commit()
        payload = {
            "ok": True,
            "database_url": args.database_url,
            "report_id": report.id,
            "sources_created": recorded.created,
            "sources_updated": recorded.updated,
            "evidence_rows": len(evidence_rows),
            "data_points": len(digest.data_points),
            "next": f"python -m backend.scripts.produce_video --report-id {report.id} "
                    f"--database-url {args.database_url} --no-render",
        }
    engine.dispose()
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
