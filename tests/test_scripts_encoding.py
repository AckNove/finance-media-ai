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
        # 停止脚本不碰库：允许删自己的 logs\worker.pid 这类运行时文件，
        # 但不许在任何 Remove-Item 里出现库/data 目录/递归删除。
        for line in text.splitlines():
            if "Remove-Item" not in line:
                continue
            lowered = line.lower()
            assert ".db" not in lowered, f"停止脚本不许删数据库文件：{line.strip()}"
            assert "data" not in lowered, f"停止脚本不许动 data 目录：{line.strip()}"
            assert "-recurse" not in lowered, f"停止脚本不许递归删除：{line.strip()}"
        return
    assert "exit 2" in text, "碰到真实开发库必须非零退出，而不是继续跑"


def test_start_and_stop_share_a_default_port():
    start = (PROJECT_ROOT / "scripts/start.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    stop = (PROJECT_ROOT / "scripts/stop.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    assert "[int]$Port = 8000" in start
    assert "[int[]]$Port = @(8000)" in stop


def test_start_script_runs_the_worker_by_default():
    """一键启动必须把后台执行器一起拉起来。

    真实故障：脚本只起 uvicorn，界面上建的研究任务就永远停在「已创建」——
    用户以为系统坏了，其实是没人去认领子任务。所以默认要起 worker，
    同时留一个 -NoWorker 给"只想看看页面"的场景。
    """
    text = (PROJECT_ROOT / "scripts/start.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    assert "[switch]$NoWorker" in text, "要留一个明确的开关，而不是让人改脚本"
    assert "backend.scripts.run_worker" in text
    assert "worker.pid" in text, "pid 要落盘，否则 stop 脚本无从下手"
    assert "started=" in text, "只记 pid 不够：Windows 会复用进程号，必须记启动时间"


def test_stop_script_only_kills_the_worker_it_started():
    text = (PROJECT_ROOT / "scripts/stop.ps1").read_bytes()[len(BOM) :].decode("utf-8")
    assert "worker.pid" in text
    assert "workerStarted" in text, "要把启动时间读回来；对不上就说明这号已经换了主人"
    assert "StartTime" in text, "判断是不是自己启动的那个进程"
    assert "$Force" in text, "拿不准时给一个人工确认的口子，而不是硬杀"


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
