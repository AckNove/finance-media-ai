"""Token 估算与成本计算（纯函数，无网络、无数据库）。

为什么要自己估算：预算闸门必须在**发请求之前**判断，而 prompt_tokens 只有响应里
才有。这里用一个保守的启发式估算（宁可估高，不少估）：

- 中日韩字符 ≈ 1 token / 1.5 字（保守取 1.5 而非常见的 1.7）；
- 其余字符 ≈ 1 token / 3.5 字符；
- 每条消息 +4 token 开销，末尾 +3。

估算值只用于闸门；实际成本一律用响应里的 ``usage`` 复算。
"""

from __future__ import annotations

from collections.abc import Sequence, Mapping
from typing import Any

CJK_TOKENS_PER_CHAR = 1.5
ASCII_CHARS_PER_TOKEN = 3.5
MESSAGE_OVERHEAD = 4
REPLY_PRIMER = 3


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0x4E00 <= code <= 0x9FFF  # 基本汉字
        or 0xF900 <= code <= 0xFAFF  # 兼容汉字
        or 0x3040 <= code <= 0x30FF  # 日文假名
        or 0xAC00 <= code <= 0xD7AF  # 韩文
        or 0x3000 <= code <= 0x303F  # 中日韩标点
        or 0xFF00 <= code <= 0xFFEF  # 全角
    )


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数（≥1）。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if _is_cjk(char))
    other = len(text) - cjk
    return max(1, int(cjk / CJK_TOKENS_PER_CHAR + other / ASCII_CHARS_PER_TOKEN) + 1)


def message_text(message: Mapping[str, Any]) -> str:
    """把一条 chat message 的 content 摊平成文本（兼容 str 与分段数组）。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def estimate_prompt_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    total = REPLY_PRIMER
    for message in messages:
        total += MESSAGE_OVERHEAD + estimate_tokens(message_text(message))
    return total


def cost_cny(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_1k_cny: float,
    output_price_per_1k_cny: float,
) -> float:
    """按千 token 单价计算人民币成本。"""
    return round(
        prompt_tokens / 1000.0 * input_price_per_1k_cny
        + completion_tokens / 1000.0 * output_price_per_1k_cny,
        6,
    )


__all__ = [
    "cost_cny",
    "estimate_prompt_tokens",
    "estimate_tokens",
    "message_text",
]
