"""状态文件的护栏：让"台账"不至于悄悄烂掉。

不是什么都要靠测试守，但这四份文件是这个项目对"现在是什么样"的唯一交代，
一旦它们和代码/库脱节，后面每一轮判断都会建立在假前提上。所以只钉三条最稳的：

1. 四份状态文件 + 执行协议真的在、而且不是空壳；
2. `PROJECT_STATE.md` 里写着"怎么复算"的命令，并且它记的真实库 sha256 与实际一致
   （真实库是只读的，这条断言天然稳定；哪天有人真的迁移了开发库，这里会第一时间炸）；
3. `API 操作数 == ROUTE_CALLERS 条目数` —— 这是启动自检也在守的不变量。

数字会随开发变（表数、页面数、用例数），所以**不**在这里钉死具体数字，
那些交给 `python -m backend.scripts.state_measure --md` 现算。
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

STATE_FILES = (
    "PROJECT_STATE.md",
    "TASK_BACKLOG.md",
    "DECISION_LOG.md",
    "KNOWN_ISSUES.md",
    "docs/dev_protocol.md",
)


@pytest.mark.parametrize("name", STATE_FILES)
def test_state_file_exists_and_is_not_a_stub(name: str) -> None:
    path = ROOT / name
    assert path.is_file(), f"缺状态文件 {name}：协议要求每轮更新它"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 800, f"{name} 只有 {len(text)} 字，像是占位"
    assert "\ufffd" not in text, f"{name} 里有替换字符，编码坏了"


def test_project_state_tells_you_how_to_remeasure_itself() -> None:
    text = (ROOT / "PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "backend.scripts.state_measure" in text, "PROJECT_STATE.md 必须写明怎么复算数字"
    assert "matrix_lint" in text, "PROJECT_STATE.md 必须写明台账自查命令"


def test_recorded_dev_db_hash_matches_the_real_file() -> None:
    """真实开发库是只读的，所以这条断言不会因为开发而漂——漂了就是出事了。"""
    db = ROOT / "data" / "finance_media.db"
    if not db.is_file():
        pytest.skip("本机没有受保护的开发库（干净克隆），跳过")
    actual = hashlib.sha256(db.read_bytes()).hexdigest()
    text = (ROOT / "PROJECT_STATE.md").read_text(encoding="utf-8")
    assert actual in text, (
        "真实开发库的 sha256 与 PROJECT_STATE.md 记的不一致：要么有人迁移/写入了这个库"
        f"（实际 {actual}），要么台账没跟着更新"
    )


def test_api_operations_match_the_permission_manifest() -> None:
    from backend.app.api.router import ROUTE_CALLERS
    from backend.app.main import create_app

    spec = create_app().openapi()
    operations = sum(
        1
        for path in spec["paths"].values()
        for method in path
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )
    assert operations == len(ROUTE_CALLERS), "接口数与 ROUTE_CALLERS 不一致：新接口忘了登记调用者身份"
