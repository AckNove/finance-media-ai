"""报告 → 视频任务 → 成片的端到端脚本，用临时库验证分派与落盘是否正确。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import Settings
from backend.app.database.base import Base
from backend.app.database.session import build_engine
from backend.app.models.evidence import Evidence
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.models.source import Source
from backend.scripts import produce_video


def _seed(tmp_path: Path) -> tuple[Any, int]:
    engine = build_engine(Settings(database_url=f"sqlite:///{tmp_path / 'produce.db'}"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        sources = [
            Source(
                title="净息差降至1.52%",
                url="https://finance.sina.com.cn/a.html",
                publisher="新浪财经",
                content_hash="a" * 64,
            ),
            Source(
                title="城商行净息差1.38%",
                url="http://www.ce.cn/b.html",
                publisher="中国经济网",
                content_hash="b" * 64,
            ),
        ]
        session.add_all(sources)
        session.flush()
        report = ResearchReport(
            title="商业银行是怎么赚钱的",
            summary="规则汇编（未使用模型）",
            findings=[
                {"text": "净息差降至1.52%", "kind": "fact", "source_ids": ["S01"]},
                {"text": "城市商业银行净息差为1.38%", "kind": "fact", "source_ids": ["S02"]},
            ],
            limitations="单一来源需佐证",
        )
        session.add(report)
        session.flush()
        evidence_rows = [
            Evidence(
                source_id=sources[0].id,
                research_report_id=report.id,
                claim="净息差降至1.52%",
                original_excerpt="净息差降至1.52%",
            ),
            Evidence(
                source_id=sources[1].id,
                research_report_id=report.id,
                claim="城市商业银行净息差为1.38%",
                original_excerpt="城商行1.38%",
            ),
        ]
        session.add_all(evidence_rows)
        session.flush()
        # 成稿闸门：只有人工确认过的结论可以进稿，所以夹具里必须先"确认"这两条。
        session.add_all(
            [
                FindingCheck(
                    research_report_id=report.id,
                    finding_index=index,
                    claim=row.claim,
                    verdict="supported",
                    reason="原文逐字可查，人工已核对",
                    evidence_ids=[row.id],
                    source_ids=[f"S{index + 1:02d}"],
                    human_review_status="confirmed",
                )
                for index, row in enumerate(evidence_rows)
            ]
        )
        session.commit()
        report_id = report.id
    engine.dispose()
    return factory, report_id


def test_protected_database_is_refused_without_the_flag() -> None:
    code = produce_video.main(
        ["--report-id", "1", "--database-url", "sqlite:///C:/x/data/finance_media.db"]
    )
    assert code == 2


def test_missing_report_is_refused(tmp_path: Path) -> None:
    code = produce_video.main(
        [
            "--report-id",
            "999",
            "--database-url",
            f"sqlite:///{tmp_path / 'empty.db'}",
            "--jobs-root",
            str(tmp_path / "jobs"),
        ]
    )
    assert code != 0


def test_a_report_becomes_a_storyboard_and_a_rendered_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report_id = _seed(tmp_path)
    jobs = tmp_path / "jobs"
    code = produce_video.main(
        [
            "--report-id",
            str(report_id),
            "--database-url",
            f"sqlite:///{tmp_path / 'produce.db'}",
            "--jobs-root",
            str(jobs),
            "--no-render",
        ]
    )
    printed = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert code == 0
    # 第一行是交接结果（它自带 stage=storyboard_ready），最后一行是渲染/排轴结果
    assert printed[0]["job_id"] == f"report_{report_id}"
    assert printed[0]["stage"] == "storyboard_ready"
    assert printed[-1]["stage"] == "timeline_ready"
    job_dir = jobs / f"report_{report_id}"
    # 分镜与交接材料都真的落在任务目录里
    assert (job_dir / "storyboard" / "storyboard.csv").is_file()
    assert (job_dir / "research" / "report.md").is_file()
    assert (job_dir / "research" / "sources.json").is_file()


def test_a_report_without_enough_sources_is_blocked_not_faked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = build_engine(Settings(database_url=f"sqlite:///{tmp_path / 'one.db'}"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        source = Source(
            title="单一来源",
            url="https://example.com/x.html",
            publisher="某个站点",
            content_hash="c" * 64,
        )
        session.add(source)
        session.flush()
        report = ResearchReport(
            title="只有一条来源",
            summary="规则汇编",
            findings=[{"text": "净息差降至1.52%", "kind": "fact", "source_ids": ["S01"]}],
            limitations="",
        )
        session.add(report)
        session.flush()
        evidence = Evidence(
            source_id=source.id,
            research_report_id=report.id,
            claim="净息差降至1.52%",
            original_excerpt="净息差降至1.52%",
        )
        session.add(evidence)
        session.flush()
        session.add(
            FindingCheck(
                research_report_id=report.id,
                finding_index=0,
                claim="净息差降至1.52%",
                verdict="supported",
                reason="原文逐字可查，人工已核对",
                evidence_ids=[evidence.id],
                source_ids=["S01"],
                human_review_status="confirmed",
            )
        )
        session.commit()
        report_id = report.id
    engine.dispose()

    code = produce_video.main(
        [
            "--report-id",
            str(report_id),
            "--database-url",
            f"sqlite:///{tmp_path / 'one.db'}",
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--no-render",
        ]
    )
    messages = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert code == 4
    assert messages[-1]["stage"] == "storyboard"
    assert messages[-1]["reason"] == "insufficient_sources"
