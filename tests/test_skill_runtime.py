"""Skill files become loadable, pinned task instructions with a separate tool gate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app.core.caller import CallerContext
from backend.app.core.job_paths import ApprovedSources
from backend.app.skills.registry import SkillRegistry, SkillRegistryError


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _caller(agent: str) -> CallerContext:
    return CallerContext.for_executor(
        agent=agent, task_id="task-1", attempt_token="attempt-1",
        db_scopes={"source_extractions"},
    )


def test_registry_discovers_pinned_local_and_vendor_skills(tmp_path: Path) -> None:
    registry = SkillRegistry(SKILLS_ROOT, audit_path=tmp_path / "skill_events.jsonl")

    assert {skill.name for skill in registry.skills} == {
        "accessible-explainer", "investigative-storytelling", "ak-signature",
        "pdf", "xlsx",
    }
    loaded = registry.load_for(_caller("financial_researcher"), "research.extract")
    assert {skill.name for skill in loaded} == {"pdf", "xlsx"}
    assert all(skill.instructions.startswith("#") for skill in loaded)
    assert "research.extract" in (tmp_path / "skill_events.jsonl").read_text(encoding="utf-8")


def test_registry_rejects_modified_skill_bytes(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "ak" / "example"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill\n---\n# Example\n", encoding="utf-8"
    )
    (root / "registry.yaml").write_text(
        "version: 1\nskills:\n  - name: example\n    path: ak/example/SKILL.md\n"
        "    sha256: " + "0" * 64 + "\n    version: '1'\n    source: local\n"
        "    license: Proprietary\n    agents: [scriptwriter]\n"
        "    task_types: [content.script]\n    tools: []\n    dependencies: []\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillRegistryError, match="sha256"):
        SkillRegistry(root, audit_path=tmp_path / "events.jsonl")


def test_skill_tool_gate_rejects_unlisted_tool(tmp_path: Path) -> None:
    registry = SkillRegistry(SKILLS_ROOT, audit_path=tmp_path / "skill_events.jsonl")
    caller = _caller("financial_researcher")

    result = registry.invoke_tool(
        caller, "research.extract", "pdf", "db_write", path="unused"
    )

    assert result.status == "denied"
    assert result.error_code == "skill_tool_denied"
    assert "skill_tool_denied" in (tmp_path / "skill_events.jsonl").read_text(encoding="utf-8")


def test_skill_selection_requires_executor_identity(tmp_path: Path) -> None:
    registry = SkillRegistry(SKILLS_ROOT, audit_path=tmp_path / "skill_events.jsonl")

    with pytest.raises(SkillRegistryError, match="executor"):
        registry.load_for(CallerContext.human(), "content.script")


def test_parser_skill_requires_job_approved_source(tmp_path: Path) -> None:
    registry = SkillRegistry(SKILLS_ROOT, audit_path=tmp_path / "skill_events.jsonl")
    caller = _caller("financial_researcher")
    original = Path(__file__).parent / "fixtures" / "sample_table.csv"
    job = ApprovedSources(tmp_path / "job")
    job.approved_dir.mkdir(parents=True)
    target = job.approved_dir / "sample_table.csv"
    shutil.copy2(original, target)

    denied = registry.invoke_tool(
        caller, "research.extract", "xlsx", "parse_xlsx",
        path=str(target), approved_sources=job,
    )
    assert denied.status == "denied"
    assert denied.error_code == "not_registered"

    job.register(target, source_id="S01", source_name="sample table")
    result = registry.invoke_tool(
        caller, "research.extract", "xlsx", "parse_xlsx",
        path=str(target), approved_sources=job,
    )
    assert result.status == "ok"
    assert result.data is not None and result.data["sha256"] == job.get("S01").sha256
