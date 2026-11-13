"""跨站转载识别：**同一篇文章**被多个站点发布时，不能当成多个独立来源。

为什么需要这一层：搜索阶段的目标是「多来源互相印证」，但中文财经内容里大量是
一家媒体发稿、几十家转载。如果按 URL 去重，转载稿各有各的 URL，会被算成
"3 个来源都这么说"，于是"多来源印证"变成"同一篇被抄了三遍"。这一层只做一件事：
把正文几乎一样的那几条**归到一个组**里，并指定其中一条当代表。

判定口径（刻意保守）：

- 只比正文，不比标题（转载常改标题，标题相似但正文无关的不该合并）；
- 先做归一化（去空白、去标点、全角转半角、英文小写），再取正文前
  ``FINGERPRINT_CHARS`` 个字算 sha256 指纹——**完全一样才算重复**；
- 指纹不同但前段高度相似（4-gram Jaccard ≥ ``SIMILAR_THRESHOLD``）的，也算
  同一篇：转载方偶尔在开头加一句"本文转自……"；
- 正文太短（< ``MIN_TEXT_CHARS``）不参与判定——几十个字的摘要撞车概率太高，
  宁可漏判也不要把两条不同来源错误合并。

**这里不做的事**：不判断"谁抄谁"，不给来源打可信度，不改任何原始字段。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "FINGERPRINT_CHARS",
    "MIN_TEXT_CHARS",
    "SIMILAR_THRESHOLD",
    "RepublishGroup",
    "fingerprint",
    "group_republished",
    "normalise",
    "similarity",
]

#: 参与指纹比较的正文字数（再往后是正文细节，转载常做删改）
FINGERPRINT_CHARS = 1200
#: 短于这个长度的正文不参与判定
MIN_TEXT_CHARS = 120
#: 4-gram Jaccard 相似度阈值：中转转载通常能做到 0.95 以上，0.9 留一点余量
SIMILAR_THRESHOLD = 0.90

_PUNCT = re.compile(r"[\s\u3000!-/:-@\[-`{-~，。、；：？！…—－·「」『』（）《》〈〉“”‘’]+")
_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}


@dataclass(frozen=True, slots=True)
class RepublishGroup:
    """一组"同一篇文章"的多个发布页。``canonical`` 是保留独立计数的那一条。"""

    canonical: str
    members: tuple[str, ...]

    @property
    def duplicates(self) -> tuple[str, ...]:
        return tuple(item for item in self.members if item != self.canonical)

    def as_dict(self) -> dict[str, object]:
        return {
            "canonical": self.canonical,
            "members": list(self.members),
            "duplicates": list(self.duplicates),
        }


def normalise(text: str) -> str:
    """归一化：全角转半角、英文小写、去掉所有空白与标点。"""
    if not text:
        return ""
    folded = text.translate(_FULLWIDTH).lower()
    return _PUNCT.sub("", folded)


def fingerprint(text: str) -> str:
    """正文指纹；正文太短或为空时返回空串（表示"不参与判定"）。"""
    body = normalise(text)
    if len(body) < MIN_TEXT_CHARS:
        return ""
    head = body[:FINGERPRINT_CHARS]
    return hashlib.sha256(head.encode("utf-8")).hexdigest()


def _grams(text: str, *, size: int = 4) -> set[str]:
    body = normalise(text)[:FINGERPRINT_CHARS]
    if len(body) < size:
        return set()
    return {body[index : index + size] for index in range(len(body) - size + 1)}


def similarity(left: str, right: str) -> float:
    """两段正文的 4-gram Jaccard 相似度（0~1）；任一为空返回 0。"""
    first, second = _grams(left), _grams(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def group_republished(
    texts: Mapping[str, str],
    *,
    order: Sequence[str] = (),
    threshold: float = SIMILAR_THRESHOLD,
) -> list[RepublishGroup]:
    """把正文重复的条目分组。

    ``texts`` 是 ``来源编号 -> 正文``；``order`` 决定谁是"代表"——**先出现的当代表**，
    因为调用方按检索/抓取顺序传入，最早那条通常就是首发（但我们不断言这一点，
    只把它当作计数代表，``as_dict()`` 里也不写"首发"二字）。
    """
    keys = [key for key in (order or list(texts)) if texts.get(key)]
    # 太短的正文不参与判定：两条 30 字的摘录撞车概率太高，宁可漏判也不误合并
    bodies = {
        key: texts[key]
        for key in keys
        if len(normalise(texts[key])) >= MIN_TEXT_CHARS
    }
    keys = [key for key in keys if key in bodies]
    prints = {key: fingerprint(body) for key, body in bodies.items()}

    groups: list[list[str]] = []
    assigned: set[str] = set()
    for key in keys:
        if key in assigned:
            continue
        members = [key]
        assigned.add(key)
        for other in keys:
            if other in assigned:
                continue
            same = bool(prints[key]) and prints[key] == prints[other]
            if not same:
                same = similarity(bodies[key], bodies[other]) >= threshold
            if same:
                members.append(other)
                assigned.add(other)
        groups.append(members)

    return [
        RepublishGroup(canonical=members[0], members=tuple(members))
        for members in groups
        if len(members) > 1
    ]


def duplicate_map(groups: Sequence[RepublishGroup]) -> dict[str, str]:
    """``重复条目 -> 代表条目`` 的平铺映射，方便落库/展示时查。"""
    mapping: dict[str, str] = {}
    for group in groups:
        for item in group.duplicates:
            mapping[item] = group.canonical
    return mapping
