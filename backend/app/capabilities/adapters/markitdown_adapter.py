"""MarkItDown 适配器：``parse_pdf`` / ``parse_xlsx`` 的第一个真实实现。

设计原则（对应 m04268 任务书「直接复用，不重复造轮子」）：

* **不自己写解析器**：PDF / XLSX / DOCX / PPTX / HTML / CSV 的解析全部交给
  Microsoft MarkItDown（https://github.com/microsoft/markitdown，MIT）。
* **只写胶水**：把 MarkItDown 的 ``text_content`` 规范化成我们 ``ParsedDocument``
  契约要求的样子（换行统一 ``\\n``、去 BOM、带字符偏移基准与字节数），
  再包成 ``ToolResult``。
* **缺依赖就明说**：MarkItDown 没装时返回 ``status="not_configured"``，
  绝不返回空字符串冒充"解析成功"。
* **无网络**：全部本地解析；本适配器不发起任何网络请求。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import tempfile
from pathlib import Path
from typing import Any

from backend.app.parsers import ExtractionKind, ParsedDocument

PARSER_NAME = "markitdown"

#: 单次返回给调用方的最大字符数（避免把 20 万字直接塞进 Agent 上下文）
DEFAULT_MAX_CHARS = 120_000

#: 后缀 → 我们的抽取值类型
SUFFIX_KIND: dict[str, ExtractionKind] = {
    ".pdf": "pdf_text",
    ".xlsx": "spreadsheet_rows",
    ".xls": "spreadsheet_rows",
    ".csv": "spreadsheet_rows",
    ".tsv": "spreadsheet_rows",
    ".html": "html_text",
    ".htm": "html_text",
    ".docx": "raw_text",
    ".pptx": "raw_text",
    ".txt": "raw_text",
    ".md": "raw_text",
    ".json": "raw_text",
    ".xml": "raw_text",
    ".epub": "raw_text",
}

#: 本适配器负责的工具名 → 允许的后缀集合
TOOL_SUFFIXES: dict[str, frozenset[str]] = {
    "parse_pdf": frozenset({".pdf"}),
    "parse_xlsx": frozenset({".xlsx", ".xls", ".csv", ".tsv"}),
}


def parser_version() -> str:
    """MarkItDown 的实际安装版本；没装时返回 ``"missing"``。"""
    try:
        return importlib.metadata.version(PARSER_NAME)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - 依赖缺失时才走
        return "missing"


def is_available() -> bool:
    """MarkItDown 是否可导入（用于启动自检与测试断言）。"""
    try:
        import markitdown  # noqa: F401
    except Exception:  # pragma: no cover - 只在实际未安装时触发
        return False
    return True


def _normalize(text: str) -> str:
    """规范化：去 BOM、换行统一为 ``\\n``。"""
    text = text.lstrip("\ufeff")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class MarkItDownParser:
    """把 MarkItDown 包成 :class:`backend.app.parsers.DocumentParser`。"""

    name = PARSER_NAME
    supported_kinds: frozenset[ExtractionKind] = frozenset(
        {"pdf_text", "spreadsheet_rows", "html_text", "raw_text"}
    )

    @property
    def version(self) -> str:
        return parser_version()

    def parse(self, payload: bytes, *, filename: str | None = None) -> ParsedDocument:
        """把原始字节解析为 :class:`ParsedDocument`。不访问网络。"""
        suffix = Path(filename or "").suffix.lower()
        kind: ExtractionKind = SUFFIX_KIND.get(suffix, "raw_text")
        if not suffix:
            raise ValueError("必须提供 filename（含后缀），否则无法判断文档类型")

        from markitdown import MarkItDown  # 延迟导入：缺依赖时报 not_configured 而不是启动崩溃

        with tempfile.TemporaryDirectory(prefix="fmai-parse-") as tmp:
            target = Path(tmp) / f"input{suffix}"
            target.write_bytes(payload)
            result = MarkItDown(enable_plugins=False).convert(str(target))
        text = _normalize(result.text_content or "")

        return ParsedDocument(
            kind=kind,
            text=text,
            parser_name=self.name,
            parser_version=self.version,
            span_offset_base=0,
            byte_size=len(payload),
            page_count=_pdf_page_count(payload) if suffix == ".pdf" else None,
        )


def _pdf_page_count(payload: bytes) -> int | None:
    """页数（PDF 用 pypdfium2 读，失败就返回 None，不影响解析结果）。"""
    try:
        import pypdfium2 as pdfium
    except Exception:  # pragma: no cover
        return None
    try:
        doc = pdfium.PdfDocument(payload)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return None


# --- ToolRegistry 提供者 ---------------------------------------------------


def _error(code: str, message: str) -> Any:
    from backend.app.capabilities.tools import ToolResult

    return ToolResult(status="error", error_code=code, error_message=message)


def parse_document_tool(
    *, caller: Any, tool: str, path: str, max_chars: int | None = None
) -> Any:
    """``parse_pdf`` / ``parse_xlsx`` 的共同实现（由 ToolRegistry 校验权限后调用）。"""
    from backend.app.capabilities.tools import ToolResult

    if not is_available():
        return ToolResult(
            status="not_configured",
            error_code="parser_unavailable",
            error_message="未安装 markitdown：pip install 'markitdown[pdf,docx,xlsx,pptx]'",
        )

    allowed = TOOL_SUFFIXES.get(tool, frozenset())
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in allowed:
        return _error(
            "unsupported_document_type",
            f"{tool} 只接受 {sorted(allowed)}，收到 {suffix or '(无后缀)'}",
        )
    if not target.is_file():
        return _error("source_not_found", f"文件不存在: {target}")

    payload = target.read_bytes()
    try:
        parsed = MarkItDownParser().parse(payload, filename=target.name)
    except Exception as exc:  # 解析失败必须显式报错，不能返回半截内容
        return _error("parse_failed", f"{type(exc).__name__}: {exc}")

    limit = DEFAULT_MAX_CHARS if max_chars is None else max_chars
    truncated = len(parsed.text) > limit
    text = parsed.text[:limit] if truncated else parsed.text

    return ToolResult(
        status="ok",
        data={
            "tool": tool,
            "parser": parsed.parser_name,
            "parser_version": parsed.parser_version,
            "kind": parsed.kind,
            "source_path": str(target),
            "byte_size": parsed.byte_size,
            "char_count": len(parsed.text),
            "returned_chars": len(text),
            "truncated": truncated,
            "page_count": parsed.page_count,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "text": text,
        },
        billable=False,
        cost_cny=0.0,
    )


def parse_pdf_tool(*, caller: Any, path: str, max_chars: int | None = None) -> Any:
    return parse_document_tool(caller=caller, tool="parse_pdf", path=path, max_chars=max_chars)


def parse_xlsx_tool(*, caller: Any, path: str, max_chars: int | None = None) -> Any:
    return parse_document_tool(caller=caller, tool="parse_xlsx", path=path, max_chars=max_chars)


#: 默认注册表要挂上的实现
DEFAULT_TOOLS: dict[str, Any] = {
    "parse_pdf": parse_pdf_tool,
    "parse_xlsx": parse_xlsx_tool,
}


def register_parser_adapters(registry: Any) -> list[str]:
    """把本适配器注册进 ``ToolRegistry``，返回注册成功的工具名（幂等）。"""
    registered: list[str] = []
    for name, provider in DEFAULT_TOOLS.items():
        registry.register(name, provider)
        registered.append(name)
    return registered


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_TOOLS",
    "MarkItDownParser",
    "PARSER_NAME",
    "SUFFIX_KIND",
    "TOOL_SUFFIXES",
    "is_available",
    "parse_document_tool",
    "parse_pdf_tool",
    "parse_xlsx_tool",
    "parser_version",
    "register_parser_adapters",
]
