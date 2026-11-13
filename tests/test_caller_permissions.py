"""M2.0 T04：CallerContext、端点权限声明与伪造身份防护。

三条必须证明的安全性质：
1. agent 身份**不可能**来自 HTTP（结构上就不允许）；
2. ``X-Caller`` 头不能提升权限，只能被记录为"有人在试探"；
3. 生产环境访问 dev 端点返回 404 而不是 403。
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from backend.app.api.router import HUMAN_ONLY, PUBLIC, ROUTE_CALLERS
from backend.app.core.caller import (
    DEV_PATH_MARKER,
    UNTRUSTED_CALLER_HEADER,
    CallerContext,
    CallerKind,
    build_http_caller,
    require_caller,
)
from backend.app.core.config import get_settings
from backend.app.core.errors import NotFoundError, PermissionDeniedError


def make_request(
    path: str = "/api/topics",
    method: str = "GET",
    headers: tuple[tuple[str, str], ...] = (),
    settings=None,
) -> Request:
    """构造一个绑定到（可选）应用的请求，使 ``request.app.state.settings`` 可用。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.state.settings = settings or get_settings()

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 45678),
        "app": app,
    }
    return Request(scope)


# --- CallerContext 结构约束 ------------------------------------------------


def test_human_caller_has_no_task_identity():
    caller = CallerContext.human()
    assert caller.kind is CallerKind.HUMAN
    assert caller.is_human and not caller.is_agent
    assert caller.task_id is None and caller.attempt_token is None


def test_agent_caller_requires_task_id_and_attempt_token():
    with pytest.raises(ValueError):
        CallerContext(kind=CallerKind.AGENT, name="financial_researcher", source="executor")


def test_agent_caller_cannot_be_constructed_from_http_source():
    with pytest.raises(ValueError):
        CallerContext(
            kind=CallerKind.AGENT,
            name="financial_researcher",
            task_id="task-1",
            attempt_token="tok-1",
            source="http",
        )


def test_non_agent_caller_cannot_carry_agent_fields():
    with pytest.raises(ValueError):
        CallerContext(kind=CallerKind.HUMAN, name="local-operator", task_id="task-1")


def test_executor_caller_is_fully_populated():
    caller = CallerContext.for_executor(
        agent="fact_checker",
        task_id="task-abc",
        attempt_token="tok-abc",
        subtask_id="sub-1",
        db_scopes={"evidence", "review_records"},
    )
    assert caller.is_agent
    assert caller.has_scope("evidence")
    assert not caller.has_scope("publications")
    assert caller.describe()["source"] == "executor"


# --- HTTP 身份构造 --------------------------------------------------------


def test_http_request_becomes_human_caller():
    caller = build_http_caller(make_request(), get_settings())
    assert caller.kind is CallerKind.HUMAN
    assert caller.forged_header is False


def test_dev_path_only_in_non_production():
    settings = get_settings()
    dev_path = f"{settings.api_prefix}{DEV_PATH_MARKER}tick"
    assert build_http_caller(make_request(path=dev_path), settings).kind is CallerKind.DEV

    production = settings.model_copy(update={"app_env": "production"})
    assert build_http_caller(make_request(path=dev_path), production).kind is CallerKind.HUMAN


@pytest.mark.parametrize("spoofed", ["agent", "dev", "AGENT", " root "])
def test_x_caller_header_never_escalates_identity(spoofed):
    caller = build_http_caller(
        make_request(headers=((UNTRUSTED_CALLER_HEADER, spoofed),)), get_settings()
    )
    assert caller.kind is CallerKind.HUMAN, "客户端头不能切换身份"
    assert caller.forged_header is True, "伪造尝试必须被记录"
    assert caller.describe()["forged_header"] is True


def test_x_caller_header_equal_human_is_not_flagged():
    caller = build_http_caller(
        make_request(headers=((UNTRUSTED_CALLER_HEADER, "human"),)), get_settings()
    )
    assert caller.forged_header is False


# --- require_caller 依赖工厂 ----------------------------------------------


def test_require_caller_rejects_wrong_kind():
    dependency = require_caller(CallerKind.AGENT)
    with pytest.raises(PermissionDeniedError) as excinfo:
        dependency(make_request())
    assert excinfo.value.details["reason"] == "permission_denied"
    assert excinfo.value.details["caller"] == "human"
    assert excinfo.value.status_code == 422


def test_dev_endpoint_is_hidden_as_404_in_production():
    dependency = require_caller(CallerKind.DEV)
    production = get_settings().model_copy(update={"app_env": "production"})
    dev_path = f"{production.api_prefix}{DEV_PATH_MARKER}tick"
    with pytest.raises(NotFoundError):
        dependency(make_request(path=dev_path, settings=production))


def test_dev_endpoint_reachable_outside_production():
    dependency = require_caller(CallerKind.DEV)
    settings = get_settings()
    dev_path = f"{settings.api_prefix}{DEV_PATH_MARKER}tick"
    caller = dependency(make_request(path=dev_path, settings=settings))
    assert caller.kind is CallerKind.DEV


# --- 端点权限清单 ---------------------------------------------------------


def test_every_openapi_operation_has_a_caller_declaration(client):
    schema = client.get("/openapi.json").json()
    operations = {
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        for method in methods
        if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}
    }
    assert operations == set(ROUTE_CALLERS), "权限清单必须与真实端点双向一致"
    # 21 个既有操作 + 8 个研究任务 + 3 个监控 + 5 个工作台 + 1 个视频交接
    # + 1 个渲染流水线 + 2 个后台渲染 + 3 个选题合集 + 7 个脚本/审核 + 3 个选题标签 = 54
    # + 3 个语义核查（列表 / 汇总 / 人工复核）= 57
    # + 1 个成稿闸门查询（draft-readiness）= 58。
    # 这个数字是刻意的"绊线"：新增端点必须同步改这里，逼人确认权限声明也补上了。
    assert len(operations) == 58


def test_only_health_is_public():
    assert ROUTE_CALLERS[("GET", "/health")] == PUBLIC
    others = {key: value for key, value in ROUTE_CALLERS.items() if key != ("GET", "/health")}
    assert others, "至少有一个受保护端点"
    assert all(value == HUMAN_ONLY for value in others.values())


def test_no_http_endpoint_accepts_agent_identity():
    """M2.0 的诚实断言：还没有任何 HTTP 端点接受 agent 身份（agent 只在进程内）。

    将来若开放 agent 可调用的端点，必须同时更新此测试与 api_design.md。
    """
    assert all(CallerKind.AGENT not in allowed for allowed in ROUTE_CALLERS.values())


def test_api_still_works_for_human_and_ignores_spoofed_header(client):
    response = client.get("/api/topics", headers={UNTRUSTED_CALLER_HEADER: "agent"})
    assert response.status_code == 200
    # 不断言 total == 0：整个测试会话共用一个临时库，别的用例会建选题。
    # 这里要验证的是"伪造的身份头没有把请求变成 agent"，而不是库里有没有数据。
    body = response.json()
    assert isinstance(body["total"], int)
    assert isinstance(body["items"], list)


def test_health_needs_no_authorization(client):
    assert client.get("/health").status_code == 200


# --- 端到端：真实 HTTP 请求无法冒充身份 ------------------------------------


def _build_probe_app(settings, allowed_kind):
    """造一个只含一个受保护端点的小应用，用于端到端验证依赖真正生效。"""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.errors import install_exception_handlers

    probe = FastAPI()
    probe.state.settings = settings
    install_exception_handlers(probe)

    @probe.get("/probe", dependencies=[Depends(require_caller(allowed_kind))])
    def probe_endpoint() -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(probe)


def test_http_request_cannot_reach_agent_only_endpoint():
    """HTTP 请求永远是 human/dev，因此 agent-only 端点必然拒绝——即使伪造头。"""
    settings = get_settings()
    with _build_probe_app(settings, CallerKind.AGENT) as probe:
        for headers in ({}, {UNTRUSTED_CALLER_HEADER: "agent"}):
            response = probe.get("/probe", headers=headers)
            assert response.status_code == 422
            body = response.json()["error"]
            assert body["code"] == "domain_validation_error"
            assert body["details"]["reason"] == "permission_denied"
            assert body["details"]["caller"] == "human"


def test_dev_only_endpoint_returns_404_in_production():
    production = get_settings().model_copy(update={"app_env": "production"})
    with _build_probe_app(production, CallerKind.DEV) as probe:
        response = probe.get("/probe", headers={UNTRUSTED_CALLER_HEADER: "dev"})
        assert response.status_code == 404, "生产环境不得暴露 dev 端点存在"
        assert response.json()["error"]["code"] == "not_found"


def test_dev_only_endpoint_works_outside_production():
    """非生产环境 + /api/_dev/ 路径下 dev 身份可用（这条路只在机器上）。"""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.errors import install_exception_handlers

    settings = get_settings()
    probe = FastAPI()
    probe.state.settings = settings
    install_exception_handlers(probe)

    @probe.get(
        f"{settings.api_prefix}{DEV_PATH_MARKER}tick",
        dependencies=[Depends(require_caller(CallerKind.DEV))],
    )
    def dev_tick() -> dict[str, str]:
        return {"ok": "true"}

    with TestClient(probe) as client:
        assert client.get(f"{settings.api_prefix}{DEV_PATH_MARKER}tick").status_code == 200
        # 同一个端点用 human 路径访问不成立（这里是 dev 专属）
        assert client.get("/probe").status_code in {404, 405}


# --- 启动自检必须核对"依赖是否真的挂上" ------------------------------------


def test_route_walk_finds_every_operation_and_its_attached_dependency(client):
    from backend.app.core.startup_checks import iter_api_routes

    app = client.app
    routes = iter_api_routes(app)
    seen = {(method, route.path): route for route in routes for method in route.methods}

    # 58 个 API 操作 + 1 个豁免端点（GET /）
    assert len(seen) == 59
    for key, allowed in ROUTE_CALLERS.items():
        if allowed == PUBLIC:
            assert seen[key].allowed is None, f"{key} 是公开端点，不应挂 require_caller"
        else:
            assert seen[key].allowed == allowed, f"{key} 的挂载与清单不一致"


def test_startup_check_detects_missing_dependency():
    """清单写了权限，但端点上忘了挂 require_caller —— 必须被自检抓出来。"""
    from fastapi import FastAPI

    from backend.app.core.startup_checks import _check_route_callers

    app = FastAPI()

    @app.get("/api/topics")
    def unprotected() -> dict[str, str]:
        return {"leak": "true"}

    result = _check_route_callers(app)
    assert result.ok is False
    assert "没有挂 require_caller" in result.detail


def test_startup_check_detects_manifest_lying_about_public():
    """端点上挂着 require_caller，清单却说是公开——同样是矛盾。"""
    from fastapi import Depends, FastAPI

    from backend.app.core.startup_checks import _check_route_callers

    app = FastAPI()

    @app.get("/health", dependencies=[Depends(require_caller(CallerKind.HUMAN))])
    def health_with_auth() -> dict[str, str]:
        return {"ok": "true"}

    result = _check_route_callers(app)
    assert result.ok is False
    assert "却挂了 require_caller" in result.detail


def test_startup_check_detects_stale_manifest_entry():
    from fastapi import Depends, FastAPI

    from backend.app.core.startup_checks import _check_route_callers

    app = FastAPI()

    @app.get("/api/ghost", dependencies=[Depends(require_caller(CallerKind.HUMAN))])
    def ghost() -> dict[str, str]:
        return {"ok": "true"}

    result = _check_route_callers(app)
    assert result.ok is False
    # 真实端点不在清单里 → 报未声明；清单里已有的条目在本 app 中都不存在 → 报过期
    assert "未声明调用者" in result.detail
    assert "不存在的端点" in result.detail
