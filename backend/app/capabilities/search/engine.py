"""真实网页搜索：多关键词、站点限制、时间筛选、分页、去重、审计。

**不用任何付费 API**：直接请求公开搜索结果页（搜狗 / 360 / 必应）并解析 HTML。
代价是要跟着站点改版走，所以：

- 每个 provider 的解析器都是独立的纯函数，坏了只影响自己；
- 三个 provider 依次尝试，**一个失败不影响其他**，全部失败才算搜索失败；
- 结果统一去重（规范化 URL + 去掉 utm_* 追踪参数），并保留"来自哪个 provider"；
- 每次搜索都写一行 JSONL 审计（``logs/search_events.jsonl``）。

诚实边界：解析出来的 ``url`` 可能是搜索引擎的跳转链接（例如搜狗的 ``/link?url=``），
这时会对它做**一次**跳转解析拿到真实地址（见 :func:`resolve_engine_redirects`），
解析不出来的保留原链接并记在 ``resolved_from`` 里；作者/发布时间在结果页上通常没有，
就留空——**不猜**。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import httpx

from backend.app.capabilities.search.safety import USER_AGENT, UnsafeUrlError, assert_public_url

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
    #: 若搜索结果原本是引擎的跳转链接，这里记原始链接，便于复核"它是怎么变过来的"
    resolved_from: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
            "rank": self.rank,
            "resolved_from": self.resolved_from,
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


def _real_target(link: Any, base: str) -> str:
    """取一条搜索结果的**真实**目标地址。

    中文搜索引擎普遍把 href 换成自己的跳转链接（``/link?url=...``），
    真实地址藏在属性里：360 用 ``data-mdurl``，部分镜像用 ``data-url`` / ``mu``。
    有就直接用；没有就退回 href（后面还有一步跳转解析兜底）。
    """
    for attribute in ("data-mdurl", "data-url", "mu", "data-href"):
        value = link.get(attribute)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return _absolute(base, link.get("href", ""))


def _is_engine_self(host: str, provider: str) -> bool:
    """结果指向搜索引擎自己（AI 助手页、站内导航）时不算有效结果。"""
    host = host.lower()
    if provider == "sogou":
        return host.endswith("sogou.com")
    if provider == "so360":
        return host.endswith("so.com") or host.endswith("360.cn")
    if provider == "bing":
        return host.endswith("bing.com")
    return False


def _engine_redirect(url: str, provider: str) -> bool:
    """判断一条结果是不是引擎自己的跳转链接（需要对它做一次解析）。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if provider == "so360":
        return host.endswith("so.com") and parsed.path.startswith("/link")
    if provider == "sogou":
        return host.endswith("sogou.com") and parsed.path.startswith("/link")
    if provider == "bing":
        return host.endswith("bing.com") and parsed.path.startswith("/ck/a")
    return False


def parse_sogou(html: str, *, base: str = "https://www.sogou.com", limit: int = 10) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, str]] = []
    for block in soup.select("div.vrwrap, div.rb"):
        link = block.select_one("h3 a")
        if link is None:
            continue
        href = _real_target(link, base)
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
        href = _real_target(link, base)
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


_SCRIPT_REDIRECTS = (
    re.compile(r"window\.location\.replace\(\s*[\"']([^\"']+)", re.I),
    re.compile(r"window\.location\.href\s*=\s*[\"']([^\"']+)", re.I),
    re.compile(r"location\.replace\(\s*[\"']([^\"']+)", re.I),
    re.compile(r"http-equiv=[\"']refresh[\"'][^>]*url=([^\"'>\s]+)", re.I),
    re.compile(r"[\"'](https?://[^\"']+)[\"']", re.I),
)


def _script_redirect(body: str) -> str:
    """从跳转中转页里读出真实地址。

    搜狗实测：``/link?url=<token>`` 返回 **200 + 288 字节的 HTML**，
    真实地址写在 ``<script>window.location.replace("http://...")</script>`` 里，
    没有 ``Location`` 头。360 的 ``/link`` 部分也是这种形态，
    所以只看状态码是读不到真实地址的。
    """
    for pattern in _SCRIPT_REDIRECTS:
        match = pattern.search(body)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
    return ""


def resolve_engine_redirects(
    hits: Sequence[SearchHit],
    *,
    http: httpx.Client,
    timeout: float = 10.0,
    resolver: Any = None,
) -> list[SearchHit]:
    """把引擎跳转链接换成真实目标地址（每条约一次 HEAD/GET，最多一跳）。

    为什么要单独一步：搜狗/360 的结果 href 是 ``/link?url=<token>``，直接抓这个地址
    会被对方 robots 拒绝（而且抓到的也不是正文）。但真实地址常常藏在属性里
    （360 的 ``data-mdurl``）——属性没有时，才用一次**不跟随**的请求读 ``Location``。

    安全上不放松：解析出来的地址仍要过 :func:`assert_public_url`，
    内网/回环/非 http(s) 一律丢掉原链接，宁可用跳转链接让下游明确失败，
    也不让"搜索引擎替我们访问内网"成为 SSRF 的旁路。

    解析失败的条目原样保留，并在 ``resolved_from`` 留空——**不假装解析成功**。
    """
    from urllib.parse import urlparse

    resolved: list[SearchHit] = []
    for hit in hits:
        if not _engine_redirect(hit.url, hit.provider):
            resolved.append(hit)
            continue
        target = ""
        for method in ("head", "get"):
            try:
                response = getattr(http, method)(
                    hit.url, follow_redirects=False, timeout=timeout
                )
            except httpx.HTTPError:
                continue
            location = response.headers.get("location", "")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                target = _absolute(hit.url, location)
                break
            if method == "get":
                # 搜狗/360 的跳转是 200 + JS 跳转页，没有 Location 头
                body = response.text
                if len(body) > 4096:
                    body = body[:4096]
                target = _script_redirect(body)
        if not target:
            resolved.append(hit)
            continue
        try:
            assert_public_url(target, resolver=resolver)
        except UnsafeUrlError as error:
            logger.warning("拒绝解析到非公网地址：%s (%s)", target, error.reason)
            resolved.append(hit)
            continue
        host = (urlparse(target).hostname or "").lower()
        if _is_engine_self(host, hit.provider):
            continue
        resolved.append(
            replace(hit, url=clean_tracking(target), resolved_from=hit.url)
        )
    return resolved


def _rededupe(hits: Sequence[SearchHit]) -> list[SearchHit]:
    """跳转解析之后重新去重并重排序号（两条跳转可能指向同一页）。"""
    seen: set[str] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        if hit.url in seen:
            continue
        seen.add(hit.url)
        unique.append(replace(hit, rank=len(unique) + 1))
    return unique


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
                from urllib.parse import urlparse

                host = (urlparse(url).hostname or "").lower()
                if _is_engine_self(host, provider) and not _engine_redirect(url, provider):
                    # 指向搜索引擎自己的 AI 页/站内导航：不是可用结果
                    continue
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
        hits = _rededupe(resolve_engine_redirects(hits, http=http, timeout=timeout))[: query.limit]
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
    "resolve_engine_redirects",
    "search",
]
