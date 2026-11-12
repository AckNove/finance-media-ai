"""把「表格里的数字」变成能算、能核对的指标。

这个模块只做纯计算，不碰数据库、不碰网络、不画图：

- `parse_number`：把中文财经文本里的数字读成 float（千分位、括号负数、单位后缀）；
- `Column` / `series_from_rows`：从 `structured.TableBlock` 或 CSV 行里取一列；
- 指标：同比、CAGR、占比、利润率、集中度（HHI）、Top-N、描述统计。

**绝对不猜**：读不出数字就返回 None，由调用方决定是跳过还是报错。
财务数字错了比没有数字更糟，所以这里宁可少算，也不做「看起来差不多」的推断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

__all__ = [
    "MISSING_TOKENS",
    "UNIT_SCALES",
    "Column",
    "NumberParseError",
    "cagr",
    "column",
    "concentration",
    "describe",
    "margin",
    "parse_number",
    "share",
    "top_n",
    "yoy",
]

# 表格里常见的「没有数据」写法：读成 None，而不是 0
MISSING_TOKENS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "nan", "null", "none", "不适用", "无"}
)

# 单位后缀 → 乘数。只收财务文本里确定含义的写法。
UNIT_SCALES: Mapping[str, float] = {
    "万亿": 1e12,
    "亿": 1e8,
    "万": 1e4,
    "千": 1e3,
    "元": 1.0,
    "％": 1.0,
    "%": 1.0,
}

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_UNIT_RE = re.compile(r"(万亿|亿|万|千|元|％|%)")


class NumberParseError(ValueError):
    """表格里的数字读不出来时抛出，消息里带原文，方便人回去看报表。"""


@dataclass(frozen=True)
class Column:
    """一列数据：值和它来自哪一行一起带着，避免算完不知道数字出处。"""

    name: str
    values: tuple[float, ...]
    labels: tuple[str, ...]
    source_rows: tuple[int, ...] = field(default=())

    @property
    def size(self) -> int:
        return len(self.values)

    def paired(self) -> tuple[tuple[str, float], ...]:
        return tuple(zip(self.labels, self.values))


def parse_number(raw: object, *, default_unit_scale: float | None = None) -> float | None:
    """把一段文本读成数字；读不出来返回 None（不抛异常，方便批量扫表）。

    支持：`1,234.5`、`（123）` 括号负数、`12.3%`、`4.28万亿元`、`-1.05％`。
    单位只做**乘数**换算，不改变百分号的语义（`12.3%` 读成 12.3，不是 0.123）。
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        if default_unit_scale is not None:
            value *= default_unit_scale
        return value

    text = str(raw).strip()
    if text.lower() in MISSING_TOKENS or text.strip("()（） ") == "":
        return None

    negative = text.startswith("(") and text.endswith(")") or text.startswith("（") and text.endswith("）")
    cleaned = text.strip("()（） ").replace(",", "").replace("，", "").replace(" ", "")
    match = _NUMBER_RE.search(cleaned)
    if match is None:
        return None

    value = float(match.group())
    if negative and value > 0:
        value = -value

    unit = _UNIT_RE.search(cleaned)
    if unit is not None:
        scale = UNIT_SCALES[unit.group(1)]
        if unit.group(1) in {"%", "％"}:
            # 百分号只是语义标记，不当乘数
            return value
        value *= scale
    elif default_unit_scale is not None:
        value *= default_unit_scale
    return value


def column(
    rows: Sequence[Mapping[str, object]],
    name: str,
    *,
    label_column: str | None = None,
    default_unit_scale: float | None = None,
    require_all: bool = False,
) -> Column:
    """取一列数值，读不出数字的行默认跳过（跳过会体现在 source_rows 里）。

    `require_all=True` 时只要有读不出的数字就抛 `NumberParseError`——
    需要严格对齐的场合（例如同比计算）应该用这个，避免静默丢行。
    """
    values: list[float] = []
    labels: list[str] = []
    source_rows: list[int] = []
    problems: list[tuple[int, object]] = []

    for index, row in enumerate(rows, start=1):
        if name not in row:
            raise KeyError(f"列不存在：{name}")
        parsed = parse_number(row.get(name), default_unit_scale=default_unit_scale)
        if parsed is None:
            problems.append((index, row.get(name)))
            continue
        values.append(parsed)
        labels.append(str(row.get(label_column)) if label_column and label_column in row else str(index))
        source_rows.append(index)

    if problems and require_all:
        detail = "; ".join(f"第{row}行 {raw!r}" for row, raw in problems[:5])
        raise NumberParseError(f"列 {name} 有 {len(problems)} 行读不出数字：{detail}")

    return Column(name=name, values=tuple(values), labels=tuple(labels), source_rows=tuple(source_rows))


def yoy(current: float, previous: float) -> float | None:
    """同比增速（百分数）。上期为 0 时返回 None，不制造无穷大。"""
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def cagr(first: float, last: float, years: float) -> float | None:
    """复合年均增速（百分数）。首期为负或年数为 0 时返回 None。"""
    if first <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def share(part: float, total: float) -> float | None:
    """占比（百分数）。总量为 0 时返回 None。"""
    if total == 0:
        return None
    return part / total * 100.0


def margin(numerator: float, denominator: float) -> float | None:
    """比率（百分数）：净息差、毛利率、净利率都可以用它，语义由调用方标注。"""
    return share(numerator, denominator)


def concentration(values: Iterable[float]) -> float | None:
    """赫芬达尔指数（HHI，0–10000）。用于看行业集中度。

    只接受非负值；全为 0 时返回 None（没有份额谈不上集中度）。
    """
    numbers = [value for value in values if value is not None]
    if not numbers or any(value < 0 for value in numbers):
        return None
    total = sum(numbers)
    if total <= 0:
        return None
    return sum((value / total * 100.0) ** 2 for value in numbers)


def top_n(items: Iterable[tuple[str, float]], n: int) -> list[tuple[str, float]]:
    """按数值降序取前 n 个，别的抹掉（用于榜单类图表）。"""
    if n <= 0:
        return []
    return sorted(items, key=lambda item: item[1], reverse=True)[:n]


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    """描述统计。空序列返回全 None，不塞 0 冒充结果。"""
    numbers = list(values)
    if not numbers:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "total": None,
            "change": None,
            "change_pct": None,
        }
    ordered = sorted(numbers)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2 == 1
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    first, last = ordered[0], ordered[-1]
    return {
        "count": len(numbers),
        "min": first,
        "max": last,
        "mean": sum(numbers) / len(numbers),
        "median": median,
        "total": sum(numbers),
        "change": numbers[-1] - numbers[0],
        "change_pct": yoy(numbers[-1], numbers[0]),
    }
