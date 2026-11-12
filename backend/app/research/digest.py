"""证据汇编：把"抓到的真实资料"整理成可复核的研究底稿。

**这不是研究报告生成器**。它只做搬运与编号：

* 给每个来源一个稳定编号（S01、S02…），带上标题、URL、抓取时间、发布时间的原文值、
  正文 SHA-256 与快照路径——方便任何人回头核对"这句话是从哪一页来的"；
* 按检索词分组列出每篇正文的摘录，并抽出带数字的句子作为**候选数据点**，
  每条都标着来源编号，**不做跨来源归纳、不下结论**；
* 明确写出"哪些分析步骤没有做、为什么没做"。

为什么单独做这一层：模型不可用时（没有 API Key）研究任务不该整条停摆——
真实资料已经抓到手，先把"能被人复核的证据表"落盘，比空等一个模型更有用。
凡是这一步产出的文字，都必须标 ``generated_by="rules"``，
**不得**在报告里冒充分析结论。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1

#: 抽"数据点"的判据：一句话里同时出现数字与单位/百分号/量级词
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_UNIT_HINTS = (
    "%",
    "％",
    "亿元",
    "万元",
    "亿",
    "万",
    "个百分点",
    "倍",
    "元",
    "美元",
    "吨",
    "家",
    "人",
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])|\n+")

#: 一条候选数据点最多取多少个字（原文摘录，不改写）
MAX_CLAIM_CHARS = 160
#: 每个来源最多抽几条候选数据点（避免报告被同一页灌满）
MAX_CLAIMS_PER_SOURCE = 6


@dataclass(frozen=True, slots=True)
class SourceRef:
    """一条被真实抓取的来源。字段值一律照抄，缺就留空——**不补、不猜**。"""

    source_id: str
    title: str
    url: str
    site_name: str = ""
    author: str = ""
    published_at: str = ""
    sha256: str = ""
    snapshot_path: str = ""
    char_count: int = 0
    query: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "site_name": self.site_name,
            "author": self.author,
            "published_at": self.published_at,
            "sha256": self.sha256,
            "snapshot_path": self.snapshot_path,
            "char_count": self.char_count,
            "query": self.query,
            "verification_status": "unverified",
        }


@dataclass(frozen=True, slots=True)
class DataPoint:
    """从正文里原样摘出来的一句带数字的话（**不是结论**）。"""

    source_id: str
    text: str
    numbers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "text": self.text, "numbers": list(self.numbers)}


@dataclass(frozen=True, slots=True)
class Digest:
    title: str
    objective: str
    sources: tuple[SourceRef, ...] = ()
    data_points: tuple[DataPoint, ...] = ()
    queries: tuple[str, ...] = ()
    generated_at: str = ""
    generated_by: str = "rules"
    notes: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    corroboration: Any = None

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def source(self, source_id: str) -> SourceRef | None:
        for item in self.sources:
            if item.source_id == source_id:
                return item
        return None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA_VERSION,
            "title": self.title,
            "objective": self.objective,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "queries": list(self.queries),
            "source_count": self.source_count,
            "data_point_count": len(self.data_points),
            "sources": [item.as_dict() for item in self.sources],
            "data_points": [item.as_dict() for item in self.data_points],
            "notes": list(self.notes),
            "meta": dict(self.meta),
        }
        if self.corroboration is not None:
            payload["corroboration"] = self.corroboration.as_dict()
        return payload

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"# {self.title}",
            "",
            f"- 研究目标：{self.objective or '（未填写）'}",
            f"- 生成时间：{self.generated_at}",
            "- 生成方式：**规则汇编（未使用模型）** —— 本文件只整理已抓取的真实资料，"
            "不含分析结论；分析、对比、结论需接入模型后另出。",
            f"- 来源数：{self.source_count}　候选数据点：{len(self.data_points)}",
            "",
        ]
        if self.queries:
            lines += ["## 检索记录", ""]
            lines += [f"- `{query}`" for query in self.queries]
            lines.append("")

        lines += ["## 来源清单", ""]
        if not self.sources:
            lines += ["（没有任何来源被抓取成功）", ""]
        else:
            lines += [
                "| 编号 | 标题 | 站点 | 发布时间（原文） | 正文字数 | 正文 SHA-256 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for source in self.sources:
                lines.append(
                    "| {sid} | {title} | {site} | {published} | {chars} | `{sha}` |".format(
                        sid=source.source_id,
                        title=_cell(source.title or "（无标题）"),
                        site=_cell(source.site_name or "—"),
                        published=_cell(source.published_at or "（页面未给出）"),
                        chars=source.char_count or "—",
                        sha=(source.sha256[:16] + "…") if source.sha256 else "—",
                    )
                )
            lines.append("")
            lines += ["URL 与快照路径：", ""]
            for source in self.sources:
                snapshot = source.snapshot_path or "（未保存快照）"
                lines.append(f"- {source.source_id} {source.url}")
                lines.append(f"  - 快照：`{snapshot}`")
            lines.append("")

        lines += ["## 候选数据点（原文摘录，未核验）", ""]
        if not self.data_points:
            lines += ["（没有从正文中抽到带数字的句子）", ""]
        else:
            for index, point in enumerate(self.data_points, start=1):
                lines.append(f"{index}. {point.text} —— 见 {point.source_id}")
            lines.append("")

        if self.corroboration is not None:
            lines.append(self.corroboration.to_markdown())

        if self.notes:
            lines += ["## 这一版没有做的事", ""]
            lines += [f"- {note}" for note in self.notes]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _cell(text: str) -> str:
    """表格单元格里的竖线会破坏 markdown 表格，换掉。"""
    return (text or "").replace("|", "／").replace("\n", " ")[:80]


def _numbers_in(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_NUMBER.findall(text)))


def extract_data_points(
    text: str,
    *,
    source_id: str,
    limit: int = MAX_CLAIMS_PER_SOURCE,
) -> list[DataPoint]:
    """从正文里抽"带数字的句子"（原样摘录，不做改写）。"""
    points: list[DataPoint] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT.split(text or ""):
        sentence = (raw or "").strip()
        if len(sentence) < 8 or len(sentence) > MAX_CLAIM_CHARS:
            continue
        numbers = _numbers_in(sentence)
        if not numbers:
            continue
        if not any(hint in sentence for hint in _UNIT_HINTS):
            continue
        key = sentence[:60]
        if key in seen:
            continue
        seen.add(key)
        points.append(DataPoint(source_id=source_id, text=sentence, numbers=numbers))
        if len(points) >= limit:
            break
    return points


def sources_from_collect(outputs: Iterable[Mapping[str, Any]]) -> list[SourceRef]:
    """把 research.collect 的输出摊成来源清单（顺序即编号顺序）。"""
    sources: list[SourceRef] = []
    seen: set[str] = set()
    for output in outputs:
        if not isinstance(output, Mapping):
            continue
        for entry in output.get("results") or []:
            if not isinstance(entry, Mapping):
                continue
            query = str(entry.get("query") or "")
            for page in entry.get("fetched") or []:
                if not isinstance(page, Mapping):
                    continue
                url = str(page.get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append(
                    SourceRef(
                        source_id=f"S{len(sources) + 1:02d}",
                        title=str(page.get("title") or ""),
                        url=url,
                        site_name=str(page.get("site_name") or ""),
                        author=str(page.get("author") or ""),
                        published_at=str(page.get("published_at") or ""),
                        sha256=str(page.get("sha256") or ""),
                        snapshot_path=str(page.get("snapshot_path") or ""),
                        char_count=int(page.get("char_count") or 0),
                        query=query,
                    )
                )
    return sources


def build_digest(
    *,
    title: str,
    objective: str = "",
    upstream: Sequence[Mapping[str, Any]] = (),
    queries: Sequence[str] = (),
    now: datetime | None = None,
    extra_notes: Sequence[str] = (),
    cross_check: bool = True,
) -> Digest:
    """由上游阶段（collect / extract）的真实输出汇编出一份底稿。"""
    collect_outputs = [
        item.get("output") if isinstance(item, Mapping) else None for item in upstream
    ]
    usable = [item for item in collect_outputs if isinstance(item, Mapping)]
    sources = sources_from_collect(usable)
    points: list[DataPoint] = []
    excerpts: dict[str, str] = {}
    for source in sources:
        excerpt = _excerpt_for(sources=usable, url=source.url)
        excerpts[source.source_id] = excerpt
        points.extend(extract_data_points(excerpt, source_id=source.source_id))

    notes = [
        "本文件由规则汇编生成，**没有使用模型**：不含分析、对比、归因与结论。",
        "候选数据点是从正文里原样摘出的句子，**未做事实核查**，引用前必须回原文确认。",
        "同一篇资料可能被多个检索词命中，已按 URL 去重。",
    ]
    if cross_check:
        notes.append(
            "“跨来源比对”只做机械比对（上下文相似度 + 数字是否相同），"
            "标出“多来源印证”与“两处说法不一致”，**不判断谁对**。"
        )
    notes.extend(extra_notes)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    corroboration = None
    if cross_check:
        from backend.app.research.corroborate import corroborate

        corroboration = corroborate(points)
    return Digest(
        title=title,
        objective=objective,
        sources=tuple(sources),
        data_points=tuple(points),
        queries=tuple(dict.fromkeys(queries)),
        generated_at=stamp.isoformat(timespec="seconds"),
        notes=tuple(notes),
        meta={"upstream_stages": len(usable), "excerpts": excerpts},
        corroboration=corroboration,
    )


def _excerpt_for(*, sources: Sequence[Mapping[str, Any]], url: str) -> str:
    for output in sources:
        for entry in output.get("results") or []:
            if not isinstance(entry, Mapping):
                continue
            for page in entry.get("fetched") or []:
                if isinstance(page, Mapping) and str(page.get("url") or "") == url:
                    return str(page.get("excerpt") or "")
    return ""


def write_digest(digest: Digest, directory: Path, *, stem: str = "digest") -> dict[str, Path]:
    """把底稿写成 markdown + JSON 两份（JSON 供程序读，markdown 供人看）。"""
    import json

    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / f"{stem}.md"
    json_path = directory / f"{stem}.json"
    markdown_path.write_text(digest.to_markdown(), encoding="utf-8")
    json_path.write_text(
        json.dumps(digest.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"markdown": markdown_path, "json": json_path}


__all__ = [
    "SCHEMA_VERSION",
    "DataPoint",
    "Digest",
    "SourceRef",
    "build_digest",
    "extract_data_points",
    "sources_from_collect",
    "write_digest",
]
