"""操作手册当合同来验：手册里写的每条命令、每个接口，都得真的存在。

为什么要有这个文件
------------------
手册是给"第一次上手的人"看的，写错一个参数名，人就会在第一步失败，而且会怀疑是系统坏了。
手工核对会漏、会过期，所以这里把手册当合同：解析出 `backend.scripts.<名字> --flag` 与 `/api/...`
两类引用，逐个去问代码——脚本参数用 `--help` 的真实输出比对，接口用 `app.openapi()` 的真实路径比对。

只验"存在与否"，不验语义（命令跑起来对不对是别处的事）；这样它便宜、稳定，能长期挂在 CI 里。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "research_hub" / "v6_0_operator_manual.md"

# `backend.scripts.render_job --job my_film --narration --output output/final.mp4`
_SCRIPT_CALL = re.compile(r"backend\.scripts\.(?P<name>[a-z_][a-z0-9_]*)")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
# 手册里可能出现 `/api/research-tasks/1/resume`、`/api/system/daily?days=7`
_API_PATH = re.compile(r"/api/[A-Za-z0-9_{}\-/]+")


@pytest.fixture(scope="module")
def manual() -> str:
    return MANUAL.read_text(encoding="utf-8")


def _script_flags(manual: str) -> dict[str, set[str]]:
    """每个脚本名字 → 手册里给它写过的参数集合（按"到下一个脚本名或行尾"截取）。

    PowerShell 的续行是行尾反引号，多行命令很常见（`mix_bgm` 就是），所以先把续行接起来，
    否则第二行写的 `--volume` 会被漏掉——漏掉就等于没验。
    """

    joined: list[str] = []
    buffer = ""
    for line in manual.splitlines():
        if line.rstrip().endswith("`"):
            buffer += line.rstrip()[:-1] + " "
            continue
        joined.append((buffer + line).strip())
        buffer = ""
    if buffer:
        joined.append(buffer.strip())

    found: dict[str, set[str]] = {}
    for line in joined:
        names = list(_SCRIPT_CALL.finditer(line))
        if not names:
            continue
        for index, match in enumerate(names):
            start = match.end()
            end = names[index + 1].start() if index + 1 < len(names) else len(line)
            fragment = line[start:end]
            flags = set(_FLAG.findall(fragment))
            name = match.group("name")
            found.setdefault(name, set()).update(flags)
    return found


# `usage: research_task.py [-h] {plan,run,status,events,stats} ...`
_SUBCOMMANDS = re.compile(r"\{([a-z][a-z0-9_,-]*)\}", re.IGNORECASE)


def _run_help(name: str, *extra: str) -> str:
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", f"backend.scripts.{name}", *extra, "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return (done.stdout or "") + (done.stderr or "")


def _help_text(name: str) -> str:
    """脚本认哪些参数。

    有子命令的脚本（`research_task run --task-id 1`）把参数写在子命令上，顶层 `--help` 里只有
    `{plan,run,status,events,stats}` 这种占位，直接比对会把手册里正确的子命令参数判成"不认"。
    所以顶层帮助里出现子命令列表时，逐个再问一遍——多跑几次 `--help`，换来的是一条真合同。
    """

    top = _run_help(name)
    parts = [top]
    for group in _SUBCOMMANDS.findall(top):
        for sub in group.split(","):
            sub = sub.strip()
            if sub:
                parts.append(_run_help(name, sub))
    return "\n".join(parts)


def test_every_script_mentioned_in_the_manual_exists(manual: str) -> None:
    names = sorted({match.group("name") for match in _SCRIPT_CALL.finditer(manual)})
    assert names, "手册里应当至少提到一个 backend.scripts.* 命令"
    missing = [name for name in names if not (ROOT / "backend" / "scripts" / f"{name}.py").is_file()]
    assert missing == [], f"手册提到了不存在的脚本：{missing}"


def test_every_documented_flag_is_accepted(manual: str) -> None:
    parsed = _script_flags(manual)
    # 反空转：解析器坏掉时（比如全都没匹配上）这条用例必须红，而不是"0 个问题所以通过"
    assert len(parsed) >= 8, f"只解析到 {len(parsed)} 个脚本命令，手册解析大概率坏了"
    assert sum(len(flags) for flags in parsed.values()) >= 20, "解析到的参数太少，等于没验"

    problems: list[str] = []
    for name, flags in sorted(parsed.items()):
        help_text = _help_text(name)
        for flag in sorted(flags):
            if flag not in help_text:
                problems.append(f"{name} 不认手册写的 {flag}")
    assert problems == [], "手册与脚本参数不一致：\n" + "\n".join(problems)


def _openapi_templates() -> dict[str, set[str]]:
    app = create_app()
    templates: dict[str, set[str]] = {}
    for path, methods in app.openapi()["paths"].items():
        templates[path] = {method.upper() for method in methods}
    return templates


def _matches_template(documented: str, template: str) -> bool:
    """把 `/api/research-tasks/1/resume` 对上 `/api/research-tasks/{task_id}/resume`。"""

    left = documented.strip("/").split("/")
    right = template.strip("/").split("/")
    if len(left) != len(right):
        return False
    for piece, pattern in zip(left, right):
        if pattern.startswith("{") and pattern.endswith("}"):
            continue
        if piece != pattern:
            return False
    return True


def test_every_documented_api_path_exists(manual: str) -> None:
    templates = _openapi_templates()
    documented = sorted({match.group(0).rstrip(".,;") for match in _API_PATH.finditer(manual)})
    assert documented, "手册里应当至少提到一个 /api/ 接口"
    assert len(documented) >= 10, f"只解析到 {len(documented)} 个接口引用，手册解析大概率坏了"
    unknown: list[str] = []
    for path in documented:
        clean = path.split("?")[0].rstrip("/") or "/"
        if not any(_matches_template(clean, template) for template in templates):
            unknown.append(clean)
    assert unknown == [], f"手册提到了不存在的接口：{unknown}"


def test_manual_documents_the_verification_and_daily_commands(manual: str) -> None:
    """这两条是"每天/每次备份都要用"的命令，手册里必须有，否则等于没交付。"""

    for needle in ("backend.scripts.daily_check", "backend.scripts.backup_verify", "backend.scripts.doctor"):
        assert needle in manual, f"手册里少了 {needle}"
