"""把一条「选题建议」变成真选题：带上来源、挂上合集，并且**不许悄悄造重复**。

为什么要有这一层：选题页的「采纳为选题」原来只发 ``{"title": ...}``，于是建议里的
12 条来源、命中的合集、分数与理由全部当场蒸发——人第二天再打开时，看到的只是一行标题，
既不知道自己当初凭什么觉得它值得做，也没法回原文。而研究任务是从选题起跑的，
起点没有来源，后面整条链路就只能重新摸一遍。

三条纪律：

1. **来源由服务端自己查**。客户端只交 ``source_ids``，标题/发布方/链接一律从库里读，
   不信任页面传来的文本（页面上的东西可能是旧的、也可能是改过的）。
2. **重复就不建**。标题归一后与已有选题相似度 ``>= SIMILAR`` 时直接拒绝，并告诉人
   已经有一个 id 多少的选题——同一个故事被采纳三次是最常见的脏数据来源。
3. **不编。**描述里只写能核实的（来源清单、命中的合集），推荐分数与理由照抄页面上那次的
   计算并注明「采纳时页面显示」，不当成事实写进选题。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.source import Source
from backend.app.models.topic import Topic, TopicColumn
from backend.app.research import topic_suggestions as suggestions
from backend.app.schemas.topic import TopicCreate
from backend.app.services.topic_service import TopicService

MAX_SOURCES = 40
MAX_REASONS = 8
MAX_DESCRIPTION = 4000

logger = logging.getLogger(__name__)


class IntakeError(RuntimeError):
    """采纳失败：带稳定的原因码，路由层据此决定回 409 还是 422。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class IntakeResult:
    """一次采纳的结果：建出来的选题 + 用到的来源 + 挂上的合集。"""

    topic: Topic
    sources: tuple[dict[str, Any], ...] = ()
    collections: tuple[str, ...] = ()
    description: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic.id,
            "title": self.topic.title,
            "sources": [dict(item) for item in self.sources],
            "collections": list(self.collections),
            "description": self.description,
            "note": self.note,
        }


def find_duplicate(session: Session, title: str) -> tuple[int, str, float] | None:
    """找一个和 ``title`` 几乎一样的已有选题：``(id, title, similarity)``。"""

    target = suggestions.normalize_title(title)
    if not target:
        return None
    best: tuple[int, str, float] | None = None
    for topic_id, existing in session.execute(select(Topic.id, Topic.title)):
        score = suggestions.similarity(target, suggestions.normalize_title(existing or ""))
        if score >= suggestions.SIMILAR and (best is None or score > best[2]):
            best = (int(topic_id), str(existing or ""), round(score, 3))
    return best


def _source_rows(session: Session, source_ids: Iterable[int]) -> tuple[list[Source], list[int]]:
    wanted = [int(value) for value in source_ids if int(value) > 0]
    if not wanted:
        return [], []
    rows = list(session.scalars(select(Source).where(Source.id.in_(wanted))).all())
    found = {row.id for row in rows}
    missing = [value for value in wanted if value not in found]
    rows.sort(key=lambda row: wanted.index(row.id))
    return rows, missing


def _describe(row: Source, index: int) -> dict[str, Any]:
    published = getattr(row, "published_at", None)
    return {
        "index": index,
        "id": row.id,
        "title": row.title,
        "publisher": (row.publisher or "").strip(),
        "url": (row.url or "").strip(),
        "published_at": published.date().isoformat() if published else "",
    }


def _description(
    *,
    items: list[dict[str, Any]],
    collections: Iterable[str],
    score: int | None,
    reasons: Iterable[str],
    adopt_note: str,
) -> str:
    lines: list[str] = ["由「选题建议」采纳而来：这些是当时支撑它的已入库来源，研究时可直接回原文核对。"]
    if score is not None:
        lines.append(f"采纳时页面显示的建议分数：{score} 分（规则打分，不是事实判断）。")
    for item in items:
        head = f"- S{item['id']}《{item['title']}》"
        extra = " · ".join(part for part in (item["publisher"], item["published_at"]) if part)
        if extra:
            head = f"{head} {extra}"
        if item["url"]:
            head = f"{head} {item['url']}"
        lines.append(head)
    picked = [code for code in collections if code]
    if picked:
        lines.append(f"当时命中的合集：{'、'.join(picked)}。")
    notes = [text.strip() for text in reasons if str(text).strip()][:MAX_REASONS]
    if notes:
        lines.append("当时的推荐理由：")
        lines.extend(f"- {text}" for text in notes)
    if adopt_note.strip():
        lines.append(f"采纳时补的说明：{adopt_note.strip()}")
    text = "\n".join(lines)
    return text[:MAX_DESCRIPTION]


def _resolve_column(session: Session, *, column_id: int | None, collections: list[str]) -> int | None:
    if column_id is not None:
        if session.get(TopicColumn, int(column_id)) is None:
            raise IntakeError("unknown_column", f"column_id={column_id} 不存在")
        return int(column_id)
    for code in collections:
        row = session.scalar(select(TopicColumn).where(TopicColumn.code == code))
        if row is not None:
            return row.id
    return None


def intake_suggestion(
    session: Session,
    *,
    title: str,
    source_ids: Iterable[int],
    column_id: int | None = None,
    score: int | None = None,
    reasons: Iterable[str] = (),
    adopt_note: str = "",
) -> IntakeResult:
    """采纳一条建议：建选题 + 把来源写进描述 + 挂合集。"""

    clean_title = (title or "").strip()
    if not clean_title:
        raise IntakeError("empty_title", "标题是空的：这条建议可能已经过期，重新算一次再采纳")

    rows, missing = _source_rows(session, source_ids)
    if missing:
        raise IntakeError(
            "unknown_sources",
            f"这些来源在当前库里查不到：{', '.join(str(value) for value in missing)}"
            "——可能换了数据库，重新算一次建议再采纳",
        )
    if not rows:
        raise IntakeError(
            "no_sources",
            "这条建议没带任何来源：只有标题就别用「采纳」，直接在「新建选题」里写标题即可",
        )
    if len(rows) > MAX_SOURCES:
        rows = rows[:MAX_SOURCES]

    duplicate = find_duplicate(session, clean_title)
    if duplicate is not None:
        topic_id, existing_title, score_of = duplicate
        raise IntakeError(
            "duplicate_topic",
            f"已经有一个很像的选题了：#{topic_id}《{existing_title}》（相似度 {score_of}）。"
            "去「选题列表」里找它，别重复建——同一个故事被采纳三次，后面每条都要分叉。",
        )

    keywords = suggestions.load_collection_keywords()
    collections = list(suggestions.collections_of(clean_title, keywords))
    resolved_column = _resolve_column(
        session, column_id=column_id, collections=collections
    )

    items = [_describe(row, index) for index, row in enumerate(rows, start=1)]
    description = _description(
        items=items,
        collections=collections,
        score=score,
        reasons=reasons,
        adopt_note=adopt_note,
    )

    topic = TopicService(session).create(
        TopicCreate(
            title=clean_title,
            description=description,
            column_id=resolved_column,
        )
    )
    attached: list[str] = []
    if resolved_column is not None:
        column = session.get(TopicColumn, resolved_column)
        if column is not None:
            try:
                TopicService(session).attach_collection(
                    topic.id, column_id=resolved_column, is_primary=True
                )
                attached.append(column.code)
            except Exception:  # noqa: BLE001 - 挂合集失败不该让已建好的选题回滚
                logger.warning("选题 %s 挂合集 %s 失败", topic.id, resolved_column, exc_info=True)

    note = f"已建选题 #{topic.id}，带上 {len(items)} 条来源"
    if attached:
        note += f"，挂到合集 {attached[0]}"
    elif collections:
        # 标题命中了合集，但库里没有这个栏目——多半是还没 seed 过 taxonomy
        note += (
            f"，标题命中合集 {'、'.join(collections)}，但库里还没有这个栏目："
            "先跑一次 `python -m backend.scripts.seed_collections`，或到「选题」页手动建栏目"
        )
    else:
        note += "，标题没命中任何合集，所以没挂合集（不影响研究）"
    return IntakeResult(
        topic=topic,
        sources=tuple(items),
        collections=tuple(attached),
        description=description,
        note=note,
    )
