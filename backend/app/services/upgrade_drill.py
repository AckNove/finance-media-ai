"""跨版本升级演练：拿一份"老库"真的升到当前版本，看数据丢没丢、回填对不对。

这个模块回答的是日常最怕的那个问题：**线上/开发库停在旧版本，升上来会不会出事。**

现有的迁移测试是按批次验的（每批建哪些表、降级能不能删干净、部分索引是不是真的部分），
但它们都从空库起步、每一批只验自己那一摊。真正的问题往往出在"**带数据**整条链升上来"：

- 老表里的行还在不在？值有没有被悄悄改写？
- 新加的列在已有行上是 `NULL` 还是给了默认值？
- 回填（把 `topics.column_id` 变成 `topic_collections` 的一行、把旧 `content_status` 映射成新
  `status`）对已有行真的执行了吗？
- 升完还能降回去再升回来吗（迁移是不是可重入）？

做法是：先把库升到**起点版本**（默认初版 `de8148733a9b`），塞进一批"那个年代才会有"的行，
再升级到 head，逐项核对。也可以给一份现成的老库（`source_db`），它会**只读复制**一份来演练，
原文件不会被打开写入。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from backend.app.core.config import PROJECT_ROOT
from backend.app.models.enums import (
    AgentStatus,
    AudienceLevel,
    ContentStatus,
    CredibilityLevel,
    KnowledgeDomain,
    LicenseStatus,
    ResearchStatus,
    ScriptStatus,
    SourceType,
    StatementNature,
    SupportType,
    VerificationStatus,
    VideoTemplate,
)

#: 演练的起点版本：M2.0 之后的初版 schema（19 张表，V5 阶段就是从这里起步的）
BASE_REVISION = "de8148733a9b"
#: 目标版本
HEAD = "head"
#: 这条链升完之后应有的业务表数量（31 张表 + alembic_version）
EXPECTED_BUSINESS_TABLES = 31
#: 绝不允许被写入的库（可以只读复制来演练，但绝不改它）
PROTECTED_DB_SUFFIX = "data/finance_media.db"

_STAMP = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class DrillError(RuntimeError):
    """演练根本没法开始（参数不合法、源库不存在等）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Step:
    """一步的结论。`ok` 为假表示这一步没通过，后面会继续跑完再汇总。"""

    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class DrillReport:
    database: str
    from_revision: str = BASE_REVISION
    to_revision: str = HEAD
    source_db: str = ""
    steps: list[Step] = field(default_factory=list)

    @property
    def failures(self) -> list[Step]:
        return [step for step in self.steps if not step.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        head = f"{self.from_revision} → {self.to_revision}，共 {len(self.steps)} 步"
        if self.ok:
            return f"升级演练通过：{head}"
        names = "、".join(step.name for step in self.failures)
        return f"升级演练没通过：{head}；卡在 {names}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "source_db": self.source_db,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "ok": self.ok,
            "summary": self.summary(),
            "steps": [step.as_dict() for step in self.steps],
        }


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = SimpleNamespace(x=[f"db_url=sqlite:///{db_path.as_posix()}"])
    return config


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def _revision_of(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return str(rows[0]["version_num"]) if rows else ""


def head_revision(db_path: Path) -> str:
    script = ScriptDirectory.from_config(_alembic_config(db_path))
    revision = script.get_current_head()
    if revision is None:  # pragma: no cover - 仓库里总有迁移
        raise DrillError("no_head", "迁移目录里没有 head")
    return str(revision)


def is_protected(path: Path) -> bool:
    """判断这是不是那条不许写的真实开发库。"""

    normalised = str(path.as_posix()).replace("\\", "/")
    return normalised.endswith(PROTECTED_DB_SUFFIX)


def _seed_rows() -> dict[str, dict[str, Any]]:
    """一批"初版 schema 那个年代才会有"的行。

    只写那一版就存在的列——这正是重点：老库不可能有新列，升上来以后新列得由迁移自己补。
    枚举值一律取 `backend.app.models.enums` 里的真值，避免"演练数据自己就违反 CHECK 约束"
    这种假失败。
    """

    stamp = _STAMP.isoformat(sep=" ")
    return {
        "topic_columns": {
            "id": 1,
            "code": "macro",
            "name": "宏观与政策",
            "display_order": 1,
            "created_at": stamp,
            "updated_at": stamp,
        },
        "topics": {
            "id": 1,
            "slug": "drill-topic",
            "title": "演练用选题：商业银行靠什么赚钱",
            "description": "升级演练塞进去的旧行",
            # 关键点：旧库里靠 content_status 表示进度，新列 status 是 B1 才加的
            "content_status": ContentStatus.PUBLISHED.value,
            "knowledge_domain": KnowledgeDomain.GENERAL.value,
            "audience_level": AudienceLevel.BEGINNER.value,
            "priority": 3,
            "football_analogy_required": 0,
            "research_status": ResearchStatus.COMPLETED.value,
            "column_id": 1,
            "created_at": stamp,
            "updated_at": stamp,
        },
        "sources": {
            "id": 1,
            "title": "演练用来源：国家金融监督管理总局 2024 年四季度银行业主要监管指标",
            "url": "https://example.com/drill-source",
            "publisher": "国家金融监督管理总局",
            "source_type": SourceType.OFFICIAL_STATISTICS.value,
            "accessed_at": stamp,
            "jurisdiction": "cn",
            "credibility_level": CredibilityLevel.HIGH.value,
            "license_status": LicenseStatus.PUBLIC_DOMAIN.value,
            "language": "zh",
            "created_at": stamp,
            "updated_at": stamp,
        },
        "research_reports": {
            "id": 1,
            "title": "演练用报告",
            "topic_id": 1,
            "summary": "这句话必须在升级之后还能一个字不差地读出来。",
            "findings": "[]",
            "limitations": "[]",
            "verification_status": VerificationStatus.UNVERIFIED.value,
            "jurisdiction": "cn",
            "version": 1,
            "created_at": stamp,
            "updated_at": stamp,
        },
        "evidence": {
            "id": 1,
            "source_id": 1,
            "claim": "演练用结论：净息差在收窄",
            "original_excerpt": "原文摘录也要原样保留。",
            "support_type": SupportType.SUPPORTS.value,
            "claim_nature": StatementNature.FACT.value,
            "verification_status": VerificationStatus.VERIFIED.value,
            "research_report_id": 1,
            "created_at": stamp,
            "updated_at": stamp,
        },
        "scripts": {
            "id": 1,
            "topic_id": 1,
            "research_report_id": 1,
            "title": "演练用脚本",
            "template": VideoTemplate.FINANCIAL_MECHANISM.value,
            "version": 1,
            "status": ScriptStatus.APPROVED.value,
            "body": "演练用正文。",
            "created_at": stamp,
            "updated_at": stamp,
        },
        "agent_runs": {
            "id": 1,
            "task_id": "drill-task-1",
            "agent_name": "financial_researcher",
            "task_type": "research.collect",
            "status": AgentStatus.SUCCEEDED.value,
            "requested_at": stamp,
            "created_at": stamp,
            "updated_at": stamp,
        },
    }


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> tuple[int, list[str]]:
    """按表里真实存在的列插入。返回（写入行数，被跳过的列名）。"""

    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if not existing:
        raise DrillError("missing_table", f"起点版本里没有 {table} 这张表")
    usable = {key: value for key, value in values.items() if key in existing}
    skipped = sorted(set(values) - set(usable))
    columns = ", ".join(usable)
    placeholders = ", ".join("?" for _ in usable)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608 - 列名来自 PRAGMA
        tuple(usable.values()),
    )
    return 1, skipped


def _seed(db_path: Path, report: DrillReport) -> dict[str, int]:
    """把演练数据塞进"老库"，返回每张表写了几行。"""

    written: dict[str, int] = {}
    skipped_columns: dict[str, list[str]] = {}
    with _connect(db_path) as connection:
        for table, values in _seed_rows().items():
            rows, skipped = _insert(connection, table, values)
            written[table] = rows
            if skipped:
                skipped_columns[table] = skipped
        connection.commit()
    detail = "，".join(f"{table} {rows} 行" for table, rows in written.items())
    if skipped_columns:
        # 不是错：老库本来就没有新列，跳过是预期行为，但写出来才叫如实
        detail += "；起点版本没有的列已跳过：" + "、".join(
            f"{table}.{','.join(columns)}" for table, columns in skipped_columns.items()
        )
    report.steps.append(Step("seed", True, detail))
    return written


def _check_data_preserved(db_path: Path, seeded: dict[str, int]) -> tuple[bool, str]:
    problems: list[str] = []
    with _connect(db_path) as connection:
        for table, expected in seeded.items():
            actual = _row_count(connection, table)
            if actual != expected:
                problems.append(f"{table} 升前 {expected} 行、升后 {actual} 行")
        summary = connection.execute("SELECT summary FROM research_reports WHERE id = 1").fetchone()
        if summary is None or summary["summary"] != "这句话必须在升级之后还能一个字不差地读出来。":
            problems.append("research_reports.summary 被改写了或读不出来")
        excerpt = connection.execute("SELECT original_excerpt FROM evidence WHERE id = 1").fetchone()
        if excerpt is None or excerpt["original_excerpt"] != "原文摘录也要原样保留。":
            problems.append("evidence.original_excerpt 被改写了或读不出来")
        title = connection.execute("SELECT title FROM topics WHERE id = 1").fetchone()
        if title is None or not str(title["title"]).startswith("演练用选题"):
            problems.append("topics.title 被改写了或读不出来")
    if problems:
        return False, "；".join(problems)
    return True, f"{len(seeded)} 张表的行数与关键字段值都没变"


def _check_backfill(db_path: Path) -> tuple[bool, str]:
    """回填是这一轮最要紧的检查：老行得被迁移翻译成新模型。"""

    problems: list[str] = []
    detail: list[str] = []
    with _connect(db_path) as connection:
        tables = _table_names(connection)
        if "topic_collections" not in tables:
            return False, "没有 topic_collections 表，B1 没落地"
        mapping = connection.execute(
            "SELECT topic_id, column_id, is_primary FROM topic_collections WHERE topic_id = 1"
        ).fetchone()
        if mapping is None:
            problems.append("老行的 column_id=1 没有回填成 topic_collections 的一行")
        elif int(mapping["is_primary"]) != 1:
            problems.append("回填出来的 topic_collections 不是主栏目（is_primary 应为 1）")
        else:
            detail.append(f"column_id={mapping['column_id']} → topic_collections(is_primary=1)")

        status = connection.execute("SELECT status FROM topics WHERE id = 1").fetchone()
        if status is None or status["status"] != "content_produced":
            problems.append(
                f"content_status=published 应映射成 status=content_produced，实际是{status['status'] if status else '空'}"
            )
        else:
            detail.append("content_status=published → status=content_produced")

        origin = connection.execute("SELECT origin_type FROM topics WHERE id = 1").fetchone()
        if origin is None or origin["origin_type"] != "manual":
            problems.append(f"新列 origin_type 没有给已有行默认值，实际是 {origin['origin_type'] if origin else '空'}")
        else:
            detail.append("新列 origin_type 默认 manual")
    if problems:
        return False, "；".join(problems)
    return True, "；".join(detail)


def _check_integrity(db_path: Path) -> tuple[bool, str]:
    with _connect(db_path) as connection:
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = _table_names(connection)
    problems: list[str] = []
    if violations:
        problems.append(f"外键检查有 {len(violations)} 条违规")
    if integrity != "ok":
        problems.append(f"integrity_check={integrity}")
    business = len(tables - {"alembic_version"})
    if business != EXPECTED_BUSINESS_TABLES:
        problems.append(f"业务表应是 {EXPECTED_BUSINESS_TABLES} 张，实际 {business} 张")
    if problems:
        return False, "；".join(problems)
    return True, f"{business} 张业务表，外键与完整性检查都干净"


def _check_revision(db_path: Path) -> tuple[bool, str]:
    expected = head_revision(db_path)
    with _connect(db_path) as connection:
        actual = _revision_of(connection)
    if actual != expected:
        return False, f"入库版本是 {actual or '空'}，脚本 head 是 {expected}"
    return True, f"revision={actual}"


def _check_no_drift(db_path: Path) -> tuple[bool, str]:
    """`alembic check`：迁移之后的库结构必须与 ORM 定义一致（否则下次自动生成会乱）。"""

    try:
        command.check(_alembic_config(db_path))
    except Exception as error:  # noqa: BLE001 - CommandError 之外也可能有其他驱动异常
        return False, f"结构与 ORM 定义不一致：{type(error).__name__}: {error}"
    return True, "结构与 ORM 定义一致（没有待生成的迁移）"


def _check_roundtrip(db_path: Path, seeded: dict[str, int]) -> tuple[bool, str]:
    """降回起点再升回来：迁移得可重入，老数据也不能少。"""

    config = _alembic_config(db_path)
    command.downgrade(config, BASE_REVISION)
    with _connect(db_path) as connection:
        tables_after_down = _table_names(connection)
        dropped = {"topic_collections", "research_tasks", "model_calls"} & tables_after_down
        if dropped:
            return False, f"降回 {BASE_REVISION} 之后这些表还在：{'、'.join(sorted(dropped))}"
        survivors = {
            table: _row_count(connection, table)
            for table in seeded
            if table in tables_after_down
        }
    lost = {table: count for table, count in survivors.items() if count != seeded[table]}
    if lost:
        detail = "、".join(f"{table} 少了 {seeded[table] - count} 行" for table, count in lost.items())
        return False, f"降级把老数据弄丢了：{detail}"

    command.upgrade(config, HEAD)
    preserved, detail = _check_data_preserved(db_path, seeded)
    if not preserved:
        return False, f"降级再升级之后数据对不上：{detail}"
    backfilled, backfill_detail = _check_backfill(db_path)
    if not backfilled:
        return False, f"降级再升级之后回填没重做：{backfill_detail}"
    return True, f"降回 {BASE_REVISION} 再升到 head，{len(survivors)} 张老表的行数不变、回填重做成功"


def run_drill(
    db_path: Path | str,
    *,
    source_db: Path | str | None = None,
    from_revision: str = BASE_REVISION,
    to_revision: str = HEAD,
    seed: bool = True,
    roundtrip: bool = True,
    drop_tables_on_finish: bool = False,
) -> DrillReport:
    """跑一遍升级演练。**只写 ``db_path`` 这个文件**；``source_db`` 只被只读复制。"""

    target = Path(db_path)
    report = DrillReport(
        database=target.as_posix(),
        from_revision=from_revision,
        to_revision=to_revision,
        source_db=Path(source_db).as_posix() if source_db else "",
    )

    if source_db is not None:
        source = Path(source_db)
        if not source.is_file():
            raise DrillError("source_missing", f"源库不存在：{source}")
        if target.exists():
            raise DrillError("target_exists", f"演练目标已存在，换个路径或先删掉：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copy2(source, target)
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        note = "（受保护开发库，只读复制）" if is_protected(source) else ""
        report.steps.append(
            Step("copy_source", before == after, f"{source} → {target}{note}", )
        )
        with _connect(target) as connection:
            current = _revision_of(connection)
        report.steps.append(Step("source_revision", bool(current), f"源库版本：{current or '空'}"))
        from_revision = current or from_revision
        report.from_revision = from_revision
    else:
        if target.exists():
            raise DrillError("target_exists", f"演练目标已存在，换个路径或先删掉：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        command.upgrade(_alembic_config(target), from_revision)
        report.steps.append(Step("prepare", True, f"空库升到起点版本 {from_revision}"))

    if seed:
        seeded = _seed(target, report)
    else:
        seeded = {}

    engine = create_engine(f"sqlite:///{target.as_posix()}")
    try:
        empty = len(inspect(engine).get_table_names()) == 0
    finally:
        engine.dispose()
    if empty:  # pragma: no cover - 除非起点版本什么都没建
        raise DrillError("empty_database", "起点版本没有建出任何表，演练没有意义")

    command.upgrade(_alembic_config(target), to_revision)
    report.steps.append(Step("upgrade", True, f"{from_revision} → {to_revision} 执行完成"))

    for name, check in (
        ("revision", lambda: _check_revision(target)),
        ("integrity", lambda: _check_integrity(target)),
        ("no_drift", lambda: _check_no_drift(target)),
    ):
        ok, detail = check()
        report.steps.append(Step(name, ok, detail))

    if not seeded:
        report.steps.append(Step("backfill", True, "没有塞数据（--no-seed），回填无从验，跳过"))
    elif from_revision == BASE_REVISION:
        ok, detail = _check_backfill(target)
        report.steps.append(Step("backfill", ok, detail))
    else:
        # 源库本来就过了 B1：回填在它身上早就发生过，这一趟没有再跑，
        # 硬查会报"column_id 没回填"这种假问题。真实的回填验证交给下面的 roundtrip
        # （降回初版 → 再升上来，那时回填会真的重跑一遍）。
        report.steps.append(
            Step(
                "backfill",
                True,
                f"跳过：源库已是 {from_revision}，回填在源库里发生过；这一项由 roundtrip 覆盖",
            )
        )

    if seeded:
        ok, detail = _check_data_preserved(target, seeded)
        report.steps.append(Step("data_preserved", ok, detail))
    else:
        report.steps.append(Step("data_preserved", True, "没有塞数据（--no-seed），跳过"))

    if roundtrip and seeded:
        ok, detail = _check_roundtrip(target, seeded)
        report.steps.append(Step("roundtrip", ok, detail))

    if drop_tables_on_finish:  # pragma: no cover - 只给人手工用
        with _connect(target) as connection:
            for table in _table_names(connection):
                if table != "alembic_version":
                    connection.execute(f"DROP TABLE {table}")
            connection.commit()

    return report
