"""真实模型冒烟：一次最小调用，把「到底能不能用」问清楚。

它不造假：没有密钥就报 ``BLOCKED``，价目未知就报 ``pricing_missing``，
预算不够就报 ``budget_blocked``——三条都不会退化成"假装成功"。

用法::

    python -m backend.scripts.model_smoke
    python -m backend.scripts.model_smoke --model deepseek-v4-pro --max-tokens 200
    python -m backend.scripts.model_smoke --database-url sqlite:///临时库.db

退出码：0 成功 / 2 拒绝受保护库 / 3 未配置（无密钥或功能开关关着）
        / 4 预算或价目拦下 / 5 调用失败
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.app.capabilities.model import ModelGateway, ModelRequest
from backend.app.capabilities.model.providers import mask_secret, provider_status
from backend.app.core.caller import CallerContext
from backend.app.core.config import PROJECT_ROOT, Settings, get_features_settings
from backend.app.core.errors import AppError

DEFAULT_PROMPT = "请用不超过100字解释商业银行的主要收入来源。"
PROTECTED_DB_SUFFIX = "data/finance_media.db"

EXIT_OK = 0
EXIT_PROTECTED = 2
EXIT_NOT_CONFIGURED = 3
EXIT_BLOCKED = 4
EXIT_FAILED = 5


class SmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_protected(database_url: str) -> bool:
    """受保护库只读：连冒烟都不许往上写 model_calls。"""
    normalised = database_url.replace("\\", "/").rstrip("/")
    return normalised.endswith(PROTECTED_DB_SUFFIX)


def _session_factory(database_url: str):
    from sqlalchemy.orm import sessionmaker

    from backend.app.database.session import build_engine

    engine = build_engine(Settings(database_url=database_url))
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _audit_row(provider: str, model: str) -> dict[str, Any]:
    """回读刚写下的审计行——"我记了账"必须是查得出来的，不是口头承诺。"""
    from sqlalchemy import select

    from backend.app.database.session import get_session_factory
    from backend.app.models.metering import ModelCall

    session = get_session_factory()()
    try:
        stmt = (
            select(ModelCall)
            .where(ModelCall.provider == provider, ModelCall.model == model)
            .order_by(ModelCall.id.desc())
            .limit(1)
        )
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return {}
        return {
            "id": row.id,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "cost_estimate_cny": float(row.cost_estimate or 0),
            "pricing_unknown": bool(row.pricing_unknown),
            "cache_hit": bool(row.cache_hit),
            "duration_ms": row.duration_ms,
            "error_message": row.error_message or "",
        }
    finally:
        session.close()


def run(
    *,
    provider: str,
    model: str,
    prompt: str,
    max_tokens: int,
    database_url: str | None,
) -> dict[str, Any]:
    status = provider_status()
    entry = status.get(provider) or {}
    feature_on = get_features_settings().model

    blockers: list[str] = []
    if entry and entry.get("status") != "ready":
        hint = {
            "missing_api_key": f"在 .env 或系统环境变量里设置 {entry.get('api_key_env') or 'API_KEY'}",
            "provider_disabled": "config/models.yaml 里把该 provider 的 enabled 改成 true",
            "missing_base_url": f"设置 {entry.get('base_url_env') or 'BASE_URL'}",
            "unknown_provider": "config/models.yaml 没登记这个 provider",
        }.get(entry.get("status", ""), "")
        blockers.append(
            f"provider={entry.get('status') or 'unknown'}"
            f"（{entry.get('message') or ''}）{('→ ' + hint) if hint else ''}"
        )
    if not feature_on:
        blockers.append(
            "features.model=false（config/settings.yaml 的人工开关，脚本不改它）"
        )
    if blockers:
        # 两个闸门一起报，用户一次就能改完，不必来回试
        raise SmokeError(
            "model_not_configured",
            "模型还不能调用：" + "；".join(blockers),
        )

    gateway = ModelGateway()
    request = ModelRequest(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        purpose="smoke",
        max_tokens=max_tokens,
        temperature=0.2,
        cacheable=False,  # 冒烟要的是真实请求，命中缓存等于没测
    )
    reply = gateway.complete(CallerContext.human(), request)

    result: dict[str, Any] = {
        "ok": reply.ok,
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "status": reply.status,
        "error_code": reply.error_code,
        "error_message": reply.error_message,
        "text": reply.text,
        "char_count": len(reply.text),
        "usage": reply.raw_usage,
        "api_key": mask_secret((status.get(provider) or {}).get("api_key_masked") or ""),
    }
    if database_url is None:
        result["audit"] = _audit_row(provider, model)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实模型冒烟（一次最小调用）")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=200, dest="max_tokens")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--allow-development-db", action="store_true")
    args = parser.parse_args(argv)

    url = args.database_url
    if url and is_protected(url) and not args.allow_development_db:
        print(
            json.dumps(
                {
                    "ok": False,
                    "blocked": True,
                    "reason": "protected_database",
                    "message": "拒绝在 data/finance_media.db 上写审计行；请用临时库",
                },
                ensure_ascii=False,
            )
        )
        return EXIT_PROTECTED

    session_factory = _session_factory(url) if url else None
    if session_factory is not None:
        from backend.app.database import session as session_module

        session_module.get_session_factory = lambda: session_factory  # type: ignore[assignment]

    try:
        payload = run(
            provider=args.provider,
            model=args.model,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
            database_url=url,
        )
    except SmokeError as error:
        print(
            json.dumps(
                {"ok": False, "blocked": True, "reason": error.code, "message": error.message},
                ensure_ascii=False,
            )
        )
        return EXIT_NOT_CONFIGURED
    except AppError as error:
        print(
            json.dumps(
                {"ok": False, "blocked": True, "reason": "app_error", "message": str(error)},
                ensure_ascii=False,
            )
        )
        return EXIT_BLOCKED

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["ok"]:
        return EXIT_OK
    if payload["status"] in {"budget_blocked", "not_configured", "disabled"}:
        return EXIT_BLOCKED
    return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
