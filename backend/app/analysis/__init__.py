"""数据分析与图表：把表格数字算成指标、画成能上镜头的图。

两个子模块分工明确：

- `metrics`：纯计算（数字解析、同比、CAGR、占比、HHI、描述统计），不画图；
- `charts`：纯画图（折线、柱状、百分比堆叠），不取数。

这里没有模型、没有网络、没有数据库：**算错了能一眼看回来**，
所以本模块的所有函数都要求调用方显式传数据进来。
"""

from __future__ import annotations

from .charts import (
    CANVAS_VERTICAL,
    CANVAS_WIDE,
    ChartResult,
    bar_chart,
    line_chart,
    share_bar,
)
from .metrics import (
    MISSING_TOKENS,
    UNIT_SCALES,
    Column,
    NumberParseError,
    cagr,
    column,
    concentration,
    describe,
    margin,
    parse_number,
    share,
    top_n,
    yoy,
)

__all__ = [
    "CANVAS_VERTICAL",
    "CANVAS_WIDE",
    "MISSING_TOKENS",
    "UNIT_SCALES",
    "ChartResult",
    "Column",
    "NumberParseError",
    "bar_chart",
    "cagr",
    "column",
    "concentration",
    "describe",
    "line_chart",
    "margin",
    "parse_number",
    "share",
    "share_bar",
    "top_n",
    "yoy",
]
