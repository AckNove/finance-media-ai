"""业务战略资产：品牌、内容分类、商业模式与路线图文档的一致性校验。

这些断言保护的是"正式战略 v2.0"的落地结果：
品牌名、六大合集、标签规范、合规红线与文档存在性。
它们不改变任何运行时行为——本阶段只做文档与配置。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.config import (
    PROJECT_ROOT,
    get_brand_config,
    get_content_rules,
    load_yaml_config,
)
from backend.app.services.agent_registry import load_roster

EXPECTED_COLLECTIONS = [
    "financial_knowledge",
    "market_briefing",
    "industry_research",
    "company_teardown",
    "football_finance",
    "business_cases",
]

EXPECTED_FORMS = [
    "short_news",
    "knowledge_video",
    "documentary",
    "article",
    "research_report",
    "series",
    "member_research",
    "course",
]

STRATEGY_DOCS = [
    "docs/brand_strategy.md",
    "docs/content_strategy.md",
    "docs/business_model.md",
    "docs/roadmap.md",
]


def get_taxonomy() -> dict:
    return load_yaml_config("content_taxonomy.yaml")


# ------------------------------------------------------------
# 品牌
# ------------------------------------------------------------


def test_brand_uses_official_ip_and_content_brand_names():
    brand = get_brand_config()["brand"]
    assert brand["ip_name"] == "电力之王AK"
    assert brand["brand_name"] == "AK带你拆解世界"
    assert brand["display_name"] == "AK带你拆解世界"
    assert "拆解世界运行的逻辑" in brand["slogan"]
    assert len(brand["personality"]) == 5
    # "电力"只是昵称，不得被写成行业定位
    assert "ip_name_note" in get_brand_config()


def test_brand_declares_business_and_disclosure_policy():
    brand = get_brand_config()
    business = brand["business"]
    assert business["disclosure_required"] is True
    assert set(business["revenue_lines"]) == {
        "free_content",
        "membership",
        "courses",
        "brand_collaboration",
    }
    assert "荐股" in business["paid_content_red_line"]
    # 选择性真人出镜是正式策略
    assert brand["on_camera"]["policy"] == "选择性真人出镜"


# ------------------------------------------------------------
# 内容分类
# ------------------------------------------------------------


def test_taxonomy_defines_exactly_the_six_collections():
    taxonomy = get_taxonomy()
    ids = [collection["id"] for collection in taxonomy["collections"]]
    assert ids == EXPECTED_COLLECTIONS
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("collection_id", EXPECTED_COLLECTIONS)
def test_each_collection_is_fully_specified(collection_id: str):
    taxonomy = get_taxonomy()
    collection = next(c for c in taxonomy["collections"] if c["id"] == collection_id)

    for key in ("name", "mission", "scope", "boundary", "typical", "risk"):
        assert collection[key], f"{collection_id} 缺少 {key}"

    primary = collection["agents"]["primary"]
    assert primary in {spec.key for spec in load_roster()}, f"{collection_id} 的主责 Agent 不在名册里"

    for template in collection["templates"]:
        path = PROJECT_ROOT / "video_engine" / "templates" / f"{template}.yaml"
        assert path.is_file(), f"{collection_id} 引用了不存在的模板 {template}"

    form_ids = {form["id"] for form in taxonomy["forms"]}
    for form in collection["forms"]:
        assert form in form_ids, f"{collection_id} 引用了未定义的内容形式 {form}"


def test_taxonomy_declares_forms_tag_dimensions_and_multilabel_gap():
    taxonomy = get_taxonomy()

    forms = taxonomy["forms"]
    assert [form["id"] for form in forms] == EXPECTED_FORMS
    durations = {form["id"]: form["duration_seconds"] for form in forms}
    assert durations["short_news"] == {"min": 60, "max": 120, "default": 90}
    assert durations["knowledge_video"] == {"min": 120, "max": 300, "default": 180}
    assert durations["documentary"] == {"min": 300, "max": 900, "default": 480}

    multi_label = taxonomy["multi_label"]
    assert multi_label["required"] is True
    assert len(multi_label["tag_dimensions"]) == 6
    # 必须诚实标注"数据库当前不支持多合集"
    assert "column_id" in multi_label["storage_today"]
    assert multi_label["gap"]
    assert "topic_collections" in " ".join(multi_label["planned"]["schema"])


def test_audience_levels_map_to_database_enum_values():
    taxonomy = get_taxonomy()
    mapping = {level["id"]: level["db_value"] for level in taxonomy["audience_levels"]}
    assert mapping == {
        "mass": "beginner",
        "learner": "intermediate",
        "researcher": "advanced",
    }


def test_report_to_content_chain_references_real_database_fields():
    chain = get_taxonomy()["report_to_content"]
    assert chain["rule"]
    joined = " ".join(chain["db_fields"])
    for field in (
        "research_reports.topic_id",
        "scripts.topic_id",
        "scripts.research_report_id",
        "publications.script_id",
        "publications.script_version",
    ):
        assert field in joined, f"关联链路缺少数据库字段 {field}"


# ------------------------------------------------------------
# 合规与风格
# ------------------------------------------------------------


def test_compliance_rules_forbid_paid_stock_advice_and_require_disclosure():
    rules = get_content_rules()

    forbidden = " ".join(rules["compliance"]["forbidden_activity"])
    assert "收费荐股" in forbidden
    assert "个性化证券投资咨询" in forbidden

    triggers = rules["compliance"]["human_review_triggers"]
    for keyword in ("推荐买入", "稳赚", "目标价", "满仓"):
        assert keyword in triggers

    collaboration = rules["collaboration"]
    assert collaboration["require_disclosure"] is True
    assert "{品牌}" in collaboration["disclosure_text_template"]

    assert rules["review"]["compliance_checklist"]
    assert rules["writing"]["duration_tiers"]["short_news"] == {"min": 60, "max": 120}
    assert rules["style"]["football_analogy_rule"]


# ------------------------------------------------------------
# 名册与文档
# ------------------------------------------------------------


def test_roster_collection_bindings_match_taxonomy():
    taxonomy = get_taxonomy()
    valid = {collection["id"] for collection in taxonomy["collections"]}
    roster_path = PROJECT_ROOT / "agents" / "roster.yaml"

    import yaml

    raw = yaml.safe_load(roster_path.read_text(encoding="utf-8"))
    for entry in raw["agents"]:
        declared = entry.get("collections")
        assert declared, f"{entry['key']} 缺少 collections 声明"
        if declared == "all":
            continue
        for collection_id in declared:
            assert collection_id in valid, f"{entry['key']} 引用了未知合集 {collection_id}"


@pytest.mark.parametrize("relative_path", STRATEGY_DOCS)
def test_strategy_documents_exist_and_are_substantive(relative_path: str):
    path = PROJECT_ROOT / relative_path
    assert path.is_file(), f"缺少战略文档: {relative_path}"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 1500, f"{relative_path} 内容过短，疑似占位"


def test_strategy_docs_cross_reference_config_sources():
    brand_doc = (PROJECT_ROOT / "docs" / "brand_strategy.md").read_text(encoding="utf-8")
    content_doc = (PROJECT_ROOT / "docs" / "content_strategy.md").read_text(encoding="utf-8")
    business_doc = (PROJECT_ROOT / "docs" / "business_model.md").read_text(encoding="utf-8")

    assert "config/brand.yaml" in brand_doc
    assert "AK带你拆解世界" in brand_doc
    assert "config/content_taxonomy.yaml" in content_doc
    for collection_name in ("金融知识", "财经时事", "产业链研究", "公司商业解剖", "金融与商业案例"):
        assert collection_name in content_doc
    assert "收费荐股" in business_doc


def test_old_brand_wording_is_gone_from_brand_config_and_readme():
    stale = ("金融知识研究台", "用足球讲金融")
    targets = [
        PROJECT_ROOT / "config" / "brand.yaml",
        PROJECT_ROOT / "README.md",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, f"{path.name} 仍残留旧品牌表述: {phrase}"
