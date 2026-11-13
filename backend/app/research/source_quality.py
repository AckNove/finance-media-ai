"""来源可信度分级与时效判断（规则，不涉及模型）。

为什么要单列一层：研究结论被质疑时，第一个问题永远是"你这句是哪儿来的、什么时候的"。
抓取阶段已经存了 URL 与页面上的发布时间，但**没人把它变成可判断的东西**——
全是新闻站点和全是交易所公告，可信度不是一个量级；三年前的统计口径拿来描述今年，
数字没错、结论是错的。

这里只做两件**纯规则**的事，且都写死在代码里可复核：

1. **站点分级**（:func:`tier_of`）：按域名后缀与一份显式白名单分成
   ``official`` / ``primary`` / ``media`` / ``aggregator`` / ``unknown`` 五档。
   注意这是**对站点性质的分级，不是对这条内容的分级**——官方站点也会发错，
   媒体也可能报道得很准。分级只用来提示"这条要不要格外去核对原文"。
2. **时效判断**（:func:`freshness_of`）：把页面上的发布时间换算成"距今多少天"，
   分成 ``fresh`` / ``recent`` / ``stale`` / ``unknown`` 四档。

**没有发布时间就是 unknown，不猜**；解析不了也是 unknown，并留一条 note。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

#: 五档可信度，从高到低。数值只用于排序与展示，不代表"可信概率"。
TIER_ORDER: tuple[str, ...] = ("official", "primary", "media", "aggregator", "unknown")

TIER_LABELS: dict[str, str] = {
    "official": "官方/监管",
    "primary": "一手披露",
    "media": "财经媒体",
    "aggregator": "聚合/自媒体",
    "unknown": "未知来源",
}

#: 监管机构、交易所、国际组织：数据以它们为准。
_OFFICIAL_HOSTS: tuple[str, ...] = (
    "pbc.gov.cn",
    "stats.gov.cn",
    "csrc.gov.cn",
    "ndrc.gov.cn",
    "mof.gov.cn",
    "miit.gov.cn",
    "nea.gov.cn",
    "moa.gov.cn",
    "customs.gov.cn",
    "safe.gov.cn",
    "sse.com.cn",
    "szse.cn",
    "bse.cn",
    "chinaclear.cn",
    "csindex.com.cn",
    "chinabond.com.cn",
    "imf.org",
    "worldbank.org",
    "oecd.org",
    "bis.org",
    "iea.org",
    "ecb.europa.eu",
    "federalreserve.gov",
    "sec.gov",
    "eia.gov",
)

#: 一手披露：上市公司公告库、企业官网/投资者关系、学术机构。
_PRIMARY_HOSTS: tuple[str, ...] = (
    "cninfo.com.cn",
    "hkexnews.hk",
    "sse.com.cn.cninfo",  # 防御性：不会命中，保留说明"优先看 cninfo"
    "ir.sse.com.cn",
)

#: 财经媒体：能读，但需要回到一手来源核对数字。
_MEDIA_HOSTS: tuple[str, ...] = (
    "xinhuanet.com",
    "news.cn",
    "people.com.cn",
    "chinanews.com.cn",
    "ce.cn",
    "yicai.com",
    "caixin.com",
    "21jingji.com",
    "stcn.com",
    "cs.com.cn",
    "cls.cn",
    "jiemian.com",
    "thepaper.cn",
    "eastmoney.com",
    "hexun.com",
    "wallstreetcn.com",
    "caijing.com.cn",
    "ft.com",
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
)

#: 聚合站与用户生成内容：搜到的入口，不是终点。
_AGGREGATOR_HOSTS: tuple[str, ...] = (
    "baidu.com",
    "sogou.com",
    "so.com",
    "toutiao.com",
    "zhihu.com",
    "weibo.com",
    "douyin.com",
    "bilibili.com",
    "xiaohongshu.com",
    "mp.weixin.qq.com",
    "163.com/dy",
    "baijiahao.baidu.com",
)

#: 官方后缀（中国政务域名与国家域名后缀）
_OFFICIAL_SUFFIXES: tuple[str, ...] = (".gov.cn", ".gov", ".gov.hk", ".gov.uk", ".edu.cn", ".edu", ".ac.cn")

#: 时效分档（天）。阈值是口径，不是真理：写出来是为了可以被讨论、被改。
FRESH_DAYS = 30
RECENT_DAYS = 180

FRESHNESS_LABELS: dict[str, str] = {
    "fresh": "近 30 天内",
    "recent": "半年内",
    "stale": "超过半年",
    "unknown": "无发布时间",
}

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y年%m月%d日",
    "%Y.%m.%d",
)

_DATE_LOOSE = re.compile(r"(\d{4})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})")


def _host_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:  # pragma: no cover - urlsplit 极少炸，留着防脏数据
        return ""
    return host.lower().lstrip(".")


def _matches(host: str, suffixes: Iterable[str]) -> bool:
    for item in suffixes:
        if not item:
            continue
        if item.startswith("."):
            if host.endswith(item) or host == item[1:]:
                return True
        elif host == item or host.endswith("." + item):
            return True
    return False


def tier_of(url: str, *, site_name: str = "") -> str:
    """按域名判定站点性质。判不出就是 ``unknown``——**不靠站点名猜**。"""

    host = _host_of(url)
    if not host:
        return "unknown"
    if _matches(host, _OFFICIAL_SUFFIXES) or _matches(host, _OFFICIAL_HOSTS):
        return "official"
    if _matches(host, _PRIMARY_HOSTS):
        return "primary"
    if _matches(host, _AGGREGATOR_HOSTS):
        return "aggregator"
    if _matches(host, _MEDIA_HOSTS):
        return "media"
    # 长尾企业官网：域名里带 ir./investor. 视作一手披露
    if host.startswith("ir.") or host.startswith("investor."):
        return "primary"
    return "unknown"


def parse_published_at(raw: str) -> datetime | None:
    """把页面上的发布时间原文解析成 datetime；解析不了返回 None（**不猜**）。"""

    text = (raw or "").strip()
    if not text:
        return None
    cleaned = text.replace("年", "-").replace("月", "-").replace("日", "") if "年" in text else text
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    loose = _DATE_LOOSE.search(cleaned)
    if loose:
        try:
            return datetime(
                int(loose.group(1)), int(loose.group(2)), int(loose.group(3)), tzinfo=UTC
            )
        except ValueError:
            return None
    return None


def freshness_of(raw: str, *, now: datetime | None = None, as_of: str | None = None) -> tuple[str, int | None]:
    """返回 ``(档位, 距今天数)``。没有发布时间 → ``("unknown", None)``。"""

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    published = parse_published_at(raw)
    reference = parse_published_at(as_of) if as_of else None
    if published is None:
        return "unknown", None
    anchor = reference or moment
    days = (anchor.date() - published.date()).days
    if days < 0:
        # 页面时间比现在还晚：要么是时区/抓取问题，要么是页面写错。不裁成 0，如实给负数口径。
        return "unknown", days
    if days <= FRESH_DAYS:
        return "fresh", days
    if days <= RECENT_DAYS:
        return "recent", days
    return "stale", days


@dataclass(frozen=True, slots=True)
class SourceQuality:
    """一条来源的可信度与时效结论（纯规则产出，可复核）。"""

    source_id: str
    url: str
    host: str
    tier: str
    freshness: str
    age_days: int | None = None
    published_at: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)

    @property
    def freshness_label(self) -> str:
        return FRESHNESS_LABELS.get(self.freshness, self.freshness)

    @property
    def age_label(self) -> str:
        if self.age_days is None:
            return "—"
        return f"{self.age_days} 天"

    @property
    def needs_original_check(self) -> bool:
        """要不要格外去核原文：媒体/聚合/未知站，或者过了半年，或者压根不知道发布多久了。"""

        return (
            self.tier in {"media", "aggregator", "unknown"}
            or self.freshness == "stale"
            or self.freshness == "unknown"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "host": self.host,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "published_at": self.published_at,
            "freshness": self.freshness,
            "freshness_label": self.freshness_label,
            "age_days": self.age_days,
            "needs_original_check": self.needs_original_check,
            "notes": list(self.notes),
        }


def assess(source: Any, *, now: datetime | None = None, as_of: str | None = None) -> SourceQuality:
    """给一个 ``SourceRef``（或任何带 source_id/url/published_at 的对象）打分。"""

    url = str(getattr(source, "url", "") or "")
    published = str(getattr(source, "published_at", "") or "")
    source_id = str(getattr(source, "source_id", "") or "")
    host = _host_of(url)
    tier = tier_of(url, site_name=str(getattr(source, "site_name", "") or ""))
    freshness, age_days = freshness_of(published, now=now, as_of=as_of)
    notes: list[str] = []
    if not published:
        notes.append("页面没有给出发布时间：无法判断是否过期，引用前请到原文确认时点")
    elif freshness == "unknown" and age_days is not None and age_days < 0:
        notes.append(f"发布时间比参照时间还晚 {abs(age_days)} 天：可能是时区或页面写错，先核原文")
    elif freshness == "unknown":
        notes.append(f"发布时间「{published}」解析不了：按无发布时间处理，不猜")
    if freshness == "stale" and age_days is not None:
        notes.append(f"距今 {age_days} 天：口径可能已变，不能拿来描述当期情况")
    return SourceQuality(
        source_id=source_id,
        url=url,
        host=host,
        tier=tier,
        freshness=freshness,
        age_days=age_days,
        published_at=published,
        notes=tuple(notes),
    )


def assess_all(
    sources: Sequence[Any], *, now: datetime | None = None, as_of: str | None = None
) -> list[SourceQuality]:
    return [assess(item, now=now, as_of=as_of) for item in sources]


def summarise(qualities: Iterable[SourceQuality]) -> dict[str, Any]:
    """汇总：各档几个、几个过期、几个没有发布时间、几个需要回原文核对。"""

    items = list(qualities)
    by_tier = {tier: 0 for tier in TIER_ORDER}
    by_freshness = {key: 0 for key in ("fresh", "recent", "stale", "unknown")}
    for item in items:
        by_tier[item.tier] = by_tier.get(item.tier, 0) + 1
        by_freshness[item.freshness] = by_freshness.get(item.freshness, 0) + 1
    return {
        "total": len(items),
        "by_tier": by_tier,
        "by_freshness": by_freshness,
        "tier_labels": {key: TIER_LABELS[key] for key in TIER_ORDER},
        "freshness_labels": dict(FRESHNESS_LABELS),
        "needs_original_check": sum(1 for item in items if item.needs_original_check),
        "missing_published_at": by_freshness["unknown"],
        "note": "站点分级按域名规则判定，只提示「要不要回原文核对」，不代表这条内容已被核实；"
        "时效阈值：30 天内为 fresh、180 天内为 recent、更早为 stale。",
    }


def to_markdown(qualities: Sequence[SourceQuality], *, title: str = "来源可信度与时效") -> str:
    summary = summarise(qualities)
    lines = [f"## {title}", "", summary["note"], ""]
    if not qualities:
        lines += ["（没有来源）", ""]
        return "\n".join(lines).rstrip() + "\n"
    lines += [
        "| 编号 | 站点 | 分级 | 发布时间（原文） | 距今 | 时效 | 提示 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in qualities:
        lines.append(
            "| {sid} | {host} | {tier} | {published} | {age} | {fresh} | {flag} |".format(
                sid=item.source_id or "—",
                host=item.host or "—",
                tier=item.tier_label,
                published=item.published_at or "（页面未给出）",
                age=item.age_label,
                fresh=item.freshness_label,
                flag="需回原文" if item.needs_original_check else "—",
            )
        )
    lines.append("")
    counts = summary["by_tier"]
    lines.append(
        "分级统计："
        + "　".join(f"{TIER_LABELS[key]} {counts[key]}" for key in TIER_ORDER)
        + f"；无发布时间 {summary['missing_published_at']} 条；需回原文核对 {summary['needs_original_check']} 条。"
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def as_of_date(moment: datetime | None = None) -> str:
    """给报告用的参照日期（``YYYY-MM-DD``）。"""

    return (moment or datetime.now(UTC)).date().isoformat()


__all__ = [
    "FRESH_DAYS",
    "FRESHNESS_LABELS",
    "RECENT_DAYS",
    "SourceQuality",
    "TIER_LABELS",
    "TIER_ORDER",
    "as_of_date",
    "assess",
    "assess_all",
    "freshness_of",
    "parse_published_at",
    "summarise",
    "tier_of",
    "to_markdown",
]

# 供类型检查之外的地方直接引用
_ = (date, field)
