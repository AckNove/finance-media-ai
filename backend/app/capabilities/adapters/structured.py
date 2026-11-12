"""带定位信息的文档抽取：页码 / 工作表 / 行列 / 段落序号。

V5.0 的硬要求：「**尽量保留页码/段落/表格/行列/工作表/数据定位**，
不能只变成一段无定位文字」。``markitdown_adapter`` 给的是整篇纯文本，
够读、不足以引用；本模块补上一层"这句话出自第几页第几行"。

设计边界：

* **不自己写格式解析器**：PDF 用 pdfplumber（MarkItDown 的依赖之一）、
  XLSX 用 openpyxl、CSV 用标准库 csv、DOCX 用 python-docx（若装了）；
* **拿不到就留空**：某一段抽不出页码，就把 ``page`` 留 ``None``，不猜；
* **tabular 数据优先出表**：Excel / CSV / PDF 表格都尽量还原成
  ``rows``，并给每行 A1 地址或 PDF 页码 + 行号，供证据系统引用。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 单个文档最多保留多少行表格数据（防止 10 万行报表把内存撑爆）
DEFAULT_MAX_TABLE_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class TextSpan:
    """一段带定位的文本。"""

    locator: str
    text: str
    kind: str = "text"  # text / table_row / heading
    page: int | None = None
    sheet: str = ""
    row: int | None = None
    char_start: int = 0
    char_end: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "kind": self.kind,
            "page": self.page,
            "sheet": self.sheet,
            "row": self.row,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class TableBlock:
    """一张表（含表头与行地址）。"""

    locator: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    page: int | None = None
    sheet: str = ""
    row_start: int | None = None
    row_end: int | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "page": self.page,
            "sheet": self.sheet,
            "row_start": self.row_start,
            "row_end": self.row_end,
            "header": list(self.header),
            "rows": [list(row) for row in self.rows],
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class StructuredDocument:
    """一次结构化抽取的结果。"""

    kind: str
    source_path: str = ""
    text: str = ""
    spans: tuple[TextSpan, ...] = ()
    tables: tuple[TableBlock, ...] = ()
    page_count: int | None = None
    sheet_names: tuple[str, ...] = ()
    extractor: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def locate(self, quote: str, *, limit: int = 5) -> list[TextSpan]:
        """找出包含 ``quote`` 的段落（用于把一句证据锚到具体位置）。"""
        needle = (quote or "").strip()
        if not needle:
            return []
        return [span for span in self.spans if needle in span.text][:limit]

    def as_dict(self, *, max_rows: int | None = None) -> dict[str, Any]:
        tables = []
        for table in self.tables:
            payload = table.as_dict()
            if max_rows is not None and len(payload["rows"]) > max_rows:
                payload["rows"] = payload["rows"][:max_rows]
                payload["rows_truncated"] = True
            tables.append(payload)
        return {
            "kind": self.kind,
            "source_path": self.source_path,
            "extractor": self.extractor,
            "char_count": self.char_count,
            "page_count": self.page_count,
            "sheet_names": list(self.sheet_names),
            "table_count": len(self.tables),
            "spans": [span.as_dict() for span in self.spans],
            "tables": tables,
            "meta": dict(self.meta),
        }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _assign_offsets(spans: Sequence[TextSpan], text: str) -> tuple[TextSpan, ...]:
    """按顺序把 ``char_start/char_end`` 填上（定位到拼接后的全文）。"""
    cursor = 0
    out: list[TextSpan] = []
    for span in spans:
        needle = span.text
        start = text.find(needle, cursor) if needle else -1
        if start < 0:
            start = cursor
        end = start + len(needle)
        out.append(
            TextSpan(
                locator=span.locator,
                text=span.text,
                kind=span.kind,
                page=span.page,
                sheet=span.sheet,
                row=span.row,
                char_start=start,
                char_end=end,
            )
        )
        cursor = end
    return tuple(out)


def _join(spans: Sequence[TextSpan]) -> str:
    return "\n\n".join(span.text for span in spans if span.text)


# --- PDF ------------------------------------------------------------------


def extract_pdf(path: Path, *, max_table_rows: int = DEFAULT_MAX_TABLE_ROWS) -> StructuredDocument:
    import pdfplumber

    spans: list[TextSpan] = []
    tables: list[TableBlock] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            try:
                page_text = _clean(page.extract_text() or "")
            except Exception:  # 单页抽不出文字（扫描件）不影响其他页
                page_text = ""
            if page_text:
                spans.append(
                    TextSpan(locator=f"p{index}", text=page_text, kind="text", page=index)
                )
            try:
                found = page.extract_tables() or []
            except Exception:
                found = []
            for t_index, raw in enumerate(found, start=1):
                rows = [tuple(_clean(cell) for cell in row) for row in raw if row]
                if not rows:
                    continue
                header, body = rows[0], tuple(rows[1:])
                if len(body) > max_table_rows:
                    body = body[:max_table_rows]
                tables.append(
                    TableBlock(
                        locator=f"p{index}-t{t_index}",
                        header=header,
                        rows=body,
                        page=index,
                        row_start=1,
                        row_end=len(body),
                    )
                )
    text = _join(spans)
    return StructuredDocument(
        kind="pdf_text",
        source_path=str(path),
        text=text,
        spans=_assign_offsets(spans, text),
        tables=tuple(tables),
        page_count=page_count,
        extractor="pdfplumber",
    )


# --- XLSX -----------------------------------------------------------------


def extract_xlsx(path: Path, *, max_table_rows: int = DEFAULT_MAX_TABLE_ROWS) -> StructuredDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    spans: list[TextSpan] = []
    tables: list[TableBlock] = []
    names: list[str] = []
    try:
        for sheet in workbook.worksheets:
            names.append(sheet.title)
            rows: list[tuple[str, ...]] = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                values = tuple(_clean(cell) for cell in row)
                if not any(values):
                    continue
                rows.append(values)
                spans.append(
                    TextSpan(
                        locator=f"{sheet.title}!{row_index}",
                        text=" | ".join(value for value in values if value),
                        kind="table_row",
                        sheet=sheet.title,
                        row=row_index,
                    )
                )
                if len(rows) > max_table_rows:
                    break
            if not rows:
                continue
            header = rows[0]
            body = tuple(rows[1:])
            tables.append(
                TableBlock(
                    locator=f"{sheet.title}!A1",
                    header=header,
                    rows=body,
                    sheet=sheet.title,
                    row_start=2,
                    row_end=1 + len(body),
                )
            )
    finally:
        workbook.close()

    text = _join(spans)
    return StructuredDocument(
        kind="spreadsheet_rows",
        source_path=str(path),
        text=text,
        spans=_assign_offsets(spans, text),
        tables=tuple(tables),
        sheet_names=tuple(names),
        extractor="openpyxl",
    )


# --- CSV / TSV ------------------------------------------------------------


def extract_csv(path: Path, *, delimiter: str | None = None) -> StructuredDocument:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - 上面最后一档 latin-1 不会失败
        content = raw.decode("utf-8", errors="replace")

    if delimiter is None:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = [tuple(_clean(cell) for cell in row) for row in reader]
    rows = [row for row in rows if any(row)]
    spans: list[TextSpan] = []
    for row_index, row in enumerate(rows, start=1):
        spans.append(
            TextSpan(
                locator=f"r{row_index}",
                text=" | ".join(value for value in row if value),
                kind="table_row",
                row=row_index,
            )
        )
    header = rows[0] if rows else ()
    body = tuple(rows[1:])
    tables: tuple[TableBlock, ...] = ()
    if rows:
        tables = (
            TableBlock(
                locator="r1",
                header=header,
                rows=body,
                row_start=1,
                row_end=len(body),
            ),
        )
    text = _join(spans)
    return StructuredDocument(
        kind="spreadsheet_rows",
        source_path=str(path),
        text=text,
        spans=_assign_offsets(spans, text),
        tables=tables,
        extractor="csv",
        meta={"delimiter": delimiter, "row_count": len(rows)},
    )


# --- DOCX -----------------------------------------------------------------


def extract_docx(path: Path) -> StructuredDocument:
    try:
        import docx  # python-docx
    except Exception:  # pragma: no cover - 未安装时走 markitdown 兜底
        return _markitdown_fallback(path, kind="raw_text")

    document = docx.Document(str(path))
    spans: list[TextSpan] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = _clean(paragraph.text)
        if not text:
            continue
        spans.append(
            TextSpan(
                locator=f"p{index}",
                text=text,
                kind="heading" if paragraph.style and "Heading" in str(paragraph.style.name) else "text",
            )
        )
    tables: list[TableBlock] = []
    for t_index, table in enumerate(document.tables, start=1):
        rows = [tuple(_clean(cell.text) for cell in row.cells) for row in table.rows]
        if not rows:
            continue
        tables.append(
            TableBlock(
                locator=f"t{t_index}",
                header=rows[0],
                rows=tuple(rows[1:]),
                row_start=1,
                row_end=len(rows) - 1,
            )
        )
    text = _join(spans)
    return StructuredDocument(
        kind="raw_text",
        source_path=str(path),
        text=text,
        spans=_assign_offsets(spans, text),
        tables=tuple(tables),
        extractor="python-docx",
        meta={"paragraph_count": len(spans), "table_count": len(tables)},
    )


# --- 兜底 -----------------------------------------------------------------


def _markitdown_fallback(path: Path, *, kind: str = "raw_text") -> StructuredDocument:
    from backend.app.capabilities.adapters.markitdown_adapter import MarkItDownParser

    parsed = MarkItDownParser().parse(path.read_bytes(), filename=path.name)
    text = parsed.text
    spans = (TextSpan(locator="doc", text=text, kind="text"),) if text else ()
    return StructuredDocument(
        kind=kind,
        source_path=str(path),
        text=text,
        spans=_assign_offsets(spans, text),
        page_count=parsed.page_count,
        extractor=f"markitdown:{parsed.parser_version}",
    )


_EXTRACTORS: dict[str, Any] = {
    ".pdf": extract_pdf,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".csv": extract_csv,
    ".tsv": extract_csv,
    ".docx": extract_docx,
}


def extract_structured(path: str | Path, *, max_table_rows: int = DEFAULT_MAX_TABLE_ROWS) -> StructuredDocument:
    """按后缀选择抽取器；不认识的格式走 MarkItDown 兜底（丢定位但仍有正文）。"""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {target}")
    extractor = _EXTRACTORS.get(target.suffix.lower())
    if extractor is None:
        return _markitdown_fallback(target)
    if extractor is extract_csv:
        return extractor(target)
    return extractor(target, max_table_rows=max_table_rows)


def locate_quote(document: StructuredDocument, quote: str) -> list[dict[str, Any]]:
    """把一句引文锚定到 ``locator``（页码/工作表行/CSV 行号）。"""
    return [span.as_dict() for span in document.locate(quote)]


def iter_rows(document: StructuredDocument) -> Iterable[tuple[str, tuple[str, ...]]]:
    """遍历所有表格行，产出 ``(locator, row)``。"""
    for table in document.tables:
        for offset, row in enumerate(table.rows, start=1):
            base = table.row_start or 1
            yield f"{table.locator}#{base + offset - 1}", row


__all__ = [
    "DEFAULT_MAX_TABLE_ROWS",
    "StructuredDocument",
    "TableBlock",
    "TextSpan",
    "extract_csv",
    "extract_docx",
    "extract_pdf",
    "extract_structured",
    "extract_xlsx",
    "iter_rows",
    "locate_quote",
]
