"""抓取安全闸门测试：协议白名单、SSRF 防护、robots.txt 判定。

全部离线：DNS 解析器由测试注入，不碰真实网络。
"""

from __future__ import annotations

import socket

import pytest

from backend.app.capabilities.search.safety import (
    RobotsDecision,
    UnsafeUrlError,
    assert_public_url,
    normalise_url,
    parse_robots,
    robots_allows,
)


def public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def test_public_https_url_passes_and_is_normalised():
    url = assert_public_url("HTTPS://Example.COM/a/b?x=1#section", resolver=public_resolver)
    assert url == "https://example.com/a/b?x=1"
    assert "#" not in url


def test_ip_literal_public_address_passes():
    assert assert_public_url("http://93.184.216.34/x", resolver=public_resolver) == (
        "http://93.184.216.34/x"
    )


@pytest.mark.parametrize(
    "url,reason",
    [
        ("", "empty_url"),
        ("   ", "empty_url"),
        ("file:///C:/Windows/win.ini", "bad_scheme"),
        ("gopher://example.com/1", "bad_scheme"),
        ("javascript:alert(1)", "bad_scheme"),
        ("http:///path", "missing_host"),
        ("http://localhost:8000/api", "blocked_host"),
        ("http://app.localhost/x", "blocked_host"),
        ("http://intranet.local/x", "blocked_host"),
        ("http://127.0.0.1:8000/x", "blocked_ip"),
        ("http://192.168.1.1/admin", "blocked_ip"),
        ("http://10.0.0.5/", "blocked_ip"),
        ("http://169.254.169.254/latest/meta-data/", "blocked_ip"),
        ("http://[::1]/", "blocked_ip"),
        ("http://0.0.0.0/", "blocked_ip"),
    ],
)
def test_unsafe_urls_are_rejected(url: str, reason: str):
    with pytest.raises(UnsafeUrlError) as excinfo:
        assert_public_url(url, resolver=public_resolver)
    assert excinfo.value.reason == reason
    assert excinfo.value.describe()["reason"] == reason


def test_hostname_resolving_to_private_ip_is_rejected():
    """域名看着正常、解析到内网 → 必须拒绝（这是 SSRF 的常见绕法）。"""

    def resolver(host: str) -> list[str]:
        return ["93.184.216.34", "127.0.0.1"]

    with pytest.raises(UnsafeUrlError) as excinfo:
        assert_public_url("http://evil.example.com/x", resolver=resolver)
    assert excinfo.value.reason == "blocked_ip"


def test_dns_failure_is_reported_as_dns_failure():
    def resolver(host: str) -> list[str]:
        raise socket.gaierror("no such host")

    with pytest.raises(UnsafeUrlError) as excinfo:
        assert_public_url("http://nope.invalid/x", resolver=resolver)
    assert excinfo.value.reason == "dns_failure"


def test_empty_dns_answer_is_rejected():
    with pytest.raises(UnsafeUrlError) as excinfo:
        assert_public_url("http://nope.invalid/x", resolver=lambda host: [])
    assert excinfo.value.reason == "dns_failure"


def test_normalise_url_keeps_query_and_drops_fragment():
    assert normalise_url("http://A.example/p?a=1#frag") == "http://a.example/p?a=1"
    assert normalise_url("http://a.example") == "http://a.example/"


def test_parse_robots_ignores_comments_and_blank_lines():
    rules = parse_robots(
        "# comment\nUser-agent: *\n\nDisallow: /private  # inline\nSitemap: /s.xml\n"
    )
    assert ("user-agent", "*") in rules
    assert ("disallow", "/private") in rules
    assert rules[-1] == ("sitemap", "/s.xml")


def test_robots_allows_everything_when_empty():
    decision = robots_allows("", path="/anything")
    assert decision == RobotsDecision(True, "robots_empty")


def test_robots_disallow_blocks_prefix():
    text = "User-agent: *\nDisallow: /private\n"
    blocked = robots_allows(text, path="/private/x")
    assert blocked.allowed is False
    assert blocked.rule == "Disallow: /private"
    allowed = robots_allows(text, path="/public/x")
    assert allowed.allowed is True


def test_robots_longer_allow_wins_over_shorter_disallow():
    text = "User-agent: *\nDisallow: /news\nAllow: /news/open\n"
    assert robots_allows(text, path="/news/open/2026").allowed is True
    assert robots_allows(text, path="/news/closed").allowed is False


def test_robots_group_for_our_bot_takes_effect():
    text = (
        "User-agent: Googlebot\nDisallow: /\n\n"
        "User-agent: FinanceMediaAI-ResearchBot\nDisallow: /nogo\n"
    )
    assert robots_allows(text, path="/nogo/a").allowed is False
    assert robots_allows(text, path="/ok").allowed is True
