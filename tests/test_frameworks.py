"""研究骨架（E 模块）的离线测试：只验证结构与判定，不联网、不花钱。"""

from __future__ import annotations

import pytest

from backend.app.research.frameworks import (
    CHAIN,
    COMPANY,
    INDUSTRY,
    FRAMEWORKS,
    FrameworkKind,
    detect_kind,
    framework_for,
    normalize_section,
    sections_of,
)


def test_every_kind_has_a_framework_with_unique_sections():
    assert set(FRAMEWORKS) == {FrameworkKind.INDUSTRY, FrameworkKind.CHAIN, FrameworkKind.COMPANY}
    for kind, framework in FRAMEWORKS.items():
        assert framework.kind == kind
        keys = framework.keys()
        assert keys, f"{kind} 没有分节"
        assert len(keys) == len(set(keys)), f"{kind} 的 section key 有重复"
        for section in framework.sections:
            assert section.title.strip()
            assert section.question.strip().endswith(("？", "?")) or section.question.strip()
            assert set(section.kinds) <= {"fact", "inference", "hypothesis"}


def test_industry_framework_covers_the_required_dimensions():
    keys = INDUSTRY.keys()
    for key in (
        "definition",
        "stage",
        "market_size",
        "structure",
        "supply_demand",
        "competition",
        "drivers",
        "cycle",
        "policy",
        "risks",
    ):
        assert key in keys


def test_chain_framework_covers_upstream_to_opportunities():
    keys = CHAIN.keys()
    for key in ("upstream", "midstream", "downstream", "cost_structure", "value_split", "bottlenecks"):
        assert key in keys


def test_company_framework_covers_financial_and_moat_sections():
    keys = COMPANY.keys()
    for key in ("profile", "model", "moat", "financials", "cashflow", "peers", "risks", "updates"):
        assert key in keys


def test_market_size_and_financials_only_accept_facts():
    assert INDUSTRY.section("market_size").kinds == ("fact",)
    for key in ("financials", "profitability", "cashflow", "balance"):
        assert COMPANY.section(key).kinds == ("fact",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("商业银行是如何赚钱的", FrameworkKind.INDUSTRY),
        ("光伏组件价格战为什么会吃掉龙头企业的现金流", FrameworkKind.INDUSTRY),
        ("光伏产业链上游硅料为什么跌", FrameworkKind.CHAIN),
        ("隆基绿能2025年财报怎么看", FrameworkKind.COMPANY),
        ("", FrameworkKind.INDUSTRY),
    ],
)
def test_detect_kind_weighs_mixed_questions(text, expected):
    assert detect_kind(text) is expected


def test_framework_for_accepts_kind_text_and_none():
    assert framework_for("industry") is INDUSTRY
    assert framework_for(FrameworkKind.COMPANY) is COMPANY
    assert framework_for("隆基绿能财报") is COMPANY
    assert framework_for(None) is INDUSTRY


def test_normalize_section_only_accepts_known_keys():
    assert normalize_section("upstream", CHAIN) == "upstream"
    assert normalize_section("  Market_Size ", INDUSTRY) == "market_size"
    assert normalize_section("市场规模", INDUSTRY) == ""
    assert normalize_section("", INDUSTRY) == ""
    assert normalize_section("upstream", None) == ""


def test_prompt_block_lists_every_section_and_its_rule():
    block = CHAIN.prompt_block()
    for section in CHAIN.sections:
        assert section.key in block
        assert section.title in block
    assert "section" in block


def test_checklist_reports_empty_and_inference_only_sections():
    findings = [
        {"claim": "硅料扩产", "section": "upstream", "kind": "fact", "source_ids": ["S01"]},
        {
            "claim": "硅片降价传导",
            "section": "midstream",
            "kind": "inference",
            "source_ids": ["S01"],
        },
        {"claim": "没归节的结论", "kind": "fact", "source_ids": ["S02"]},
    ]
    checklist = CHAIN.checklist(findings)
    assert checklist["title"] == CHAIN.title
    assert "downstream" in checklist["empty_sections"]
    assert "upstream" not in checklist["empty_sections"]
    assert checklist["inference_only_sections"] == ["midstream"]
    assert checklist["unsectioned"] == ["没归节的结论"]
    assert checklist["coverage"] == pytest.approx(2 / len(CHAIN.sections), abs=1e-3)


def test_checklist_does_not_force_unsectioned_claims_into_a_section():
    checklist = INDUSTRY.checklist([{"claim": "孤零零的一句", "kind": "fact", "source_ids": ["S01"]}])
    assert checklist["unsectioned"] == ["孤零零的一句"]
    assert len(checklist["empty_sections"]) == len(INDUSTRY.sections)
    assert checklist["coverage"] == 0.0


def test_sections_of_returns_framework_order_with_items():
    findings = [
        {"section": "risks", "claim": "a"},
        {"section": "stage", "claim": "b"},
    ]
    grouped = sections_of(INDUSTRY, findings)
    assert [section.key for section, _ in grouped] == list(INDUSTRY.keys())
    by_key = {section.key: items for section, items in grouped}
    assert [item["claim"] for item in by_key["stage"]] == ["b"]
    assert [item["claim"] for item in by_key["risks"]] == ["a"]
    assert by_key["definition"] == []


def test_as_dict_is_json_ready():
    payload = INDUSTRY.as_dict()
    assert payload["kind"] == "industry"
    assert len(payload["sections"]) == len(INDUSTRY.sections)
    assert all(set(item) >= {"key", "title", "question", "kinds"} for item in payload["sections"])
