"""选题建议：从已经抓到的来源里挑出值得做的题目（纯规则，不联网、不花钱）。

为什么不用模型：
这一步的产出一旦被采纳就会花真钱去做研究，所以判断标准必须能被人工复核。
因此每条建议都给出**分数构成与理由**（"为什么它排这么前"），而不是一句
"AI 觉得这个好"。模型只在后续的规划与写作环节出场。

打分维度（都可解释、都可调）：
1. 时效（fresh/recent/stale/unknown）——热点选题过期就废。
2. 来源层级（官方/一手/媒体/聚合/未知）——同一条说法来自交易所公告，
   和来自自媒体，值得投入的程度完全不同。
3. 是否有数据（标题里带单位的量）——能落到数字的题目更不容易讲空。
4. 是否命中长期内容合集——偏离品牌的题目即使热也不该做。
5. 多来源共同报道（同一标题出现在 ≥2 个不同发布方）——值得做的强信号。

另外两条硬规则：
- 与已有选题高度相似（字符 3-gram Jaccard ≥ ``SIMILAR``）直接丢弃，
  不做"再提一次同样的题"。
- 标题过短（< ``TITLE_MIN``）不做——通常只是"公告"、"日报"这种壳。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence

from backend.app.research import source_quality

#: 默认只看最近多少天的来源
WINDOW_DAYS = 30
#: 一次最多读多少条来源（防止把整个库读进内存）
MAX_SOURCES = 800
#: 低于这个分数不进候选（避免"什么都能上"）
MIN_SCORE = 40
#: 标题长度门槛：太短的多半是栏目名或"公告"
TITLE_MIN = 6
TITLE_MAX = 60
#: 与已有选题的字符 3-gram Jaccard 超过它就算重复
SIMILAR = 0.82

_TIER_POINTS = {"official": 24, "primary": 20, "media": 12, "aggregator": 3, "unknown": 6}
_FRESH_POINTS = {"fresh": 30, "recent": 18, "stale": 6, "unknown": 4}
_TIER_LABEL = source_quality.TIER_LABELS
_FRESH_LABEL = source_quality.FRESHNESS_LABELS

#: 标题里"带单位的量"（与视频数据卡同一套口径，避免两处规则打架）。
#: **长的单位写在前面**：正则交替按顺序命中，"5.2万亿" 必须先匹配 "万亿"，
#: 否则只会吃到 "万"，分数与理由里就会出现半个单位。
_NUMBER_WITH_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个百分点|万亿元|亿美元|亿元|万元|万亿|美元|亿|万|倍|元|%|％)"
)
#: 归一化时丢掉的字符：空白、标点、括号、书名号等
_DROP = re.compile(r"[\s\u3000，。、；：！？“”‘’（）【】《》〈〉「」『』\-—–_/\\|·,.;:!?\"'()\[\]{}<>]+")
#: 明显的"壳标题"（栏目名 / 日报 / 免责声明），不当作选题
_SHELLS = (
    "免责声明",
    "版权声明",
    "关于我们",
    "联系我们",
    "网站地图",
    "首页",
    "登录",
    "注册",
    "日报",
    "晚报",
    "早报",
)
#: 合集关键词切分符
_SCOPE_SPLIT = re.compile(r"[、，,；;/\n]+")


@dataclass(frozen=True)
class Candidate:
    """一条候选：来自某一个已入库来源的一个标题。"""

    source_id: int
    title: str
    url: str = ""
    publisher: str = ""
    published_at: str = ""
    age_days: int | None = None
    tier: str = "unknown"
    freshness: str = "unknown"


@dataclass(frozen=True)
class Suggestion:
    """一条建议：同一标题的多条来源被合并成一条候选题目。"""

    title: str
    normalized: str
    score: int
    reasons: tuple[str, ...]
    source_ids: tuple[int, ...]
    urls: tuple[str, ...]
    collections: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    tier: str = "unknown"
    freshness: str = "unknown"
    age_days: int | None = None

    @property
    def tier_label(self) -> str:
        return _TIER_LABEL.get(self.tier, self.tier)

    @property
    def freshness_label(self) -> str:
        return _FRESH_LABEL.get(self.freshness, self.freshness)

    @property
    def corroborated(self) -> bool:
        """是否有多家不同发布方报道同一标题。"""
        return len(self.publishers) >= 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "score": self.score,
            "reasons": list(self.reasons),
            "source_ids": list(self.source_ids),
            "urls": list(self.urls),
            "publishers": list(self.publishers),
            "collections": list(self.collections),
            "numbers": list(self.numbers),
            "tier": self.tier,
            "tier_label": self.tier_label,
            "freshness": self.freshness,
            "freshness_label": self.freshness_label,
            "age_days": self.age_days,
            "corroborated": self.corroborated,
        }


def normalize_title(title: str) -> str:
    """把标题归一成可比较的形状（去空白与标点，英文小写）。"""
    return _DROP.sub("", title or "").strip().lower()


def _trigrams(text: str) -> set[str]:
    if len(text) < 3:
        return {text} if text else set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


def similarity(left: str, right: str) -> float:
    """字符 3-gram Jaccard 相似度（0~1）。空串按 0 处理。"""
    a, b = _trigrams(left), _trigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def numbers_in(title: str) -> tuple[str, ...]:
    """标题里带单位的量（保序去重）。"""
    seen: list[str] = []
    for match in _NUMBER_WITH_UNIT.finditer(title or ""):
        value = match.group(0).strip()
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def load_collection_keywords(config: dict[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """从 ``config/content_taxonomy.yaml`` 取「合集 id → 关键词」。

    关键词来自每个合集的 ``scope``（按顿号/逗号切）与 ``name``，
    这样新增合集只要改 YAML，不必改代码。
    """
    if config is None:
        from backend.app.core.config import load_yaml_config

        config = load_yaml_config("content_taxonomy.yaml")
    keywords: dict[str, tuple[str, ...]] = {}
    for item in config.get("collections") or []:
        code = str(item.get("id") or "").strip()
        if not code:
            continue
        words: list[str] = []
        name = str(item.get("name") or "").strip()
        if name:
            words.append(name)
        for scope in item.get("scope") or []:
            for piece in _SCOPE_SPLIT.split(str(scope)):
                text = piece.strip()
                # 只保留 2~8 字的短语：太短会误命中，太长不会出现在标题里
                if 2 <= len(text) <= 8 and text not in words:
                    words.append(text)
        keywords[code] = tuple(words)
    return keywords


def collections_of(title: str, keywords: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """标题命中的合集（按 YAML 顺序，去重）。"""
    text = title or ""
    hits = [code for code, words in keywords.items() if any(word in text for word in words)]
    return tuple(hits)


def _is_shell(title: str) -> bool:
    text = normalize_title(title)
    if not text:
        return True
    return any(shell in (title or "") for shell in _SHELLS) or len(text) < TITLE_MIN


def score_candidate(
    candidate: Candidate,
    *,
    keywords: dict[str, tuple[str, ...]],
    publishers: int = 1,
) -> tuple[int, list[str]]:
    """给一条候选打分，同时返回**理由**（每条理由都要能对上号）。"""
    reasons: list[str] = []
    score = 0

    fresh_points = _FRESH_POINTS.get(candidate.freshness, 4)
    score += fresh_points
    if candidate.age_days is not None:
        reasons.append(f"{_FRESH_LABEL.get(candidate.freshness, candidate.freshness)}（{candidate.age_days} 天前）+{fresh_points}")
    else:
        reasons.append(f"没有发布时间，按低分处理 +{fresh_points}")

    tier_points = _TIER_POINTS.get(candidate.tier, 6)
    score += tier_points
    reasons.append(f"来源层级：{_TIER_LABEL.get(candidate.tier, candidate.tier)} +{tier_points}")

    numbers = numbers_in(candidate.title)
    if numbers:
        score += 8
        reasons.append(f"标题里带数据（{'、'.join(numbers)}）+8")

    hits = collections_of(candidate.title, keywords)
    if hits:
        gained = min(20, 10 * len(hits))
        score += gained
        reasons.append(f"命中长期合集（{'、'.join(hits)}）+{gained}")

    if publishers >= 2:
        score += 12
        reasons.append(f"{publishers} 个不同发布方报道同一件事 +12")

    return score, reasons


def suggest(
    candidates: Iterable[Candidate],
    *,
    existing_titles: Sequence[str] = (),
    keywords: dict[str, tuple[str, ...]] | None = None,
    window_days: int = WINDOW_DAYS,
    limit: int = 10,
    min_score: int = MIN_SCORE,
) -> list[Suggestion]:
    """把候选来源合并、去重、打分、排序，返回建议列表。

    规则（全部确定性，可复现）：
    - 超过 ``window_days`` 的丢弃（``age_days`` 未知的保留但分数低）；
    - 壳标题（日报/免责声明等）与过短标题丢弃；
    - 同一归一化标题合并为一条，``publishers`` 计不同发布方；
    - 与 ``existing_titles`` 任一条相似度 ≥ ``SIMILAR`` 的丢弃；
    - 低于 ``min_score`` 的丢弃；按 (分数降序, 越新越前, 标题) 排序。
    """
    if keywords is None:
        keywords = load_collection_keywords()

    grouped: dict[str, list[Candidate]] = {}
    for item in candidates:
        if item.age_days is not None and item.age_days > window_days:
            continue
        if _is_shell(item.title):
            continue
        key = normalize_title(item.title)
        if not key:
            continue
        grouped.setdefault(key, []).append(item)

    existing = [normalize_title(text) for text in existing_titles if text]
    existing = [text for text in existing if text]

    suggestions: list[Suggestion] = []
    for key, group in grouped.items():
        if any(similarity(key, old) >= SIMILAR for old in existing):
            continue
        # 同一件事取"最新、层级最高"的那条作为代表
        def rank(item: Candidate) -> tuple[int, int, int]:
            return (
                item.age_days if item.age_days is not None else 10**6,
                source_quality.TIER_ORDER.index(item.tier)
                if item.tier in source_quality.TIER_ORDER
                else len(source_quality.TIER_ORDER),
                item.source_id,
            )

        group = sorted(group, key=rank)
        lead = group[0]
        publishers = tuple(
            dict.fromkeys(item.publisher or item.url for item in group if (item.publisher or item.url))
        )
        score, reasons = score_candidate(lead, keywords=keywords, publishers=len(publishers))
        if score < min_score:
            continue
        suggestions.append(
            Suggestion(
                title=lead.title.strip()[:TITLE_MAX],
                normalized=key,
                score=score,
                reasons=tuple(reasons),
                source_ids=tuple(item.source_id for item in group),
                urls=tuple(dict.fromkeys(item.url for item in group if item.url)),
                collections=collections_of(lead.title, keywords),
                numbers=numbers_in(lead.title),
                publishers=publishers,
                tier=lead.tier,
                freshness=lead.freshness,
                age_days=lead.age_days,
            )
        )

    suggestions.sort(
        key=lambda item: (
            -item.score,
            item.age_days if item.age_days is not None else 10**6,
            item.title,
        )
    )
    return suggestions[: max(0, limit)]


def candidate_of(
    *,
    source_id: int,
    title: str,
    url: str = "",
    publisher: str = "",
    published_at: str = "",
    now: datetime | None = None,
) -> Candidate:
    """把一条来源转成候选（层级与时效都交给 ``source_quality`` 判）。"""
    freshness, age_days = source_quality.freshness_of(published_at, now=now)
    return Candidate(
        source_id=source_id,
        title=title,
        url=url,
        publisher=publisher,
        published_at=published_at,
        age_days=age_days,
        tier=source_quality.tier_of(url, site_name=publisher),
        freshness=freshness,
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def collect_candidates(session, *, window_days: int = WINDOW_DAYS, now: datetime | None = None,
                       limit: int = MAX_SOURCES) -> list[Candidate]:
    """从库里读最近抓到的来源，转成候选（只读）。"""
    from sqlalchemy import select

    from backend.app.models.source import Source

    moment = now or datetime.now(UTC)
    rows = session.scalars(
        select(Source).order_by(Source.id.desc()).limit(max(1, limit))
    ).all()
    items: list[Candidate] = []
    for row in rows:
        candidate = candidate_of(
            source_id=int(row.id),
            title=str(row.title or ""),
            url=str(row.url or ""),
            publisher=str(row.publisher or ""),
            published_at=_as_text(row.published_at),
            now=moment,
        )
        # 抓取时间比发布时间可靠：没有发布时间的，用入库时间兜底
        if candidate.age_days is None and row.created_at is not None:
            candidate = candidate_of(
                source_id=int(row.id),
                title=str(row.title or ""),
                url=str(row.url or ""),
                publisher=str(row.publisher or ""),
                published_at=_as_text(row.created_at),
                now=moment,
            )
        if candidate.age_days is not None and candidate.age_days > window_days:
            continue
        items.append(candidate)
    return items


def existing_topic_titles(session, *, limit: int = 2000) -> list[str]:
    """已有选题的标题（用于去重，只读）。"""
    from sqlalchemy import select

    from backend.app.models.topic import Topic

    return [str(title or "") for title in session.scalars(select(Topic.title).limit(limit)).all()]


def suggest_from_db(
    session,
    *,
    window_days: int = WINDOW_DAYS,
    limit: int = 10,
    min_score: int = MIN_SCORE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """一次取齐：来源 → 候选 → 建议（路由直接用这个）。"""
    moment = now or datetime.now(UTC)
    candidates = collect_candidates(session, window_days=window_days, now=moment)
    keywords = load_collection_keywords()
    items = suggest(
        candidates,
        existing_titles=existing_topic_titles(session),
        keywords=keywords,
        window_days=window_days,
        limit=limit,
        min_score=min_score,
    )
    return {
        "generated_at": moment.isoformat(),
        "window_days": window_days,
        "min_score": min_score,
        "scanned": len(candidates),
        "items": [item.as_dict() for item in items],
        "note": (
            "按规则从已入库来源里挑的，分数只用来排序，采纳与否由人定；"
            "数据来源就是 sources 表里已经抓到的那些，没有额外联网。"
        ),
    }
