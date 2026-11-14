"""从一个空库开始，验到"能建选题、能建任务、能跑一格"——一条命令。

## 为什么需要这个脚本

`doctor.py` 查的是**环境**（Python、依赖、ffmpeg、配置能不能读）；
`upgrade_drill.py` 查的是**迁移**（老库能不能升上来）；
`daily_check.py` 查的是**运行中**的库有没有欠账。

缺的是第一脚：**一个空库文件能不能长成一个能用的库**。这条路上有四件事会在新手面前
依次翻车——迁移没跑、栏目没种子（于是建选题 422）、启动自检不过、worker 抢不到活。
每一步单独都有人测过，但"从零按顺序走一遍"没人走过。

## 它怎么保证不花钱、不联网

- 进程里**删掉**所有模型密钥环境变量，并设置 `FM_SKIP_DOTENV=1`：`research.plan`
  于是走规则拆词（`planner="keywords"`），不产生任何调用与费用；
- 走到 `research.collect` 之前**把检索开关临时关掉**（`features.search`），
  这一步的预期结果就是 `blocked: feature_disabled`——它证明链路在"没开搜索"时
  会**如实停下**，而不是假装成功。想验真检索请用 `python -m backend.scripts.flow_check`。

## 用法

```powershell
python -m backend.scripts.bootstrap_check                 # 用临时库，跑完即删
python -m backend.scripts.bootstrap_check --keep          # 保留库文件，方便自己接着点
python -m backend.scripts.bootstrap_check --json          # 机器可读
```

退出码：0 全部通过 / 1 有步骤失败 / 2 目标库不可用（受保护库、非法 URL）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, inspect, text

from backend.app.core.config import PROJECT_ROOT, clear_settings_cache

#: 受保护的真实开发库：只读，任何脚本都不许拿它做演练
PROTECTED_DB_SUFFIX = "data/finance_media.db"

#: 模型密钥环境变量：脚本会**从进程里删掉**它们，保证一次都不花钱
MODEL_KEY_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
)

#: 起点版本该有的表数（迁移到 head 之后）
#: 这里不写死表数——由迁移自己决定；只在报告里如实打印


@dataclass
class Step:
    """一步的结论。``ok=None`` 表示"按预期没有发生"，不算失败。"""

    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class BootstrapReport:
    database_url: str
    steps: list[Step] = field(default_factory=list)

    @property
    def failures(self) -> list[Step]:
        return [step for step in self.steps if not step.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        head = "空库到能用：全部通过" if self.ok else "空库到能用：有步骤失败"
        lines = [f"{head}（{len(self.steps)} 步，失败 {len(self.failures)} 步）"]
        for step in self.steps:
            mark = "[OK  ]" if step.ok else "[FAIL]"
            lines.append(f"{mark} {step.name}: {step.detail}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_url": self.database_url,
            "ok": self.ok,
            "steps": [step.as_dict() for step in self.steps],
            "summary": self.summary(),
        }


def is_protected(database_url: str) -> bool:
    """受保护库判定：后缀匹配（容忍反斜杠与相对路径）。"""

    flat = database_url.replace("\\", "/").rstrip("/")
    return flat.endswith(PROTECTED_DB_SUFFIX)


def _alembic_config(database_url: str):
    from alembic import command
    from alembic.config import Config

    # `alembic.ini` 在仓库根（不在 backend/migrations 下），script_location 才指到 migrations
    ini = PROJECT_ROOT / "alembic.ini"
    config = Config(str(ini))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = SimpleNamespace(x=[f"db_url={database_url}"])
    return config, command


def _table_count(engine) -> int:
    return len([name for name in inspect(engine).get_table_names() if name != "alembic_version"])


def _revision_of(engine) -> str:
    with engine.connect() as connection:
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
    return str(row[0]) if row else ""


def _strip_model_keys() -> dict[str, str]:
    """从进程里删掉模型密钥并禁止 .env 注入——这个脚本一次都不许花钱。

    返回被删掉的键值，调用方负责在退出前原样放回去（这是别人的进程，不能顺手改坏）。
    """

    saved: dict[str, str] = {}
    for name in MODEL_KEY_ENV_VARS:
        value = os.environ.pop(name, None)
        if value is not None:
            saved[name] = value
    os.environ["FM_SKIP_DOTENV"] = "1"
    return saved


def _restore_model_keys(saved: dict[str, str]) -> None:
    """把 ``_strip_model_keys`` 拿走的键值放回去。"""

    for name, value in saved.items():
        os.environ[name] = value


def _select_column_id(client) -> tuple[int, str]:
    """从种子里挑一个栏目；顺带证明栏目接口能给前端用。"""

    response = client.get("/api/topics/columns")
    if response.status_code != 200:
        raise RuntimeError(f"/api/topics/columns -> {response.status_code} {response.text[:200]}")
    columns = response.json()
    if not isinstance(columns, list) or not columns:
        raise RuntimeError("栏目列表是空的：种子没跑成功")
    first = columns[0]
    return int(first["id"]), str(first.get("name") or first.get("code") or first["id"])


def run_bootstrap(
    database_url: str,
    *,
    seed: bool = True,
    drive_worker: bool = True,
    artifacts_root: Path | None = None,
) -> BootstrapReport:
    """按顺序走完"空库 → 能用"，返回逐步结论。"""

    report = BootstrapReport(database_url=database_url)
    url = database_url
    # 研究产物写到库文件旁边，不落到仓库的 content/research（那是真研究的目录）
    if artifacts_root is None:
        marker = "sqlite:///"
        target = url.split(marker)[-1] if marker in url else ""
        base = Path(target).parent if target else Path(tempfile.gettempdir())
        artifacts_root = base / "research"
    artifacts_root.mkdir(parents=True, exist_ok=True)

    # ---- 1. 建库并迁移到 head -------------------------------------------
    try:
        config, command = _alembic_config(url)
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        revision = _revision_of(engine)
        tables = _table_count(engine)
        report.steps.append(Step("migrate", True, f"迁移到 head：revision={revision}，业务表 {tables} 张"))
    except Exception as error:  # noqa: BLE001 - 每一步的失败都要变成报告里的一行
        report.steps.append(Step("migrate", False, f"{type(error).__name__}: {error}"))
        return report

    # ---- 2. 种子（栏目）--------------------------------------------------
    if not seed:
        report.steps.append(Step("seed", True, "跳过（--no-seed）"))
    else:
        try:
            from backend.scripts.seed_collections import seed_collections

            stats = seed_collections(engine)
            report.steps.append(
                Step(
                    "seed",
                    stats["total"] > 0 and stats["created"] + stats["unchanged"] == stats["total"],
                    f"栏目种子：{stats}",
                )
            )
        except Exception as error:  # noqa: BLE001
            report.steps.append(Step("seed", False, f"{type(error).__name__}: {error}"))
            return report

    # ---- 3. 把进程切到这份库，跑启动自检 ---------------------------------
    saved_keys = _strip_model_keys()
    saved_url = os.environ.get("DATABASE_URL")
    saved_skip = os.environ.get("FM_SKIP_DOTENV")
    os.environ["DATABASE_URL"] = url
    clear_settings_cache()
    try:
        from backend.app.database.session import dispose_engine

        dispose_engine()
        from backend.app.core.startup_checks import run_startup_checks
        from backend.app.main import create_app

        app = create_app()
        check = run_startup_checks(app)
        passed = sum(1 for result in check.results if result.ok)
        note = f"启动自检 {passed}/{len(check.results)} 项通过"
        if check.warnings:
            note += f"（{len(check.warnings)} 条提醒：{check.warnings[0].name}）"
        report.steps.append(Step("startup", check.ok, note + "；模型密钥已从进程移除" if saved_keys else note))
    except Exception as error:  # noqa: BLE001
        report.steps.append(Step("startup", False, f"{type(error).__name__}: {error}"))
        _restore_environment(saved_url, saved_skip, saved_keys)
        return report

    # ---- 4. 走真实 HTTP 接口：健康、栏目、建选题、建任务 -----------------
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            health = client.get("/health")
            status = client.get("/api/system/status")
            report.steps.append(
                Step(
                    "api",
                    health.status_code == 200 and status.status_code == 200,
                    f"/health={health.status_code}，/api/system/status={status.status_code}",
                )
            )

            column_id, column_name = _select_column_id(client)
            topic = client.post(
                "/api/topics",
                json={
                    "title": "空库验收：商业银行靠什么赚钱",
                    "description": "bootstrap_check 建的选题，用来验证从零开始这条路",
                    "column_id": column_id,
                },
            )
            report.steps.append(
                Step(
                    "topic",
                    topic.status_code == 201,
                    f"建选题 -> {topic.status_code}（栏目 {column_id}·{column_name}）"
                    if topic.status_code == 201
                    else f"建选题 -> {topic.status_code} {topic.text[:200]}",
                )
            )
            if topic.status_code != 201:
                return report
            topic_id = int(topic.json()["id"])

            task = client.post(
                "/api/research-tasks",
                json={
                    "title": "商业银行靠什么赚钱（空库验收）",
                    "topic_id": topic_id,
                    "objective": "商业银行靠什么赚钱？净息差收窄之后靠什么补？",
                },
            )
            task_ok = task.status_code == 201
            detail = f"建研究任务 -> {task.status_code}"
            if task_ok:
                payload = task.json()
                task_id = int(payload["id"])
                subtasks = payload.get("subtasks") or []
                detail = f"建研究任务 -> 201（task={task_id}，{len(subtasks)} 个子任务）"
            report.steps.append(Step("task", task_ok, detail if task_ok else detail + f" {task.text[:200]}"))
            if not task_ok:
                return report

            # ---- 5. worker 真跑：第一格规划（不花钱），第二格该如实停下 ----
            if drive_worker:
                report.steps.append(_drive_worker(task_id, artifacts_root=artifacts_root))
                report.steps.append(
                    _expect_honest_stop(client, task_id, artifacts_root=artifacts_root)
                )
            else:
                report.steps.append(Step("worker", True, "跳过（--no-worker）"))

            # ---- 6. 账要干净：一行模型调用都不许有 ----------------------
            report.steps.append(_check_no_spending(engine, client, task_id))
    except Exception as error:  # noqa: BLE001
        report.steps.append(Step("api", False, f"{type(error).__name__}: {error}"))
        return report
    finally:
        # 这是**别人的进程**：验完把环境变量与引擎恢复原状，别把调用方带跑偏
        _restore_environment(saved_url, saved_skip, saved_keys)

    return report


def _restore_environment(
    database_url: str | None, skip_dotenv: str | None, saved_keys: dict[str, str]
) -> None:
    """把 ``DATABASE_URL`` / ``FM_SKIP_DOTENV`` / 模型密钥恢复成调用前的样子。"""

    from backend.app.database.session import dispose_engine

    if database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = database_url
    if skip_dotenv is None:
        os.environ.pop("FM_SKIP_DOTENV", None)
    else:
        os.environ["FM_SKIP_DOTENV"] = skip_dotenv
    _restore_model_keys(saved_keys)
    dispose_engine()
    clear_settings_cache()


def _describe_subtask(entry: Any) -> str:
    """worker 回执里的 ``completed`` 是子任务号列表（int），也容忍将来变成字典。"""

    if isinstance(entry, dict):
        return str(entry.get("task_type") or entry.get("subtask_id") or entry)
    return str(entry)


def _drive_worker(task_id: int, *, artifacts_root: Path | None = None) -> Step:
    """让 worker 推进一次。期望：规划阶段走规则拆词，0 次模型调用。"""

    try:
        from backend.app.execution.executor import AgentExecutor
        from backend.app.execution.worker import Worker
        from backend.app.execution.runtime import TaskRuntime
        from backend.app.core.config import get_execution_settings

        execution = get_execution_settings()
        runtime = TaskRuntime(
            lease_seconds=execution.lease_seconds,
            heartbeat_seconds=execution.heartbeat_seconds,
        )
        worker = Worker(
            runtime,
            AgentExecutor(artifacts_root=artifacts_root),
            name="bootstrap-check",
            poll_seconds=0,
        )
        report = worker.run_until_idle(max_iterations=1, task_id=task_id)
        if not report.completed:
            return Step("worker", False, f"一格都没干成：{report.as_dict()}")
        return Step(
            "worker",
            True,
            f"第一格完成（子任务 {_describe_subtask(report.completed[0])}）",
        )
    except Exception as error:  # noqa: BLE001
        return Step("worker", False, f"{type(error).__name__}: {error}")


def _blocked_reasons_of(snapshot: dict[str, Any]) -> list[str]:
    """从任务快照里收集"被挡住"的子任务原因。"""

    reasons: list[str] = []
    for item in snapshot.get("subtasks") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "blocked":
            continue
        reason = str(item.get("failure_reason") or item.get("reason") or "")
        if reason:
            reasons.append(reason)
    return reasons


def _expect_honest_stop(
    client, task_id: int, *, artifacts_root: Path | None = None, max_rounds: int = 4
) -> Step:
    """把检索开关临时关掉，一路往下推：期望采集阶段 blocked: feature_disabled。

    这一步验的是"**会如实停下**"——链路在没开搜索时不假装成功、不偷偷联网，
    也不是耗掉重试把任务判死。路上遇到人工闸门就按现实做法放行（``POST /resume``）。
    """

    try:
        from backend.app.capabilities.search import tool as search_tool

        original = search_tool._search_enabled
        search_tool._search_enabled = lambda: False  # type: ignore[assignment]
        gates_cleared = 0
        blocked: list[str] = []
        try:
            from backend.app.execution.executor import AgentExecutor
            from backend.app.execution.worker import Worker
            from backend.app.execution.runtime import TaskRuntime
            from backend.app.core.config import get_execution_settings

            execution = get_execution_settings()
            runtime = TaskRuntime(
                lease_seconds=execution.lease_seconds,
                heartbeat_seconds=execution.heartbeat_seconds,
            )
            worker = Worker(
                runtime,
                AgentExecutor(artifacts_root=artifacts_root),
                name="bootstrap-check",
                poll_seconds=0,
            )

            for _ in range(max_rounds):
                worker.run_until_idle(max_iterations=3, task_id=task_id)
                snapshot = client.get(f"/api/research-tasks/{task_id}").json()
                blocked = _blocked_reasons_of(snapshot)
                hit = [text for text in blocked if "feature_disabled" in text]
                if hit:
                    return Step(
                        "honest_stop",
                        True,
                        f"采集阶段如实停下（{hit[0][:80]}），任务状态 {snapshot.get('status')}"
                        f"，其间放行人工闸门 {gates_cleared} 次",
                    )

                status = str(snapshot.get("status") or "")
                if status == "paused":
                    resume = client.post(f"/api/research-tasks/{task_id}/resume")
                    if resume.status_code == 200:
                        gates_cleared += 1
                        continue
                    blocked.append(f"resume -> {resume.status_code} {resume.text[:120]}")
                    break
                if status in ("completed", "failed", "cancelled"):
                    break
        finally:
            search_tool._search_enabled = original  # type: ignore[assignment]

        snapshot = client.get(f"/api/research-tasks/{task_id}").json()
        return Step(
            "honest_stop",
            False,
            f"关掉检索开关后没有一步因它停下：blocked={blocked}，任务状态 {snapshot.get('status')}",
        )
    except Exception as error:  # noqa: BLE001
        return Step("honest_stop", False, f"{type(error).__name__}: {error}")


def _check_no_spending(engine, client, task_id: int) -> Step:
    """钱一分都不该花：``model_calls`` 必须 0 行，事件流必须有记录。"""

    try:
        with engine.connect() as connection:
            calls = connection.execute(text("SELECT COUNT(*) FROM model_calls")).scalar_one()
            events = connection.execute(
                text("SELECT COUNT(*) FROM task_events WHERE research_task_id = :task"),
                {"task": task_id},
            ).scalar_one()
        costs = client.get("/api/system/costs")
        ok = calls == 0 and events > 0 and costs.status_code == 200
        return Step(
            "no_spending",
            ok,
            f"model_calls={calls} 行，事件 {events} 条，/api/system/costs={costs.status_code}",
        )
    except Exception as error:  # noqa: BLE001
        return Step("no_spending", False, f"{type(error).__name__}: {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.bootstrap_check",
        description="从一个空库开始验到能用：迁移、种子、启动自检、建选题、建任务、worker 一格",
    )
    parser.add_argument("--database-url", default="", help="目标 SQLite 库（默认用临时文件，跑完即删）")
    parser.add_argument("--keep", action="store_true", help="保留临时库文件并打印路径")
    parser.add_argument("--no-seed", action="store_true", help="不跑栏目种子（默认跑）")
    parser.add_argument("--no-worker", action="store_true", help="不推 worker（默认推两格）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    created_dir: Path | None = None
    if args.database_url:
        url = args.database_url
        if is_protected(url):
            print(
                f"拒绝：{url} 是受保护的真实开发库。请换一个库（或让它默认用临时库）。",
                file=sys.stderr,
            )
            return 2
        if not url.startswith("sqlite:"):
            print(f"拒绝：目前只支持 SQLite（拿到的是 {url}）。", file=sys.stderr)
            return 2
    else:
        created_dir = Path(tempfile.mkdtemp(prefix="fmai_bootstrap_"))
        target = created_dir / "fresh.db"
        url = f"sqlite:///{target.as_posix()}"

    # ``--json`` 时把运行期间的日志赶去 stderr：应用启动会自己配 logging 到 stdout，
    # 不这么遮挡一下，JSON 前面就会混进 "INFO | backend.app.main | ..." 之类的行。
    real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    try:
        report = run_bootstrap(url, seed=not args.no_seed, drive_worker=not args.no_worker)
    finally:
        sys.stdout = real_stdout

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=1))
    else:
        print(report.summary())
        if created_dir is not None:
            if args.keep:
                print(f"库文件已保留：{created_dir}")
            else:
                import shutil

                shutil.rmtree(created_dir, ignore_errors=True)
                print("库文件是临时的，已删除（要看细节加 --keep）")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
