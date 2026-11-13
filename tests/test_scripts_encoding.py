"""Windows 脚本与运维脚本的护栏测试。

这些断言看起来"太琐碎"，但每一条都对应过真实故障：

* PowerShell 5.1 按系统 ANSI 代码页解析 **无 BOM** 的 .ps1，中文机器上是 GBK，
  脚本里的中文注释/提示会变乱码甚至语法错误 → 必须 UTF-8 **带 BOM**。
* 编辑工具保存时默认不带 BOM，所以这条测试是唯一能拦住"改完就坏"的东西。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PS1_FILES = ["scripts/start.ps1", "scripts/watchdog.ps1", "scripts/stop.ps1"]
BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize("relative", PS1_FILES)
def test_powershell_scripts_are_utf8_with_bom(relative):
    path = PROJECT_ROOT / relative
    assert path.exists(), f"缺少 {relative}"
    raw = path.read_bytes()
    assert raw.startswith(BOM), f"{relative} 必须存成 UTF-8 带 BOM，否则中文机器上会乱码"
    text = raw[len(BOM) :].decode("utf-8")
    assert "finance_media.db" in text, "受保护库的名字应当出现在提示里（说明真的防了这一手）"


@pytest.mark.parametrize("relative", PS1_FILES)
def test_powershell_scripts_refuse_the_protected_database(relative):
    text = (PROJECT_ROOT / relative).read_bytes()[len(BOM) :].decode("utf-8")
    if relative.endswith("stop.ps1"):
        # 停止脚本不碰库，但也不能出现删库/迁移命令
        assert "Remove-Item" not in text
        return
    assert "exit 2" in text, "碰到真实开发库必须非零退出，而不是继续跑"


def test_start_and_stop_share_a_default_port():
    start = (PROJECT_ROOT / "scripts/start.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    stop = (PROJECT_ROOT / "scripts/stop.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    assert "[int]$Port = 8000" in start
    assert "[int[]]$Port = @(8000)" in stop


def test_watchdog_has_backoff_and_restart_ceiling():
    text = (PROJECT_ROOT / "scripts/watchdog.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    assert "MaxRestarts" in text
    assert "[Math]::Min($delay * 2, 60)" in text, "重启必须退避且封顶，否则崩了会刷爆日志"
    assert "HealthTimeoutSeconds" in text, "进程活着但 /health 不通也算失败"


# --- python 运维脚本 -------------------------------------------------------


OPS_SCRIPTS = [
    "backend/scripts/backup_db.py",
    "backend/scripts/restore_db.py",
    "backend/scripts/doctor.py",
    "backend/scripts/start.ps1",
]


@pytest.mark.parametrize(
    "relative",
    ["backend/scripts/backup_db.py", "backend/scripts/restore_db.py", "backend/scripts/doctor.py"],
)
def test_ops_scripts_parse_and_expose_main(relative):
    path = PROJECT_ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "main" in functions, f"{relative} 应当提供 main(argv) 以便 __main__ 调用"


@pytest.mark.parametrize(
    "relative",
    ["backend/scripts/backup_db.py", "backend/scripts/restore_db.py"],
)
def test_backup_scripts_mention_the_protected_suffix(relative):
    text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
    assert "finance_media.db" in text, "备份/恢复都必须认得受保护库，不能一把梭覆盖"
