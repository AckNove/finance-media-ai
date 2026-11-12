"""跨来源交叉印证：只做**可机器验证**的比对，不产生判断。

模型不可用时，研究底稿里已经有一张"原样摘录的数据点表"。人真正需要的是再多两列信息：

1. **这个数字有没有别的来源也说**（≥2 个独立来源出现同一组数字 → ``corroborated``）；
2. **同一件事不同来源说了不同的数**（上下文高度相似但数字不一致 → 冲突，必须标出来）。

两条规则都能被第三个人重算，因此可以写在报告里；而"这个数字意味着什么"属于分析，
**不在这一层做**——这里既不排序重要性，也不下结论，更不给可信度评分。

判定规则（写死、可重算）：

* 上下文的键 = 去掉数字、单位词、标点与空白之后剩下的汉字／字母；
* 两条上下文按 4-gram 集合的 Jaccard 相似度比对，≥ ``min_overlap`` 视为"在说同一件事"；
* 在说同一件事的数据点连成一簇（并查集，**只连不同来源之间**）；
* 簇内某个数字被 ≥ ``min_sources`` 个不同来源说到 → 记为印证；
* 簇内两条数据点的数字集合完全不相交 → 记为冲突（同一件事，两种说法）。

刻意**不做**的事：不做同义词归并（"净息差" ≠ 自动等同"利差"）、不做单位换算猜测、
不把相似度当成事实判断。规则保守，宁可漏认，不可误合并。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from backend.app.research.digest import DataPoint

#: 上下文相似度阈值：4-gram Jaccard。0.5 上下在中文短句里已经要求"半句以上重合"。
DEFAULT_MIN_OVERLAP = 0.5
#: 至少几个**不同来源**说到同一组数字才算印证
DEFAULT_MIN_SOURCES = 2
#: 上下文里少于这个字数的句子不参与冲突判定（太短必然误配）
MIN_CONTEXT_CHARS = 6

_UNIT_WORDS = (
    "%",
    "％",
    "亿元",
    "万元",
    "亿美元",
    "个百分点",
    "万亿",
    "亿",
    "万",
    "元",
    "倍",
    "家",
    "人",
    "吨",
)
_STRIP = re.compile(r"[\d\s，。、；：！？,.;:!?\"'“”‘’（）()《》\[\]【】—…·\-+*=<>|/\\]+")


def _context(text: str) -> str:
    """去掉数字、单位词与标点，只留"说的是哪件事"的字面。"""
    cleaned = text or ""
    for unit in _UNIT_WORDS:
        cleaned = cleaned.replace(unit, "")
    return _STRIP.sub("", cleaned)


def _grams(text: str, *, size: int = 4) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def context_overlap(left: str, right: str) -> float:
    """两条上下文的 4-gram Jaccard 相似度（0.0–1.0）。"""
    left_grams, right_grams = _grams(left), _grams(right)
    if not left_grams or not right_grams:
        return 0.0
    shared = left_grams & right_grams
    return len(shared) / len(left_grams | right_grams)


_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def key_numbers(point: DataPoint) -> tuple[str, ...]:
    """参与比对的关键数字：去掉纯年份（2024、1998 这类），它们不构成"同一个数"。"""
    return tuple(number for number in point.numbers if not _YEAR.match(number))


def number_key(point: DataPoint) -> str:
    """数据点的数字集合键（去重排序），用于"是不是同一个数"。"""
    return "|".join(sorted(set(key_numbers(point))))


def _sorted_numbers(numbers: Iterable[str]) -> list[str]:
    """数字按数值排序（能转就转，转不了退回字符串序），输出给人看的原样文本。"""
    unique = list(dict.fromkeys(numbers))

    def key(value: str) -> tuple[int, float, str]:
        try:
            return (0, float(value), value)
        except ValueError:
            return (1, 0.0, value)

    return sorted(unique, key=key)


@dataclass(frozen=True, slots=True)
class Corroborated:
    """≥2 个独立来源出现了同一组数字。"""

    numbers: tuple[str, ...]
    points: tuple[DataPoint, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(point.source_id for point in self.points))

    @property
    def source_count(self) -> int:
        return len(self.source_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "numbers": list(self.numbers),
            "source_ids": list(self.source_ids),
            "source_count": self.source_count,
            "quotes": [{"source_id": point.source_id, "text": point.text} for point in self.points],
        }


@dataclass(frozen=True, slots=True)
class Conflict:
    """同一件事，不同来源给了不同的数字。"""

    left: DataPoint
    right: DataPoint
    overlap: float

    def as_dict(self) -> dict[str, object]:
        return {
            "overlap": round(self.overlap, 3),
            "left": {"source_id": self.left.source_id, "text": self.left.text,
                     "numbers": list(self.left.numbers)},
            "right": {"source_id": self.right.source_id, "text": self.right.text,
                      "numbers": list(self.right.numbers)},
        }


@dataclass(frozen=True, slots=True)
class Corroboration:
    """一次交叉印证的完整结果（全部可重算）。"""

    corroborated: tuple[Corroborated, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    single_source: tuple[DataPoint, ...] = ()
    checked_points: int = 0
    min_overlap: float = DEFAULT_MIN_OVERLAP
    min_sources: int = DEFAULT_MIN_SOURCES
    rules: tuple[str, ...] = field(default_factory=tuple)

    @property
    def corroborated_count(self) -> int:
        return len(self.corroborated)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    def as_dict(self) -> dict[str, object]:
        return {
            "checked_points": self.checked_points,
            "corroborated_count": self.corroborated_count,
            "conflict_count": self.conflict_count,
            "single_source_count": len(self.single_source),
            "min_overlap": self.min_overlap,
            "min_sources": self.min_sources,
            "rules": list(self.rules),
            "corroborated": [item.as_dict() for item in self.corroborated],
            "conflicts": [item.as_dict() for item in self.conflicts],
            "single_source": [
                {"source_id": point.source_id, "text": point.text} for point in self.single_source
            ],
        }

    def _grouped_corroborated(self) -> list[tuple[str, list["Corroborated"]]]:
        """把"同样的两句原文"对应的多条数字合成一行显示。

        数据层仍然一个数字一条记录（好核对、好重算）；只是排版时合并，
        免得同一句话因为带了三四个数字就被重复打印三四次。
        """
        groups: dict[tuple[str, ...], list[Corroborated]] = {}
        for item in self.corroborated:
            key = tuple(point.text for point in item.points)
            groups.setdefault(key, []).append(item)
        grouped: list[tuple[str, list[Corroborated]]] = []
        for items in groups.values():
            numbers = " / ".join(f"`{number}`" for item in items for number in item.numbers)
            grouped.append((numbers, items))
        return grouped

    def to_markdown(self) -> str:
        lines: list[str] = [
            "## 跨来源比对（规则计算，未使用模型）",
            "",
            f"检视候选数据点 {self.checked_points} 条；判据：数字集合相同 + 来源不同才叫印证，"
            f"上下文 4-gram 相似度 ≥ {self.min_overlap} 且数字不同才叫冲突。",
            "",
        ]
        if self.corroborated:
            lines += [f"### 多个来源说到同一组数字（{self.corroborated_count} 组）", ""]
            for numbers, items in self._grouped_corroborated():
                sources = "、".join(items[0].source_ids)
                lines.append(
                    f"- 数字 {numbers} —— {items[0].source_count} 个来源（{sources}）"
                )
                for point in items[0].points:
                    lines.append(f"  - {point.source_id}：{point.text}")
            lines.append("")
        else:
            lines += ["### 多个来源说到同一组数字", "", "（没有发现）", ""]

        if self.conflicts:
            lines += [f"### 同一件事数字不一致（{self.conflict_count} 处，需人工判断）", ""]
            for item in self.conflicts:
                lines.append(
                    f"- 相似度 {item.overlap:.2f}：{item.left.source_id} 说"
                    f"「{item.left.text}」；{item.right.source_id} 说「{item.right.text}」"
                )
            lines += [
                "",
                "> 这里只标「两处说法不一致」，**不判断谁对**。口径、时点、统计范围都可能不同，"
                "必须回原文核对后再决定用哪个。",
                "",
            ]
        else:
            lines += ["### 同一件事数字不一致", "", "（没有发现；注意这只代表没检出，不代表数据正确）", ""]

        if self.single_source:
            lines += [
                f"### 只有单一来源的说法（{len(self.single_source)} 条，引用前应另找佐证）",
                "",
            ]
            for point in self.single_source[:10]:
                lines.append(f"- {point.source_id}：{point.text}")
            if len(self.single_source) > 10:
                lines.append(f"- …… 另有 {len(self.single_source) - 10} 条，见 JSON")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _contexts(points: Sequence[DataPoint]) -> list[str]:
    return [_context(point.text) for point in points]


class _DisjointSet:
    """并查集：把"在说同一件事"的数据点连成簇。"""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, index: int) -> int:
        while self._parent[index] != index:
            self._parent[index] = self._parent[self._parent[index]]
            index = self._parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def corroborate(
    points: Sequence[DataPoint],
    *,
    min_sources: int = DEFAULT_MIN_SOURCES,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> Corroboration:
    """对候选数据点做跨来源比对；规则见模块 docstring。"""
    usable = [point for point in points if point.numbers]
    contexts = _contexts(usable)
    groups = _DisjointSet(len(usable))

    # 第一步：不同来源之间、上下文足够像的两条，连成一簇（记下相似度供冲突展示）
    overlaps: dict[tuple[int, int], float] = {}
    for left in range(len(usable)):
        if len(contexts[left]) < MIN_CONTEXT_CHARS:
            continue
        for right in range(left + 1, len(usable)):
            if usable[left].source_id == usable[right].source_id:
                continue
            score = context_overlap(contexts[left], contexts[right])
            if score >= min_overlap:
                groups.union(left, right)
                overlaps[(left, right)] = score

    # 第二步：按簇汇总——簇内被 ≥min_sources 个来源说到的数字才算印证
    clusters: dict[int, list[int]] = {}
    for index in range(len(usable)):
        clusters.setdefault(groups.find(index), []).append(index)

    corroborated: list[Corroborated] = []
    conflicts: list[Conflict] = []
    single: list[DataPoint] = []
    conflict_signatures: set[tuple[str, str]] = set()
    for members in clusters.values():
        if len({usable[index].source_id for index in members}) < 2:
            single.extend(usable[index] for index in members)
            continue

        source_ids_by_number: dict[str, list[int]] = {}
        for index in members:
            for number in key_numbers(usable[index]):
                source_ids_by_number.setdefault(number, [])
                if index not in source_ids_by_number[number]:
                    source_ids_by_number[number].append(index)

        # 一个数字一条记录：分开列才不会把"城商行 1.38%"和"民营银行 4.11%"混成一句
        for number in _sorted_numbers(source_ids_by_number):
            indexes = source_ids_by_number[number]
            sources = {usable[index].source_id for index in indexes}
            if len(sources) < min_sources:
                continue
            # 每个来源只留一条引文，最多三条，避免同义重复刷屏
            picked: list[DataPoint] = []
            used_sources: set[str] = set()
            for index in indexes:
                point = usable[index]
                if point.source_id in used_sources:
                    continue
                used_sources.add(point.source_id)
                picked.append(point)
                if len(picked) >= 3:
                    break
            corroborated.append(
                Corroborated(numbers=(number,), points=tuple(picked))
            )

        # 簇内关键数字集合完全不相交的两条 = 同一件事的两种说法
        # 同一种"说法组合"只报一次：三个来源都说 A，一个说 B 时不该出现三条一样的冲突
        for left in range(len(members)):
            for right in range(left + 1, len(members)):
                left_index, right_index = members[left], members[right]
                left_numbers = set(key_numbers(usable[left_index]))
                right_numbers = set(key_numbers(usable[right_index]))
                if not left_numbers or not right_numbers:
                    continue
                if left_numbers & right_numbers:
                    continue
                score = overlaps.get((min(left_index, right_index), max(left_index, right_index)))
                if score is None:
                    continue
                signature = tuple(
                    sorted((number_key(usable[left_index]), number_key(usable[right_index])))
                )
                if signature in conflict_signatures:
                    continue
                conflict_signatures.add(signature)
                conflicts.append(
                    Conflict(left=usable[left_index], right=usable[right_index], overlap=score)
                )

    rules = (
        "上下文 4-gram 相似度 ≥ %.2f 的不同来源数据点 → 视为在说同一件事" % min_overlap,
        "同一簇内某数字（不含年份）被不少于 %d 个来源说到 → 记为印证" % min_sources,
        "同一簇内两条数据点的关键数字集合完全不相交 → 记为冲突（不判断谁对）",
        "不做同义词归并、不做单位换算、不给可信度打分",
    )
    corroborated.sort(key=lambda item: (-item.source_count, item.numbers))
    conflicts.sort(key=lambda item: -item.overlap)
    return Corroboration(
        corroborated=tuple(corroborated),
        conflicts=tuple(conflicts),
        single_source=tuple(single),
        checked_points=len(usable),
        min_overlap=min_overlap,
        min_sources=min_sources,
        rules=rules,
    )


def corroborate_from_dicts(items: Iterable[dict[str, object]]) -> Corroboration:
    """从底稿 JSON 里的 ``data_points`` 还原并比对（给下游脚本用）。"""
    points: list[DataPoint] = []
    for item in items:
        text = str(item.get("text") or "")
        source_id = str(item.get("source_id") or "")
        numbers = item.get("numbers") or []
        if not text or not source_id:
            continue
        points.append(
            DataPoint(
                source_id=source_id,
                text=text,
                numbers=tuple(str(number) for number in numbers),
            )
        )
    return corroborate(points)


__all__ = [
    "Conflict",
    "Corroborated",
    "Corroboration",
    "DEFAULT_MIN_OVERLAP",
    "DEFAULT_MIN_SOURCES",
    "context_overlap",
    "corroborate",
    "corroborate_from_dicts",
    "number_key",
]
