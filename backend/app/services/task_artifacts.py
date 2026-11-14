"""一个研究任务"这一步到底做出了什么"——把产物目录里的文件列出来、能读的读一段。

为什么需要它：人工闸门（``topic_approval`` / ``verify.claims`` 之类）要求人看完再放行，
可产物以前只落在磁盘（``content/research/task-<id>*/``），界面上只看得见"子任务 succeeded"。
看不见东西的闸门等于盖章。

三条纪律：

1. **只读**：这里不写任何文件，也不调用模型；
2. **只在自己那格目录里**：``task-<id>`` 或 ``task-<id>-<指纹>``，路径里有 ``..`` 一律拒绝；
3. **不假装看全了**：超过上限的文件/字符如实标 ``truncated`` 与 ``skipped``，不静默截断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

#: 产物根（与 ``backend/app/execution/executor.py`` 的 ``DEFAULT_ARTIFACTS_ROOT`` 同源）
DEFAULT_ROOT = PROJECT_ROOT / "content" / "research"

#: 这些后缀当文本读；其余只报大小
TEXT_SUFFIXES = frozenset({".md", ".json", ".csv", ".txt", ".yaml", ".yml"})

#: 一次最多列多少个文件
MAX_FILES = 20

#: 每个文件最多读多少字符
MAX_TEXT_CHARS = 8000

#: 整个响应最多带多少字符（防止一次拉回半个库）
MAX_TOTAL_CHARS = 24000


class ArtifactError(RuntimeError):
    """产物读不了。``reason`` 是稳定码，前端按它决定话术。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ArtifactFile:
    """一个产物文件。``text`` 为空表示"没读"或"不是文本"。"""

    name: str
    relative_path: str
    byte_size: int
    modified_at: datetime | None
    kind: str
    text: str = ""
    truncated: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "byte_size": self.byte_size,
            "modified_at": self.modified_at.isoformat() if self.modified_at else None,
            "kind": self.kind,
            "text": self.text,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ArtifactBundle:
    """一个任务的产物清单。``note`` 用一句话说清"看没看全"。"""

    task_id: int
    directories: list[str] = field(default_factory=list)
    files: list[ArtifactFile] = field(default_factory=list)
    skipped: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "directories": list(self.directories),
            "files": [item.as_dict() for item in self.files],
            "skipped": self.skipped,
            "note": self.note,
        }


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else DEFAULT_ROOT


def candidate_dirs(task_id: int, *, root: Path | None = None) -> list[Path]:
    """``task-<id>`` 与 ``task-<id>-<指纹>`` 两种目录，**按修改时间新的排前面**。

    不要"精确名优先"：``task-<id>`` 往往是早期那一份（还没有指纹后缀时的产物），
    它不一定是最新一次研究的输出。
    """

    base = _root(root)
    if not base.is_dir():
        return []
    found = [base / f"task-{task_id}"]
    found.extend(item for item in base.glob(f"task-{task_id}-*") if item.is_dir())
    existing = [item for item in found if item.is_dir()]
    existing.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return existing


def _kind_of(suffix: str) -> str:
    lowered = suffix.lower()
    if lowered == ".md":
        return "markdown"
    if lowered == ".json":
        return "json"
    if lowered == ".csv":
        return "csv"
    if lowered in (".yaml", ".yml"):
        return "yaml"
    if lowered == ".txt":
        return "text"
    return "binary"


def _read_text(path: Path, *, budget: int) -> tuple[str, bool]:
    """读一段文本。超出 ``budget`` 就截断并如实标记。"""

    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= budget:
        return text, False
    return text[:budget], True


def _objective_of(directory: Path) -> str:
    """从 ``digest.json`` 读这次研究的目标；读不出来返回空串（早期阶段还没写）。"""

    digest = directory / "digest.json"
    if not digest.is_file():
        return ""
    try:
        payload = json.loads(digest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("objective") or "")
    return ""


def _pick_directory(
    directories: list[Path], objective: str
) -> tuple[Path, str]:
    """在可能来自别的库/别次研究的同名目录里，挑最像"这一条任务"的那份。

    为什么需要挑：产物目录名是 ``task-<id>`` 与 ``task-<id>-<指纹>``，**id 只在
    一个数据库里唯一**——演示库的 task-3 和工作库的 task-3 会落到同一个根下。
    按 mtime 取最新会把别的研究的产物摆到这条任务下面，比"看不到"更糟。

    返回 ``(目录, 匹配级别)``，级别：``match``（digest 目标一致）/``unknown``
    （还没写 digest，早期阶段）/``foreign``（明确是别的研究目标）。
    """

    if not objective:
        return directories[0], "unknown"
    unknown: list[Path] = []
    for directory in directories:
        stated = _objective_of(directory)
        if not stated:
            unknown.append(directory)
            continue
        if stated.strip() == objective.strip():
            return directory, "match"
    if unknown:
        return unknown[0], "unknown"
    return directories[0], "foreign"


def collect_artifacts(
    task_id: int,
    *,
    root: Path | None = None,
    objective: str = "",
    max_files: int = MAX_FILES,
    max_text_chars: int = MAX_TEXT_CHARS,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> ArtifactBundle:
    """列出这个任务的产物；文本类顺手读一段（有上限）。

    ``objective`` 给定时用它把"别的研究留下的同名目录"挑出去，见 ``_pick_directory``。
    """

    directories = candidate_dirs(task_id, root=root)
    if not directories:
        return ArtifactBundle(
            task_id=task_id,
            note=(
                "还没找到这个任务的产物目录（content/research/task-<id>）："
                "任务可能还没跑到第一步，或者产物落在别处。"
            ),
        )

    # 同一个 id 下可能有多份目录（不同研究目标各一份，用指纹区分）。
    # 只列**最新那份**——混着列会把两次研究的产物摆在一起，看着像同一条任务的输出。
    current, matched = _pick_directory(directories, objective)
    entries: list[Path] = [item for item in current.rglob("*") if item.is_file()]
    entries.sort(key=lambda item: item.stat().st_mtime, reverse=True)

    files: list[ArtifactFile] = []
    used_chars = 0
    skipped = 0
    for path in entries:
        if len(files) >= max_files:
            skipped = len(entries) - len(files)
            break
        stat = path.stat()
        size = int(stat.st_size)
        suffix = path.suffix.lower()
        kind = _kind_of(suffix)
        text = ""
        truncated = False
        if suffix in TEXT_SUFFIXES and used_chars < max_total_chars:
            budget = min(max_text_chars, max_total_chars - used_chars)
            try:
                text, truncated = _read_text(path, budget=budget)
            except OSError:
                text = ""
                truncated = False
            used_chars += len(text)
        files.append(
            ArtifactFile(
                name=path.name,
                relative_path=path.relative_to(_root(root)).as_posix(),
                byte_size=size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                kind=kind,
                text=text,
                truncated=truncated,
            )
        )

    notes = [f"共 {len(entries)} 个产物文件，列了 {len(files)} 个"]
    if matched == "foreign":
        notes.append(
            "这个任务的产物目录里只有别的研究目标留下的 digest："
            "可能来自另一个数据库（同一个 id 不跨库唯一），这里的内容不一定是这条任务产出的"
        )
    if len(directories) > 1:
        if objective:
            notes.append(
                f"同一个 id 下还有 {len(directories) - 1} 个别的产物目录，"
                "这里列的是研究目标对得上的那一份"
            )
        else:
            notes.append(
                f"同一个 id 下还有 {len(directories) - 1} 个别的产物目录"
                "（不同研究目标各一份），这里只列最新的那份"
            )
    if skipped:
        notes.append(f"另有 {skipped} 个没列出（一次最多 {max_files} 个）")
    if used_chars >= max_total_chars:
        notes.append(f"正文合计到 {max_total_chars} 字符就停了，后面的没读")
    return ArtifactBundle(
        task_id=task_id,
        directories=[path.relative_to(_root(root)).as_posix() for path in directories],
        files=files,
        skipped=skipped,
        note="；".join(notes),
    )


__all__ = [
    "ArtifactBundle",
    "ArtifactError",
    "ArtifactFile",
    "DEFAULT_ROOT",
    "MAX_FILES",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_CHARS",
    "TEXT_SUFFIXES",
    "candidate_dirs",
    "collect_artifacts",
]