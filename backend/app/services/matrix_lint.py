"""需求矩阵的机器校验：让"说 DONE"有据可查。

为什么需要这个
--------------
`docs/research_hub/v6_0_requirements_matrix.md` 是这个项目对"做完了什么"的唯一台账，
八十多行 DONE 全靠人手写。手写台账最大的风险不是写错字，而是**写着写着就飘了**：
引用的文件被改名、状态手滑写成别的词、某一行其实只有计划没有代码。

所以这里只做三件事，全部是可机器判定的：

1. 状态只用六种取值（`DONE` / `PARTIAL` / `TEST_ONLY` / `CODE_ONLY` / `BLOCKED` /
   `NOT_IMPLEMENTED`），写成 `**DONE**` 这种加粗形式也认，但会记一条提示；
2. 行里引用的**具体文件**必须真实存在（`backend/app/**` 这种通配、`{a,b}` 这种
   占位、`web/app.js#pageOperations` 这种带锚点的，不算具体文件，跳过）；
3. 标成 `DONE` 的行，至少得引到一个真实存在的文件——没有落脚点的 DONE 就是口号。

它不判断"功能好不好用"（那要靠测试与真机验收），只判断**台账有没有说谎**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

STATUSES: tuple[str, ...] = (
    "DONE",
    "PARTIAL",
    "TEST_ONLY",
    "CODE_ONLY",
    "BLOCKED",
    "NOT_IMPLEMENTED",
)

STATUS_SEVERITY = {
    "DONE": "done",
    "PARTIAL": "partial",
    "TEST_ONLY": "partial",
    "CODE_ONLY": "partial",
    "BLOCKED": "blocked",
    "NOT_IMPLEMENTED": "missing",
}

REPO_PREFIXES: tuple[str, ...] = (
    "backend/",
    "tests/",
    "web/",
    "docs/",
    "scripts/",
    "content/",
    "config/",
    "skills/",
    "tools/",
    "data/",
)

_TOKEN = re.compile(r"`([^`\n]+)`")
_SEPARATOR = re.compile(r"^\|[\s:\-|]+\|$")


@dataclass(frozen=True)
class MatrixRow:
    """矩阵里的一行需求。"""

    line: int
    requirement: str
    where: str
    status: str
    gap: str = ""
    raw: str = ""

    @property
    def normalised_status(self) -> str:
        return normalise_status(self.status)


@dataclass(frozen=True)
class LintFinding:
    check: str
    severity: str
    detail: str
    line: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "detail": self.detail,
            "line": self.line,
        }


@dataclass
class LintReport:
    path: str
    rows: list[MatrixRow] = field(default_factory=list)
    findings: list[LintFinding] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    referenced_paths: int = 0
    checked_paths: int = 0

    @property
    def errors(self) -> list[LintFinding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[LintFinding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def infos(self) -> list[LintFinding]:
        return [item for item in self.findings if item.severity == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [f"{len(self.rows)} 行需求"]
        if self.status_counts:
            parts.append(
                "、".join(f"{key} {value}" for key, value in sorted(self.status_counts.items()))
            )
        parts.append(f"引用文件 {self.referenced_paths} 个（逐个核对 {self.checked_paths} 个）")
        if self.errors:
            parts.append(f"{len(self.errors)} 个问题")
        elif self.warnings:
            parts.append(f"{len(self.warnings)} 条提示")
        else:
            parts.append("没有发现问题")
        return "，".join(parts)

    def status_lines(self) -> list[tuple[str, int]]:
        return sorted(self.status_counts.items())

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "rows": len(self.rows),
            "status_counts": dict(self.status_counts),
            "referenced_paths": self.referenced_paths,
            "checked_paths": self.checked_paths,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
            "infos": [item.as_dict() for item in self.infos],
            "summary": self.summary(),
        }


def normalise_status(cell: str) -> str:
    """把 `**DONE**`、`` `DONE` ``、`done` 统一成 `DONE`；认不出来就原样返回。"""

    text = str(cell or "").strip().strip("*").strip().strip("`").strip()
    upper = text.upper()
    return upper if upper in STATUSES else text


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_header(cells: list[str]) -> bool:
    return bool(cells) and cells[0] in ("需求", "模块") and "状态" in cells


def parse_rows(text: str) -> list[MatrixRow]:
    """把矩阵里的需求行读出来。表头、分隔线、空行都不算。"""

    rows: list[MatrixRow] = []
    for number, line in enumerate(str(text).splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or _SEPARATOR.match(stripped):
            continue
        cells = _cells(stripped)
        if len(cells) < 4 or _is_header(cells):
            continue
        rows.append(
            MatrixRow(
                line=number,
                requirement=cells[0],
                where=cells[1],
                status=cells[2],
                gap=" | ".join(cells[3:]),
                raw=stripped,
            )
        )
    return rows


def is_placeholder(path: str) -> bool:
    """通配、占位、带锚点的写法都不算"具体文件"。"""

    text = str(path or "").strip()
    if not text:
        return True
    if any(mark in text for mark in ("*", "{", "}", "<", ">", "#")):
        return True
    return text.endswith("/")


#: 台账里常用简写（`core/config.py`、`models/`），挨个前缀试一遍就知道指的是哪个文件。
SHORTHAND_PREFIXES: tuple[str, ...] = (
    "backend/app/",
    "backend/",
    "tests/",
    "web/",
    "scripts/",
    "docs/research_hub/",
    "config/",
    "content/",
    "skills/",
)

_BRACE = re.compile(r"\{([^{}]*)\}")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_METHOD = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+", re.IGNORECASE)
_INDEX_SUFFIXES = (".py", ".js", ".ps1", ".yaml", ".yml", ".json", ".md", ".sql")
_INDEX_DIRS = ("backend", "web", "scripts", "config", "skills")


@dataclass(frozen=True)
class SourceIndex:
    """仓库里"有哪些东西"的一份快照，用来判断台账引用的名字是不是真的存在。

    只认三种存在性：文件路径、文件名、以及源码里出现过的字样（函数名、表名、接口路径
    都会以字面量出现在某处）。**它不验证行为对不对**，只验证"这东西确实在这个仓库里"。
    """

    root: Path
    basenames: dict[str, list[str]] = field(default_factory=dict)
    blob: str = ""

    def has_path(self, path: str) -> bool:
        return (self.root / path).exists()

    def has_basename(self, name: str) -> bool:
        return name in self.basenames

    def mentions(self, text: str) -> bool:
        needle = str(text or "").strip()
        return bool(needle) and needle in self.blob


def build_index(root: Path | str) -> SourceIndex:
    root_path = Path(root)
    basenames: dict[str, list[str]] = {}
    chunks: list[str] = []
    for directory in _INDEX_DIRS:
        base = root_path / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _INDEX_SUFFIXES:
                continue
            basenames.setdefault(path.name, []).append(
                str(path.relative_to(root_path)).replace("\\", "/")
            )
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:  # pragma: no cover - 只有权限问题时才会走到
                continue
    return SourceIndex(root=root_path, basenames=basenames, blob="\n".join(chunks))



def _expand_braces(text: str) -> list[str]:
    """把 `backend/app/{api,core}` 展开成两条具体路径（只展开一层，够用且可预测）。"""

    match = _BRACE.search(text)
    if not match:
        return [text]
    head, tail = text[: match.start()], text[match.end() :]
    variants: list[str] = []
    for option in match.group(1).split(","):
        variants.extend(_expand_braces(f"{head}{option.strip()}{tail}"))
    return variants


def resolve_evidence(root: Path | str, token: str) -> list[Path]:
    """把一个引用（可能带简写、brace、通配）解析成磁盘上真实存在的路径。

    返回空列表表示"这个引用落不到任何文件上"——只有这时才算台账没证据。
    """

    root_path = Path(root)
    text = str(token or "").strip().split("#", 1)[0]
    if not text:
        return []
    found: list[Path] = []
    for variant in _expand_braces(text):
        candidates: list[str] = []
        if variant.startswith(REPO_PREFIXES):
            candidates.append(variant)
        else:
            candidates.extend(f"{prefix}{variant}" for prefix in SHORTHAND_PREFIXES)
        for candidate in candidates:
            if "*" in candidate:
                found.extend(sorted(root_path.glob(candidate)))
            elif (root_path / candidate).exists():
                found.append(root_path / candidate)
    return found


def referenced_paths(text: str) -> dict[str, int]:
    """矩阵里出现的、形如仓库内文件的路径（含出现次数）。"""

    counts: dict[str, int] = {}
    for token in _TOKEN.findall(str(text)):
        cleaned = token.strip()
        if cleaned.startswith(REPO_PREFIXES) and "/" in cleaned:
            counts[cleaned] = counts.get(cleaned, 0) + 1
    return counts


def _paths_in(text: str) -> Iterable[str]:
    for token in _TOKEN.findall(str(text)):
        cleaned = token.strip()
        if cleaned.startswith(REPO_PREFIXES) and "/" in cleaned:
            yield cleaned


def evidence_kind(index: SourceIndex, token: str) -> str:
    """这条引用落在哪儿：``path`` / ``basename`` / ``mention`` / 空串表示找不到。

    三类引用都要能认出来：文件路径（含台账常用的简写）、裸文件名（``tokens.py``）、
    以及源码里以字面量出现过的符号或接口路径（``executor._skill_notes()``、
    ``GET /api/system/daily``、``GET /api/system/{costs,providers,agents}``）。

    含斜杠的引用只按"路径"与"整个字符串在源码里出现过"来判——**不能**退化成
    "里面某个单词出现过"：那样 ``app`` 这种片段到处都是，什么都能算成证据，
    这个校验就白做了。
    """

    text = str(token or "").strip()
    if not text:
        return ""
    if resolve_evidence(index.root, text):
        return "path"
    body = _METHOD.sub("", text).split("#", 1)[0].strip().rstrip("/")
    if not body:
        return ""
    variants = [body, *_expand_braces(body)]
    if "/" in body:
        return "mention" if any(index.mentions(item) for item in variants) else ""
    if index.has_basename(body):
        return "basename"
    if any(index.mentions(item) for item in variants):
        return "mention"
    for identifier in _IDENTIFIER.findall(body):
        if index.mentions(identifier):
            return "mention"
    return ""


def lint(root: Path | str, matrix: Path | str, *, index: SourceIndex | None = None) -> LintReport:
    """核对一份矩阵。只读，不修改文件。"""

    root_path = Path(root)
    matrix_path = Path(matrix)
    text = matrix_path.read_text(encoding="utf-8")
    source_index = index or build_index(root_path)
    report = LintReport(path=str(matrix_path))
    report.rows = parse_rows(text)

    for row in report.rows:
        status = row.normalised_status
        if status not in STATUSES:
            report.findings.append(
                LintFinding(
                    check="unknown_status",
                    severity="error",
                    detail=f"「{row.requirement}」的状态是「{row.status}」，不在六种取值里",
                    line=row.line,
                )
            )
            continue
        report.status_counts[status] = report.status_counts.get(status, 0) + 1
        if row.status.strip().startswith("*"):
            report.findings.append(
                LintFinding(
                    check="bold_status",
                    severity="info",
                    detail=f"「{row.requirement}」的状态写成了加粗形式，统一成 `{status}` 更好读",
                    line=row.line,
                )
            )

    counts = referenced_paths(text)
    report.referenced_paths = len(counts)
    for path, times in sorted(counts.items()):
        if is_placeholder(path):
            continue
        report.checked_paths += 1
        if not (root_path / path).exists():
            report.findings.append(
                LintFinding(
                    check="missing_path",
                    severity="error",
                    detail=f"矩阵引用了不存在的文件：{path}（出现 {times} 次）",
                )
            )

    for row in report.rows:
        if row.normalised_status != "DONE":
            continue
        kinds = [evidence_kind(source_index, token) for token in _TOKEN.findall(row.raw)]
        if not any(kinds):
            report.findings.append(
                LintFinding(
                    check="done_without_evidence",
                    severity="error",
                    detail=f"「{row.requirement}」标了 DONE，但引用的东西在仓库里一个也找不到",
                    line=row.line,
                )
            )

    return report


def render_text(report: LintReport) -> str:
    lines = [f"矩阵：{report.path}", report.summary()]
    for status, count in report.status_lines():
        lines.append(f"  · {status}：{count} 行")
    for finding in report.findings:
        where = f"（第 {finding.line} 行）" if finding.line else ""
        lines.append(f"  [{finding.severity}] {finding.check}{where} {finding.detail}")
    return "\n".join(lines)


__all__ = [
    "LintFinding",
    "LintReport",
    "MatrixRow",
    "STATUSES",
    "STATUS_SEVERITY",
    "is_placeholder",
    "lint",
    "normalise_status",
    "parse_rows",
    "referenced_paths",
    "render_text",
]
