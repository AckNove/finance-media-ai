"""话题标签的唯一口径。

为什么单独一个模块：标签此前是**从排期的「备注」框里按空格切出来的**——
备注写一句话（中文没空格）就整段变成一个标签，写一整段笔记就变成十几个莫名其妙的
标签，而人完全看不出这件事。标签又是发布必填项（三个平台都有个数建议），
所以它得有自己的字段、自己的清洗规则，并且**只在这里清洗**。

规则（都不猜内容，只做格式清洗）：
- 分隔符认空格、逗号（中英文）、顿号、分号；`#` 前缀去掉；
- 去重保序、去空、单个标签限长、整体限个数；
- 不做同义词合并、不做热门词推荐、不替你编标签——编出来的标签会把人带到错误的流量池。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

#: 一条排期最多留多少个标签（超出的直接丢，并在导出时说明）
MAX_TAGS = 12

#: 单个标签最长多少个字符
MAX_TAG_CHARS = 24

#: 分隔符：空白、中英文逗号、顿号、中英文分号
_SPLIT = re.compile(r"[\s,，、;；]+")


def split_tags(value: str | None) -> list[str]:
    """把一串标签文本切成干净的列表（去 `#`、去重保序）。"""
    if not value:
        return []
    result: list[str] = []
    for raw in _SPLIT.split(str(value)):
        word = raw.strip().lstrip("#").strip()
        if not word:
            continue
        if len(word) > MAX_TAG_CHARS:
            word = word[:MAX_TAG_CHARS]
        if word not in result:
            result.append(word)
    return result


def normalise_tags(value: str | None) -> str:
    """入库形态：空格分隔、去重、限量。空值一律存空串。"""
    return " ".join(split_tags(value)[:MAX_TAGS])


def dropped_tags(value: str | None) -> int:
    """被限量丢掉几个（导出时如实用来说明）。"""
    return max(0, len(split_tags(value)) - MAX_TAGS)


def format_hashtags(tags: Iterable[str]) -> str:
    """人直接复制的形态：`#a #b #c`。"""
    return " ".join(f"#{tag}" for tag in tags if tag)


def tag_note(tags: Sequence[str], *, platform_min: int = 0, platform_max: int = 0) -> str:
    """给导出清单用的一句话：现在有几个、平台建议几个。不编造建议值。"""
    count = len(tags)
    if platform_max and platform_min:
        if count < platform_min:
            return f"标签 {count} 个，平台建议 {platform_min}~{platform_max} 个：不够的请手工补（这里不替你编）"
        if count > platform_max:
            return f"标签 {count} 个，超过平台建议的 {platform_max} 个"
        return f"标签 {count} 个，在平台建议的 {platform_min}~{platform_max} 个之间"
    return f"标签 {count} 个"


__all__ = [
    "MAX_TAG_CHARS",
    "MAX_TAGS",
    "dropped_tags",
    "format_hashtags",
    "normalise_tags",
    "split_tags",
    "tag_note",
]
