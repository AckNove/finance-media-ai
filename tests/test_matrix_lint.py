"""把需求矩阵当账本来查：DONE 得站得住，数字得对得上。

这个文件守三件事（都是可机器判定的，不涉及"好不好用"）：

1. 矩阵里的状态只许用六种取值、每行都得有状态；
2. 标成 ``DONE`` 的行，引用的文件/函数/接口**必须真的在仓库里存在**；
3. 表头写的规模数字（张表 / API 操作 / 页面 / 工具）必须与代码里实测的一致——
   这类数字最容易在加功能时忘记改，然后变成"报告比实际好看"。

第 2 条只验证**存在性**：它证明不了"这个功能真的能用"，那是测试与真机验收的事。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.app.services.matrix_lint import (
    STATUSES,
    build_index,
    evidence_kind,
    is_placeholder,
    lint,
    normalise_status,
    parse_rows,
    referenced_paths,
    resolve_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "research_hub" / "v6_0_requirements_matrix.md"


@pytest.fixture(scope="module")
def text() -> str:
    return MATRIX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index():
    return build_index(ROOT)


# ---- 解析 ---------------------------------------------------------------


def test_rows_are_parsed_without_headers(text: str) -> None:
    rows = parse_rows(text)
    assert len(rows) >= 60, "矩阵行数掉到 60 以下，多半是解析器坏了或文件被截断"
    assert all(row.requirement for row in rows)
    assert all(row.line > 0 for row in rows)
    # 表头不能被当成需求行
    assert not [row for row in rows if row.requirement in ("需求", "模块")]


def test_status_normalisation_accepts_bold_and_code(text: str) -> None:
    assert normalise_status("**DONE**") == "DONE"
    assert normalise_status("`BLOCKED`") == "BLOCKED"
    assert normalise_status("done") == "DONE"
    assert normalise_status("差不多做完") == "差不多做完"
    statuses = {row.normalised_status for row in parse_rows(text)}
    assert statuses <= set(STATUSES), statuses - set(STATUSES)


# ---- 存在性 -------------------------------------------------------------


def test_placeholders_are_recognised() -> None:
    assert is_placeholder("backend/app/**")
    assert is_placeholder("backend/app/{api,core}")
    assert is_placeholder("web/app.js#pageOperations")
    assert is_placeholder("content/jobs/<job_id>/")
    assert not is_placeholder("backend/app/video/preflight.py")


def test_shorthand_resolves_to_real_files(index) -> None:
    """台账里常写简写（`core/config.py`、`execution/{runtime,worker}.py`）。"""

    assert resolve_evidence(ROOT, "core/config.py")
    assert resolve_evidence(ROOT, "execution/{runtime,worker}.py")
    assert resolve_evidence(ROOT, "config/*.yaml")
    assert not resolve_evidence(ROOT, "core/does_not_exist_here.py")


def test_symbols_and_api_paths_count_as_evidence(index) -> None:
    assert evidence_kind(index, "backend/app/main.py") == "path"
    assert evidence_kind(index, "core/config.py") == "path"
    assert evidence_kind(index, "tokens.py") == "basename"
    assert evidence_kind(index, "executor._skill_notes()") == "mention"
    assert evidence_kind(index, "/api/system/daily") == "mention"
    assert evidence_kind(index, "这个符号绝对不存在_zzz") == ""


# ---- 整份矩阵 -----------------------------------------------------------


def test_the_matrix_has_no_broken_references(index) -> None:
    report = lint(ROOT, MATRIX, index=index)
    problems = "\n".join(item.detail for item in report.errors)
    assert report.ok, problems
    assert report.rows, "一行都没解析出来"
    assert report.checked_paths >= 30, f"只核对了 {report.checked_paths} 个文件，解析器是不是坏了"


def test_every_referenced_repo_path_exists(text: str) -> None:
    missing = [path for path in referenced_paths(text) if not is_placeholder(path) and not (ROOT / path).exists()]
    assert missing == []


def test_bold_status_is_only_a_style_note(index) -> None:
    """`**DONE**` 与 `DONE` 语义一样，所以只记提示、不算错。

    顺带钉住"账本本身干净"：真矩阵里不该再有加粗状态（已经统一成纯 `DONE`）。
    """

    report = lint(ROOT, MATRIX, index=index)
    assert not [item for item in report.findings if item.check == "bold_status" and item.severity != "info"]
    bold = [item.line for item in report.findings if item.check == "bold_status"]
    assert bold == [], f"这些行的状态还带着加粗：{bold}"


def test_a_fabricated_done_row_is_caught(tmp_path, index) -> None:
    """反向测试：把一条 DONE 的引用改成不存在的东西，必须报错。"""

    fake = tmp_path / "matrix.md"
    fake.write_text(
        "| 需求 | 实现位置 | 状态 | 差距 |\n"
        "| --- | --- | --- | --- |\n"
        "| 编的功能 | `backend/app/编的模块.py` | DONE | — |\n",
        encoding="utf-8",
    )
    report = lint(ROOT, fake, index=index)
    checks = {item.check for item in report.findings if item.severity == "error"}
    assert "missing_path" in checks
    assert "done_without_evidence" in checks
    assert not report.ok


def test_unknown_status_is_caught(tmp_path, index) -> None:
    fake = tmp_path / "matrix.md"
    fake.write_text(
        "| 需求 | 实现位置 | 状态 | 差距 |\n"
        "| --- | --- | --- | --- |\n"
        "| 差不多就行 | `backend/app/main.py` | 基本完成 | — |\n",
        encoding="utf-8",
    )
    report = lint(ROOT, fake, index=index)
    assert [item.check for item in report.errors] == ["unknown_status"]


# ---- 表头数字必须与代码一致 ---------------------------------------------


def _header_counts(text: str) -> dict[str, int]:
    """从矩阵开头的规模数字里抠出四项（张表 / API 操作 / 页面 / 工具）。

    只取**最后一次**出现的数字：矩阵开头留着一条"审计基准"（写的是当时的历史值，
    例如 29 张表），最近一次刷新写在后面，对得上代码的应该是后者。
    """

    patterns = {
        "tables": r"(\d+)\s*张表",
        "operations": r"(\d+)\s*个 API 操作",
        "pages": r"(\d+)\s*个页面",
        "tools": r"(\d+)\s*个工具",
    }
    head = "\n".join(text.splitlines()[:20])
    found: dict[str, int] = {}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, head)
        if matches:
            found[key] = int(matches[-1])
    return found


def test_header_numbers_match_the_code(text: str) -> None:
    from backend.app.api.router import ROUTE_CALLERS
    from backend.app.capabilities.tools import TOOL_NAMES
    from backend.app.main import create_app
    from backend.app.models import Base

    spec = create_app().openapi()
    operations = sum(
        1
        for path in spec["paths"].values()
        for method in path
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    pages = len(re.findall(r"\{ path: '/[^']*', title:", app_js))

    expected = {
        "tables": len(Base.metadata.tables),
        "operations": operations,
        "pages": pages,
        "tools": len(TOOL_NAMES),
    }
    claimed = _header_counts(text)
    assert claimed, "表头里找不到规模数字，解析规则要跟着文档格式改"
    assert claimed == expected, f"矩阵表头写的是 {claimed}，代码里是 {expected}"
    assert len(ROUTE_CALLERS) == operations, "权限清单与实际操作数不一致"


# ---- 命令行 -------------------------------------------------------------


def test_cli_reports_a_clean_matrix(capsys) -> None:
    import json

    from backend.scripts.matrix_lint import main

    code = main(["--matrix", str(MATRIX), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["rows"] >= 60


def test_cli_returns_two_when_the_matrix_is_missing(tmp_path, capsys) -> None:
    from backend.scripts.matrix_lint import main

    code = main(["--matrix", str(tmp_path / "没有这份文件.md")])
    assert code == 2
    assert "读不到矩阵文件" in capsys.readouterr().err
