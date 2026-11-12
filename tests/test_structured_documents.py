"""带定位信息的结构化抽取测试（页码 / 工作表 / 行列 / 段落序号）。

夹具全部在 ``tmp_path`` 里现造：PDF 用 matplotlib、XLSX 用 openpyxl、CSV 直接写，
不依赖仓库里的二进制文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.capabilities.adapters.structured import (
    StructuredDocument,
    extract_csv,
    extract_structured,
    extract_xlsx,
    iter_rows,
    locate_quote,
)


def make_pdf(target: Path, pages: int = 2) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # 用系统自带的微软雅黑，避免 matplotlib 默认字体缺中文字形刷一屏 warning
    for candidate in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(candidate).is_file():
            font_manager.fontManager.addfont(candidate)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=candidate).get_name()
            break

    for index in range(pages):
        figure = plt.figure(figsize=(4, 3))
        figure.text(0.1, 0.6, f"第 {index + 1} 页净息差说明 NIM {index + 1}.85%")
        figure.text(0.1, 0.4, f"Page {index + 1} revenue structure")
        target_page = target if index == 0 else target.with_name(f"page{index + 1}.pdf")
        figure.savefig(target_page, format="pdf")
        plt.close(figure)
    return target


def make_xlsx(target: Path) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "利润表"
    sheet.append(["项目", "2025", "2024"])
    sheet.append(["营业收入", 1200, 1100])
    sheet.append(["净利润", 320, 280])
    other = workbook.create_sheet("备注")
    other.append(["说明"])
    other.append(["单位为百万元"])
    workbook.save(target)
    return target


def test_csv_rows_get_sequential_locators(tmp_path: Path):
    path = tmp_path / "data.csv"
    path.write_text("项目,金额\n利息净收入,880\n手续费,120\n", encoding="utf-8")
    document = extract_csv(path)
    assert document.extractor == "csv"
    assert [span.locator for span in document.spans] == ["r1", "r2", "r3"]
    assert document.tables[0].header == ("项目", "金额")
    assert document.tables[0].rows == (("利息净收入", "880"), ("手续费", "120"))
    assert iter_rows(document).__next__()[0].startswith("r1#")


def test_xlsx_keeps_sheet_names_and_row_addresses(tmp_path: Path):
    path = make_xlsx(tmp_path / "report.xlsx")
    document = extract_xlsx(path)
    assert document.extractor == "openpyxl"
    assert document.sheet_names == ("利润表", "备注")
    locators = [span.locator for span in document.spans]
    assert "利润表!1" in locators and "利润表!2" in locators
    assert "备注!2" in locators
    income = next(table for table in document.tables if table.sheet == "利润表")
    assert income.header == ("项目", "2025", "2024")
    assert income.rows[0] == ("营业收入", "1200", "1100")
    assert income.row_start == 2


def test_xlsx_quote_can_be_located_to_a_row(tmp_path: Path):
    path = make_xlsx(tmp_path / "report.xlsx")
    document = extract_xlsx(path)
    anchors = locate_quote(document, "净利润")
    assert anchors
    assert anchors[0]["locator"] == "利润表!3"
    assert anchors[0]["sheet"] == "利润表"
    assert anchors[0]["row"] == 3


def test_pdf_reports_page_numbers_and_offsets(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    path = make_pdf(tmp_path / "one.pdf", pages=1)
    document = extract_structured(path)
    assert document.extractor == "pdfplumber"
    assert document.page_count == 1
    assert document.spans
    assert document.spans[0].page == 1
    assert document.spans[0].locator == "p1"
    assert "净息差" in document.spans[0].text
    assert document.spans[0].char_end > document.spans[0].char_start


def test_pdf_quote_anchor_points_at_the_page(tmp_path: Path):
    pytest.importorskip("pdfplumber")
    path = make_pdf(tmp_path / "two.pdf", pages=1)
    document = extract_structured(path)
    anchors = locate_quote(document, "NIM 1.85%")
    assert anchors and anchors[0]["page"] == 1


def test_unknown_suffix_falls_back_to_markitdown(tmp_path: Path):
    path = tmp_path / "notes.md"
    path.write_text("# 标题\n\n正文一段\n", encoding="utf-8")
    document = extract_structured(path)
    assert document.extractor.startswith("markitdown")
    assert document.spans[0].locator == "doc"
    assert "正文一段" in document.text


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        extract_structured(tmp_path / "nope.xlsx")


def test_locate_returns_empty_for_blank_quote():
    document = StructuredDocument(kind="raw_text", text="abc")
    assert document.locate("   ") == []
    assert locate_quote(document, "zzz") == []


def test_as_dict_truncates_long_tables(tmp_path: Path):
    path = tmp_path / "many.csv"
    lines = ["col"] + [f"v{index}" for index in range(50)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    document = extract_csv(path)
    payload = document.as_dict(max_rows=5)
    assert payload["tables"][0]["rows_truncated"] is True
    assert len(payload["tables"][0]["rows"]) == 5
    assert payload["table_count"] == 1


def test_parse_xlsx_tool_can_attach_structured_document(tmp_path: Path):
    from backend.app.capabilities.adapters.markitdown_adapter import parse_xlsx_tool

    path = make_xlsx(tmp_path / "report.xlsx")
    result = parse_xlsx_tool(caller=None, path=str(path), structured=True)
    assert result.status == "ok", result.error_message
    assert result.data["document"] is not None
    assert result.data["locations"]["extractor"] == "openpyxl"
    assert result.data["locations"]["tables"] == 2
    assert "利润表" in result.data["locations"]["sheets"]
