"""真实抓取：安全可见范围内的一次 GET + 正文提取 + 快照落盘。

设计要点：

- **每一跳都过安全闸门**（重定向后重新校验 IP），不做 ``follow_redirects=True``；
- **字节上限**：边流边计数，超限立即中止（默认 5 MB），避免被超大页面拖死；
- **robots.txt**：按 UA 判定，拿不到 robots 时按"允许但记录"处理；
- **快照**：原始字节按 sha256 落盘，事后可复核"这段文字当时确实在页面上"；
- **抽取失败不算抓取失败**：页面拿到了但抽不出发布时间，就把字段留空，
  由上层标"待核实"，绝不编造。

本模块只负责"拿到并结构化"，不判断内容真伪（那是事实核查 Agent 的事）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from backend.app.capabilities.search.safety import (
    USER_AGENT,
    Resolver,
    RobotsDecision,
    UnsafeUrlError,
    assert_public_url,
    robots_allows,
)

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_MAX_REDIRECTS = 5

TEXT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
)

#: 正文里要丢掉的标签（导航/页脚/脚本都会污染证据文本）
DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "aside", "form", "svg")

_DATETIME_PATTERNS = (
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})",
    r"(\d{4})-(\d{2})-(\d{2})",
    r"(\d{4})/(\d{2})/(\d{2})",
    r"(\d{4})年(\d{1,2})月(\d{1,2})日",
)


class FetchError(RuntimeError):
    """抓取失败。``reason`` 是稳定错误码。"""

    def __init__(self, reason: str, message: str, *, url: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.url = url

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message, "url": self.url}


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """一次抓取的结构化结果（含可追溯字段）。"""

    url: str
    final_url: str
    status: int
    content_type: str
    byte_size: int
    sha256: str
    fetched_at: str
    text: str
    title: str = ""
    published_at: str = ""
    author: str = ""
    site_name: str = ""
    encoding: str = "utf-8"
    truncated: bool = False
    redirects: tuple[str, ...] = ()
    snapshot_path: str = ""
    robots_reason: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "fetched_at": self.fetched_at,
            "title": self.title,
            "published_at": self.published_at,
            "author": self.author,
            "site_name": self.site_name,
            "encoding": self.encoding,
            "truncated": self.truncated,
            "redirects": list(self.redirects),
            "snapshot_path": self.snapshot_path,
            "robots_reason": self.robots_reason,
            "char_count": self.char_count,
            "text": self.text,
        }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_datetime(raw: str | None) -> str:
    """把页面上的日期字符串规整成 ``YYYY-MM-DD`` / ``YYYY-MM-DDTHH:MM``。

    认不出来就返回空串——**不猜**。
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    for pattern in _DATETIME_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = [int(item) for item in match.groups()]
        year, month, day = parts[0], parts[1], parts[2]
        if not (1970 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
            return ""
        if len(parts) >= 5:
            hour, minute = parts[3], parts[4]
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"
        return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _meta_content(soup: Any, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _json_ld_fields(soup: Any) -> dict[str, str]:
    """从 JSON-LD 里挖 datePublished / author / headline（挖不到就空）。"""
    found: dict[str, str] = {}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        payload = script.string or script.get_text() or ""
        payload = payload.strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            if not found.get("published_at") and item.get("datePublished"):
                found["published_at"] = str(item["datePublished"])
            if not found.get("title") and item.get("headline"):
                found["title"] = str(item["headline"])
            author = item.get("author")
            if not found.get("author") and author:
                if isinstance(author, Mapping):
                    found["author"] = str(author.get("name") or "")
                elif isinstance(author, list) and author:
                    first = author[0]
                    found["author"] = (
                        str(first.get("name") or "") if isinstance(first, Mapping) else str(first)
                    )
                else:
                    found["author"] = str(author)
    return {key: value for key, value in found.items() if value}


def extract_article(html: str, *, url: str = "") -> dict[str, str]:
    """从 HTML 里抽标题 / 正文纯文本 / 发布时间 / 作者 / 站点名。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    # JSON-LD 藏在 <script> 里，而 <script> 稍后会被整段丢掉 —— 先挖再丢。
    json_ld = _json_ld_fields(soup)
    for tag_name in DROP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    title = (
        _meta_content(soup, prop="og:title")
        or json_ld.get("title", "")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    published = (
        _meta_content(soup, prop="article:published_time")
        or _meta_content(soup, name="pubdate")
        or _meta_content(soup, name="publishdate")
        or _meta_content(soup, name="date")
        or json_ld.get("published_at", "")
    )
    author = (
        _meta_content(soup, name="author")
        or _meta_content(soup, prop="article:author")
        or json_ld.get("author", "")
    )
    site_name = _meta_content(soup, prop="og:site_name")

    body = soup.body or soup
    raw_text = body.get_text("\n")
    lines = [line.strip() for line in raw_text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return {
        "title": title.strip(),
        "text": text,
        "published_at": parse_datetime(published),
        "author": author.strip(),
        "site_name": site_name.strip(),
    }


def _decode(body: bytes, content_type: str) -> tuple[str, str]:
    charset = ""
    match = re.search(r"charset=([\w\-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).lower()
    for candidate in ([charset] if charset else []) + ["utf-8", "gb18030", "latin-1"]:
        if not candidate:
            continue
        try:
            return body.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8(replace)"


def _snapshot_target(snapshot_dir: Path, url: str, sha256: str, content_type: str) -> Path:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "unknown").replace(":", "_")
    suffix = ".html"
    if "json" in content_type:
        suffix = ".json"
    elif content_type.startswith("text/plain"):
        suffix = ".txt"
    elif "xml" in content_type:
        suffix = ".xml"
    return snapshot_dir / host / f"{sha256}{suffix}"


def fetch_page(
    url: str,
    *,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    resolver: Resolver | None = None,
    respect_robots: bool = True,
    save_snapshot: bool = True,
    snapshot_dir: Path | None = None,
    user_agent: str = USER_AGENT,
) -> FetchedPage:
    """抓一个页面并结构化。任何一步不合规都抛 ``FetchError``。"""
    try:
        safe_url = assert_public_url(url, resolver=resolver)
    except UnsafeUrlError as error:
        raise FetchError(error.reason, error.message, url=url) from error

    owned_client = client is None
    http = client or httpx.Client(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": user_agent, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    robots_cache: dict[str, tuple[str, RobotsDecision]] = {}
    redirects: list[str] = []
    current = safe_url
    try:
        for _hop in range(max_redirects + 1):
            robots_reason = ""
            if respect_robots:
                decision = _robots_for(http, current, robots_cache, user_agent=user_agent)
                robots_reason = decision.reason
                if not decision.allowed:
                    raise FetchError(
                        "robots_disallowed",
                        f"robots.txt 禁止抓取（{decision.rule or decision.reason}）",
                        url=current,
                    )
            try:
                with http.stream("GET", current) as response:
                    status = response.status_code
                    headers = response.headers
                    redirect_target = headers.get("location")
                    if status in (301, 302, 303, 307, 308) and redirect_target:
                        nxt = str(httpx.URL(current).join(redirect_target))
                        try:
                            current = assert_public_url(nxt, resolver=resolver)
                        except UnsafeUrlError as error:
                            raise FetchError(
                                error.reason, f"重定向到不安全地址：{error.message}", url=nxt
                            ) from error
                        redirects.append(current)
                        continue
                    content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
                    if status >= 400:
                        raise FetchError(
                            "http_error", f"HTTP {status}", url=current
                        )
                    if content_type and not content_type.startswith(TEXT_CONTENT_TYPES):
                        raise FetchError(
                            "unsupported_content_type",
                            f"不支持的内容类型：{content_type}",
                            url=current,
                        )
                    body = bytearray()
                    truncated = False
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            truncated = True
                            break
                    raw = bytes(body[:max_bytes])
                    headers_snapshot = dict(headers)
            except httpx.TimeoutException as error:
                raise FetchError("timeout", f"请求超时：{error}", url=current) from error
            except httpx.HTTPError as error:
                raise FetchError("network_error", f"网络错误：{error}", url=current) from error

            text, encoding = _decode(raw, headers_snapshot.get("content-type", ""))
            page_text = ""
            title = author = site = published = ""
            if content_type.startswith(("text/html", "application/xhtml+xml")):
                extracted = extract_article(text, url=current)
                page_text = extracted["text"]
                title = extracted["title"]
                author = extracted["author"]
                site = extracted["site_name"]
                published = extracted["published_at"]
            else:
                page_text = text.strip()

            digest = hashlib.sha256(raw).hexdigest()
            snapshot_path = ""
            if save_snapshot:
                target_dir = snapshot_dir or _default_snapshot_dir()
                target = _snapshot_target(target_dir, current, digest, content_type)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(raw)
                snapshot_path = str(target)

            return FetchedPage(
                url=url,
                final_url=current,
                status=status,
                content_type=content_type,
                byte_size=len(raw),
                sha256=digest,
                fetched_at=_utcnow_iso(),
                text=page_text,
                title=title,
                published_at=published,
                author=author,
                site_name=site,
                encoding=encoding,
                truncated=truncated,
                redirects=tuple(redirects),
                snapshot_path=snapshot_path,
                robots_reason=robots_reason,
                meta={"headers": {k.lower(): v for k, v in headers_snapshot.items()}},
            )
        raise FetchError("too_many_redirects", f"重定向超过 {max_redirects} 跳", url=url)
    finally:
        if owned_client:
            http.close()


def _default_snapshot_dir() -> Path:
    from backend.app.core.config import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "snapshots"


def _robots_for(
    client: httpx.Client,
    url: str,
    cache: dict[str, tuple[str, RobotsDecision]],
    *,
    user_agent: str,
) -> RobotsDecision:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    cached = cache.get(origin)
    if cached is None:
        robots_url = f"{origin}/robots.txt"
        text = ""
        reachable = False
        try:
            response = client.get(robots_url, timeout=10.0)
            if response.status_code == 200:
                text = response.text
                reachable = True
        except httpx.HTTPError:
            reachable = False
        decision = (
            robots_allows(text, path=path, user_agent=user_agent)
            if reachable
            else RobotsDecision(True, "robots_unreachable")
        )
        cache[origin] = (text, decision)
        return decision
    stored_text, _ = cached
    return robots_allows(stored_text, path=path, user_agent=user_agent)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "FetchError",
    "FetchedPage",
    "extract_article",
    "fetch_page",
    "parse_datetime",
]
