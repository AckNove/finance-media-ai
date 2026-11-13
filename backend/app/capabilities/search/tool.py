"""``web_search`` / ``fetch_url`` 工具适配器（V5.0 M3）。

把搜索与抓取包装成 ``ToolRegistry`` 能派发的工具：

- ``web_search``：关键词 + 站点限制 + 时间筛选 + 分页 → 去重后的结果列表；
- ``fetch_url``：单页抓取 → 正文、标题、发布时间、作者、快照路径与 SHA-256。

**状态映射诚实**：所有源都失败 → ``error`` + ``search_unavailable``；URL 不安全 →
``error`` + 具体原因码（``blocked_ip`` 等，来自 :class:`UnsafeUrlError`）；
robots 禁止 → ``error`` + ``robots_disallowed``。绝不把失败包装成 "ok"。
"""

from __future__ import annotations

from typing import Any, Sequence

from backend.app.capabilities.search.engine import (
    PROVIDERS,
    SearchQuery,
    SearchUnavailable,
    search,
)
from backend.app.capabilities.search.fetcher import FetchError, fetch_page
from backend.app.capabilities.tools import ToolResult
from backend.app.core.caller import CallerContext


def _search_enabled() -> bool:
    """``features.search`` 是**唯一**的联网检索总闸。

    V5.0 之前这个开关只挡工作台的搜索页，流水线里的 ``web_search`` 照跑——
    于是"关掉"是假的。现在工具层也读同一个开关：关着就明确拒绝，不偷偷联网。
    """

    try:
        from backend.app.core.config import get_features_settings

        return bool(get_features_settings().search)
    except Exception:  # pragma: no cover - 配置坏掉时不阻塞，交给上层报错
        return True


def _disabled(tool: str) -> ToolResult:
    return ToolResult(
        status="error",
        error_code="feature_disabled",
        error_message=(
            f"{tool} 需要联网检索，但 config/settings.yaml 里 features.search=false。"
            "把 features.search 改成 true 再试；关着时系统不会发起任何外部请求。"
        ),
        data={"feature": "search", "enabled": False},
    )


def make_web_search(*, transport: Any = None) -> Any:
    """构造 ``web_search`` provider（``transport`` 仅供测试注入 MockTransport）。"""

    def web_search(
        *,
        caller: CallerContext,
        keywords: str,
        site: str = "",
        time_range: str = "",
        page: int = 1,
        limit: int = 10,
        providers: Sequence[str] | None = None,
        **_: Any,
    ) -> ToolResult:
        if not _search_enabled():
            return _disabled("web_search")
        try:
            query = SearchQuery(
                keywords=keywords,
                site=site,
                time_range=time_range,  # type: ignore[arg-type]
                page=page,
                limit=limit,
            )
        except ValueError as error:
            return ToolResult(
                status="error", error_code="bad_arguments", error_message=str(error)
            )
        try:
            report = search(
                query,
                providers=tuple(providers) if providers else PROVIDERS,
                transport=transport,
            )
        except SearchUnavailable as error:
            return ToolResult(
                status="error",
                error_code="search_unavailable",
                error_message=error.message,
                data=error.describe(),
            )
        return ToolResult(status="ok", data=report.as_dict())

    web_search.__name__ = "web_search"
    return web_search


def make_fetch_url(*, transport: Any = None) -> Any:
    """构造 ``fetch_url`` provider。"""

    def fetch_url(
        *,
        caller: CallerContext,
        url: str,
        respect_robots: bool = True,
        save_snapshot: bool = True,
        max_bytes: int = 5_000_000,
        **_: Any,
    ) -> ToolResult:
        if not _search_enabled():
            return _disabled("fetch_url")
        try:
            page = fetch_page(
                url,
                transport=transport,
                respect_robots=respect_robots,
                save_snapshot=save_snapshot,
                max_bytes=max_bytes,
            )
        except FetchError as error:
            return ToolResult(
                status="error",
                error_code=error.reason,
                error_message=error.message,
                data=error.describe(),
            )
        return ToolResult(status="ok", data=page.as_dict())

    fetch_url.__name__ = "fetch_url"
    return fetch_url


def register_search_adapters(registry: Any, *, transport: Any = None) -> None:
    """把搜索工具挂到注册表上（幂等：重复调用只是覆盖同名 provider）。"""
    registry.register("web_search", make_web_search(transport=transport))
    registry.register("fetch_url", make_fetch_url(transport=transport))


__all__ = ["make_fetch_url", "make_web_search", "register_search_adapters"]
