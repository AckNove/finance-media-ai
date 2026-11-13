"""把一行中文按画幅宽度换算成"放得下的字号"（字幕与数据卡共用一套口径）。

为什么要单独一个模块：字幕（渲染阶段）和数据卡（画板阶段）是两处完全不同的代码，
但"一行字多宽"必须是同一个估算，否则会出现"字幕缩了、卡面没缩"这种同一句话两种
待遇的问题——这正是单位表那次踩过的坑。

估算只用于挑字号，不追求精确排版：中文全角按 1.35 个字号、半角按 0.7 个字号。
1.35 这个系数是实测出来的——46px 的 18 个全角字在 1080 宽画面上正好铺满。
"""

from __future__ import annotations

from typing import Iterable

#: 一行字最多占画幅宽度的多少。留出左右边距，长句才不会顶到屏幕边缘。
WIDTH_BUDGET = 0.9

#: 一个全角字大概占多少个字号。
CJK_ADVANCE = 1.35

#: 一个半角字符大概占多少个字号。
ASCII_ADVANCE = 0.7

#: 字号下限：再小就没人看得清了，宁可让上游把句子截短。
MIN_SIZE = 32


def display_width(text: str) -> float:
    """按"占几个字号"估算一行文字的宽度。"""
    total = 0.0
    for char in text:
        total += ASCII_ADVANCE if ord(char) < 0x2E80 else CJK_ADVANCE
    return total


def fit_size(
    widths: Iterable[float],
    *,
    base: int,
    canvas_width: int,
    budget: float = WIDTH_BUDGET,
    minimum: int = MIN_SIZE,
) -> int:
    """在这些文字里挑一个放得下的字号：**只降不升**，下限 ``minimum``。"""
    longest = max((float(value) for value in widths), default=0.0)
    if longest <= 0:
        return base
    fitted = int(canvas_width * budget / longest)
    return max(minimum, min(base, fitted))


def fit_text_size(
    texts: Iterable[str],
    *,
    base: int,
    canvas_width: int,
    budget: float = WIDTH_BUDGET,
    minimum: int = MIN_SIZE,
) -> int:
    """同上，但直接吃原始文字。"""
    return fit_size(
        (display_width(text) for text in texts),
        base=base,
        canvas_width=canvas_width,
        budget=budget,
        minimum=minimum,
    )


__all__ = [
    "ASCII_ADVANCE",
    "CJK_ADVANCE",
    "MIN_SIZE",
    "WIDTH_BUDGET",
    "display_width",
    "fit_size",
    "fit_text_size",
]
