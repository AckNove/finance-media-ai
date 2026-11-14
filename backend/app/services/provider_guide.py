"""服务商设置页要说的话：缺什么、去哪写、写完怎么验。

分工
----
* `model_readiness.py`：回答「这个**模型**现在能不能调」；
* 本模块：回答「这个**服务商**缺哪个变量、写在哪个文件、写完怎么确认它真的通了」。

为什么值得单独写
----------------
模型页说「缺密钥」很好，但人下一步会卡在三个问题上：
1. 变量名叫什么？（`DEEPSEEK_API_KEY`）
2. 写在哪儿？——系统环境变量，还是仓库根的 `.env`？两个都行，但改完 `.env`
   **必须重启服务**（`.env` 只在进程启动时读一次），不重启会以为没生效；
3. 怎么知道配对了？——`model_smoke` 已经存在，但页面上从没指过路。

三条硬规矩
----------
* **只报存在性，不回显值**：这里只回答「进程里有 / `.env` 里有」，一个字符都不显示；
* **不替人发请求**：`verify_command` 只是把命令写给用户，页面自己不发；
* **不猜**：服务商没声明 `api_key_env`（比如本地模型）就如实说「这个服务商不需要密钥」。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from backend.app.core.config import PROJECT_ROOT, get_models_config


ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
MODELS_FILE = PROJECT_ROOT / "config" / "models.yaml"


@dataclass(frozen=True)
class ProviderGuide:
    name: str
    display_name: str
    enabled: bool
    status: str
    note: str
    key_env: str = ""
    base_url_env: str = ""
    base_url: str = ""
    key_in_process: bool = False
    key_in_env_file: bool = False
    env_file: str = ""
    env_file_exists: bool = False
    models: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[str, ...] = field(default_factory=tuple)
    verify_command: str = ""
    ready: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "status": self.status,
            "note": self.note,
            "key_env": self.key_env,
            "base_url_env": self.base_url_env,
            "base_url": self.base_url,
            "key_in_process": self.key_in_process,
            "key_in_env_file": self.key_in_env_file,
            "env_file": self.env_file,
            "env_file_exists": self.env_file_exists,
            "models": list(self.models),
            "steps": list(self.steps),
            "verify_command": self.verify_command,
            "ready": self.ready,
        }


def env_file_keys(path: Path | None = None) -> tuple[str, ...]:
    """读出 `.env` 里出现过的**变量名**（不含值，也不解析引号里的花活）。"""

    target = path or ENV_FILE
    if not target.exists():
        return ()
    names: list[str] = []
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - 权限之类
        return ()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _value_present(environ: Mapping[str, str] | None, name: str) -> bool:
    if not name:
        return False
    source = environ if environ is not None else os.environ
    value = source.get(name)
    return bool(value and str(value).strip())


def _models_of(provider: str, models_config: Any) -> tuple[str, ...]:
    config = models_config or get_models_config()
    return tuple(item.model for item in config.models if item.provider == provider and item.enabled)


def _steps(
    *,
    provider: str,
    status: str,
    key_env: str,
    base_url_env: str,
    env_file: Path,
    key_in_process: bool,
    key_in_env_file: bool,
    enabled: bool,
) -> tuple[str, ...]:
    steps: list[str] = []
    if not enabled:
        steps.append(
            f"先在 config/models.yaml 把 providers.{provider}.enabled 改成 true"
            "（这一步得你自己改——页面不改配置）"
        )
    if key_env:
        if not key_in_process and not key_in_env_file:
            steps.append(f"在仓库根的 {env_file.name} 里加一行：{key_env}=你的密钥（{env_file} 已被 .gitignore 排除）")
        elif key_in_env_file and not key_in_process:
            steps.append(
                f"{env_file.name} 里已经有 {key_env}，但当前进程没读到——"
                f"{env_file.name} 只在启动时读一次，重启服务才会生效"
            )
        elif key_in_process:
            steps.append(f"{key_env} 已在当前进程里（来源可能是 .env 或系统环境变量）")
    else:
        steps.append("这个服务商不需要密钥（本地模型）")
    if base_url_env:
        steps.append(f"接口地址默认用内置值；要换就写 {base_url_env}（同样写在 {env_file.name} 或系统环境变量）")
    if status != "ready":
        steps.append("改完执行 scripts\\stop.ps1 再 scripts\\start.ps1 重启（不要热改）")
        steps.append("重启后先跑一次下面的验证命令；它会真的发一次最小请求、花几分钱，并把这次调用记进 model_calls")
    return tuple(steps)


def _verify_command(provider: str, models: tuple[str, ...]) -> str:
    """验证命令必须落到**这个服务商自己的模型**上。

    早先写成「找不到就用全局第一个模型」，于是 `openai` 那行会给人一条
    `--provider openai --model deepseek-flash` 的命令——照着跑必然失败，
    而且会让人以为是密钥的问题。
    """

    if models:
        return (
            f"python -m backend.scripts.model_smoke --provider {provider}"
            f" --model {models[0]} --max-tokens 120"
        )
    return f"python -m backend.scripts.model_smoke --provider {provider}"


def guide_of(
    providers: Mapping[str, Mapping[str, Any]],
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    models_config: Any = None,
) -> list[ProviderGuide]:
    target = env_file or ENV_FILE
    names = set(env_file_keys(target))
    items: list[ProviderGuide] = []
    for name, entry in providers.items():
        key_env = str(entry.get("api_key_env") or "")
        base_url_env = str(entry.get("base_url_env") or "")
        models = _models_of(name, models_config)
        verify = _verify_command(name, models)
        status = str(entry.get("status", "unknown"))
        enabled = bool(entry.get("enabled", False))
        steps = list(
            _steps(
                provider=name,
                status=status,
                key_env=key_env,
                base_url_env=base_url_env,
                env_file=target,
                key_in_process=_value_present(environ, key_env),
                key_in_env_file=bool(key_env and key_env in names),
                enabled=enabled,
            )
        )
        if not models:
            steps.insert(
                0,
                f"这个服务商下还没有启用的模型：先到 config/models.yaml 给它加一个模型（或把现有模型的 enabled 打开、价目填上）",
            )
        items.append(
            ProviderGuide(
                name=name,
                display_name=str(entry.get("display_name") or name),
                enabled=enabled,
                status=status,
                note=str(entry.get("message") or ""),
                key_env=key_env,
                base_url_env=base_url_env,
                base_url=str(entry.get("base_url") or ""),
                key_in_process=_value_present(environ, key_env),
                key_in_env_file=bool(key_env and key_env in names),
                env_file=str(target),
                env_file_exists=target.exists(),
                models=models,
                steps=tuple(steps),
                verify_command=verify,
                ready=status == "ready",
            )
        )
    # 能用的排前面，未启用的排后面
    items.sort(key=lambda item: (not item.ready, not item.enabled, item.name))
    return items


def summary_of(items: list[ProviderGuide], *, env_file: Path | None = None) -> dict[str, Any]:
    target = env_file or ENV_FILE
    ready = [item.name for item in items if item.ready]
    missing = [item.name for item in items if item.enabled and not item.ready]
    if not items:
        note = "config/models.yaml 里还没有登记任何服务商。"
    elif not ready:
        note = "当前一个服务商都用不了：按下面每一行的步骤做，改完重启服务再跑验证命令。"
    elif missing:
        note = f"{len(ready)} 个服务商可用；已启用但还差东西的有：{'、'.join(missing)}。"
    elif len(ready) == len(items):
        note = f"{len(ready)} 个服务商都可用。"
    else:
        note = f"只有 {len(ready)} 个服务商可用；其余未启用（要用就在 config/models.yaml 里打开）。"
    return {
        "env_file": str(target),
        "env_file_exists": target.exists(),
        "example_file": str(ENV_EXAMPLE),
        "models_file": str(MODELS_FILE),
        "restart_hint": "改 .env 之后必须重启服务（scripts\\stop.ps1 → scripts\\start.ps1）：.env 只在进程启动时读一次。",
        "note": note,
        "items": [item.as_dict() for item in items],
    }


__all__ = ["ProviderGuide", "ENV_FILE", "env_file_keys", "guide_of", "summary_of"]
