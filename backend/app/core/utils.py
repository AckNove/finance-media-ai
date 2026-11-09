"""通用工具函数（时间、文本、哈希）。"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone

_ASCII_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def utcnow() -> datetime:
    """带时区的当前 UTC 时间（统一时间基准）。"""
    return datetime.now(timezone.utc)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str, *, max_length: int = 80) -> str:
    """生成 URL 友好的 slug。

    中文字符无法直接转写为 ASCII（第一阶段不引入拼音库），
    因此当标题以中文为主时回退为随机短标识，保证唯一且稳定可用。
    """
    lowered = text.strip().lower()
    ascii_part = _ASCII_SLUG_RE.sub("-", lowered).strip("-")
    ascii_part = ascii_part[:max_length].strip("-")
    if len(ascii_part) >= 3 and not _CJK_RE.search(text):
        return ascii_part
    suffix = secrets.token_hex(4)
    return f"item-{suffix}"


def new_task_id() -> str:
    """生成 Agent 任务 ID（统一协议中的 task_id）。"""
    return f"task-{secrets.token_hex(8)}"
