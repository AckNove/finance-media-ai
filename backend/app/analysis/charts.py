"""把数据画成能上镜头的图：确定、可复现、带来源行。

三条硬规矩：

1. **同样的输入必须得到同样的字节**（不写时间戳、不写随机种子、字体固定），
   否则视频每次重渲都会产出不同文件，哈希对不上就没法做证据链。
2. **中文字形必须真的存在**：按 Noto Sans SC → 微软雅黑 → 系统默认 的顺序找，
   找不到会在 `ChartResult.font_family` 里写 `fallback`，让人知道这张图有风险。
3. **图上必须留出处行**：`source_note` 直接画在左下角，图离开正文也说得清数字出处。

这里只负责"画"，不负责"取数"——取数在 `metrics.py`，两者都不碰数据库。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

__all__ = [
    "CANVAS_VERTICAL",
    "CANVAS_WIDE",
    "ChartResult",
    "bar_chart",
    "line_chart",
    "share_bar",
]

CANVAS_VERTICAL = (1080, 1350)
CANVAS_WIDE = (1920, 1080)

_FONT_CANDIDATES = (
    # 微软雅黑优先：它是常规字重，matplotlib 不会为变量字体刷一屏 weight 警告
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)

# 与视频画板同一套配色，图放进片子不会有色差
INK = "#0b1220"
PAPER = "#f2f5f7"
DIM = "#94a3b8"
LIME = "#b6ff1f"
CYAN = "#35e0ff"
MAGENTA = "#ff2bd6"
SERIES_COLORS = (LIME, CYAN, MAGENTA, "#ffb020", "#8b5cf6", "#22c55e")


@dataclass(frozen=True)
class ChartResult:
    """一次出图的结果。路径、哈希、尺寸都带上，方便直接写进素材清单。"""

    path: Path
    kind: str
    sha256: str
    byte_size: int
    width: int
    height: int
    dpi: int
    series_count: int
    point_count: int
    font_family: str
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "series_count": self.series_count,
            "point_count": self.point_count,
            "font_family": self.font_family,
            **({"meta": self.meta} if self.meta else {}),
        }


def _prepare(size: tuple[int, int], dpi: int):
    """建画布并挂中文字体，返回 (figure, axes, font_family)。"""
    import logging

    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib import font_manager, pyplot

    # 变量字体（Noto Sans SC VF）缺常规字重时 matplotlib 会逐字刷警告，
    # 那不是错误但会淹掉真正要看的信息，这里只压这一条。
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

    family = "fallback"
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            font_manager.fontManager.addfont(str(candidate))
            family = font_manager.FontProperties(fname=str(candidate)).get_name()
            break

    pyplot.rcParams.update(
        {
            "font.family": family if family != "fallback" else "sans-serif",
            "axes.unicode_minus": False,
            "figure.facecolor": INK,
            "axes.facecolor": INK,
            "text.color": PAPER,
            "axes.labelcolor": DIM,
            "xtick.color": DIM,
            "ytick.color": DIM,
            "axes.edgecolor": "#24304a",
            "axes.grid": True,
            "grid.color": "#1b2438",
            "grid.linewidth": 1.0,
        }
    )
    width, height = size
    figure = pyplot.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    axes = figure.add_subplot(1, 1, 1)
    return figure, axes, family


def _finish(figure, path: Path, *, kind: str, dpi: int, series_count: int, point_count: int,
            font_family: str, source_note: str, meta: dict | None,
            bottom_pad: float = 0.035) -> ChartResult:
    """统一收尾：留出处行、按无时间戳的方式存盘、算哈希。"""
    from matplotlib import pyplot

    if source_note:
        figure.text(0.012, 0.014, source_note, fontsize=9, color=DIM, ha="left", va="bottom")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout(rect=(0, bottom_pad, 1, 1))
    # metadata 里不放任何时间戳：同输入必须同字节
    figure.savefig(path, dpi=dpi, facecolor=INK, metadata={"Software": None}, format="png")
    pyplot.close(figure)

    payload = path.read_bytes()
    width, height = int(figure.get_size_inches()[0] * dpi), int(figure.get_size_inches()[1] * dpi)
    return ChartResult(
        path=path,
        kind=kind,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        width=width,
        height=height,
        dpi=dpi,
        series_count=series_count,
        point_count=point_count,
        font_family=font_family,
        meta=dict(meta or {}),
    )


def line_chart(
    series: Sequence[tuple[str, Sequence[float]]],
    labels: Sequence[str],
    *,
    output: Path,
    title: str = "",
    unit: str = "",
    size: tuple[int, int] = CANVAS_VERTICAL,
    dpi: int = 160,
    source_note: str = "",
    highlight_last: bool = True,
) -> ChartResult:
    """折线图：趋势类数字（净息差、收益率、股价）用它。"""
    if not series:
        raise ValueError("至少需要一条序列")
    if any(len(values) != len(labels) for _, values in series):
        raise ValueError("每条序列的长度必须与 labels 一致")

    figure, axes, family = _prepare(size, dpi)
    for index, (name, values) in enumerate(series):
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        axes.plot(range(len(labels)), list(values), color=color, linewidth=3.0, label=name)
        if highlight_last and values:
            axes.scatter([len(labels) - 1], [list(values)[-1]], color=color, s=90, zorder=5)
            axes.annotate(
                f"{list(values)[-1]:,.1f}{unit}",
                (len(labels) - 1, list(values)[-1]),
                textcoords="offset points",
                xytext=(-6, 24),
                ha="right",
                color=color,
                fontsize=13,
            )
    if title:
        axes.set_title(title, fontsize=20, color=PAPER, pad=16, loc="left")
    if unit:
        axes.set_ylabel(unit, fontsize=12)
    step = max(1, len(labels) // 8)
    axes.set_xticks(list(range(0, len(labels), step)))
    axes.set_xticklabels([labels[i] for i in range(0, len(labels), step)], fontsize=10)
    if len(series) > 1:
        legend = axes.legend(loc="upper left", frameon=False, fontsize=11)
        for text in legend.get_texts():
            text.set_color(PAPER)

    return _finish(
        figure,
        output,
        kind="line",
        dpi=dpi,
        series_count=len(series),
        point_count=len(labels),
        font_family=family,
        source_note=source_note,
        meta={"series": [name for name, _ in series]},
    )


def bar_chart(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    output: Path,
    title: str = "",
    unit: str = "",
    size: tuple[int, int] = CANVAS_VERTICAL,
    dpi: int = 160,
    source_note: str = "",
    value_format: str = "{:,.1f}",
) -> ChartResult:
    """柱状图：结构类数字（收入构成、各业务占比、Top-N 榜单）用它。"""
    if len(labels) != len(values):
        raise ValueError("labels 与 values 长度必须一致")
    if not labels:
        raise ValueError("至少需要一个数据点")

    figure, axes, family = _prepare(size, dpi)
    positions = list(range(len(labels)))
    colors = [SERIES_COLORS[i % len(SERIES_COLORS)] for i in positions]
    bars = axes.bar(positions, list(values), color=colors, width=0.62)
    for bar, value in zip(bars, values):
        axes.annotate(
            value_format.format(value) + unit,
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            textcoords="offset points",
            xytext=(0, 6),
            ha="center",
            color=PAPER,
            fontsize=12,
        )
    if title:
        axes.set_title(title, fontsize=20, color=PAPER, pad=16, loc="left")
    if unit:
        axes.set_ylabel(unit, fontsize=12)
    axes.set_xticks(positions)
    axes.set_xticklabels(list(labels), fontsize=11)

    return _finish(
        figure,
        output,
        kind="bar",
        dpi=dpi,
        series_count=1,
        point_count=len(labels),
        font_family=family,
        source_note=source_note,
        meta={"labels": list(labels)},
    )


def share_bar(
    parts: Sequence[tuple[str, float]],
    *,
    output: Path,
    title: str = "",
    size: tuple[int, int] = (1080, 720),
    dpi: int = 160,
    source_note: str = "",
) -> ChartResult:
    """百分比堆叠条：份额/构成类数字（收入结构、资金流向）用它。"""
    if not parts:
        raise ValueError("至少需要一个构成项")
    total = sum(value for _, value in parts)
    if total <= 0:
        raise ValueError("构成项之和必须大于 0")

    figure, axes, family = _prepare(size, dpi)
    left = 0.0
    legend_items: list[tuple[object, str]] = []
    for index, (name, value) in enumerate(parts):
        percent = value / total * 100.0
        bar = axes.barh([0], [percent], left=left, color=SERIES_COLORS[index % len(SERIES_COLORS)],
                        height=0.30)
        legend_items.append((bar, f"{name} {percent:.1f}%"))
        if percent >= 6:  # 太窄不写字，避免叠在一起看不清
            axes.text(left + percent / 2, 0, f"{percent:.1f}%", ha="center", va="center",
                      color=INK, fontsize=13, fontweight="bold")
        left += percent
    axes.set_yticks([])
    axes.set_xlim(0, 100)
    axes.set_ylim(-0.5, 0.5)
    axes.set_xlabel("占比 %", fontsize=12)
    axes.grid(False)
    if title:
        axes.set_title(title, fontsize=20, color=PAPER, pad=16, loc="left")
    # 图例放到画布底部：条带很宽，图例压在坐标轴上会挡住刻度
    legend = figure.legend(
        handles=[item[0] for item in legend_items],
        labels=[item[1] for item in legend_items],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.055),
        ncol=2,
        frameon=False,
        fontsize=12,
    )
    for text in legend.get_texts():
        text.set_color(PAPER)

    return _finish(
        figure,
        output,
        kind="share",
        dpi=dpi,
        series_count=1,
        point_count=len(parts),
        font_family=family,
        source_note=source_note,
        meta={"parts": [name for name, _ in parts]},
        bottom_pad=0.20,
    )
