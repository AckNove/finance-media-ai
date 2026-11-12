"""Load pinned Agent Skills without treating their prose as executable capability."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.app.capabilities.tools import TOOL_NAMES, ToolRegistry, ToolResult, get_tool_registry
from backend.app.core.caller import CallerContext, CallerKind
from backend.app.core.config import get_agent_routing
from backend.app.core.job_paths import ApprovedSources, PathNotAllowedError


class SkillRegistryError(ValueError):
    """A Skill declaration, file, identity, or dependency failed validation."""


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    name: str
    description: str
    instructions: str
    version: str
    source: str
    license: str
    sha256: str
    agents: frozenset[str]
    task_types: frozenset[str]
    tools: frozenset[str]
    dependencies: tuple[str, ...]
    upstream_commit: str | None = None


class SkillRegistry:
    """Discover pinned instructions and allow only their declared ToolRegistry tools."""

    def __init__(self, root: Path, *, audit_path: Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.audit_path = audit_path or self.root.parent / "logs" / "skill_events.jsonl"
        manifest_path = self.root / "registry.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise SkillRegistryError("skill registry version must be 1")
        declarations = manifest.get("skills")
        if not isinstance(declarations, list):
            raise SkillRegistryError("skills must be a list")

        routing = get_agent_routing()
        skills: dict[str, LoadedSkill] = {}
        declared_paths: set[Path] = set()
        for entry in declarations:
            if not isinstance(entry, dict):
                raise SkillRegistryError("skill declaration must be a mapping")
            relative = Path(str(entry.get("path", "")))
            path = (self.root / relative).resolve()
            if path == self.root or self.root not in path.parents or path.name != "SKILL.md":
                raise SkillRegistryError(f"skill path outside registry: {relative}")
            if any(part.is_symlink() for part in (self.root / relative).parents if part != self.root):
                raise SkillRegistryError(f"skill path contains symlink: {relative}")
            if not path.is_file():
                raise SkillRegistryError(f"missing skill file: {relative}")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry.get("sha256"):
                raise SkillRegistryError(f"sha256 mismatch for {relative}")
            text = raw.decode("utf-8-sig")
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise SkillRegistryError(f"missing YAML frontmatter: {relative}")
            metadata = yaml.safe_load(parts[1])
            if not isinstance(metadata, dict):
                raise SkillRegistryError(f"invalid YAML frontmatter: {relative}")
            name = str(entry.get("name", ""))
            if not name or metadata.get("name") != name or name in skills:
                raise SkillRegistryError(f"duplicate or mismatched skill name: {name}")
            agents = frozenset(entry.get("agents") or ())
            task_types = frozenset(entry.get("task_types") or ())
            tools = frozenset(entry.get("tools") or ())
            if not agents or not task_types or not tools.issubset(TOOL_NAMES):
                raise SkillRegistryError(f"invalid agent/task/tool declarations: {name}")
            for task_type in task_types:
                task_spec = routing.task_types.get(task_type)
                if task_spec is None or not agents.intersection(task_spec.allowed_agents):
                    raise SkillRegistryError(f"skill {name} has no eligible agent for {task_type}")
                if not tools.issubset(task_spec.tools):
                    raise SkillRegistryError(f"skill {name} requests unregistered task tools")
            for agent in agents:
                if agent not in routing.agents or not tools.issubset(routing.tools_for(agent)):
                    raise SkillRegistryError(f"skill {name} requests unregistered agent tools")
            source = str(entry.get("source", ""))
            commit = entry.get("upstream_commit")
            if source != "local" and (
                not source.startswith("https://github.com/")
                or not isinstance(commit, str)
                or len(commit) != 40
            ):
                raise SkillRegistryError(f"vendor skill {name} needs pinned provenance")
            version = str(entry.get("version", ""))
            license_name = str(entry.get("license", ""))
            if not version or not license_name:
                raise SkillRegistryError(f"skill {name} needs version and license")
            skills[name] = LoadedSkill(
                name=name,
                description=str(metadata.get("description", "")),
                instructions=parts[2].strip(),
                version=version,
                source=source,
                license=license_name,
                sha256=digest,
                agents=agents,
                task_types=task_types,
                tools=tools,
                dependencies=tuple(entry.get("dependencies") or ()),
                upstream_commit=commit,
            )
            declared_paths.add(path)
        found_paths = {path.resolve() for path in self.root.rglob("SKILL.md")}
        if declared_paths != found_paths:
            raise SkillRegistryError("every SKILL.md must be registered exactly once")
        self._skills = skills

    @property
    def skills(self) -> tuple[LoadedSkill, ...]:
        return tuple(self._skills.values())

    def _record(self, event: str, caller: CallerContext, task_type: str, **details: Any) -> None:
        payload = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "agent": caller.name,
            "task_id": caller.task_id,
            "task_type": task_type,
            **details,
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def load_for(self, caller: CallerContext, task_type: str) -> tuple[LoadedSkill, ...]:
        routing = get_agent_routing()
        if caller.kind is not CallerKind.AGENT or caller.source != "executor":
            raise SkillRegistryError("skills may only load for executor Agent identity")
        if caller.name not in routing.allowed_agents_for(task_type):
            raise SkillRegistryError(f"agent {caller.name} cannot run {task_type}")
        selected = tuple(
            skill for skill in self.skills
            if caller.name in skill.agents and task_type in skill.task_types
        )
        for skill in selected:
            missing = [name for name in skill.dependencies if importlib.util.find_spec(name) is None]
            if missing:
                self._record("load_failed", caller, task_type, skill=skill.name, missing=missing)
                raise SkillRegistryError(f"skill {skill.name} missing dependencies: {missing}")
            self._record("loaded", caller, task_type, skill=skill.name, sha256=skill.sha256)
        return selected

    def invoke_tool(
        self,
        caller: CallerContext,
        task_type: str,
        skill_name: str,
        tool: str,
        *,
        registry: ToolRegistry | None = None,
        approved_sources: ApprovedSources | None = None,
        **arguments: Any,
    ) -> ToolResult:
        selected = {skill.name: skill for skill in self.load_for(caller, task_type)}
        skill = selected.get(skill_name)
        if skill is None or tool not in skill.tools:
            self._record("tool_denied", caller, task_type, skill=skill_name,
                         tool=tool, reason="skill_tool_denied")
            return ToolResult(status="denied", error_code="skill_tool_denied",
                              error_message=f"skill {skill_name} cannot use {tool}")
        if tool in {"parse_pdf", "parse_xlsx"}:
            if approved_sources is None or not isinstance(arguments.get("path"), str):
                reason = "not_registered"
            else:
                try:
                    approved_sources.resolve(arguments["path"])
                except PathNotAllowedError as error:
                    reason = error.reason
                else:
                    reason = ""
            if reason:
                self._record("tool_denied", caller, task_type, skill=skill_name,
                             tool=tool, reason=reason)
                return ToolResult(status="denied", error_code=reason,
                                  error_message="parser source is not approved for this job")
        result = (registry or get_tool_registry()).dispatch(caller, tool, **arguments)
        self._record("tool_call", caller, task_type, skill=skill_name,
                     tool=tool, status=result.status, error_code=result.error_code)
        return result
