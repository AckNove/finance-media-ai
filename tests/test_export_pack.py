"""标签字段与交付包：从「备注当标签」到「一处拿全」。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.services.content_tags import (
    MAX_TAGS,
    dropped_tags,
    format_hashtags,
    normalise_tags,
    split_tags,
    tag_note,
)
from backend.app.services.export_service import (
    FILM_DIR_NAME,
    README_NAME,
    TAGS_NAME,
    ExportError,
    _attach_film,
    export_entry,
)
from backend.app.models.enums import CalendarStatus, ScriptStatus
from backend.app.models.operations import ContentCalendar
from backend.app.models.research import ResearchReport
from backend.app.models.script import Script
from backend.app.models.topic import Topic


# ---- 标签清洗 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("储能 电芯 #政策", ["储能", "电芯", "政策"]),
        ("储能,电芯、政策；产能", ["储能", "电芯", "政策", "产能"]),
        ("储能，储能，电芯", ["储能", "电芯"]),
        ("   ", []),
        ("", []),
        (None, []),
    ],
)
def test_split_tags_handles_separators_and_duplicates(raw: str | None, expected: list[str]) -> None:
    assert split_tags(raw) == expected


def test_normalise_tags_joins_with_single_spaces() -> None:
    assert normalise_tags(" 储能 ,, #电芯 ") == "储能 电芯"


def test_tags_are_capped_and_dropped_count_is_honest() -> None:
    many = " ".join(f"标签{i}" for i in range(MAX_TAGS + 3))
    assert len(split_tags(many)) == MAX_TAGS + 3
    assert len(normalise_tags(many).split()) == MAX_TAGS
    assert dropped_tags(many) == 3


def test_long_tag_is_clipped_not_rejected() -> None:
    long_tag = "储" * 40
    assert len(split_tags(long_tag)[0]) == 24


def test_format_hashtags_and_note() -> None:
    assert format_hashtags(["储能", "电芯"]) == "#储能 #电芯"
    assert "平台建议 3~5" in tag_note(["储能"], platform_min=3, platform_max=5)
    assert tag_note(["储", "能", "电", "芯", "价", "格"], platform_min=3, platform_max=5).startswith(
        "标签 6 个，超过"
    )
    assert tag_note([]) == "标签 0 个"


# ---- 交付包 --------------------------------------------------------------


def _topic(session) -> int:
    topic = Topic(title="储能行业", slug=f"energy-{datetime.now(UTC).timestamp()}")
    session.add(topic)
    session.commit()
    return topic.id


def _script(session, *, topic_id: int, report_id: int | None = None) -> Script:
    script = Script(
        topic_id=topic_id,
        research_report_id=report_id,
        title="储能这条链到底谁在赚钱",
        body="2024年储能装机 78.3GWh，同比增长 100%。电芯价格跌到 0.3 元/Wh。",
        segments=[{"text": "2024年储能装机 78.3GWh，同比增长 100%。", "role": "evidence"}],
        disclaimer="资料整理，不构成投资建议。",
        status=ScriptStatus.APPROVED.value,
        version=1,
    )
    session.add(script)
    session.commit()
    return script


def _entry(session, *, script_id: int, tags: str, notes: str = "", job_id: str | None = None):
    entry = ContentCalendar(
        title="储能那期",
        platform="douyin",
        status=CalendarStatus.RENDERED.value,
        script_id=script_id,
        video_job_id=job_id,
        notes=notes,
        tags=tags,
    )
    session.add(entry)
    session.commit()
    return entry


def _job(root: Path, job_id: str, *, film: bytes = b"fake-mp4-bytes") -> Path:
    job_dir = root / job_id
    (job_dir / "output").mkdir(parents=True)
    (job_dir / "output" / "final.mp4").write_bytes(film)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "draft_gate": {"usable": True},
                "outline": {"source": "script"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_dir


def test_tags_come_from_the_tags_field_not_from_notes(db_session, tmp_path) -> None:
    """这条是本次的核心：备注不再被当成标签。"""
    topic_id = _topic(db_session)
    script = _script(db_session, topic_id=topic_id)
    # 备注是一整句话（中文没有空格），以前会整段变成一个标签
    entry = _entry(db_session, script_id=script.id, tags="储能 电芯价格", notes="研究储能行业重点看电芯价格")
    jobs = tmp_path / "jobs"
    _job(jobs, "job-tags")

    result = export_entry(
        db_session,
        entry.id,
        root=tmp_path / "exports",
        jobs_root=jobs,
        now=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
    )

    assert result["tags"] == ["储能", "电芯价格"]
    assert "研究储能行业重点看电芯价格" not in result["tags"]
    text = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
    assert "#储能" in text and "#电芯价格" in text


def test_empty_tags_warns_instead_of_inventing_them(db_session, tmp_path) -> None:
    topic_id = _topic(db_session)
    script = _script(db_session, topic_id=topic_id)
    entry = _entry(db_session, script_id=script.id, tags="", notes="银行 利率")
    jobs = tmp_path / "jobs"

    result = export_entry(db_session, entry.id, root=tmp_path / "exports", jobs_root=jobs)

    assert result["tags"] == []
    assert any("没有标签" in item for item in result["warnings"])
    # 备注里的词没有被偷偷拿来当标签
    assert "银行" not in " ".join(result["tags"])


def test_export_pack_contains_film_readme_and_tags(db_session, tmp_path) -> None:
    topic_id = _topic(db_session)
    script = _script(db_session, topic_id=topic_id)
    entry = _entry(db_session, script_id=script.id, tags="储能 电芯 政策", job_id="job-pack")
    jobs = tmp_path / "jobs"
    _job(jobs, "job-pack", film=b"x" * 2048)

    result = export_entry(
        db_session,
        entry.id,
        root=tmp_path / "exports",
        jobs_root=jobs,
        now=datetime(2026, 5, 20, 10, 0, tzinfo=UTC),
    )

    base = Path(result["export_path"])
    assert (base / README_NAME).is_file()
    assert (base / TAGS_NAME).is_file()
    film = result["film_file"]
    assert film["status"] == "copied"
    assert film["relative"].startswith(FILM_DIR_NAME)
    copied = base / film["relative"]
    assert copied.is_file() and copied.stat().st_size == 2048
    assert film["method"] in {"hardlink", "copy"}

    readme = (base / README_NAME).read_text(encoding="utf-8")
    assert "交付包" in readme and FILM_DIR_NAME in readme and TAGS_NAME in readme

    tags_text = (base / TAGS_NAME).read_text(encoding="utf-8")
    assert "#储能" in tags_text

    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    assert meta["tags"] == ["储能", "电芯", "政策"]
    assert meta["film_file"]["status"] == "copied"


def test_export_pack_without_a_film_says_so(db_session, tmp_path) -> None:
    topic_id = _topic(db_session)
    script = _script(db_session, topic_id=topic_id)
    entry = _entry(db_session, script_id=script.id, tags="储能")

    result = export_entry(db_session, entry.id, root=tmp_path / "exports")

    assert result["film_file"]["status"] == "none"
    readme = (Path(result["export_path"]) / README_NAME).read_text(encoding="utf-8")
    assert "没绑视频任务" in readme


def test_missing_film_file_is_reported_not_silently_skipped(tmp_path) -> None:
    """片子拷到一半没了这种竞态：如实报出来，不许写一个空壳进包里。

    正常路径上「片子文件不在」会先被导出闸门拦成 `film_missing`（拒绝导出），
    所以这里直接单测 ``_attach_film``——它必须把事实说出来，而不是默默跳过。
    """
    base = tmp_path / "pack"
    base.mkdir()
    result = _attach_film(base, {"film": {"path": str(tmp_path / "never" / "final.mp4")}})

    assert result["status"] == "missing"
    assert "不在了" in result["note"]
    assert not (base / FILM_DIR_NAME).exists()


def test_film_copy_falls_back_when_hardlink_is_impossible(db_session, tmp_path, monkeypatch) -> None:
    """跨盘/无权限时退回复制——不能因为硬链接失败就导不出。"""
    topic_id = _topic(db_session)
    script = _script(db_session, topic_id=topic_id)
    entry = _entry(db_session, script_id=script.id, tags="储能", job_id="job-fallback")
    jobs = tmp_path / "jobs"
    _job(jobs, "job-fallback", film=b"y" * 4096)

    def boom(*args, **kwargs):
        raise OSError("cross-device link")

    monkeypatch.setattr("backend.app.services.export_service.os.link", boom)
    result = export_entry(db_session, entry.id, root=tmp_path / "exports", jobs_root=jobs)

    assert result["film_file"]["method"] == "copy"
    assert (Path(result["export_path"]) / result["film_file"]["relative"]).stat().st_size == 4096


def test_export_still_refuses_an_invalid_draft_even_with_tags(db_session, tmp_path) -> None:
    """标签齐全也不能绕过片子那一关。"""
    topic_id = _topic(db_session)
    report = ResearchReport(
        title="报告",
        summary="一句话",
        findings=[],
        limitations="探针数据",
        jurisdiction="cn",
        version=1,
    )
    db_session.add(report)
    db_session.commit()
    script = _script(db_session, topic_id=topic_id, report_id=report.id)
    entry = _entry(db_session, script_id=script.id, tags="储能", job_id="job-invalid")
    jobs = tmp_path / "jobs"
    job_dir = _job(jobs, "job-invalid")
    payload = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    payload["draft_gate"] = {"usable": False, "invalidated_reason": "人工把结论撤回"}
    (job_dir / "job.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ExportError) as error:
        export_entry(db_session, entry.id, root=tmp_path / "exports", jobs_root=jobs)

    assert error.value.reason == "draft_invalid"
    assert not (tmp_path / "exports").exists()
