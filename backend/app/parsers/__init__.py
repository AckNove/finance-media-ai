"""文档解析层（parsers，M2.0 T04）。

**M2.0 只有契约，没有任何解析实现**：
- 不引入 pdfminer / PyMuPDF / openpyxl 等依赖；
- ``parse_pdf`` / ``parse_xlsx`` 工具在 ``capabilities.tools`` 中登记为未实现；
- 这里只定义"解析产物长什么样"，让 2.2/2.3 阶段的实现有唯一落点。

解析产物的硬约束（对应 database_design.md §7.5）：
- ``span_offset_base`` 必须随产物一起返回，证据的字符偏移以它为基准；
- ``text`` 是规范化后的纯文本（换行统一 ``\\n``、剔除 BOM）；
- 不允许把"模型摘要"当成解析产物——摘要只能进 ``source_summaries``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

ExtractionKind = Literal[
    "html_text", "pdf_text", "spreadsheet_rows", "raw_text", "ocr_text"
]


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """一次解析的产物（写库前的最小单位）。"""

    kind: ExtractionKind
    text: str
    parser_name: str
    parser_version: str
    # 证据字符偏移的基准（通常 0；分页抽取时为该页在全文中的起始码点）
    span_offset_base: int = 0
    byte_size: int = 0
    page_count: int | None = None

    def __post_init__(self) -> None:
        if self.span_offset_base < 0:
            raise ValueError("span_offset_base 不能为负")
        if "\r\n" in self.text:
            raise ValueError("解析产物必须把换行规范化为 \\n")


@runtime_checkable
class DocumentParser(Protocol):
    """解析器协议。实现必须是无网络、纯本地的。"""

    name: str
    version: str
    supported_kinds: frozenset[ExtractionKind]

    def parse(self, payload: bytes, *, filename: str | None = None) -> ParsedDocument:
        """把原始字节解析为 ParsedDocument。不得访问网络。"""
        ...


class ParserNotImplementedError(NotImplementedError):
    """M2.0 的解析器占位异常：明确表示"尚未实现"，而不是返回空结果。"""


__all__ = [
    "DocumentParser",
    "ExtractionKind",
    "ParsedDocument",
    "ParserNotImplementedError",
]
