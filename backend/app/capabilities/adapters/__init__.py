"""能力适配器（adapters）——把成熟开源项目接到我们自己的 ToolRegistry 上。

这里只放**极薄的胶水**：第三方项目负责真正干活（解析、编码、渲染），
我们只负责把它们的输出翻译成 ``ParsedDocument`` / ``ToolResult`` 契约。

已接入：

* ``markitdown_adapter`` —— Microsoft MarkItDown（MIT），提供 ``parse_pdf`` / ``parse_xlsx``。

未接入（不得假装存在）：搜索、抓取、模型调用、向量检索、TTS、视频渲染。
"""

from __future__ import annotations

from backend.app.capabilities.adapters import markitdown_adapter
from backend.app.capabilities.adapters.markitdown_adapter import (
    MarkItDownParser,
    register_parser_adapters,
)

__all__ = ["MarkItDownParser", "markitdown_adapter", "register_parser_adapters"]
