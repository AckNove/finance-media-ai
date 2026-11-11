"""请求追踪：X-Request-Id 的生成、透传、校验与日志。

请求 ID 只用于排障，**不是权限依据**——任何人都能传这个头，
它换不来任何身份。
"""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

from backend.app.core.request_context import (
    REQUEST_ID_HEADER,
    get_request_id,
    new_request_id,
    sanitize_request_id,
)
from backend.app.main import create_app

GENERATED_PATTERN = re.compile(r"^req-[0-9a-f]{16}$")


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_new_request_id_shape():
    value = new_request_id()
    assert GENERATED_PATTERN.match(value)
    assert new_request_id() != value


@pytest.mark.parametrize(
    "raw",
    ["abc", "req-1234567890abcdef", "a.b_c-1", "A" * 64],
)
def test_sanitize_accepts_safe_ids(raw: str):
    assert sanitize_request_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "bad id", "a" * 65, "has/slash", "带中文"],
)
def test_sanitize_rejects_unsafe_ids(raw: str | None):
    assert sanitize_request_id(raw) is None


def test_response_carries_generated_request_id(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id is not None
    assert GENERATED_PATTERN.match(request_id)


def test_client_request_id_is_echoed(client: TestClient):
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc.123"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc.123"


def test_invalid_client_request_id_is_replaced(client: TestClient):
    # 用 ASCII 但不合法的值（斜杠不在白名单里）；HTTP 头本身不能装非 ASCII
    response = client.get("/health", headers={REQUEST_ID_HEADER: "bad/id"})
    echoed = response.headers[REQUEST_ID_HEADER]
    assert echoed != "bad/id"
    assert GENERATED_PATTERN.match(echoed)


def test_request_id_present_on_error_responses(client: TestClient):
    """错误响应也必须带 ID，否则线上根本没法把报错和日志对上。"""
    response = client.get("/api/topics/999999")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "not_found"
    assert response.headers[REQUEST_ID_HEADER]


def test_request_id_is_not_a_credential(client: TestClient):
    """伪造请求 ID 不能改变调用者身份：agent 专属动作依然被拒。"""
    response = client.get(
        "/api/topics",
        headers={REQUEST_ID_HEADER: "req-0000000000000000", "X-Caller": "agent"},
    )
    # 本机请求就是 human（X-Caller 只作为审计标签），所以这里应当 200，
    # 而不是因为传了奇怪的头就变成别的身份或 422。
    assert response.status_code == 200


def test_access_log_line_contains_request_id():
    """访问日志必须带 request_id 与路径。

    两个坑都在这里踩过：
    1. 不能用 ``caplog``——应用启动时 ``configure_logging`` 会 ``force=True``
       重建根 logger 的 handler，把 pytest 的捕获 handler 一起换掉；
    2. 访问日志在响应发送**之后**才写，而 ``TestClient.get()`` 在响应发出时就
       返回，所以断言必须放在客户端上下文退出之后，否则会与写日志的线程抢跑。
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - 测试用途
            records.append(record)

    target = logging.getLogger("backend.app.api.middleware")
    collector = _Collector()
    previous_level = target.level
    target.addHandler(collector)
    target.setLevel(logging.INFO)
    try:
        with TestClient(create_app()) as local_client:
            response = local_client.get("/health", headers={REQUEST_ID_HEADER: "trace-log-1"})
    finally:
        target.removeHandler(collector)
        target.setLevel(previous_level)

    assert response.status_code == 200
    messages = [record.getMessage() for record in records]
    assert messages, "中间件没有写出任何访问日志"
    assert any("trace-log-1" in message and "/health" in message for message in messages)


def test_request_id_is_reset_between_requests(client: TestClient):
    """请求结束后上下文变量必须复位，不能污染下一个请求。"""
    client.get("/health", headers={REQUEST_ID_HEADER: "trace-first"})
    assert get_request_id() == ""

    response = client.get("/health")
    assert response.headers[REQUEST_ID_HEADER] != "trace-first"
