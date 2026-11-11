"""调用者身份（CallerContext，M2.0 T04）。

安全模型（不可协商）：
1. **身份由服务端构造，客户端无法自证**。HTTP 请求只可能被识别为 ``human``
   或（非生产环境的）``dev``；``agent`` 身份只能由执行层在进程内构造。
2. ``X-Caller`` 头只是审计标签，不是凭据：它的值既不能提升权限、也不能切换身份。
   出现 ``X-Caller: agent`` 这类伪造时，记录告警并按 ``human`` 处理（见
   ``CallerContext.forged_header``）。
3. ``agent`` 身份必须携带 ``task_id`` 与 ``attempt_token``，并可与
   ``research_subtasks.attempt_token`` 比对（T07 建表后启用），用于识别租约丢失后的迟到写入。
4. ``dev`` 身份只在 ``app_env != production`` 且路径位于 ``/api/_dev/`` 下时才可能出现；
   生产环境访问 dev 端点返回 404（不暴露"这里有个隐藏接口"）。

为什么这么严：如果 agent 身份能由外部 HTTP 声明，任何人都能以"某个 Agent"的名义写库，
权限矩阵就退化成装饰品。所以 agent 身份在结构上不可能来自网络。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import Request

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import NotFoundError, PermissionDeniedError

logger = logging.getLogger(__name__)

# 客户端可以发送、但服务端不采信的头部
UNTRUSTED_CALLER_HEADER = "X-Caller"
DEV_PATH_MARKER = "/_dev/"


class CallerKind(str, Enum):
    """调用者类型。"""

    HUMAN = "human"
    AGENT = "agent"
    DEV = "dev"


@dataclass(frozen=True, slots=True)
class CallerContext:
    """一次操作的调用者身份。不可变，可安全传递与记录。"""

    kind: CallerKind
    name: str
    task_id: str | None = None
    subtask_id: str | None = None
    attempt_token: str | None = None
    db_scopes: frozenset[str] = field(default_factory=frozenset)
    dry_run: bool = False
    # 身份来源：http / executor（审计与排障用，不参与鉴权判定）
    source: str = "http"
    # 客户端是否发送了不被采信的 X-Caller（存在即说明有人在试探）
    forged_header: bool = False

    def __post_init__(self) -> None:
        if self.kind is CallerKind.AGENT:
            if not (self.task_id and self.attempt_token):
                raise ValueError(
                    "agent 身份必须由执行器构造，并携带 task_id 与 attempt_token"
                )
            if self.source != "executor":
                raise ValueError("agent 身份只能由执行层构造（source 必须是 executor）")
        elif self.task_id or self.attempt_token or self.db_scopes:
            raise ValueError("只有 agent 身份可以携带 task_id / attempt_token / db_scopes")

    # --- 构造入口 ---------------------------------------------------------
    @classmethod
    def human(cls, name: str = "local-operator", *, forged_header: bool = False) -> CallerContext:
        """本机人类操作者（唯一可能来自 HTTP 的写操作身份）。"""
        return cls(kind=CallerKind.HUMAN, name=name, source="http", forged_header=forged_header)

    @classmethod
    def dev(cls, name: str = "dev-operator") -> CallerContext:
        """开发/测试用的运维身份（仅非生产环境，且只对 /api/_dev/* 生效）。"""
        return cls(kind=CallerKind.DEV, name=name, source="http")

    @classmethod
    def for_executor(
        cls,
        *,
        agent: str,
        task_id: str,
        attempt_token: str,
        subtask_id: str | None = None,
        db_scopes: frozenset[str] | set[str] | None = None,
        dry_run: bool = False,
    ) -> CallerContext:
        """执行层专用：为一个 Agent 子任务构造身份（进程内调用，不经过网络）。"""
        return cls(
            kind=CallerKind.AGENT,
            name=agent,
            task_id=task_id,
            subtask_id=subtask_id,
            attempt_token=attempt_token,
            db_scopes=frozenset(db_scopes or ()),
            dry_run=dry_run,
            source="executor",
        )

    # --- 便捷判定 ---------------------------------------------------------
    @property
    def is_agent(self) -> bool:
        return self.kind is CallerKind.AGENT

    @property
    def is_human(self) -> bool:
        return self.kind is CallerKind.HUMAN

    def has_scope(self, scope: str) -> bool:
        return scope in self.db_scopes

    def describe(self) -> dict[str, Any]:
        """供日志与审计使用的摘要（不含任何凭据）。"""
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "name": self.name,
            "source": self.source,
            "dry_run": self.dry_run,
        }
        if self.is_agent:
            payload["task_id"] = self.task_id
            payload["subtask_id"] = self.subtask_id
        if self.forged_header:
            payload["forged_header"] = True
        return payload


def build_http_caller(request: Request, settings: Settings) -> CallerContext:
    """从 HTTP 请求构造调用者身份。

    只依赖服务端事实：路径 + 运行环境。``X-Caller`` 头只用于审计提示（可能是伪造），
    绝不据此提升或切换身份。
    """
    path = request.url.path
    prefix = settings.api_prefix.rstrip("/")
    is_dev_path = path.startswith(f"{prefix}{DEV_PATH_MARKER}")
    if is_dev_path and settings.app_env != "production":
        caller = CallerContext.dev()
    else:
        caller = CallerContext.human()

    raw_header = (request.headers.get(UNTRUSTED_CALLER_HEADER) or "").strip().lower()
    if raw_header and raw_header != CallerKind.HUMAN.value:
        logger.warning(
            "untrusted %s header ignored: %r on %s %s (HTTP callers can only be human/dev)",
            UNTRUSTED_CALLER_HEADER,
            raw_header,
            request.method,
            path,
        )
        # dataclasses.replace 保持不可变语义
        caller = CallerContext(
            kind=caller.kind,
            name=caller.name,
            source=caller.source,
            forged_header=True,
        )
    return caller


def settings_for_request(request: Request) -> Settings:
    """取该请求所属应用的真实配置。

    为什么不用 ``Depends(get_settings)``：那是带 ``lru_cache`` 的进程级单例，
    而应用实际生效的是 ``app.state.settings``（``create_app(settings)`` 可注入）。
    鉴权判定必须跟随**应用自己的配置**，否则一旦两者不一致，
    "生产环境隐藏 dev 端点"这种判定就可能看错环境。
    """
    state_settings = getattr(getattr(request, "app", None), "state", None)
    candidate = getattr(state_settings, "settings", None)
    if isinstance(candidate, Settings):
        return candidate
    return get_settings()


def require_caller(*kinds: CallerKind):
    """FastAPI 依赖工厂：声明某个端点接受哪些调用者类型。

    用法：``dependencies=[Depends(require_caller(CallerKind.HUMAN))]``

    返回的依赖函数带有 ``allowed_kinds`` 属性，供启动自检**静态核对**
    "清单里写了什么"和"端点上真的挂上了什么"是否一致——只核对清单会漏掉
    "清单写了但忘了挂依赖"这种最危险的错。
    """

    allowed = frozenset(kinds)
    if not allowed:  # pragma: no cover - 编程错误，启动即暴露
        raise ValueError("require_caller 至少需要一种调用者类型")

    def dependency(request: Request) -> CallerContext:
        settings = settings_for_request(request)
        # 生产环境把 dev 端点彻底隐藏成 404，而不是 403
        if CallerKind.DEV in allowed and settings.app_env == "production":
            raise NotFoundError("接口不存在")
        caller = build_http_caller(request, settings)
        if caller.kind not in allowed:
            raise PermissionDeniedError(
                f"{caller.kind.value} 身份无权执行该操作",
                required=[kind.value for kind in kinds],
                caller=caller.kind.value,
            )
        return caller

    dependency.allowed_kinds = allowed  # type: ignore[attr-defined]
    dependency.__name__ = (
        "require_caller(" + ",".join(k.value for k in sorted(allowed, key=str)) + ")"
    )
    return dependency


def allowed_kinds_of(dependency: Any) -> frozenset[CallerKind] | None:
    """读取某个依赖函数声明的调用者集合；不是 ``require_caller`` 的产物就返回 None。"""
    value = getattr(dependency, "allowed_kinds", None)
    return value if isinstance(value, frozenset) else None


__all__ = [
    "CallerContext",
    "CallerKind",
    "DEV_PATH_MARKER",
    "UNTRUSTED_CALLER_HEADER",
    "allowed_kinds_of",
    "build_http_caller",
    "require_caller",
]
