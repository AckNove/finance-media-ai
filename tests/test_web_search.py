"""真实搜索测试：三个 provider 的解析、去重、失败降级与审计。

全部用 ``httpx.MockTransport`` 与内联 HTML，不碰真实网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app.capabilities.search.engine import (
    PROVIDERS,
    SearchHit,
    SearchQuery,
    SearchUnavailable,
    clean_tracking,
    parse_bing,
    parse_so360,
    parse_sogou,
    resolve_engine_redirects,
    search,
)
from backend.app.capabilities.search.tool import make_fetch_url, make_web_search
from backend.app.core.caller import CallerContext

SOGOU_HTML = """
<div class="results">
  <div class="vrwrap">
    <h3><a href="https://example.com/a?utm_source=sogou">央行发布货币政策报告</a></h3>
    <div class="str-text">报告指出净息差收窄</div>
  </div>
  <div class="rb">
    <h3><a href="/link?url=abc123">商业银行盈利结构</a></h3>
    <div class="str_info">手续费收入占比上升</div>
  </div>
</div>
"""

SO360_HTML = """
<ul class="result">
  <li class="res-list">
    <h3><a href="https://example.com/b">净息差与银行估值</a></h3>
    <p class="res-desc">净息差决定银行盈利能力</p>
  </li>
  <li class="res-list ad">
    <h3><a href="https://ads.example.com/x">广告别进来</a></h3>
    <p class="res-desc">这是广告</p>
  </li>
</ul>
"""

BING_HTML = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/c">银行理财规模回升</a></h2>
    <p>理财规模同比回升</p>
  </li>
</ol>
"""


def agent_caller() -> CallerContext:
    return CallerContext.for_executor(
        agent="financial_researcher",
        task_id="task-1",
        attempt_token="tok-1",
        db_scopes={"sources", "evidence"},
    )


# --- 查询对象 -------------------------------------------------------------


def test_query_rejects_empty_keywords():
    with pytest.raises(ValueError):
        SearchQuery(keywords="   ")


@pytest.mark.parametrize(
    "kwargs",
    [{"page": 0}, {"limit": 0}, {"limit": 51}, {"time_range": "last_week"}],
)
def test_query_rejects_bad_arguments(kwargs):
    with pytest.raises(ValueError):
        SearchQuery(keywords="银行", **kwargs)


def test_query_adds_site_prefix_and_splits_terms():
    query = SearchQuery(keywords="净息差 银行", site="pbc.gov.cn", time_range="month")
    assert query.effective_keywords == "site:pbc.gov.cn 净息差 银行"
    assert query.terms == ["净息差", "银行"]


def test_query_does_not_double_site_prefix():
    query = SearchQuery(keywords="site:pbc.gov.cn 净息差", site="pbc.gov.cn")
    assert query.effective_keywords.count("site:") == 1


# --- 解析器 ---------------------------------------------------------------


def test_parse_sogou_reads_blocks_and_absolutises_links():
    items = parse_sogou(SOGOU_HTML)
    assert [item["title"] for item in items] == ["央行发布货币政策报告", "商业银行盈利结构"]
    assert items[0]["url"].startswith("https://example.com/a")
    assert items[1]["url"].startswith("https://www.sogou.com/link?url=")
    assert "净息差收窄" in items[0]["snippet"]


def test_parse_so360_skips_ads():
    items = parse_so360(SO360_HTML)
    assert [item["title"] for item in items] == ["净息差与银行估值"]
    assert "广告" not in json.dumps(items, ensure_ascii=False)


def test_parse_bing_reads_algo_blocks():
    items = parse_bing(BING_HTML)
    assert items[0]["url"] == "https://example.com/c"
    assert "理财规模" in items[0]["snippet"]


def test_parsers_return_empty_on_unrelated_html():
    assert parse_sogou("<html><body>什么也没有</body></html>") == []
    assert parse_so360("<html><body>什么也没有</body></html>") == []
    assert parse_bing("<html><body>什么也没有</body></html>") == []


def test_clean_tracking_drops_only_tracking_params():
    assert clean_tracking("https://a.example/x?utm_source=s&id=7&spm=1") == "https://a.example/x?id=7"
    assert clean_tracking("https://a.example/x") == "https://a.example/x"


# --- 搜索主流程 -----------------------------------------------------------


def make_transport(pages: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    """按 host 返回不同 HTML；未登记的 host 返回 500。"""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        status, body = pages.get(host, (500, "boom"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def test_search_collects_and_dedupes_across_providers(tmp_path: Path):
    duplicated = SOGOU_HTML.replace("https://example.com/a?utm_source=sogou", "https://example.com/b")
    transport = make_transport(
        {
            "www.sogou.com": (200, duplicated),
            "www.so.com": (200, SO360_HTML),
            "cn.bing.com": (200, BING_HTML),
        }
    )
    audit = tmp_path / "search.jsonl"
    report = search(SearchQuery(keywords="银行", limit=5), transport=transport, audit_path=audit)
    urls = [hit.url for hit in report.hits]
    assert urls.count("https://example.com/b") == 1, "同 URL 必须去重"
    assert report.ok is True
    assert report.providers_tried[:2] == ("sogou", "so360")
    assert [hit.rank for hit in report.hits] == list(range(1, len(report.hits) + 1))
    assert report.errors == {}

    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "web_search"
    assert entry["hits"] == len(report.hits)
    assert entry["query"]["keywords"] == "银行"


def test_search_falls_back_when_first_provider_fails(tmp_path: Path):
    transport = make_transport({"www.so.com": (200, SO360_HTML)})
    report = search(
        SearchQuery(keywords="银行"),
        transport=transport,
        audit_path=tmp_path / "a.jsonl",
    )
    assert [hit.provider for hit in report.hits] == ["so360"]
    assert "sogou" in report.errors
    assert report.errors["sogou"].startswith("http_500")


def test_search_raises_when_all_providers_fail(tmp_path: Path):
    transport = make_transport({})
    with pytest.raises(SearchUnavailable) as excinfo:
        search(
            SearchQuery(keywords="银行"),
            transport=transport,
            audit_path=tmp_path / "a.jsonl",
        )
    assert len(excinfo.value.reasons) == len(PROVIDERS)
    assert excinfo.value.describe()["reason"] == "search_unavailable"


def test_search_records_parse_error_without_crashing(tmp_path: Path):
    broken = make_transport({"www.sogou.com": (200, "<html>空</html>"), "www.so.com": (200, SO360_HTML)})
    report = search(
        SearchQuery(keywords="银行"), transport=broken, audit_path=tmp_path / "a.jsonl"
    )
    assert report.errors["sogou"] == "no_results"
    assert report.hits[0].provider == "so360"


# --- 工具适配器 -----------------------------------------------------------


def test_web_search_tool_returns_report(tmp_path: Path):
    transport = make_transport({"www.sogou.com": (200, SOGOU_HTML)})
    tool = make_web_search(transport=transport)
    result = tool(caller=agent_caller(), keywords="净息差", limit=3)
    assert result.ok is True
    assert result.data["hits"][0]["title"] == "央行发布货币政策报告"
    assert result.data["query"]["keywords"] == "净息差"


def test_web_search_tool_maps_failure_to_error():
    tool = make_web_search(transport=make_transport({}))
    result = tool(caller=agent_caller(), keywords="银行")
    assert result.status == "error"
    assert result.error_code == "search_unavailable"
    assert result.ok is False


def test_web_search_tool_rejects_bad_arguments():
    tool = make_web_search(transport=make_transport({}))
    result = tool(caller=agent_caller(), keywords="  ")
    assert result.error_code == "bad_arguments"


def test_fetch_url_tool_maps_unsafe_url_to_error():
    tool = make_fetch_url(transport=make_transport({}))
    result = tool(caller=agent_caller(), url="http://127.0.0.1:8000/secret")
    assert result.status == "error"
    assert result.error_code == "blocked_ip"


# --- 真实目标地址解析 -----------------------------------------------------
# 背景：中文引擎把 href 换成自己的跳转链接（/link?url=<token>），抓这种地址会被
# robots 拒绝，也抓不到正文。真实地址要么在属性里（360 的 data-mdurl），
# 要么得读一次跳转的 Location。


SO360_WITH_MDURL = """
<ul class="result">
  <li class="res-list">
    <h3><a href="https://www.so.com/link?url=tok" data-mdurl="https://www.toutiao.com/article/762411835509571638">净息差与银行估值</a></h3>
    <p class="res-desc">净息差决定银行盈利能力</p>
  </li>
</ul>
"""


def test_parse_so360_prefers_data_mdurl_over_redirect_href():
    items = parse_so360(SO360_WITH_MDURL)
    assert items[0]["url"] == "https://www.toutiao.com/article/762411835509571638"


def test_parse_sogou_uses_mu_attribute_when_present():
    html = """
    <div class="vrwrap">
      <h3><a href="/link?url=x" mu="https://www.pbc.gov.cn/zhengce/1.html">货币政策报告</a></h3>
      <div class="str-text">报告正文</div>
    </div>
    """
    items = parse_sogou(html)
    assert items[0]["url"] == "https://www.pbc.gov.cn/zhengce/1.html"


def _resolver(ip: str):
    def resolve(host: str) -> list[str]:
        return [ip]

    return resolve


def _redirect_transport(location: str, *, search_html: str = SOGOU_HTML) -> httpx.MockTransport:
    """搜狗结果页正常，``/link`` 跳转到 ``location``。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/link"):
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, text=search_html, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def test_resolve_engine_redirects_follows_location_and_marks_origin():
    transport = _redirect_transport("https://www.pbc.gov.cn/zhengce/1.html")
    with httpx.Client(transport=transport) as http:
        hits = resolve_engine_redirects(
            [
                SearchHit(
                    title="货币政策报告",
                    url="https://www.sogou.com/link?url=abc123",
                    snippet="",
                    provider="sogou",
                    rank=1,
                )
            ],
            http=http,
            resolver=_resolver("93.184.216.34"),
        )
    assert hits[0].url == "https://www.pbc.gov.cn/zhengce/1.html"
    assert hits[0].resolved_from == "https://www.sogou.com/link?url=abc123"
    assert hits[0].as_dict()["resolved_from"].startswith("https://www.sogou.com/link")


def test_resolve_engine_redirects_keeps_original_when_target_is_private():
    transport = _redirect_transport("http://127.0.0.1:8000/admin")
    with httpx.Client(transport=transport) as http:
        hits = resolve_engine_redirects(
            [
                SearchHit(
                    title="内网地址",
                    url="https://www.sogou.com/link?url=abc123",
                    snippet="",
                    provider="sogou",
                    rank=1,
                )
            ],
            http=http,
            resolver=_resolver("93.184.216.34"),
        )
    assert hits[0].url == "https://www.sogou.com/link?url=abc123", "宁可用跳转链接，也不让引擎把我们指向内网"
    assert hits[0].resolved_from == ""


def test_resolve_engine_redirects_keeps_original_when_location_missing():
    transport = _redirect_transport("")  # 302 但没有 Location
    with httpx.Client(transport=transport) as http:
        hits = resolve_engine_redirects(
            [
                SearchHit(
                    title="没有 Location",
                    url="https://www.sogou.com/link?url=abc123",
                    snippet="",
                    provider="sogou",
                    rank=1,
                )
            ],
            http=http,
            resolver=_resolver("93.184.216.34"),
        )
    assert hits[0].url == "https://www.sogou.com/link?url=abc123"


def test_search_skips_results_pointing_at_the_engine_itself(tmp_path: Path):
    """360 会把自家 AI 页混进结果（ai.so.com），那不是可用来源。"""
    html = """
    <ul>
      <li class="res-list">
        <h3><a href="https://ai.so.com/search?q=x">360 AI 搜索</a></h3>
        <p class="res-desc">引擎自己的页面</p>
      </li>
      <li class="res-list">
        <h3><a href="https://www.toutiao.com/article/1" data-mdurl="https://www.toutiao.com/article/1">真正的文章</a></h3>
        <p class="res-desc">正文在这里</p>
      </li>
    </ul>
    """
    transport = make_transport({"www.so.com": (200, html)})
    report = search(
        SearchQuery(keywords="净息差", limit=5),
        transport=transport,
        audit_path=tmp_path / "s.jsonl",
    )
    assert [hit.url for hit in report.hits] == ["https://www.toutiao.com/article/1"]

# --- features.search 总闸 ---------------------------------------------------


def test_search_tools_refuse_when_the_search_flag_is_off(monkeypatch):
    """关掉 features.search 之后，工具层必须直接拒绝，且**一个请求都不发**。"""

    from backend.app.core import config as config_module
    from backend.app.core.config_schema import FeaturesSettings

    calls = {"count": 0}

    def handler(request):  # pragma: no cover - 被调用就说明闸门没生效
        calls["count"] += 1
        return httpx.Response(200, text=SOGOU_HTML)

    monkeypatch.setattr(
        config_module,
        "get_features_settings",
        lambda: FeaturesSettings(model=True, search=False, execution=True),
    )

    search_tool = make_web_search(transport=httpx.MockTransport(handler))
    fetch_tool = make_fetch_url(transport=httpx.MockTransport(handler))

    for result in (
        search_tool(caller=agent_caller(), keywords="净息差"),
        fetch_tool(caller=agent_caller(), url="https://www.pbc.gov.cn/zhengcehuobisi/1.html"),
    ):
        assert result.status == "error"
        assert result.error_code == "feature_disabled"
        assert result.data["enabled"] is False

    assert calls["count"] == 0, "闸门关着还发请求，说明这个开关是假的"


def test_search_tools_work_when_the_flag_is_on(monkeypatch):
    from backend.app.core import config as config_module
    from backend.app.core.config_schema import FeaturesSettings

    monkeypatch.setattr(
        config_module,
        "get_features_settings",
        lambda: FeaturesSettings(model=True, search=True, execution=True),
    )
    tool = make_web_search(transport=make_transport({"www.sogou.com": (200, SOGOU_HTML)}))
    result = tool(caller=agent_caller(), keywords="净息差", limit=3)
    assert result.ok is True
