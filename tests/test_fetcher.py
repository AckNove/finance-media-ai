"""真实抓取测试：正文抽取、快照、字节上限、重定向与 SSRF。

全部用 ``httpx.MockTransport``，不碰真实网络。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from backend.app.capabilities.search.fetcher import (
    FetchError,
    extract_article,
    fetch_page,
    parse_datetime,
)

PUBLIC = "http://example.com"


def resolver(host: str) -> list[str]:
    """所有域名都"解析"到一个公网 IP，避免测试碰 DNS。"""
    return ["93.184.216.34"]


ARTICLE_HTML = """<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8">
  <title>页面标题（title 标签）</title>
  <meta property="og:title" content="商业银行如何赚钱">
  <meta property="article:published_time" content="2026-03-18T09:30:00+08:00">
  <meta property="og:site_name" content="示例财经">
  <script type="application/ld+json">
    {"@type":"NewsArticle","datePublished":"2026-03-18T09:30:00+08:00",
     "author":{"@type":"Person","name":"张三"}}
  </script>
</head>
<body>
  <nav>导航不应出现</nav>
  <article>
    <h1>商业银行如何赚钱</h1>
    <p>净息差是核心，其次是手续费收入。</p>
  </article>
  <script>var tracking = 1;</script>
  <footer>页脚不应出现</footer>
</body></html>
"""


def make_transport(
    *,
    robots: str | None = None,
    robots_status: int = 200,
    page: str = ARTICLE_HTML,
    page_status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    redirect: str | None = None,
    counter: dict[str, int] | None = None,
) -> httpx.MockTransport:
    calls: dict[str, int] = counter if counter is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] = calls.get(path, 0) + 1
        if path == "/robots.txt":
            if robots is None:
                return httpx.Response(robots_status, text="")
            return httpx.Response(robots_status, text=robots)
        if redirect is not None:
            return httpx.Response(302, headers={"location": redirect})
        return httpx.Response(
            page_status, content=page.encode("utf-8"), headers={"content-type": content_type}
        )

    return httpx.MockTransport(handler)


def test_fetch_extracts_article_and_writes_snapshot(tmp_path: Path):
    page = fetch_page(
        f"{PUBLIC}/a/1",
        transport=make_transport(),
        resolver=resolver,
        snapshot_dir=tmp_path,
    )
    assert page.status == 200
    assert page.title == "商业银行如何赚钱"
    assert page.published_at.startswith("2026-03-18")
    assert page.author == "张三"
    assert page.site_name == "示例财经"
    assert "净息差是核心" in page.text
    assert "导航不应出现" not in page.text
    assert "页脚不应出现" not in page.text
    assert "tracking" not in page.text
    assert page.redirects == ()
    assert page.robots_reason == "robots_empty"
    assert len(page.sha256) == 64
    snapshot = Path(page.snapshot_path)
    assert snapshot.is_file()
    assert snapshot.read_bytes() == ARTICLE_HTML.encode("utf-8")
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == page.sha256
    assert page.char_count > 0


def test_snapshot_can_be_disabled(tmp_path: Path):
    page = fetch_page(
        f"{PUBLIC}/a/1",
        transport=make_transport(),
        resolver=resolver,
        save_snapshot=False,
        snapshot_dir=tmp_path,
    )
    assert page.snapshot_path == ""
    assert list(tmp_path.glob("**/*")) == []


def test_robots_disallow_stops_the_fetch(tmp_path: Path):
    transport = make_transport(robots="User-agent: *\nDisallow: /private\n")
    with pytest.raises(FetchError) as excinfo:
        fetch_page(
            f"{PUBLIC}/private/x",
            transport=transport,
            resolver=resolver,
            snapshot_dir=tmp_path,
        )
    assert excinfo.value.reason == "robots_disallowed"
    assert "Disallow: /private" in excinfo.value.message


def test_robots_can_be_ignored_explicitly(tmp_path: Path):
    transport = make_transport(robots="User-agent: *\nDisallow: /private\n")
    page = fetch_page(
        f"{PUBLIC}/private/x",
        transport=transport,
        resolver=resolver,
        respect_robots=False,
        snapshot_dir=tmp_path,
    )
    assert page.status == 200
    assert page.robots_reason == ""


def test_redirect_to_private_address_is_blocked(tmp_path: Path):
    transport = make_transport(redirect="http://127.0.0.1:8000/admin")
    with pytest.raises(FetchError) as excinfo:
        fetch_page(f"{PUBLIC}/a", transport=transport, resolver=resolver, snapshot_dir=tmp_path)
    assert excinfo.value.reason == "blocked_ip"
    assert "重定向" in excinfo.value.message


def test_redirect_chain_is_followed_and_recorded(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/middle"})
        if request.url.path == "/middle":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200, content=ARTICLE_HTML.encode("utf-8"), headers={"content-type": "text/html"}
        )

    page = fetch_page(
        f"{PUBLIC}/start",
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        snapshot_dir=tmp_path,
    )
    assert page.final_url == f"{PUBLIC}/final"
    assert page.redirects == (f"{PUBLIC}/middle", f"{PUBLIC}/final")
    assert page.title == "商业银行如何赚钱"


def test_redirect_loop_stops_after_max_hops(tmp_path: Path):
    transport = make_transport(redirect="/loop")
    with pytest.raises(FetchError) as excinfo:
        fetch_page(
            f"{PUBLIC}/loop",
            transport=transport,
            resolver=resolver,
            max_redirects=3,
            snapshot_dir=tmp_path,
        )
    assert excinfo.value.reason == "too_many_redirects"


def test_http_error_is_reported(tmp_path: Path):
    transport = make_transport(page_status=404)
    with pytest.raises(FetchError) as excinfo:
        fetch_page(f"{PUBLIC}/a", transport=transport, resolver=resolver, snapshot_dir=tmp_path)
    assert excinfo.value.reason == "http_error"
    assert "404" in excinfo.value.message


def test_binary_content_type_is_rejected(tmp_path: Path):
    transport = make_transport(page="binary", content_type="image/png")
    with pytest.raises(FetchError) as excinfo:
        fetch_page(f"{PUBLIC}/a.png", transport=transport, resolver=resolver, snapshot_dir=tmp_path)
    assert excinfo.value.reason == "unsupported_content_type"


def test_byte_cap_truncates_instead_of_loading_everything(tmp_path: Path):
    big = "<html><body><p>" + ("数" * 5000) + "</p></body></html>"
    transport = make_transport(page=big)
    page = fetch_page(
        f"{PUBLIC}/big",
        transport=transport,
        resolver=resolver,
        max_bytes=500,
        snapshot_dir=tmp_path,
    )
    assert page.truncated is True
    assert page.byte_size == 500


def test_plain_text_page_keeps_body_as_is(tmp_path: Path):
    transport = make_transport(page="纯文本内容\n第二行", content_type="text/plain; charset=utf-8")
    page = fetch_page(f"{PUBLIC}/a.txt", transport=transport, resolver=resolver, snapshot_dir=tmp_path)
    assert page.text == "纯文本内容\n第二行"
    assert page.title == ""


def test_unsafe_url_fails_before_any_request(tmp_path: Path):
    counter: dict[str, int] = {}
    with pytest.raises(FetchError) as excinfo:
        fetch_page(
            "http://169.254.169.254/latest/meta-data/",
            transport=make_transport(counter=counter),
            resolver=resolver,
            snapshot_dir=tmp_path,
        )
    assert excinfo.value.reason == "blocked_ip"
    assert counter == {}


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 页面写的是本地时间：不编造时区，只做格式规整（时区偏移不保留，见 docstring）
        ("2026-03-18T09:30:00+08:00", "2026-03-18T09:30"),
        ("2026-03-18 09:30", "2026-03-18T09:30"),
        ("2026-03-18", "2026-03-18"),
        ("2026/03/18", "2026-03-18"),
        ("2026年3月8日", "2026-03-08"),
        ("", ""),
        ("昨天", ""),
        (None, ""),
    ],
)
def test_parse_datetime_is_strict(raw, expected):
    """"认不出来就留空"，绝不猜一个日期出来。"""
    assert parse_datetime(raw) == expected


def test_extract_article_survives_missing_metadata():
    extracted = extract_article("<html><body><p>只有正文</p></body></html>", url=PUBLIC)
    assert extracted["text"].strip() == "只有正文"
    assert extracted["title"] == ""
    assert extracted["published_at"] == ""
    assert extracted["author"] == ""


def test_fetch_error_describes_itself():
    error = FetchError("http_error", "HTTP 500", url="http://example.com/x")
    described = error.describe()
    assert described["reason"] == "http_error"
    assert described["url"] == "http://example.com/x"
