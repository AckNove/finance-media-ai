"""SQLite WAL 模式的**显式**初始化入口（M2.0 专项整改）。

为什么单独一个模块，而不是写在连接事件里：

``PRAGMA journal_mode=WAL`` 是**持久化到数据库文件头**的属性——只要在某个库上执行过一次，
该库以后每次打开都是 WAL（并生成 ``-wal`` / ``-shm`` 附属文件）。把它放进
``build_engine()`` 的 ``connect`` 事件，意味着"任何一次连接都会被悄悄改写库文件"：
跑一次 ``/api/system/status``、跑一次测试、import 一下应用，都会改。这既不可审计，
也会让"我只读一下"变成"我改了你的库"。

因此本模块的规则是：

1. 连接事件只做**连接级**参数（``foreign_keys`` / ``busy_timeout`` / ``synchronous``），
   这些不改库文件；
2. WAL 只能通过本模块的 :func:`enable_wal` **显式调用**；
3. 调用必须指明目标数据库，并且：
   - 只接受文件型 SQLite（拒绝 ``:memory:`` 与非 SQLite URL）；
   - 目标文件必须已存在（不新建、不猜测路径）；
   - 默认拒绝对真实开发库 ``data/finance_media.db`` 操作，需 ``allow_development_db=True``；
   - 返回修改前后的 ``journal_mode``，便于审计与演练。

用法::

    from backend.app.database.wal import enable_wal

    result = enable_wal("sqlite:///C:/tmp/t.db")          # 临时库
    print(result.before, "->", result.after)

    # 真实开发库必须显式放行（M2.0 阶段禁止执行）
    enable_wal("sqlite:///C:/Users/25803/finance-media-ai/data/finance_media.db",
               allow_development_db=True)

命令行入口见 ``backend/scripts/enable_wal.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.app.core.config import PROJECT_ROOT

#: 显式初始化时希望得到的日志模式
WAL_JOURNAL_MODE = "WAL"

#: SQLite 默认（回滚日志）模式：未显式初始化时库文件应保持这个值
DEFAULT_JOURNAL_MODE = "delete"

#: 受保护的真实开发库（相对仓库根）；M2.0 阶段禁止对它做任何结构或模式变更
PROTECTED_DB_SUFFIX = "data/finance_media.db"


class WalInitializationError(RuntimeError):
    """显式初始化被拒绝。``reason`` 是稳定的机器可读原因码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class WalResult:
    """一次显式初始化的结果（frozen：调用方不能改这份审计记录）。"""

    database_url: str
    target_path: str | None
    before: str | None
    after: str | None
    changed: bool
    already_enabled: bool
    dry_run: bool
    protected: bool

    def describe(self) -> str:
        if self.dry_run:
            return (
                f"dry-run target={self.target_path} protected={self.protected} "
                f"(未连接、未修改)"
            )
        return (
            f"{self.before} -> {self.after} target={self.target_path} "
            f"changed={self.changed} already_enabled={self.already_enabled}"
        )


def is_protected_database_url(database_url: str) -> bool:
    """目标是否指向受保护的真实开发库（按路径后缀判定，容忍相对/绝对与反斜杠）。"""
    normalized = database_url.replace("\\", "/").lower()
    return normalized.endswith(PROTECTED_DB_SUFFIX)


def resolve_sqlite_target(database_url: str) -> Path:
    """把 SQLite URL 解析成绝对文件路径；不合法就抛 :class:`WalInitializationError`。

    只做解析，不触碰文件系统内容（除了 ``resolve()`` 的字符串规范化）。
    """
    try:
        url = make_url(database_url)
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都归为配置错误
        raise WalInitializationError("invalid_url", f"无法解析数据库 URL：{database_url}") from exc

    if not url.drivername.startswith("sqlite"):
        raise WalInitializationError(
            "not_sqlite",
            f"只支持 SQLite（当前 dialect={url.drivername}）：WAL 是 SQLite 特有的日志模式",
        )

    database = url.database
    if database is None or database in {"", ":memory:"}:
        raise WalInitializationError(
            "in_memory",
            "内存库没有可持久化的 journal_mode，拒绝初始化（请给出真实文件路径）",
        )

    path = Path(database)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def enable_wal(
    database_url: str,
    *,
    allow_development_db: bool = False,
    dry_run: bool = False,
) -> WalResult:
    """显式把目标 SQLite 库切到 WAL；返回前后对照。

    ``dry_run=True`` 时**完全不连接数据库**，只回报将要操作的目标与保护标记。
    """
    target = resolve_sqlite_target(database_url)
    protected = is_protected_database_url(database_url) or is_protected_database_url(
        target.as_posix()
    )

    if protected and not allow_development_db:
        raise WalInitializationError(
            "protected_database",
            f"{target} 是受保护的真实开发库，需要显式 allow_development_db=True",
        )
    if not target.exists():
        raise WalInitializationError(
            "missing_database",
            f"目标库不存在：{target}（本模块只处理已存在的库，不新建文件）",
        )
    if not target.is_file():
        raise WalInitializationError("not_a_file", f"目标不是文件：{target}")

    if dry_run:
        return WalResult(
            database_url=database_url,
            target_path=str(target),
            before=None,
            after=None,
            changed=False,
            already_enabled=False,
            dry_run=True,
            protected=protected,
        )

    # 刻意用不带任何事件监听器的裸引擎：初始化只做这一件事
    engine = create_engine(f"sqlite:///{target.as_posix()}", future=True)
    try:
        with engine.connect() as connection:
            before = str(connection.execute(text("PRAGMA journal_mode")).scalar())
            applied = str(
                connection.execute(text(f"PRAGMA journal_mode={WAL_JOURNAL_MODE}")).scalar()
            )
            after = str(connection.execute(text("PRAGMA journal_mode")).scalar())
    finally:
        engine.dispose()

    before_lower = before.lower()
    after_lower = after.lower()
    if after_lower != WAL_JOURNAL_MODE.lower():
        raise WalInitializationError(
            "switch_failed",
            f"切换失败：请求 {WAL_JOURNAL_MODE}，实际 {after!r}（applied={applied!r}）",
        )

    return WalResult(
        database_url=database_url,
        target_path=str(target),
        before=before_lower,
        after=after_lower,
        changed=before_lower != after_lower,
        already_enabled=before_lower == WAL_JOURNAL_MODE.lower(),
        dry_run=False,
        protected=protected,
    )
