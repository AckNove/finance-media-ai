"""启动自检：不一致的配置必须让进程起不来，而不是带病运行。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core import startup_checks as checks
from backend.app.core.errors import NotFoundError, PermissionDeniedError
from backend.app.core.startup_checks import (
    StartupCheckError,
    assert_startup_ok,
    run_startup_checks,
)
from backend.app.main import create_app

EXPECTED_CHECK_NAMES = {
    "config_sections",
    "task_type_registry",
    "agent_roster",
    "tool_specs",
    "route_callers",
    "pricing",
    "database",
}


def _app():
    return create_app()


def test_all_checks_pass_in_repository_state():
    """当前仓库状态必须全绿：配置、注册表、端点声明都是一致的。"""
    app = _app()
    report = run_startup_checks(app)

    assert {r.name for r in report.results} == EXPECTED_CHECK_NAMES
    assert report.errors == ()
    assert report.ok is True
    assert f"/{len(report.results)} 项通过" in report.summary()


def test_report_marks_pricing_as_warning_not_error():
    """价目缺失是已知状态（Q8=B），只能是 warning，绝不能阻塞启动。"""
    app = _app()
    report = run_startup_checks(app)
    pricing = next(r for r in report.results if r.name == "pricing")

    assert pricing.severity == "warning"
    assert pricing.ok is False  # 当前 config/models.yaml 里确实没有价目
    assert "missing" in pricing.detail or "缺失" in pricing.detail
    # 但整体依然可以启动
    assert report.ok is True
    assert_startup_ok(report)


def test_task_type_registry_error_is_reported(monkeypatch: pytest.MonkeyPatch):
    """注册表不一致必须变成 error 级问题。"""
    monkeypatch.setattr(checks, "validate_registry", lambda: ["agents/x/PROMPT.md 多声明了 foo.bar"])
    report = run_startup_checks(_app())

    registry = next(r for r in report.results if r.name == "task_type_registry")
    assert registry.ok is False
    assert registry.severity == "error"
    assert report.ok is False
    with pytest.raises(StartupCheckError) as excinfo:
        assert_startup_ok(report)
    assert "foo.bar" in str(excinfo.value)


def test_undeclared_route_is_detected(monkeypatch: pytest.MonkeyPatch):
    """漏声明调用者的端点必须被发现（双向比对）。"""
    trimmed = dict(checks.ROUTE_CALLERS)
    trimmed.pop(("GET", "/health"))
    monkeypatch.setattr(checks, "ROUTE_CALLERS", trimmed)

    report = run_startup_checks(_app())
    route_check = next(r for r in report.results if r.name == "route_callers")

    assert route_check.ok is False
    assert "未声明调用者" in route_check.detail
    assert "/health" in route_check.detail


def test_stale_route_declaration_is_detected(monkeypatch: pytest.MonkeyPatch):
    """声明了不存在的端点同样算错（防止删除路由后忘记同步登记表）。"""
    extended = dict(checks.ROUTE_CALLERS)
    extended[("GET", "/api/does-not-exist")] = frozenset()
    monkeypatch.setattr(checks, "ROUTE_CALLERS", extended)

    report = run_startup_checks(_app())
    route_check = next(r for r in report.results if r.name == "route_callers")

    assert route_check.ok is False
    assert "声明了不存在的端点" in route_check.detail


def test_missing_prompt_file_is_reported(monkeypatch: pytest.MonkeyPatch):
    """角色提示词缺失属于 error：没有提示词的角色无法开工。"""
    monkeypatch.setattr(checks, "roster_keys_missing_prompts", lambda: ["ghost_agent"])
    report = run_startup_checks(_app())

    roster = next(r for r in report.results if r.name == "agent_roster")
    assert roster.ok is False
    assert "ghost_agent" in roster.detail


def test_tool_spec_mismatch_is_reported(monkeypatch: pytest.MonkeyPatch):
    """工具名清单与规格不一致必须被发现。"""
    monkeypatch.setattr(checks, "TOOL_NAMES", (*checks.TOOL_NAMES, "ghost_tool"))
    report = run_startup_checks(_app())

    tool_check = next(r for r in report.results if r.name == "tool_specs")
    assert tool_check.ok is False
    assert "ghost_tool" in tool_check.detail


def test_startup_report_is_attached_to_app_state():
    """自检结果要能从 app.state 读到（运维与测试都靠它）。"""
    app = _app()
    with TestClient(app):
        report = app.state.startup_report
        assert report.ok is True
        assert len(report.results) == len(EXPECTED_CHECK_NAMES)


def test_permission_errors_stay_in_domain_validation_shape():
    """权限拒绝复用 422 信封（自检相关错误类型不应改变错误契约）。"""
    error = PermissionDeniedError(required=["human"], caller="agent")
    assert error.status_code == 422
    assert error.code == "domain_validation_error"
    assert error.details["reason"] == "permission_denied"

    not_found = NotFoundError()
    assert not_found.status_code == 404
