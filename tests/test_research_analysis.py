"""``research.analysis`` 的离线测试：不联网、不花钱。

真实调用由 ``backend/scripts/model_smoke.py`` 和端到端验收跑；这里用
``httpx.MockTransport`` 把"模型返回什么"变成可控输入，专门盯三件事：

1. 提示词里**只有证据**（来源 + 数据点），不夹带外部事实；
2. 模型胡说时（没挂来源、不是 JSON、空结果）必须被拦住或丢弃，不能进报告；
3. 成本与 Token 如实回到 ``AnalysisResult``，失败时不假装成功。
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.capabilities.model import BudgetContext, ModelGateway
from backend.app.capabilities.model.providers import ProviderError
from backend.app.core.config_schema import ModelsConfig
from backend.app.research import analysis as analysis_module
from backend.app.research.analysis import (
    AnalysisError,
    analyze,
    build_messages,
    parse_analysis,
)
from backend.app.research.digest import DataPoint, Digest, SourceRef


def make_digest(*, with_snapshot: bool = False) -> Digest:
    sources = (
        SourceRef(
            source_id="S01",
            title="2024年四季度银行业主要监管指标",
            url="https://example.com/a",
            site_name="国家金融监督管理总局",
            author="",
            published_at="2025-02-21",
            sha256="a" * 64,
            snapshot_path="",
            char_count=1200,
            query="商业银行 净息差",
        ),
        SourceRef(
            source_id="S02",
            title="商业银行收入结构观察",
            url="https://example.com/b",
            site_name="某财经媒体",
            author="",
            published_at="2025-03-02",
            sha256="b" * 64,
            snapshot_path="",
            char_count=900,
            query="商业银行 收入结构",
        ),
    )
    points = (
        DataPoint(
            source_id="S01",
            text="2024年第四季度，商业银行净息差降至1.52%。",
            numbers=("2024", "1.52%"),
        ),
        DataPoint(
            source_id="S02",
            text="利息净收入占营业收入约七成。",
            numbers=(),
        ),
    )
    return Digest(
        title="商业银行是如何赚钱的",
        objective="商业银行是如何赚钱的？",
        sources=sources,
        data_points=points,
        queries=("商业银行 收入结构",),
        generated_at="2026-11-12T00:00:00+00:00",
        generated_by="rules",
        notes=(),
        meta={},
        corroboration=None,
    )


def build_config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "version": 1,
            "updated_at": "2026-11-12",
            "cost": {
                "currency": "CNY",
                "pricing_policy": "reject_unknown",
                "approved_exceptions": [],
                "cache_ttl_hours": 24,
            },
            "providers": {
                "deepseek": {
                    "display_name": "DeepSeek",
                    "base_url_env": "DEEPSEEK_BASE_URL",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "enabled": True,
                }
            },
            "models": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "display_name": "flash",
                    "context_window": 1_000_000,
                    "input_price_per_1k_cny": 0.00213,
                    "output_price_per_1k_cny": 0.00852,
                    "enabled": True,
                }
            ],
        }
    )


def ok_response(text: str, *, prompt: int = 900, completion: int = 260) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": "deepseek-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


GOOD_JSON = json.dumps(
    {
        "summary": "商业银行主要靠存贷利差，息差收窄正在压缩这块收入。",
        "findings": [
            {
                "claim": "净息差降到1.52%，利息收入的量价基础在变薄",
                "kind": "fact",
                "source_ids": ["S01"],
                "numbers": ["1.52%"],
            },
            {
                "claim": "利息净收入仍占营业收入约七成，短期难以被手续费替代",
                "kind": "inference",
                "source_ids": ["S01", "S02"],
                "numbers": [],
            },
        ],
        "data_gaps": ["缺少手续费净收入的同业分位数"],
    },
    ensure_ascii=False,
)


class Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self.response


def build_gateway(recorder: Recorder) -> ModelGateway:
    return ModelGateway(
        models_config=build_config(),
        transport=httpx.MockTransport(recorder),
        environ={"DEEPSEEK_API_KEY": "sk-test-key-0001"},
        retries=0,
    )


# --- 提示词 ---------------------------------------------------------------


def test_prompt_carries_only_the_evidence_we_collected() -> None:
    digest = make_digest()
    system, user = build_messages(digest)
    assert "只能使用用户提供的【证据】" in system["content"]
    body = user["content"]
    assert "商业银行是如何赚钱的？" in body
    assert "[S01] 2024年四季度银行业主要监管指标" in body
    assert "净息差降至1.52%" in body
    assert "[S02] 商业银行收入结构观察" in body
    # 提示词里不得出现"你自己补充事实"这类放行话术
    assert "禁止引入证据之外的事实" in system["content"]


def test_prompt_uses_snapshot_text_when_the_digest_has_no_excerpt(
    tmp_path, monkeypatch
) -> None:
    snapshot = tmp_path / "s01.txt"
    snapshot.write_text("监管口径：商业银行净息差1.52%，较上年下降。", encoding="utf-8")
    digest = make_digest()
    patched = SourceRef(
        source_id="S01",
        title="快照来源",
        url="https://example.com/a",
        site_name="站",
        author="",
        published_at="",
        sha256="a" * 64,
        snapshot_path=str(snapshot),
        char_count=40,
        query="",
    )
    digest = Digest(
        title=digest.title,
        objective=digest.objective,
        sources=(patched,) + digest.sources[1:],
        data_points=digest.data_points,
        queries=digest.queries,
        generated_at=digest.generated_at,
        generated_by=digest.generated_by,
        notes=(),
        meta={},
        corroboration=None,
    )
    _system, user = build_messages(digest)
    assert "监管口径：商业银行净息差1.52%" in user["content"]


# --- 解析 ---------------------------------------------------------------


def test_parse_keeps_findings_that_carry_a_known_source() -> None:
    summary, findings, gaps, dropped = parse_analysis(
        GOOD_JSON, known_sources=["S01", "S02"]
    )
    assert "存贷利差" in summary
    assert len(findings) == 2
    assert findings[0].kind == "fact"
    assert findings[0].source_ids == ("S01",)
    assert findings[0].numbers == ("1.52%",)
    assert gaps == ("缺少手续费净收入的同业分位数",)
    assert dropped == ()


def test_parse_drops_findings_without_a_source() -> None:
    payload = json.dumps(
        {
            "summary": "结论",
            "findings": [
                {"claim": "有来源的结论", "kind": "fact", "source_ids": ["S01"]},
                {"claim": "没来源的结论", "kind": "fact", "source_ids": []},
                {"claim": "来源不存在", "kind": "fact", "source_ids": ["S99"]},
            ],
            "data_gaps": [],
        },
        ensure_ascii=False,
    )
    _summary, findings, _gaps, dropped = parse_analysis(
        payload, known_sources=["S01"]
    )
    assert [item.claim for item in findings] == ["有来源的结论"]
    assert dropped == ("没来源的结论", "来源不存在")


def test_parse_tolerates_code_fences_and_normalises_kind() -> None:
    fenced = "```json\n" + json.dumps(
        {
            "summary": "小结",
            "findings": [
                {"claim": "结论", "kind": "瞎写", "source_ids": ["S01"]}
            ],
            "data_gaps": [],
        },
        ensure_ascii=False,
    ) + "\n```"
    _summary, findings, _gaps, _dropped = parse_analysis(
        fenced, known_sources=["S01"]
    )
    assert findings[0].kind == "inference"


@pytest.mark.parametrize(
    "payload",
    [
        "抱歉，我无法完成。",
        "{不是合法 JSON}",
        json.dumps({"summary": "", "findings": [], "data_gaps": []}),
        json.dumps([1, 2, 3]),
    ],
)
def test_parse_refuses_output_it_cannot_trust(payload: str) -> None:
    with pytest.raises(AnalysisError):
        parse_analysis(payload, known_sources=["S01"])


# --- 调用 ---------------------------------------------------------------


def test_analyze_returns_usage_and_cost_from_the_gateway() -> None:
    recorder = Recorder(ok_response(GOOD_JSON, prompt=900, completion=260))
    result = analyze(
        make_digest(), gateway=build_gateway(recorder), budget=BudgetContext(), max_tokens=2400
    )
    assert result.provider == "deepseek"
    assert result.model == "deepseek-flash"
    assert len(result.findings) == 2
    assert result.prompt_tokens == 900
    assert result.completion_tokens == 260
    # 900/1000*0.00213 + 260/1000*0.00852 = 0.001917 + 0.0022152
    assert round(result.cost_cny, 6) == round(0.001917 + 0.0022152, 6)
    assert result.raw_text == GOOD_JSON
    assert len(recorder.calls) == 1
    body = json.loads(recorder.calls[0].content.decode("utf-8"))
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 2400
    assert "DEEPSEEK_API_KEY" not in recorder.calls[0].headers.get("authorization", "")


def test_analyze_refuses_a_truncated_reply_instead_of_parsing_half_json() -> None:
    """finish_reason=length 说明回答被 max_tokens 截断——半截 JSON 绝不能当结果用。"""
    truncated = httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": GOOD_JSON[:40]},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 900, "completion_tokens": 2400, "total_tokens": 3300},
        },
    )
    gateway = build_gateway(Recorder(truncated))
    with pytest.raises(AnalysisError) as excinfo:
        # 显式给上限，免得断言依赖默认值：默认值会随真实长回答一起调大
        analyze(make_digest(), gateway=gateway, budget=BudgetContext(), max_tokens=2400)
    assert excinfo.value.reason == "truncated"
    assert "max_tokens" in str(excinfo.value)


def test_analyze_refuses_when_no_provider_is_ready(monkeypatch) -> None:
    def fake_status(**_kwargs):
        return {"deepseek": {"status": "missing_api_key"}}

    monkeypatch.setattr(
        "backend.app.capabilities.model.providers.provider_status", fake_status
    )
    with pytest.raises(AnalysisError) as excinfo:
        analyze(make_digest(), gateway=build_gateway(Recorder(ok_response(GOOD_JSON))))
    assert excinfo.value.reason == "no_model"


def test_analyze_reports_gateway_errors_instead_of_faking_a_result() -> None:
    recorder = Recorder(httpx.Response(429, json={"error": {"message": "rate limited"}}))
    gateway = build_gateway(recorder)
    with pytest.raises(AnalysisError) as excinfo:
        analyze(make_digest(), gateway=gateway, budget=BudgetContext())
    assert excinfo.value.reason
    assert excinfo.value.reason != "ok"


def test_analyze_keeps_the_key_out_of_the_result() -> None:
    recorder = Recorder(ok_response(GOOD_JSON))
    result = analyze(
        make_digest(), gateway=build_gateway(recorder), budget=BudgetContext()
    )
    blob = json.dumps(result.as_dict(), ensure_ascii=False)
    assert "sk-test-key-0001" not in blob
    markdown = result.to_markdown(objective="商业银行是如何赚钱的？")
    assert "sk-test-key-0001" not in markdown
    assert "S01" in markdown


def test_to_markdown_lists_gaps_and_dropped_items() -> None:
    payload = json.dumps(
        {
            "summary": "小结",
            "findings": [
                {"claim": "有来源", "kind": "inference", "source_ids": ["S01"]}
            ],
            "data_gaps": ["缺手续费分位数"],
        },
        ensure_ascii=False,
    )
    _summary, findings, gaps, dropped = parse_analysis(payload, known_sources=["S01"])
    result = analysis_module.AnalysisResult(
        summary="小结",
        findings=findings,
        data_gaps=gaps,
        raw_text=payload,
        provider="deepseek",
        model="deepseek-flash",
        dropped=dropped,
    )
    markdown = result.to_markdown(objective="目标")
    assert "证据缺口" in markdown
    assert "缺手续费分位数" in markdown
    assert "[推断] 有来源 —— S01" in markdown


def test_provider_error_is_raised_when_nothing_is_configured(monkeypatch) -> None:
    """``provider_status`` 之外的路径：解析 provider 直接抛 ProviderError 也要变成 no_model。"""

    def fake_status(**_kwargs):
        return {}

    monkeypatch.setattr(
        "backend.app.capabilities.model.providers.provider_status", fake_status
    )
    with pytest.raises(AnalysisError) as excinfo:
        analyze(make_digest())
    assert excinfo.value.reason == "no_model"
    assert issubclass(ProviderError, Exception)

def test_a_retried_analysis_charges_every_attempt_not_just_the_last_one() -> None:
    """撞上限重试的第一次也是真花了钱：分析账单必须累加。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append(int(body["max_tokens"]))
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-cut",
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": GOOD_JSON[:40]},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 900,
                        "completion_tokens": body["max_tokens"],
                        "total_tokens": 900 + body["max_tokens"],
                    },
                },
            )
        return ok_response(GOOD_JSON, prompt=700, completion=200)

    result = analyze(
        make_digest(),
        gateway=build_gateway(handler),
        budget=BudgetContext(),
        max_tokens=3000,
    )
    assert len(calls) == 2  # 第一次撞上限，第二次才成功
    assert result.prompt_tokens == 900 + 700
    assert result.completion_tokens == 3000 + 200
    # 0.9*0.00213 + 3.0*0.00852 + 0.7*0.00213 + 0.2*0.00852
    assert result.cost_cny == pytest.approx(0.030672, abs=1e-6)
