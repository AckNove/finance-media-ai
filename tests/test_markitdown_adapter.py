"""MarkItDown 适配器：第一个真实工具实现的回归测试。

这些用例不联网、不读用户目录：CSV/XLSX/PDF 都在测试内即时生成（openpyxl +
matplotlib），因此可以在任何机器上重复跑。
"""

from __future__ import annotations

import pytest

from backend.app.capabilities.adapters import markitdown_adapter as adapter
from backend.app.capabilities.tools import get_tool_registry
from backend.app.core.caller import CallerContext
from backend.app.parsers import ParsedDocument

pytestmark = pytest.mark.skipif(
    not adapter.is_available(), reason="未安装 markitdown（pip install 'markitdown[pdf,docx,xlsx,pptx]'）"
)


def _csv_bytes() -> bytes:
    return (
        "日期,收盘,开盘\n"
        "2025-12-31,2.079,2.082\n"
        "2025-12-30,2.070,2.055\n"
    ).encode("utf-8")


def _xlsx_path(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "收益率"
    ws.append(["日期", "收盘"])
    ws.append(["2025-12-31", 2.079])
    ws.append(["2025-12-30", 2.070])
    target = tmp_path / "yields.xlsx"
    wb.save(target)
    return target


def _pdf_path(tmp_path):
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(4, 3))
    fig.text(0.1, 0.6, "JGB 10Y yield 2.079 percent", fontsize=12)
    target = tmp_path / "note.pdf"
    fig.savefig(target)
    plt.close(fig)
    return target


def _agent_caller() -> CallerContext:
    return CallerContext.for_executor(
        agent="financial_researcher",
        task_id="task-adapter",
        attempt_token="tok-adapter",
        db_scopes={"source_extractions"},
    )


# --- ParsedDocument 契约 ---------------------------------------------------


def test_parser_reports_version_and_supported_kinds():
    parser = adapter.MarkItDownParser()
    assert parser.name == "markitdown"
    assert parser.version not in {"", "missing"}
    assert {"pdf_text", "spreadsheet_rows"} <= set(parser.supported_kinds)


def test_parse_csv_bytes_returns_spreadsheet_rows():
    parsed = adapter.MarkItDownParser().parse(_csv_bytes(), filename="table.csv")
    assert isinstance(parsed, ParsedDocument)
    assert parsed.kind == "spreadsheet_rows"
    assert "2.079" in parsed.text
    assert parsed.span_offset_base == 0
    assert parsed.byte_size == len(_csv_bytes())


def test_parse_pdf_bytes_returns_pdf_text_with_page_count(tmp_path):
    payload = _pdf_path(tmp_path).read_bytes()
    parsed = adapter.MarkItDownParser().parse(payload, filename="note.pdf")
    assert parsed.kind == "pdf_text"
    assert parsed.text.strip(), "PDF 解析结果为空，不能算解析成功"
    assert parsed.page_count == 1


def test_parse_normalizes_crlf():
    separator = "\r\n"
    payload = ("a,b" + separator + "1,2" + separator).encode("utf-8")
    parsed = adapter.MarkItDownParser().parse(payload, filename="t.csv")
    assert separator not in parsed.text, "解析产物必须把 CRLF 规范化成 \\n"
    # MarkItDown 会把 CSV 转成 markdown 表格，这里断言值真的解析出来了
    assert "| a | b |" in parsed.text
    assert "| 1 | 2 |" in parsed.text


def test_parse_requires_filename_for_type_detection():
    with pytest.raises(ValueError):
        adapter.MarkItDownParser().parse(_csv_bytes(), filename=None)


# --- ToolResult 信封 -------------------------------------------------------


def test_xlsx_tool_returns_ok_envelope(tmp_path):
    result = adapter.parse_xlsx_tool(caller=_agent_caller(), path=str(_xlsx_path(tmp_path)))
    assert result.status == "ok", result.error_message
    data = result.data or {}
    assert data["kind"] == "spreadsheet_rows"
    assert data["parser"] == "markitdown"
    assert data["byte_size"] > 0
    assert len(data["sha256"]) == 64
    assert "2.079" in data["text"]
    assert result.billable is False


def test_pdf_tool_returns_ok_envelope(tmp_path):
    result = adapter.parse_pdf_tool(caller=_agent_caller(), path=str(_pdf_path(tmp_path)))
    assert result.status == "ok", result.error_message
    assert (result.data or {})["kind"] == "pdf_text"
    assert (result.data or {})["page_count"] == 1


def test_tool_rejects_wrong_suffix(tmp_path):
    csv_file = tmp_path / "table.csv"
    csv_file.write_bytes(_csv_bytes())
    result = adapter.parse_pdf_tool(caller=_agent_caller(), path=str(csv_file))
    assert result.status == "error"
    assert result.error_code == "unsupported_document_type"


def test_tool_reports_missing_file(tmp_path):
    result = adapter.parse_pdf_tool(caller=_agent_caller(), path=str(tmp_path / "nope.pdf"))
    assert result.status == "error"
    assert result.error_code == "source_not_found"


def test_tool_truncates_without_losing_counts(tmp_path):
    result = adapter.parse_xlsx_tool(
        caller=_agent_caller(), path=str(_xlsx_path(tmp_path)), max_chars=10
    )
    assert result.status == "ok"
    data = result.data or {}
    assert data["truncated"] is True
    assert data["returned_chars"] == 10
    assert data["char_count"] > 10


def test_tool_reports_not_configured_when_parser_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "is_available", lambda: False)
    result = adapter.parse_pdf_tool(caller=_agent_caller(), path=str(tmp_path / "x.pdf"))
    assert result.status == "not_configured"
    assert result.error_code == "parser_unavailable"


# --- 权限闸门（经默认注册表）-----------------------------------------------


def test_default_registry_denies_human_on_agent_only_parser():
    result = get_tool_registry().dispatch(CallerContext.human(), "parse_pdf", path="whatever.pdf")
    assert result.status == "denied"
    assert result.error_code == "agent_only"


def test_default_registry_denies_agent_without_scope(tmp_path):
    caller = CallerContext.for_executor(
        agent="financial_researcher", task_id="t", attempt_token="k", db_scopes={"sources"}
    )
    result = get_tool_registry().dispatch(caller, "parse_pdf", path=str(tmp_path / "x.pdf"))
    assert result.status == "denied"
    assert result.error_code == "scope_denied"


def test_unimplemented_tools_still_say_so():
    """适配器上线后，其余工具仍必须诚实返回 not_implemented。"""
    for tool in ("web_search", "llm_complete", "fact_check"):
        result = get_tool_registry().dispatch(CallerContext.human(), tool)
        assert result.status in {"denied", "not_implemented"}


# --- Word 文档（docx）-----------------------------------------------------


def _docx_path(tmp_path):
    """手写一个最小 docx（就是 zip + word/document.xml），不引新依赖。"""
    import zipfile

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>净息差从 2.10% 降到 1.52%。</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>手续费及佣金净收入占比 19.3%。</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    target = tmp_path / "notes.docx"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return target


def test_docx_is_parsed_through_markitdown(tmp_path):
    result = adapter.parse_docx_tool(caller=_agent_caller(), path=str(_docx_path(tmp_path)))
    assert result.status == "ok", result.error_message
    data = result.data or {}
    text = data["text"]
    assert "净息差" in text
    assert "19.3%" in text
    assert data["kind"] == "raw_text"
    assert data["parser"] == "markitdown"
    assert result.billable is False


def test_docx_tool_rejects_other_suffixes(tmp_path):
    target = tmp_path / "table.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    result = adapter.parse_docx_tool(caller=_agent_caller(), path=str(target))
    assert result.status == "error"
    assert result.error_code == "unsupported_document_type"


def test_docx_is_reachable_through_the_default_registry(tmp_path):
    result = get_tool_registry().dispatch(
        CallerContext.for_executor(
            agent="financial_researcher",
            task_id="t-docx",
            attempt_token="k-docx",
            db_scopes={"source_extractions"},
        ),
        "parse_docx",
        path=str(_docx_path(tmp_path)),
    )
    assert result.status == "ok", result.error_message
    assert "净息差" in (result.data or {})["text"]
