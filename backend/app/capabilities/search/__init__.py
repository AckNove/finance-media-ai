"""真实搜索与抓取能力（V5.0 研究引擎的入口）。

三个层次，各自可单独测：

- :mod:`safety`：URL 合法性、SSRF 防护、robots.txt 判定；
- :mod:`fetcher`：逐跳跟重定向的抓取 + 正文抽取 + 快照落盘；
- :mod:`engine`：多 provider 搜索、去重、审计。

:mod:`tool` 把它们包装成 ``ToolRegistry`` 里的 ``web_search`` / ``fetch_url`` 两个工具。
"""

from backend.app.capabilities.search.engine import (
    PROVIDERS,
    SearchHit,
    SearchQuery,
    SearchReport,
    SearchUnavailable,
    clean_tracking,
    search,
)
from backend.app.capabilities.search.fetcher import (
    FetchError,
    FetchedPage,
    extract_article,
    fetch_page,
    parse_datetime,
)
from backend.app.capabilities.search.safety import (
    ALLOWED_SCHEMES,
    USER_AGENT,
    RobotsDecision,
    UnsafeUrlError,
    assert_public_url,
    normalise_url,
    parse_robots,
    robots_allows,
)
from backend.app.capabilities.search.tool import (
    make_fetch_url,
    make_web_search,
    register_search_adapters,
)

__all__ = [
    "ALLOWED_SCHEMES",
    "FetchError",
    "FetchedPage",
    "PROVIDERS",
    "RobotsDecision",
    "SearchHit",
    "SearchQuery",
    "SearchReport",
    "SearchUnavailable",
    "USER_AGENT",
    "UnsafeUrlError",
    "assert_public_url",
    "clean_tracking",
    "extract_article",
    "fetch_page",
    "make_fetch_url",
    "make_web_search",
    "normalise_url",
    "parse_datetime",
    "parse_robots",
    "register_search_adapters",
    "robots_allows",
    "search",
]
