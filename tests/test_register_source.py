"""真实素材登记命令：文件进白名单 + 记哈希/许可，一条命令做完。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.core.job_paths import ApprovedSources
from backend.scripts import register_source


def _job(root: Path, job_id: str = "job_a") -> Path:
    job_dir = root / job_id
    (job_dir / "input" / "approved_sources").mkdir(parents=True, exist_ok=True)
    return job_dir


def _png(path: Path, payload: bytes = b"\x89PNG\r\n\x1a\nfake") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_it_copies_the_file_into_the_whitelist_and_records_it(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    job_dir = _job(jobs)
    outside = _png(tmp_path / "收入结构.png", b"\x89PNG\r\n\x1a\nAAA")

    code = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(outside),
        "--source-id", "S01", "--source-name", "商业银行收入结构（年报图）",
        "--publisher", "某银行", "--source-url", "https://example.com/a", "--published-at", "2026-03-01",
        "--license", "已获授权", "--commercial-use", "allowed", "--credit", "来源：某银行 2026 年报",
    ])
    assert code == 0

    target = job_dir / "input" / "approved_sources" / outside.name
    assert target.is_file()
    assert outside.is_file()  # 原始位置不动

    entries = {item.source_id: item for item in ApprovedSources(job_dir).load_register().entries}
    entry = entries["S01"]
    assert entry.source_name == "商业银行收入结构（年报图）"
    assert entry.commercial_use == "allowed"
    assert entry.license_name == "已获授权"
    assert entry.byte_size == outside.stat().st_size
    assert len(entry.sha256) == 64
    assert (job_dir / "source_register.csv").is_file()

    out = capsys.readouterr().out
    assert "已登记 S01" in out
    assert "可商用=allowed" in out
    assert "预检会拦住" not in out


def test_a_non_commercial_file_is_registered_but_flagged(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    outside = _png(tmp_path / "截图.png")

    code = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(outside),
        "--source-id", "S02", "--source-name", "新闻截图",
        "--license", "仅内部参考", "--commercial-use", "not_allowed",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "预检会拦住" in out
    assert "确认可以商用后" in out


def test_the_same_file_is_reused_instead_of_copied_twice(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    job_dir = _job(jobs)
    outside = _png(tmp_path / "same.csv", b"a,b\n1,2\n")

    first = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(outside),
        "--source-id", "S01", "--source-name", "第一份",
    ])
    second = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(outside),
        "--source-id", "S02", "--source-name", "同一个文件换个编号",
    ])
    assert (first, second) == (0, 0)
    target = job_dir / "input" / "approved_sources" / "same.csv"
    assert target.is_file()
    # 只有一个文件，没有 same-1.csv 之类的副本；清单写在任务根目录
    assert len(list((job_dir / "input" / "approved_sources").iterdir())) == 1
    assert (job_dir / "source_register.csv").is_file()
    # 两次登记都要在清单里：第一次不能被第二次挤掉
    rows = (job_dir / "source_register.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3  # 表头 + S01 + S02


def test_a_name_clash_with_different_content_is_refused(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    job_dir = _job(jobs)
    first = _png(tmp_path / "a" / "chart.png", b"\x89PNG\r\n\x1a\nFIRST")
    register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(first),
        "--source-id", "S01", "--source-name", "第一份",
    ])

    other = _png(tmp_path / "b" / "chart.png", b"\x89PNG\r\n\x1a\nSECOND")
    code = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(other),
        "--source-id", "S02", "--source-name", "同名但内容不同",
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "name_taken" in err
    assert "不覆盖别人的素材" in err
    # 白名单里那份没被改
    kept = (job_dir / "input" / "approved_sources" / "chart.png").read_bytes()
    assert kept == b"\x89PNG\r\n\x1a\nFIRST"


def test_no_copy_refuses_a_file_outside_the_whitelist(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    outside = _png(tmp_path / "outside.png")

    code = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(outside),
        "--source-id", "S01", "--source-name", "不许复制",
        "--no-copy",
    ])
    assert code == 1
    assert "not_inside" in capsys.readouterr().err


def test_unknown_job_and_missing_file_are_usage_errors(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    assert register_source.main([
        "--job", "nope", "--jobs-root", str(jobs), "--file", str(tmp_path / "x.png"),
        "--source-id", "S01", "--source-name", "x",
    ]) == 2
    assert "任务目录不存在" in capsys.readouterr().err

    _job(jobs)
    assert register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(tmp_path / "gone.png"),
        "--source-id", "S01", "--source-name", "x",
    ]) == 2
    assert "文件不存在" in capsys.readouterr().err


def test_missing_required_flags_explain_themselves(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    assert register_source.main(["--job", "job_a", "--jobs-root", str(jobs)]) == 2
    err = capsys.readouterr().err
    assert "--file" in err and "--source-id" in err and "--source-name" in err


def test_list_shows_what_is_registered(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    file = _png(tmp_path / "one.png")
    register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(file),
        "--source-id", "S01", "--source-name", "第一份",
        "--license", "已获授权", "--commercial-use", "allowed",
    ])
    capsys.readouterr()

    assert register_source.main(["--job", "job_a", "--jobs-root", str(jobs), "--list"]) == 0
    out = capsys.readouterr().out
    assert "已登记 1 份素材" in out
    assert "可进片" in out
    assert "sha256=" in out


def test_json_output_is_parseable(tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    file = _png(tmp_path / "one.png")
    code = register_source.main([
        "--job", "job_a", "--jobs-root", str(jobs), "--file", str(file),
        "--source-id", "S01", "--source-name", "第一份", "--json", "--commercial-use", "allowed",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_id"] == "S01"
    assert payload["usable"] is True
    assert payload["action"] == "copied"
    assert payload["relative"].endswith("one.png")


def test_invalid_license_value_is_rejected_by_argparse(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    _job(jobs)
    with pytest.raises(SystemExit):
        register_source.main([
            "--job", "job_a", "--jobs-root", str(jobs), "--file", str(tmp_path / "x.png"),
            "--source-id", "S01", "--source-name", "x", "--commercial-use", "maybe",
        ])
