"""配置与安全基线：YAML 配置可解析、关键约束存在、密钥不入库。"""

from __future__ import annotations

from pathlib import Path

import yaml

from backend.app.core.config import (
    PROJECT_ROOT,
    get_app_config,
    get_brand_config,
    get_content_rules,
    get_research_sources,
    get_settings,
    load_yaml_config,
)

REQUIRED_CONFIG_FILES = (
    "settings.yaml",
    "brand.yaml",
    "content_rules.yaml",
    "research_sources.yaml",
)

SECRET_MARKERS = ("sk-", "Bearer ", "api_key:", "password:", "secret:")


def test_all_required_config_files_exist_and_parse():
    config_dir = PROJECT_ROOT / "config"
    for filename in REQUIRED_CONFIG_FILES:
        path = config_dir / filename
        assert path.is_file(), f"缺少配置文件: {path}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data, f"{filename} 解析结果不是非空字典"


def test_settings_video_spec_is_vertical_1080x1920_30fps():
    settings = get_app_config()
    video = settings["video"]
    assert video["width"] == 1080
    assert video["height"] == 1920
    assert video["fps"] == 30
    assert video["aspect_ratio"] == "9:16"
    # 第一阶段不启用渲染
    assert video["rendering_enabled"] is False


def test_integrations_are_disabled_by_default():
    integrations = get_app_config()["integrations"]
    for key in ("llm", "web_search", "vector_store"):
        assert integrations[key]["enabled"] is False, key


def test_brand_policy_forbids_auto_publish_and_requires_analogy_boundaries():
    brand = get_brand_config()
    assert brand["platforms"]["auto_publish"] is False
    policy = brand["analogy_policy"]
    assert policy["require_scope_statement"] is True
    assert policy["require_limitations_statement"] is True
    assert 0 < policy["max_analogy_share_of_script"] < 1
    assert brand["tone"]["football_element"]


def test_content_rules_forbid_investment_advice():
    rules = get_content_rules()
    compliance = rules["compliance"]
    assert compliance["allow_investment_advice"] is False
    assert compliance["require_disclaimer"] is True
    assert rules["evidence"]["require_evidence_for_facts"] is True


def test_research_sources_define_credibility_and_independence_rules():
    sources = get_research_sources()
    assert set(sources["credibility_scale"]) == {"high", "medium", "low", "unknown"}
    assert sources["primary"] and sources["secondary"] and sources["tertiary"]
    assert sources["forbidden"]

    rules = "\n".join(sources["rules"])
    assert "两个独立来源" in rules
    assert "accessed_at" in rules
    assert "jurisdiction" in rules


def test_runtime_settings_points_at_project_directories():
    settings = get_settings()
    assert settings.app_name == "finance-media-ai"
    assert settings.config_dir == PROJECT_ROOT / "config"
    assert settings.agents_dir == PROJECT_ROOT / "agents"
    assert settings.video_engine_dir == PROJECT_ROOT / "video_engine"
    assert settings.resolved_database_url().startswith("sqlite:")
    assert Path(settings.sqlite_file_path()).is_absolute()


def test_env_example_lists_variables_without_real_secrets():
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DATABASE_URL" in env_example
    for line in env_example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, value = stripped.split("=", 1)
        value = value.strip()
        assert not value.startswith("sk-"), f".env.example 出现疑似真实密钥: {stripped}"
        assert len(value) < 120, f".env.example 占位值异常长: {stripped}"


def test_gitignore_excludes_env_and_local_artifacts():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", ".venv", "__pycache__", "*.db"):
        assert pattern in gitignore, f".gitignore 缺少: {pattern}"
    assert ".env.example" in gitignore  # 允许提交示例


def test_video_templates_match_declared_spec():
    templates_dir = PROJECT_ROOT / "video_engine" / "templates"
    template_files = sorted(templates_dir.glob("*.yaml"))
    assert {p.stem for p in template_files} == {
        "football_explainer",
        "financial_mechanism",
        "industry_research",
        "case_documentary",
    }

    for path in template_files:
        data = load_yaml_config(f"../video_engine/templates/{path.name}")
        assert data["id"] == path.stem
        assert data["canvas"]["width"] == 1080
        assert data["canvas"]["height"] == 1920
        assert data["canvas"]["fps"] == 30
        durations = data["target_duration_seconds"]
        assert durations["min"] < durations["default"] < durations["max"]
        shares = [segment["share"] for segment in data["structure"]]
        assert abs(sum(shares) - 1.0) < 0.01, f"{path.stem} 段落占比之和不为 1"
        for segment in data["structure"]:
            assert segment["requirement"], f"{path.stem}/{segment['key']} 缺少要求"


def test_qc_checks_cover_twelve_gates():
    qc = load_yaml_config("../video_engine/quality_control/qc_checks.yaml")
    ids = [check["id"] for check in qc["checks"]]
    assert len(ids) == len(set(ids)) == 12
    for required in ("canvas", "duration", "disclaimer_present", "investment_advice"):
        assert required in ids
    assert qc["spec"]["canvas"]["width"] == 1080
