"""抓取安全闸门：URL 合法性、SSRF 防护、robots.txt 遵从。

三条硬规则（V5.0 §研究引擎）：

1. **只允许 http/https**，其他协议（file://、gopher://、data:）一律拒绝；
2. **不允许打到内网 / 回环 / 链路本地 / 保留地址**——否则一个
   ``http://127.0.0.1:8000/api/...`` 就能让研究 Agent 读走本机数据；
   域名必须先解析成 IP 再判定，解析出的**每一个** IP 都要过检查；
3. **robots.txt 说不行就不行**：按 UA 判定 ``can_fetch``，robots 拿不到时按
   "允许但记录" 处理（这是 RFC 9309 的实践折中，并写进审计日志）。

重定向同样要过这三关：每跳都重新做 IP 检查，而不是只看第一跳。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

#: 研究抓取使用的 UA（写清楚是谁，方便站点管理员识别）
USER_AGENT = (
    "Mozilla/5.0 (compatible; FinanceMediaAI-ResearchBot/1.0; "
    "+https://github.com/AckNove/finance-media-ai)"
)

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 常见"看起来是外网其实指向内网"的域名后缀
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")

Resolver = Callable[[str], Iterable[str]]


class UnsafeUrlError(ValueError):
    """URL 不安全。``reason`` 是稳定错误码，供 API 与前端展示。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def describe(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


def default_resolver(host: str) -> list[str]:
    """把主机名解析成 IP 列表（失败时抛 ``socket.gaierror``）。"""
    infos = socket.getaddrinfo(host, None)
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _is_forbidden_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True  # 解析不出来的地址一律不放行
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def normalise_url(url: str) -> str:
    """去掉锚点、把 host 变小写，其余保持原样（不改查询参数）。"""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def assert_public_url(url: str, *, resolver: Resolver | None = None) -> str:
    """校验并返回规范化后的 URL；不安全就抛 ``UnsafeUrlError``。"""
    if not url or not url.strip():
        raise UnsafeUrlError("empty_url", "URL 为空")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            "bad_scheme", f"只允许 http/https，收到 {scheme or '（无协议）'}"
        )
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise UnsafeUrlError("missing_host", "URL 缺少主机名")
    if host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("blocked_host", f"拒绝抓取本地主机：{host}")

    # 主机名本身就是 IP 时直接判定；否则逐个解析结果判定
    addresses: list[str]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        resolve = resolver or default_resolver
        try:
            addresses = list(resolve(host))
        except (socket.gaierror, OSError) as error:
            raise UnsafeUrlError("dns_failure", f"域名解析失败：{host}（{error}）") from error
        if not addresses:
            raise UnsafeUrlError("dns_failure", f"域名没有解析结果：{host}")
    else:
        addresses = [host]

    forbidden = [address for address in addresses if _is_forbidden_ip(address)]
    if forbidden:
        raise UnsafeUrlError(
            "blocked_ip", f"{host} 解析到内网/保留地址 {forbidden[0]}，拒绝抓取"
        )
    return normalise_url(url)


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """robots.txt 判定结果（含"为什么这么判"，写进审计日志）。"""

    allowed: bool
    reason: str
    rule: str = ""


def parse_robots(text: str) -> list[tuple[str, str]]:
    """把 robots.txt 解析成 ``(directive, value)`` 序列（保留原顺序）。"""
    rules: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        directive, _, value = line.partition(":")
        rules.append((directive.strip().lower(), value.strip()))
    return rules


def robots_allows(text: str, *, path: str, user_agent: str = USER_AGENT) -> RobotsDecision:
    """按 RFC 9309 的简化实现判断 ``path`` 是否允许抓取。

    只看与我们 UA 相关的分组：``User-agent: *`` 或名字出现在 UA 里的分组；
    组内 ``Disallow`` 最长匹配优先，``Allow`` 同长度时优先。
    """
    rules = parse_robots(text)
    if not rules:
        return RobotsDecision(True, "robots_empty")

    ua_token = user_agent.split("/")[0].lower()  # "mozilla" 之类太宽，另取产品名
    product = ""
    if "financemediaai-researchbot" in user_agent.lower():
        product = "financemediaai-researchbot"

    groups: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    current_agents: list[str] = []
    for directive, value in rules:
        if directive == "user-agent":
            if current is not None and current_agents:
                groups.append(current)
                current = None
                current_agents = []
            current_agents.append(value.lower())
        elif directive in {"allow", "disallow"}:
            if current is None:
                current = {"allow": [], "disallow": [], "agents": current_agents}  # type: ignore[dict-item]
            current[directive].append(value)
        elif directive in {"sitemap", "crawl-delay", "host"}:
            continue
    if current is not None:
        groups.append(current)

    def matches(agent: str) -> bool:
        if agent == "*":
            return True
        if product and product in agent:
            return True
        return agent and agent in ua_token

    applicable = [group for group in groups if any(matches(a) for a in group.get("agents", []))]
    if not applicable:
        return RobotsDecision(True, "no_matching_group")

    best_len = -1
    best_allow = True
    best_rule = ""
    for group in applicable:
        for rule in group["disallow"]:  # type: ignore[union-attr]
            if rule and path.startswith(rule) and len(rule) >= best_len:
                if len(rule) > best_len or not best_allow:
                    best_len, best_allow, best_rule = len(rule), False, f"Disallow: {rule}"
        for rule in group["allow"]:  # type: ignore[union-attr]
            if rule and path.startswith(rule) and len(rule) >= best_len:
                best_len, best_allow, best_rule = len(rule), True, f"Allow: {rule}"

    if best_len < 0:
        return RobotsDecision(True, "no_matching_rule")
    return RobotsDecision(best_allow, "matched", best_rule)


__all__ = [
    "ALLOWED_SCHEMES",
    "RobotsDecision",
    "UnsafeUrlError",
    "USER_AGENT",
    "assert_public_url",
    "default_resolver",
    "normalise_url",
    "parse_robots",
    "robots_allows",
]
