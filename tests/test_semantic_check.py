"""语义核查（P0）的离线测试：不联网、不花钱。

用 ``httpx.MockTransport`` 把"模型说了什么"变成可控输入，专门盯四件事：

1. 提示词只带我们收集到的证据，不许它引入外部知识；
2. 模型说"被支持"但给不出能在证据里逐字找到的片段 → **降级**，不能算支持；
3. 各种不合规输出（未知判定、缺条目、乱码）都要落到"未核实"这一档，不能猜；
4. 模型判定与人工复核是两条独立状态线，任何代码都不得把前者写成后者。
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.capabilities.model import ModelGateway
from backend.app.core.config_schema import ModelsConfig
from backend.app.research.semantic import (
    ISSUE_CODES,
    VERDICTS,
    EvidenceUnit,
    SemanticError,
    build_messages,
    check_findings,
    parse_checks,
)

EVIDENCE = (
    EvidenceUnit(
        evidence_id=11,
        source_id="S01",
        text="2024年第四季度，商业银行净息差降至1.52%，较上年同期下降17个基点。",
    ),
    EvidenceUnit(
        evidence_id=12,
        source_id="S02",
        text="2024年，商业银行利息净收入占营业收入的比重约为七成。",
    ),
)

FINDINGS = (
    {
        "text": "净息差已降至1.52%，利息收入的量价基础在变薄",
        "kind": "fact",
        "source_ids": ["S01"],
        "numbers": ["1.52%"],
    },
    {
        "text": "利息净收入仍占营业收入约七成",
        "kind": "fact",
        "source_ids": ["S02"],
        "numbers": [],
    },
)


def build_config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "version": 1,
            "updated_at": "2026-11-13",
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


def ok_response(text: str, *, prompt: int = 1200, completion: int = 400) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-verify",
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


def build_gateway(handler) -> ModelGateway:
    return ModelGateway(
        models_config=build_config(),
        transport=httpx.MockTransport(handler),
        environ={"DEEPSEEK_API_KEY": "sk-test-key-0002"},
        retries=0,
    )


def payload(*checks: dict) -> str:
    return json.dumps({"checks": list(checks)}, ensure_ascii=False)


# --- 提示词 ---------------------------------------------------------------


def test_prompt_carries_only_our_evidence_and_the_eight_error_codes() -> None:
    system, user = build_messages(list(FINDINGS), list(EVIDENCE))
    text = system["content"]
    assert "只能使用" in text
    assert "逐字" in text
    for code in ISSUE_CODES:
        assert code in text
    # 证据编号与原文都要在提示词里，模型才有东西可引
    assert '"evidence_id": 11' in user["content"] and '"evidence_id": 12' in user["content"]
    assert "净息差降至1.52%" in user["content"]


# --- 解析：支持与反对都要有逐字片段 ---------------------------------------


def test_supported_verdict_keeps_verbatim_quotes_and_evidence_ids() -> None:
    text = payload(
        {
            "index": 0,
            "verdict": "supported",
            "reason": "原文直接给出净息差与降幅",
            "supporting": ["净息差降至1.52%"],
            "contradicting": [],
            "missing": [],
            "issues": [],
        },
        {
            "index": 1,
            "verdict": "partially_supported",
            "reason": "原文只说约七成，没有精确值",
            "supporting": ["利息净收入占营业收入的比重约为七成"],
            "contradicting": [],
            "missing": ["缺少精确金额"],
            "issues": [{"code": "partial_support", "note": "只有约数"}],
        },
    )
    checks = parse_checks(text, findings=list(FINDINGS), evidence=list(EVIDENCE))
    assert [item.verdict for item in checks] == ["supported", "partially_supported"]
    assert checks[0].evidence_ids == (11,)
    assert checks[0].supporting == ("净息差降至1.52%",)
    assert checks[1].evidence_ids == (12,)
    assert checks[1].issues[0].code == "partial_support"
    assert checks[1].missing == ("缺少精确金额",)


def test_supported_without_a_verifiable_quote_is_downgraded() -> None:
    text = payload(
        {
            "index": 0,
            "verdict": "supported",
            "reason": "我认为没问题",
            "supporting": ["净息差已经崩了"],  # 证据里没有这句话
            "contradicting": [],
            "missing": [],
            "issues": [],
        }
    )
    checks = parse_checks(text, findings=[FINDINGS[0]], evidence=list(EVIDENCE))
    assert checks[0].verdict == "insufficient_evidence"
    assert "downgraded:no_verifiable_supporting_quote" in checks[0].notes
    assert "supporting_quote_not_found" in checks[0].notes
    # 引不出来的"支持"不许留在记录里冒充证据
    assert checks[0].supporting == ()


def test_contradicted_without_a_verifiable_quote_falls_back_to_unverifiable() -> None:
    text = payload(
        {
            "index": 0,
            "verdict": "contradicted",
            "reason": "我记得不是这个数",
            "supporting": [],
            "contradicting": ["净息差其实是2.5%"],
            "missing": [],
            "issues": [],
        }
    )
    checks = parse_checks(text, findings=[FINDINGS[0]], evidence=list(EVIDENCE))
    assert checks[0].verdict == "unverifiable"
    assert "downgraded:no_verifiable_contradicting_quote" in checks[0].notes


def test_a_quote_from_a_source_the_finding_never_cited_does_not_count() -> None:
    """结论只挂了 S01，模型却拿 S02 的原文当支持——不算（跨来源张冠李戴）。"""
    text = payload(
        {
            "index": 0,
            "verdict": "supported",
            "reason": "",
            "supporting": ["利息净收入占营业收入的比重约为七成"],
            "contradicting": [],
            "missing": [],
            "issues": [],
        }
    )
    checks = parse_checks(text, findings=[FINDINGS[0]], evidence=list(EVIDENCE))
    assert checks[0].verdict == "insufficient_evidence"


def test_unknown_verdict_and_missing_entries_become_unverifiable() -> None:
    text = payload(
        {"index": 0, "verdict": "看起来对", "supporting": [], "issues": [{"code": "胡编的码"}]}
    )
    checks = parse_checks(text, findings=list(FINDINGS), evidence=list(EVIDENCE))
    assert checks[0].verdict == "unverifiable"
    assert any(note.startswith("unknown_verdict:") for note in checks[0].notes)
    assert any(note.startswith("unknown_issue:") for note in checks[0].notes)
    # 第 2 条模型压根没提 → 也只能是"无法判断"
    assert checks[1].verdict == "unverifiable"
    assert "missing_from_reply" in checks[1].notes


def test_every_verdict_stays_inside_the_allowed_vocabulary() -> None:
    checks = parse_checks(
        payload({"index": 0, "verdict": "SUPPORTED", "supporting": ["净息差降至1.52%"]}),
        findings=[FINDINGS[0]],
        evidence=list(EVIDENCE),
    )
    assert checks[0].verdict in VERDICTS
    assert checks[0].verdict == "supported"  # 大小写不该影响判定


# --- 调用：成本、截断、无模型 ---------------------------------------------


def test_check_findings_reports_tokens_cost_and_never_confirms_by_human() -> None:
    gateway = build_gateway(
        lambda request: ok_response(
            payload({"index": 0, "verdict": "supported", "supporting": ["净息差降至1.52%"]})
        )
    )
    report = check_findings(list(FINDINGS[:1]), list(EVIDENCE), gateway=gateway)
    assert report.model_used is True
    assert report.provider == "deepseek" and report.model == "deepseek-flash"
    assert report.prompt_tokens == 1200 and report.completion_tokens == 400
    assert report.cost_cny == pytest.approx(0.005964, abs=1e-9)  # 1.2*0.00213 + 0.4*0.00852
    assert report.model_call_id is not None
    check = report.checks[0]
    assert check.confirmed_by_human is False
    assert check.human_review_status == "pending"
    counts = report.verdict_counts()
    assert counts["supported"] == 1
    assert counts["contradicted"] == 0  # 五个档位都在，方便前端直接画分布
    markdown = report.to_markdown(objective="商业银行是如何赚钱的")
    assert "人工" in markdown and "supported" in markdown


def test_truncated_reply_is_refused_instead_of_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-cut",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{\"checks\": [{\"ind"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": body["max_tokens"],
                    "total_tokens": 1200 + body["max_tokens"],
                },
            },
        )

    gateway = build_gateway(handler)
    with pytest.raises(SemanticError) as error:
        check_findings(list(FINDINGS), list(EVIDENCE), gateway=gateway)
    assert error.value.reason == "truncated"
    assert "max_tokens" in str(error.value)


def test_no_model_raises_instead_of_returning_a_fake_verdict() -> None:
    disabled = build_config().model_copy(
        update={
            "models": [
                item.model_copy(update={"enabled": False}) for item in build_config().models
            ]
        }
    )
    gateway = ModelGateway(
        models_config=disabled,
        transport=httpx.MockTransport(lambda request: ok_response(payload())),
        environ={"DEEPSEEK_API_KEY": "sk-test-key-0002"},
        retries=0,
    )
    with pytest.raises(SemanticError) as error:
        check_findings(list(FINDINGS), list(EVIDENCE), gateway=gateway)
    assert error.value.reason == "no_model"


def test_empty_evidence_marks_every_finding_insufficient_without_calling_the_model() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return ok_response(payload())

    report = check_findings(list(FINDINGS), [], gateway=build_gateway(handler))
    assert called == []
    assert report.model_used is False
    assert {item.verdict for item in report.checks} == {"insufficient_evidence"}
    assert all(item.human_review_status == "pending" for item in report.checks)

def test_evidence_of_the_claim_itself_is_kept_when_the_pool_is_capped() -> None:
    """证据超过上限时，结论自己挂的来源必须优先留下。

    真实场景：一次研究有三百多条证据，按 id 顺序切前 30 条时，某条结论引用的 S16
    根本没进候选，模型只能说"给定证据里没有 S16 原文"——报告因此被大面积判成证据不足。
    """
    from backend.app.research.semantic import EvidenceUnit, _select_evidence

    pool = [EvidenceUnit(evidence_id=i, source_id=f"S{i:02d}", text=f"原文 {i}") for i in range(1, 61)]
    findings = [{"text": "结论甲", "source_ids": ["S55", "S56"]}]
    picked = _select_evidence(findings, pool, limit=10)

    labels = {item.source_id for item in picked}
    assert len(picked) == 10
    assert "S55" in labels and "S56" in labels
    # 剩下的名额按顺序补，不改变原有相对顺序
    assert [item.evidence_id for item in picked[:2]] == [55, 56]


