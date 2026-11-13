"""内容质检（I 模块）的离线测试：只核对可判定的东西，不判断好坏。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.app.video.content_qc import (
    CONTENT_QC_JSON,
    CONTENT_QC_MD,
    ContentQcError,
    review_and_write,
    review_job,
    summarise_numbers,
)

STORYBOARD_FIELDS = [
    "shot_id",
    "start_time",
    "end_time",
    "narration",
    "subtitle",
    "on_screen_text",
    "source_ids",
]


def _write_storyboard(job: Path, rows: list[dict[str, str]]) -> None:
    directory = job / "storyboard"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "storyboard.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STORYBOARD_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in STORYBOARD_FIELDS})


def _make_job(tmp_path: Path, name: str = "job") -> Path:
    job = tmp_path / name
    (job / "research").mkdir(parents=True)
    (job / "research" / "report.md").write_text(
        "# 报告\n\n2024 年组件价格跌到 0.68 元/W，行业毛利率降至 12.5%。\n", encoding="utf-8"
    )
    (job / "research" / "evidence.json").write_text(
        json.dumps(
            [{"text": "价格跌到 0.68 元/W", "original_excerpt": "毛利率降至 12.5%"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job


def test_clean_job_passes_with_no_errors(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(
        job,
        [
            {
                "shot_id": "S1",
                "narration": "组件价格跌到 0.68 元/W，这已经是现金成本线以下。",
                "subtitle": "组件价格跌到 0.68 元/W",
                "on_screen_text": "0.68 元/W",
                "source_ids": "S01",
            },
            {
                "shot_id": "S2",
                "narration": "行业毛利率降到 12.5%。",
                "subtitle": "行业毛利率降到 12.5%",
                "on_screen_text": "12.5%",
                "source_ids": "S02",
            },
        ],
    )

    report = review_job(job)
    assert report.ok, report.as_dict()
    assert report.errors == []
    assert report.stats["shots"] == 2
    assert report.stats["shots_with_narration"] == 2


def test_number_missing_from_material_is_an_error(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(
        job,
        [
            {
                "shot_id": "S1",
                "narration": "亏损面扩大到 47.9%。",
                "on_screen_text": "47.9%",
                "source_ids": "S01",
            }
        ],
    )

    report = review_job(job)
    assert not report.ok
    assert [item.check for item in report.errors] == ["number_not_in_material"]
    assert "47.9%" in report.errors[0].detail


def test_rounded_number_is_only_a_warning(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(
        job,
        [
            {
                "shot_id": "S1",
                "narration": "毛利率降到 12.5%。",
                "on_screen_text": "12.51%",
                "source_ids": "S01",
            }
        ],
    )

    report = review_job(job)
    assert report.ok, report.as_dict()
    assert [item.check for item in report.warnings] == ["number_rounded"]


def test_shot_without_source_is_an_error(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(job, [{"shot_id": "S1", "narration": "没有来源的一句", "source_ids": ""}])

    report = review_job(job)
    checks = [item.check for item in report.errors]
    assert "shot_without_source" in checks


def test_subtitle_not_from_narration_is_a_warning(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(
        job,
        [
            {
                "shot_id": "S1",
                "narration": "价格跌到现金成本线以下。",
                "subtitle": "另外写的一句字幕",
                "source_ids": "S01",
            }
        ],
    )

    report = review_job(job)
    assert "subtitle_not_from_narration" in [item.check for item in report.warnings]


def test_repeated_on_screen_text_is_a_warning(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(
        job,
        [
            {"shot_id": "S1", "narration": "第一句", "on_screen_text": "0.68 元/W", "source_ids": "S01"},
            {"shot_id": "S2", "narration": "第二句", "on_screen_text": "0.68 元/W", "source_ids": "S01"},
        ],
    )

    report = review_job(job)
    assert "repeated_on_screen" in [item.check for item in report.warnings]


def test_assets_need_commercial_licence(tmp_path: Path):
    job = _make_job(tmp_path)
    _write_storyboard(job, [{"shot_id": "S1", "narration": "有旁白", "source_ids": "S01"}])
    with (job / "assets_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asset_id", "path", "commercial_use"])
        writer.writeheader()
        writer.writerow({"asset_id": "A1", "path": "assets/a.png", "commercial_use": "allowed"})
        writer.writerow({"asset_id": "A2", "path": "assets/b.png", "commercial_use": "editorial_only"})
        writer.writerow({"asset_id": "A3", "path": "assets/c.png", "commercial_use": ""})

    report = review_job(job)
    checks = [item.check for item in report.errors]
    assert checks.count("asset_licence_not_commercial") == 1
    assert checks.count("asset_licence_missing") == 1
    assert report.stats["assets_listed"] == 3


def test_missing_storyboard_is_reported_as_blocked(tmp_path: Path):
    job = _make_job(tmp_path)
    try:
        review_job(job)
    except ContentQcError as error:
        assert error.reason == "missing_storyboard"
    else:  # pragma: no cover - 缺分镜必须报错
        raise AssertionError("缺分镜时应该抛 ContentQcError")


def test_review_and_write_outputs_both_files(tmp_path: Path):
    job = _make_job(tmp_path, "job-x")
    _write_storyboard(job, [{"shot_id": "S1", "narration": "有旁白", "source_ids": "S01"}])

    report, files = review_and_write("job-x", root=tmp_path)
    assert report.job_id == "job-x"
    assert Path(files["json"]).name == CONTENT_QC_JSON
    assert Path(files["markdown"]).name == CONTENT_QC_MD
    payload = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert "不判断内容好坏" in payload["note"]
    markdown = Path(files["markdown"]).read_text(encoding="utf-8")
    assert "内容质检" in markdown
    assert "不能说明内容合格" in markdown


def test_summarise_numbers_keeps_order_and_drops_duplicates():
    rows = [
        {"on_screen_text": "0.68 元/W", "narration": "跌到 0.68 元/W"},
        {"on_screen_text": "", "narration": "毛利率 12.5%"},
    ]
    assert summarise_numbers(rows) == ["0.68元/W", "12.5%"]


def test_api_runs_content_qc_for_a_job(monkeypatch, tmp_path: Path, client):
    from backend.app.api.routes import workspace as workspace_routes

    job = _make_job(tmp_path, "job-api")
    _write_storyboard(job, [{"shot_id": "S1", "narration": "有旁白", "source_ids": "S01"}])
    monkeypatch.setattr(workspace_routes, "JOBS_ROOT", tmp_path)

    response = client.post("/api/workspace/jobs/job-api/content-qc")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["job_id"] == "job-api"
    assert payload["files"]["json"].endswith(CONTENT_QC_JSON)


def test_api_reports_missing_storyboard_as_blocked(monkeypatch, tmp_path: Path, client):
    from backend.app.api.routes import workspace as workspace_routes

    job = _make_job(tmp_path, "job-empty")
    monkeypatch.setattr(workspace_routes, "JOBS_ROOT", tmp_path)

    response = client.post("/api/workspace/jobs/job-empty/content-qc")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["reason"] == "missing_storyboard"


def test_api_rejects_an_unknown_job(monkeypatch, tmp_path: Path, client):
    from backend.app.api.routes import workspace as workspace_routes

    monkeypatch.setattr(workspace_routes, "JOBS_ROOT", tmp_path)
    response = client.post("/api/workspace/jobs/nope/content-qc")
    assert response.status_code == 404
    assert response.json()["error"]["details"]["reason"] == "unknown_job"
