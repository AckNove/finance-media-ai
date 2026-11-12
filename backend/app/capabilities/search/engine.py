"""真实网页搜索：多关键词、站点限制、时间筛选、分页、去重、审计。

**不用任何付费 API**：直接请求公开搜索结果页（搜狗 / 360 / 必应）并解析 HTML。
代价是要跟着站点改版走，所以：

- 每个 provider 的解析器都是独立的纯函数，坏了只影响自己；
- 三个 provider 依次尝试，**一个失败不影响其他**，全部失败才算搜索失败；
- 结果统一去重（规范化 URL + 去掉 utm_* 追踪参数），并保留"来自哪个 provider"；
- 每次搜索都写一行 JSONL 审计（``logs/search_events.jsonl``）。

诚实边界：解析出来的 ``url`` 可能是搜索引擎的跳转链接（例如搜狗的 ``/link?url=``），
作者/发布时间在结果页上通常没有，就留空——**不猜**。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import httpx

from backend.app.capabilities.search.safety import USER_AGENT

logger = logging.getLogger(__name__)

TimeRange = Literal["", "day", "week", "month", "year"]

#: 可用的 provider（顺序即尝试顺序，前者失败自动落到后者）
PROVIDERS = ("sogou", "so360", "bing")

_TRACKING_PREFIXES = ("utm_", "spm", "from", "src", "ref", "channel")

_TIME_PARAMS: dict[str, dict[str, str]] = {
    # 搜狗：tsn=1 一天 / 2 一周 / 3 一月 / 4 一年
    "sogou": {"day": "1", "week": "2", "month": "3", "year": "4"},
    # 360：adv_t 语义接近
    "so360": {"day": "1", "week": "2", "month": "3", "year": "4"},
    # 必应使用 filters=ex1:"ez5" 之类的语法，这里只做一天/一周的粗粒度
    "bing": {"day": "ez1", "week": "ez2", "month": "ez3"},
}


class SearchUnavailable(RuntimeError):
    """所有 provider 都失败。``reasons`` 里是每个 provider 的失败原因。"""

    def __init__(self, message: str, *, reasons: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.reasons = tuple(reasons)

    def describe(self) -> dict[str, Any]:
        return {"reason": "search_unavailable", "message": self.message, "attempts": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """一次搜索的完整意图（关键词之外还有站点、时间与分页）。"""

    keywords: str
    site: str = ""
    time_range: TimeRange = ""
    page: int = 1
    limit: int = 10

    def __post_init__(self) -> None:
        if not self.keywords.strip():
            raise ValueError("搜索关键词不能为空")
        if self.page < 1:
            raise ValueError("page 从 1 开始")
        if not (1 <= self.limit <= 50):
            raise ValueError("limit 必须在 1..50 之间")
        if self.time_range not in ("", "day", "week", "month", "year"):
            raise ValueError(f"不支持的 time_range: {self.time_range}")

    @property
    def effective_keywords(self) -> str:
        keywords = self.keywords.strip()
        if self.site:
            site = self.site.strip()
            if not keywords.lower().startswith("site:"):
                keywords = f"site:{site} {keywords}"
        return keywords

    @property
    def terms(self) -> list[str]:
        return [part for part in re.split(r"\s+", self.keywords.strip()) if part]

    def as_dict(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "site": self.site,
            "time_range": self.time_range,
            "page": self.page,
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    provider: str
    rank: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
            "rank": self.rank,
        }


@dataclass(frozen=True, slots=True)
class SearchReport:
    query: SearchQuery
    hits: tuple[SearchHit, ...]
    providers_tried: tuple[str, ...]
    errors: Mapping[str, str] = field(default_factory=dict)
    searched_at: str = ""
    raw_count: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.hits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.as_dict(),
            "hits": [hit.as_dict() for hit in self.hits],
            "providers_tried": list(self.providers_tried),
            "errors": dict(self.errors),
            "searched_at": self.searched_at,
            "raw_count": self.raw_count,
        }


def clean_tracking(url: str) -> str:
    """去掉 utm_* 等追踪参数，便于去重与引用。"""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _absolute(base: str, href: str) -> str:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "#", "mailto:")):
        return ""
    if href.startswith("//"):
        return f"{urlsplit(base).scheme}:{href}"
    if href.startswith("/"):
        parts = urlsplit(base)
        return f"{parts.scheme}://{parts.netloc}{href}"
    if not href.startswith("http"):
        return ""
    return href


def _provider_url(provider: str, query: SearchQuery) -> str:
    q = query.effective_keywords
    if provider == "sogou":
        url = f"https://www.sogou.com/web?query={quote_plus(q)}"
        if query.page > 1:
            url += f"&page={query.page}"
        time_value = _TIME_PARAMS["sogou"].get(query.time_range)
        if time_value:
            url += f"&tsn={time_value}"
        return url
    if provider == "so360":
        url = f"https://www.so.com/s?q={quote_plus(q)}"
        if query.page > 1:
            url += f"&pn={query.page}"
        time_value = _TIME_PARAMS["so360"].get(query.time_range)
        if time_value:
            url += f"&adv_t={time_value}"
        return url
    url = f"https://cn.bing.com/search?q={quote_plus(q)}"
    if query.page > 1:
        url += f"&first={(query.page - 1) * 10 + 1}"
    time_value = _TIME_PARAMS["bing"].get(query.time_range)
    if time_value:
        url += f'&filters=ex1%3a"{time_value}"'
    return url


def parse_sogou(html: str, *, base: str = "https://www.sogou.com", limit: int = 10) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, str]] = []
    for block in soup.select("div.vrwrap, div.rb"):
        link = block.select_one("h3 a")
        if link is None:
            continue
        href = _absolute(base, link.get("href", ""))
        title = link.get_text(strip=True)
        if not href or not title:
            continue
        snippet_node = block.select_one("div.str-text, div.ft, div.str_info")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        found.append({"title": title, "url": href, "snippet": snippet})
        if len(found) >= limit:
            break
    return found


def parse_so360(html: str, *, base: str = "https://www.so.com", limit: int = 10) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, str]] = []
    for block in soup.select("li.res-list"):
        link = block.select_one("h3 a")
        if link is None:
            continue
        classes = " ".join(block.get("class") or [])
        href = _absolute(base, link.get("href", ""))
        title = link.get_text(strip=True)
        if "ad" in classes.split() or not href or not title:
            continue
        snippet_node = block.select_one("p.res-desc, div.res-rich, p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        found.append({"title": title, "url": href, "snippet": snippet})
        if len(found) >= limit:
            break
    return found


def parse_bing(html: str, *, base: str = "https://cn.bing.com", limit: int = 10) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, str]] = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if link is None:
            continue
        href = _absolute(base, link.get("href", ""))
        title = link.get_text(strip=True)
        if not href or not title:
            continue
        snippet_node = block.select_one("p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        found.append({"title": title, "url": href, "snippet": snippet})
        if len(found) >= limit:
            break
    return found


_PARSERS = {"sogou": parse_sogou, "so360": parse_so360, "bing": parse_bing}


def search(
    query: SearchQuery,
    *,
    providers: Sequence[str] = PROVIDERS,
    transport: httpx.BaseTransport | None = None,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
    audit_path: Path | None = None,
) -> SearchReport:
    """按顺序尝试各 provider，返回去重后的结果与错误清单。"""
    owned = client is None
    http = client or httpx.Client(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    hits: list[SearchHit] = []
    seen: set[str] = set()
    errors: dict[str, str] = {}
    tried: list[str] = []
    raw_count = 0
    try:
        for provider in providers:
            parser = _PARSERS.get(provider)
            if parser is None:
                errors[provider] = f"unknown_provider:{provider}"
                continue
            tried.append(provider)
            try:
                response = http.get(_provider_url(provider, query))
            except httpx.HTTPError as error:
                errors[provider] = f"network_error:{error}"
                continue
            if response.status_code >= 400:
                errors[provider] = f"http_{response.status_code}"
                continue
            try:
                items = parser(response.text, limit=query.limit * 3)
            except Exception as error:  # 解析器坏了自己扛，不影响其他 provider
                errors[provider] = f"parse_error:{error}"
                continue
            if not items:
                errors[provider] = "no_results"
                continue
            raw_count += len(items)
            for item in items:
                url = clean_tracking(item["url"])
                if url in seen:
                    continue
                seen.add(url)
                hits.append(
                    SearchHit(
                        title=item["title"],
                        url=url,
                        snippet=item["snippet"],
                        provider=provider,
                        rank=len(hits) + 1,
                    )
                )
                if len(hits) >= query.limit:
                    break
            if len(hits) >= query.limit:
                break
    finally:
        if owned:
            http.close()

    report = SearchReport(
        query=query,
        hits=tuple(hits),
        providers_tried=tuple(tried),
        errors=errors,
        searched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        raw_count=raw_count,
    )
    _audit(report, audit_path=audit_path)
    if not report.hits:
        raise SearchUnavailable(
            "所有搜索源都没有返回可用结果", reasons=[f"{k}:{v}" for k, v in errors.items()]
        )
    return report


def _audit(report: SearchReport, *, audit_path: Path | None = None) -> None:
    path = audit_path or _default_audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": report.searched_at,
                "kind": "web_search",
                "query": report.query.as_dict(),
                "providers": list(report.providers_tried),
                "hits": len(report.hits),
                "raw_count": report.raw_count,
                "errors": dict(report.errors),
            },
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as error:  # 审计失败不能影响搜索本身
        logger.warning("写搜索审计日志失败：%s", error)


def _default_audit_path() -> Path:
    from backend.app.core.config import PROJECT_ROOT

    return PROJECT_ROOT / "logs" / "search_events.jsonl"


__all__ = [
    "PROVIDERS",
    "SearchHit",
    "SearchQuery",
    "SearchReport",
    "SearchUnavailable",
    "clean_tracking",
    "parse_bing",
    "parse_so360",
    "parse_sogou",
    "search",
]
