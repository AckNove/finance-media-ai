"""把"同指标、不同期（或不同来源）"的数字拼成一张真实图表，替掉那几张数据卡。

为什么要有这一层：规则分镜以前是「每句带单位的数字 → 一张数据卡」，六个数据点
就是六张卡，整条片子看起来像会动的 PPT。可是同样的六个数字里，只要有两三个
是**同一个指标的同一口径**（"2023年组件产能920GW""2025年名义产能超1000GW"），
它们真正的信息是「变化」——那应该是一张柱状图，不是三张各写一个数字的卡片。

三条硬口径（宁可不出图，也不出误导的图）：

1. **同单位同指标**才算一组：单位不同（GW 与 亿元）或说的是两件事（"产能"与
   "产量"）绝不画在同一根轴上。
2. **至少一条有明确的期**：横轴得能写"哪一期/哪个来源"，否则一个柱子和另一个
   柱子凭什么摆在一起。没有期、也没有第二个来源的，退回数据卡。
3. **值必须为正、且日期数字不算**：`unit_numbers()` 已经挡掉了"11月2日"这类。
   负值（亏损）不在本轮范围——柱子往下的画法要单独设计，先如实不做。

图表由本仓库程序绘制（matplotlib），按原文数字落图、图上印出处，登记进
`assets_manifest.csv` 的 B 类素材；不碰任何外部素材。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from backend.app.video.outline import unit_numbers

#: 一张图表至少要有两根柱子，否则不叫对比。
MIN_MEMBERS = 2

#: 一条片子最多画几张：够说明"变化"，又不至于每段都换一张图让人眼花。
MAX_CHARTS = 2

#: 图表镜头的默认时长（秒）。比数据卡（4.5）长一点，因为图上有轴标签要读。
SECONDS_PER_CHART = 6.0

#: 图表落在任务目录的哪里（与素材清单里的相对路径一致）。
CHART_DIR = Path("assets") / "charts"


class ChartShotError(ValueError):
    """图表镜头生成失败。``reason`` 稳定，供接口与测试判断。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ChartPlan:
    """一张图表的计划（还没落图，所以测试可以不碰 matplotlib 就验证分组规则）。"""

    keyword: str
    unit: str
    title: str
    labels: tuple[str, ...]
    values: tuple[float, ...]
    display: tuple[str, ...]  # 柱子上写的原文数字（"920GW"），不重新四舍五入
    narration: str  # 这些柱子各自的原句，一句不改
    source_ids: tuple[str, ...]
    point_indexes: tuple[int, ...]
    asset_id: str = ""
    relative_path: str = ""
    path: Path | None = None

    @property
    def size(self) -> int:
        return len(self.values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "unit": self.unit,
            "title": self.title,
            "labels": list(self.labels),
            "values": list(self.values),
            "display": list(self.display),
            "source_ids": list(self.source_ids),
            "point_indexes": list(self.point_indexes),
            "asset_id": self.asset_id,
            "relative_path": self.relative_path,
        }


#: 这些词只是"怎么变"，不是"变的什么"。拿它们当横轴口径会把「营业收入下降36%」
#: 和「组件收入下降33%」拼成一张叫"下降"的图——两件事被画成一条趋势，比不出图更糟。
_WEAK_KEYWORDS = frozenset(
    {
        "下降", "下滑", "增长", "提升", "减少", "上升", "增加", "回落", "收窄", "扩大",
        "同比", "环比", "合计", "总计", "占", "为", "达到",
    }
)


def _member_label(number: Any, source_id: str) -> str:
    """横轴标签：优先用原文里的期（2023年/2024年三季度），没有就用来源号。"""
    label = str(getattr(number, "label", "") or "").strip()
    if label:
        return label
    source = str(source_id or "").strip()
    return source or "—"


def plan_charts(
    points: Sequence[Any],
    *,
    max_charts: int = MAX_CHARTS,
) -> tuple[list[ChartPlan], set[int]]:
    """按 `(单位, 指标)` 分组，返回 (图表计划, 被图表消费的数据点下标集合)。"""
    buckets: dict[tuple[str, str], list[tuple[int, Any, str]]] = {}
    for index, point in enumerate(points):
        text = " ".join(str(getattr(point, "text", "") or "").split())
        source_id = str(getattr(point, "source_id", "") or "")
        for number in unit_numbers(text):
            if not number.comparable:
                continue
            buckets.setdefault((number.unit, number.keyword), []).append((index, number, source_id))

    candidates: list[tuple[tuple[int, int], ChartPlan]] = []
    for (unit, keyword), members in buckets.items():
        if len(members) < MIN_MEMBERS:
            continue
        if keyword in _WEAK_KEYWORDS or len(keyword) < 2:
            # "下降""增长"这种词不指向任何指标：用它当口径会把两件不同的事画成一条线。
            continue
        labels = [_member_label(number, source_id) for _, number, source_id in members]
        if len(set(labels)) != len(labels):
            # 两根柱子写同一个标签（"2023年"出现两次）就没法分谁是谁：这种组不出图，
            # 那几句仍然各出一张数据卡，人自己能看出重复。
            continue
        period_flags = {bool(str(getattr(number, "label", "") or "").strip()) for _, number, _ in members}
        if len(period_flags) > 1:
            # 一半是期、一半是来源号：横轴一半"2023年"一半"S05"，读者没法把它当一条线读。
            # 要么全是期（时间序列），要么全是来源（跨来源比口径），混着就不画。
            continue
        if not period_flags.pop():
            # 全部都没有期：既不是时间序列，也说不清"为什么摆一起"，退回数据卡。
            continue
        ordered = sorted(members, key=lambda item: item[1].label or item[2])
        display = tuple(number.text for _, number, _ in ordered)
        values = tuple(number.value for _, number, _ in ordered)
        if any(value <= 0 for value in values):
            continue
        point_indexes = tuple(dict.fromkeys(index for index, _, _ in ordered))
        source_ids = tuple(dict.fromkeys(source_id for _, _, source_id in ordered))
        narration = " ".join(
            " ".join(str(getattr(points[index], "text", "") or "").split())
            for index in point_indexes
        )
        candidates.append(
            (
                (len(ordered), sum(len(item[1].label) for item in ordered)),
                ChartPlan(
                    keyword=keyword,
                    unit=unit,
                    title=f"{keyword}（{unit}）",
                    labels=tuple(_member_label(number, source_id) for _, number, source_id in ordered),
                    values=values,
                    display=display,
                    narration=narration,
                    source_ids=source_ids,
                    point_indexes=point_indexes,
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    plans: list[ChartPlan] = []
    consumed: set[int] = set()
    for _, plan in candidates:
        if len(plans) >= max_charts:
            break
        if consumed & set(plan.point_indexes):
            # 两个组抢同一个数据点（"产能920GW"既能进"产能"也能进"名义产能"）时，
            # 先到先得：一张图解释一件事，不让同一句同时支撑两张图。
            continue
        plans.append(replace(plan, asset_id=f"CHART_{len(plans) + 1:02d}"))
        consumed.update(plan.point_indexes)
    return plans, consumed


def write_chart(
    job_root: Path | str,
    plan: ChartPlan,
    *,
    canvas: tuple[int, int],
    shot_id: str,
) -> ChartPlan:
    """按计划落一张 PNG 并登记进素材清单，返回带路径的计划。"""
    from backend.app.analysis.charts import bar_chart
    from backend.app.video.generated import GeneratedAssetError, register_generated_asset

    root = Path(job_root)
    target = root / CHART_DIR / f"{shot_id}_{plan.asset_id.lower()}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    sources = "、".join(plan.source_ids) or "来源见分镜"
    try:
        bar_chart(
            list(plan.labels),
            list(plan.values),
            output=target,
            title=plan.title,
            unit=plan.unit,
            size=(canvas[0], canvas[1]),
            source_note=f"数字取自原文（{sources}）；口径以各来源为准",
        )
    except Exception as error:  # noqa: BLE001 - 画不出来就不画，别把整条片子拖死
        raise ChartShotError("chart_failed", f"{plan.asset_id}: 画图失败：{error}") from error

    relative = target.relative_to(root).as_posix()
    try:
        register_generated_asset(
            root,
            target,
            kind="chart",
            purpose=f"真实数字图表 {plan.title}",
            credit=f"本仓库按原文数字程序生成（{sources}）",
            used_in_shots=shot_id,
            asset_id=plan.asset_id,
        )
    except (GeneratedAssetError, OSError, ValueError) as error:
        raise ChartShotError("chart_not_registered", f"{plan.asset_id}: 登记失败：{error}") from error
    return replace(plan, relative_path=relative, path=target)


def visual_mix(rows: Sequence[dict[str, str]]) -> dict[str, Any]:
    """这一版分镜里"数据卡"占多少、图表与底板占多少。

    只在 `OUTLINE.md` 与测试里用来说明"是不是一堆会动的 PPT"；不参与闸门判定——
    「好不好看」不由程序定，程序只报事实。
    """
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("asset_type", "") or "")
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    cards = counts.get("composition", 0)
    charts = sum(
        1 for row in rows if str(row.get("asset_id", "")).startswith("CHART_")
    )
    return {
        "total": total,
        "by_type": counts,
        "cards": cards,
        "charts": charts,
        "card_ratio": round(cards / total, 3) if total else 0.0,
    }


__all__ = [
    "CHART_DIR",
    "MAX_CHARTS",
    "MIN_MEMBERS",
    "SECONDS_PER_CHART",
    "ChartPlan",
    "ChartShotError",
    "plan_charts",
    "visual_mix",
    "write_chart",
]
