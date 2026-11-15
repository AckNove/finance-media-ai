"""看看这个库的迁移版本对不对得上代码。

为什么需要这个：`scripts/start.ps1` 原本只在**库不存在**时才 `alembic upgrade head`。
库一旦存在就直接用——于是搬机器、换分支、拉新代码之后，工作库可能还停在两个版本以前，
服务照样起得来，只是某些页面会在查询时炸（历史真事：`no such column: topics.is_active`）。
"一键启动"应该保证"起来的这套代码和它用的库是同一版"，而不是让人自己发现。

本模块只做**只读判断**，不改库：

    python -m backend.scripts.db_revision --database-url sqlite:///data/finance_media.work.db

输出一行 JSON：`{"current": "...", "head": "...", "status": "current", "detail": "..."}`。

`status` 五种取值：

| status | 含义 | 该做什么 |
| --- | --- | --- |
| `fresh` | 库文件在，但还没有任何迁移记录（空库/刚建的空文件） | 跑 `alembic upgrade head` |
| `current` | 与代码同一个版本 | 直接用 |
| `behind` | 库比代码旧（缺迁移） | 跑 `alembic upgrade head` |
| `ahead` | 库比代码新（拿着旧代码连新库） | **不要自动降级**，换回对应的代码 |
| `unknown` | 迁移记录对不上任何已知版本 | 人工看，别猜 |

退出码：`current`/`fresh` → 0；`behind` → 3；`ahead`/`unknown` → 4；连不上库 → 5。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATUS_FRESH = "fresh"
STATUS_CURRENT = "current"
STATUS_BEHIND = "behind"
STATUS_AHEAD = "ahead"
STATUS_UNKNOWN = "unknown"

#: 这些状态下一键启动应该先把库升到 head 再起服务。
NEEDS_UPGRADE = frozenset({STATUS_FRESH, STATUS_BEHIND})
#: 这些状态下不允许自动处理，必须人来看。
REFUSE = frozenset({STATUS_AHEAD, STATUS_UNKNOWN})

EXIT_CODES = {
    STATUS_CURRENT: 0,
    STATUS_FRESH: 0,
    STATUS_BEHIND: 3,
    STATUS_AHEAD: 4,
    STATUS_UNKNOWN: 4,
}
EXIT_UNREACHABLE = 5


@dataclass(frozen=True)
class RevisionState:
    current: str | None
    head: str | None
    status: str
    detail: str = ""

    @property
    def needs_upgrade(self) -> bool:
        return self.status in NEEDS_UPGRADE

    @property
    def refuses(self) -> bool:
        return self.status in REFUSE

    def as_dict(self) -> dict[str, object]:
        return {
            "current": self.current,
            "head": self.head,
            "status": self.status,
            "detail": self.detail,
            "needs_upgrade": self.needs_upgrade,
            "refuses": self.refuses,
        }


def head_revision() -> str | None:
    """代码里的迁移头（不连库）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


def current_revision(database_url: str) -> str | None:
    """库里记的版本号。库文件不存在、没有 `alembic_version` 表都返回 `None`。"""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        engine = create_engine(database_url)
    else:
        path = url.database or ""
        if path and path != ":memory:" and not Path(path).is_absolute():
            path = str(PROJECT_ROOT / path)
        engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            return str(row[0]) if row else None
    except Exception:  # noqa: BLE001 - 表不存在/库不存在/空文件，都归为"还没版本"
        return None
    finally:
        engine.dispose()


def classify(current: str | None, head: str | None) -> RevisionState:
    """把「库里是什么版本」和「代码是什么版本」判成一个状态。纯函数，方便测。"""
    if head is None:
        return RevisionState(current, head, STATUS_UNKNOWN, "代码里读不到迁移头，alembic.ini 可能坏了")
    if current is None:
        return RevisionState(current, head, STATUS_FRESH, "库里还没有迁移记录（空库或空文件），需要建表")
    if current == head:
        return RevisionState(current, head, STATUS_CURRENT, "库与代码同一个版本")
    known, chain = _history()
    if current not in known:
        return RevisionState(
            current, head, STATUS_UNKNOWN,
            f"库里的版本 {current} 不在代码的迁移链上（可能来自别的分支/被手工改过）",
        )
    if head not in known:
        return RevisionState(
            current, head, STATUS_UNKNOWN,
            f"代码里的版本 {head} 不在迁移链上（alembic.ini 可能指错了目录）",
        )
    if known[current] < known[head]:
        missing = chain[known[current] + 1: known[head] + 1]
        return RevisionState(
            current, head, STATUS_BEHIND,
            "库比代码旧，缺 " + str(len(missing)) + " 个迁移：" + " → ".join(missing),
        )
    return RevisionState(
        current, head, STATUS_AHEAD,
        f"库比代码新（库在 {current}，代码只到 {head}）：请换回对应版本的代码，不要自动降级",
    )


def _history() -> tuple[dict[str, int], list[str]]:
    """整条迁移链上每个版本的位置（0 = 最旧），以及按位置排好的版本号列表。

    注意这里**不**按传进来的 head 截断：旧代码连新库时，库里的版本必须仍然能被认出来，
    否则会被误判成"不认识这个版本"而不是"库比代码新"。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    ordered = [revision.revision for revision in script.walk_revisions()]
    ordered.reverse()
    return {revision: index for index, revision in enumerate(ordered)}, ordered


def inspect(database_url: str) -> RevisionState:
    try:
        return classify(current_revision(database_url), head_revision())
    except Exception as error:  # noqa: BLE001 - 连不上库也要给出可读结论
        return RevisionState(None, None, STATUS_UNKNOWN, f"检查失败：{error}")


def default_database_url() -> str:
    """与 start.ps1 默认一致：独立的工作库，永不指向受保护开发库。"""
    return "sqlite:///" + (PROJECT_ROOT / "data" / "finance_media.work.db").as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="看看这个库的迁移版本对不对得上代码（只读）")
    parser.add_argument("--database-url", default="", help="默认是 data/finance_media.work.db")
    parser.add_argument("--json", action="store_true", help="输出 JSON（start.ps1 用这个）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    url = args.database_url or default_database_url()
    state = inspect(url)
    if args.json:
        print(json.dumps(state.as_dict(), ensure_ascii=False))
    else:
        print(f"库：{url}")
        print(f"库版本：{state.current or '（无）'}")
        print(f"代码版本：{state.head or '（读不到）'}")
        print(f"结论：{state.status} —— {state.detail}")
    return EXIT_CODES.get(state.status, EXIT_UNREACHABLE)


if __name__ == "__main__":
    sys.exit(main())
