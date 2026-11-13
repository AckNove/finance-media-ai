"""账号数据 CSV 批量导入：能写的写、撞时间的跳过、坏行带行号报出来。

全部离线。解析是纯函数（``parse_metrics_csv``），落库走 ``OperationsService``。
"""

from __future__ import annotations

import csv
import io

import pytest

from backend.app.services.metrics_import import CsvImportError, parse_metrics_csv
from backend.app.services.operations_service import OperationsService


def _csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue()


GOOD = _csv(
    [
        ["captured_at", "platform", "views", "likes", "comments", "shares", "followers_delta"],
        ["2026-11-01", "douyin", "12,345", "678", "12", "5", "30"],
        ["2026-11-02 08:30", "douyin", "20000", "900", "20", "8", "41"],
    ]
)


# --- 解析层 -----------------------------------------------------------------


def test_parses_chinese_headers_and_thousand_separators() -> None:
    text = _csv(
        [
            ["采集时间", "平台", "播放量", "点赞", "评论", "涨粉"],
            ["2026年11月1日", "douyin", "1,234", "56", "7", "3"],
        ]
    )
    result = parse_metrics_csv(text, platform="douyin")
    assert result.ok
    assert len(result.imported) == 1
    row = result.imported[0]
    assert (row.views, row.likes, row.comments, row.followers_delta) == (1234, 56, 7, 3)
    assert row.captured_at.year == 2026


def test_missing_time_column_is_refused_whole() -> None:
    text = _csv([["views", "likes"], ["1", "2"]])
    with pytest.raises(CsvImportError) as error:
        parse_metrics_csv(text)
    assert error.value.reason == "missing_column"
    assert "采集时间" in error.value.message


def test_missing_all_count_columns_is_refused() -> None:
    text = _csv([["captured_at"], ["2026-11-01"]])
    with pytest.raises(CsvImportError) as error:
        parse_metrics_csv(text)
    assert error.value.reason == "missing_column"


def test_empty_csv_and_header_only_are_refused() -> None:
    with pytest.raises(CsvImportError) as empty:
        parse_metrics_csv("   ")
    assert empty.value.reason == "empty_csv"
    with pytest.raises(CsvImportError) as only_header:
        parse_metrics_csv("captured_at,views\n")
    assert only_header.value.reason == "no_rows"


def test_bad_rows_are_reported_with_line_numbers_not_guessed() -> None:
    text = _csv(
        [
            ["captured_at", "views"],
            ["2026-11-01", "100"],
            ["2026-11-02", "很多"],
            ["", "50"],
            ["2026-11-04", "-3"],
        ]
    )
    result = parse_metrics_csv(text)
    assert [row.views for row in result.imported] == [100]
    assert not result.ok
    lines = sorted(item["line"] for item in result.errors)
    assert lines == [3, 4, 5]
    assert "不是数字" in next(item["reason"] for item in result.errors if item["line"] == 3)


def test_platform_mismatch_is_a_row_error() -> None:
    text = _csv(
        [
            ["captured_at", "platform", "views"],
            ["2026-11-01", "xiaohongshu", "100"],
        ]
    )
    result = parse_metrics_csv(text, platform="douyin")
    assert result.imported == []
    assert "不一致" in result.errors[0]["reason"]


def test_blank_lines_are_skipped_silently() -> None:
    text = _csv([["captured_at", "views"], ["2026-11-01", "10"], ["", ""]])
    result = parse_metrics_csv(text)
    assert result.ok
    assert len(result.imported) == 1


def test_too_many_rows_is_refused() -> None:
    rows = [["captured_at", "views"]] + [[f"2026-01-{(i % 28) + 1:02d}", str(i)] for i in range(600)]
    with pytest.raises(CsvImportError) as error:
        parse_metrics_csv(_csv(rows))
    assert error.value.reason == "too_many_rows"


# --- 落库层 -----------------------------------------------------------------


def _entry(db_session):
    from backend.app.schemas.operations import CalendarCreate

    return OperationsService(db_session).create(
        CalendarCreate(title="储能价格战：一条视频", platform="douyin", status="planned")
    )


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    from backend.app.models.operations import ContentCalendar, PlatformMetric

    db_session.query(PlatformMetric).delete()
    db_session.query(ContentCalendar).delete()
    db_session.commit()
    yield


def test_import_writes_rows_and_marks_source_as_csv(db_session) -> None:
    entry = _entry(db_session)
    result = OperationsService(db_session).import_metrics(entry.id, GOOD)
    assert result["imported"] == 2
    assert result["skipped"] == 0
    rows = OperationsService(db_session).list_metrics(entry.id)
    assert {row.source for row in rows} == {"csv"}
    assert max(row.views for row in rows) == 20000


def test_import_skips_timestamps_that_already_exist(db_session) -> None:
    entry = _entry(db_session)
    service = OperationsService(db_session)
    service.import_metrics(entry.id, GOOD)
    again = service.import_metrics(entry.id, GOOD)
    assert again["imported"] == 0
    assert again["skipped"] == 2
    assert "历史不覆盖" in again["skipped_detail"][0]["reason"]


def test_import_reports_errors_but_keeps_good_rows(db_session) -> None:
    entry = _entry(db_session)
    text = _csv(
        [
            ["captured_at", "views"],
            ["2026-11-05", "10"],
            ["2026-11-06", "abc"],
        ]
    )
    result = OperationsService(db_session).import_metrics(entry.id, text)
    assert result["imported"] == 1
    assert result["errors"] == 1
    assert result["error_detail"][0]["line"] == 3


def test_api_endpoint_imports_and_reports_bad_csv(client) -> None:
    created = client.post(
        "/api/calendar",
        json={"title": "光伏产业链：一条视频", "platform": "douyin", "status": "planned"},
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]

    ok = client.post(f"/api/calendar/{entry_id}/metrics/import", json={"csv_text": GOOD})
    assert ok.status_code == 200, ok.text
    assert ok.json()["imported"] == 2

    bad = client.post(
        f"/api/calendar/{entry_id}/metrics/import",
        json={"csv_text": "views,likes\n1,2\n"},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["reason"] == "missing_column"


def test_api_endpoint_404s_for_unknown_entry(client) -> None:
    response = client.post("/api/calendar/9999/metrics/import", json={"csv_text": GOOD})
    assert response.status_code == 404
