"""产物清单与人工闸门：闸门前得看得见东西，闸门得说清"看完点哪里"。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services import task_artifacts, task_gate


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_only_the_newest_directory_is_listed(tmp_path: Path) -> None:
    """同一个 id 下多份目录时只列最新那份——混着列会把两次研究摆在一起。"""

    old = tmp_path / "task-7"
    _write(old / "digest.md", "# 老的一次")
    new = tmp_path / "task-7-deadbeef"
    _write(new / "analysis.md", "# 新的一次")
    # 让 new 明确更新
    import os

    os.utime(old / "digest.md", (1_700_000_000, 1_700_000_000))

    bundle = task_artifacts.collect_artifacts(7, root=tmp_path)

    names = [item.name for item in bundle.files]
    assert names == ["analysis.md"]
    assert bundle.directories[0] == "task-7-deadbeef"
    assert "只列最新的那份" in bundle.note


def test_text_files_carry_their_body_and_binaries_do_not(tmp_path: Path) -> None:
    root = tmp_path
    _write(root / "task-3" / "report.md", "# 报告\n净息差 1.52%")
    _write(root / "task-3" / "notes.txt", "随手记")
    (root / "task-3" / "plate.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)

    bundle = task_artifacts.collect_artifacts(3, root=root)
    by_name = {item.name: item for item in bundle.files}

    assert by_name["report.md"].kind == "markdown"
    assert "1.52%" in by_name["report.md"].text
    assert by_name["notes.txt"].kind == "text"
    assert by_name["plate.png"].kind == "binary"
    assert by_name["plate.png"].text == ""
    assert by_name["plate.png"].byte_size > 40


def test_long_files_are_truncated_and_say_so(tmp_path: Path) -> None:
    _write(tmp_path / "task-5" / "digest.json", json.dumps({"filler": "字" * 5000}, ensure_ascii=False))

    bundle = task_artifacts.collect_artifacts(5, root=tmp_path, max_text_chars=200)

    item = bundle.files[0]
    assert item.truncated is True
    assert len(item.text) == 200


def test_the_text_budget_is_shared_across_files(tmp_path: Path) -> None:
    """总量到顶就停读，后面的只报大小——不能假装全读了。"""

    for index in range(3):
        _write(tmp_path / "task-9" / f"part{index}.md", "字" * 500)

    bundle = task_artifacts.collect_artifacts(
        9, root=tmp_path, max_text_chars=500, max_total_chars=600
    )

    assert len(bundle.files) == 3
    assert sum(len(item.text) for item in bundle.files) <= 600
    assert "正文合计到 600 字符就停了" in bundle.note


def test_missing_directory_is_reported_not_raised(tmp_path: Path) -> None:
    bundle = task_artifacts.collect_artifacts(404, root=tmp_path)

    assert bundle.files == []
    assert "还没找到这个任务的产物目录" in bundle.note


def test_candidate_dirs_sorts_by_mtime(tmp_path: Path) -> None:
    import os

    older = tmp_path / "task-2"
    newer = tmp_path / "task-2-cafe1234"
    older.mkdir()
    newer.mkdir()
    os.utime(older, (1_600_000_000, 1_600_000_000))
    os.utime(newer, (1_700_000_000, 1_700_000_000))

    assert [path.name for path in task_artifacts.candidate_dirs(2, root=tmp_path)] == [
        "task-2-cafe1234",
        "task-2",
    ]


class _Row:
    """够用的 ResearchSubtask 替身。"""

    def __init__(self, *, sequence: int, status: str, gate: dict | None = None,
                 require: bool = False, row_id: int | None = None) -> None:
        self.id = row_id if row_id is not None else sequence
        self.sequence = sequence
        self.status = status
        self.input_payload = {"require_human_review": require, "human_gate": gate} if require else {}


def test_gate_points_at_the_step_that_was_stopped() -> None:
    """闸门标记写在**做完的那一格**上：做完 topic.discover 才停下等人立项。"""

    rows = [
        _Row(sequence=1, status="succeeded"),
        _Row(sequence=2, status="succeeded", require=True,
             gate={"id": "topic_approval", "name": "人工立项"}),
        _Row(sequence=3, status="ready"),
    ]

    gate = task_gate.gate_of(rows, task_status="paused")

    assert gate is not None
    assert gate.gate_id == "topic_approval"
    assert gate.label == "人工立项"
    assert gate.after_sequence == 2
    assert "继续跑" in gate.next_step


def test_gate_is_not_reported_when_the_task_is_not_paused() -> None:
    """没停在 paused 就别报闸门——那会拿一道早就过去的门当现状。"""

    rows = [
        _Row(sequence=2, status="succeeded", require=True, gate={"id": "topic_approval"}),
        _Row(sequence=3, status="running"),
    ]

    assert task_gate.gate_of(rows, task_status="running") is None


def test_the_latest_gate_wins() -> None:
    rows = [
        _Row(sequence=2, status="succeeded", require=True, gate={"id": "topic_approval"}),
        _Row(sequence=8, status="succeeded", require=True,
             gate={"id": "fact_check_gate", "name": "事实核查（人工确认）"}),
    ]

    gate = task_gate.gate_of(rows, task_status="paused")

    assert gate is not None
    assert gate.gate_id == "fact_check_gate"
    assert gate.after_sequence == 8
    assert "语义核查" in gate.next_step


def test_unknown_gate_falls_back_to_generic_advice() -> None:
    rows = [
        _Row(sequence=4, status="succeeded", require=True, gate={"id": "made_up_gate"}),
    ]

    gate = task_gate.gate_of(rows, task_status="paused")

    assert gate is not None
    assert gate.gate_id == "made_up_gate"
    assert gate.label == ""
    assert gate.next_step == task_gate.GENERIC_STEP


def _make_task(client) -> int:
    columns = client.get("/api/topics/columns").json()
    column_id = columns[0]["id"] if columns else None
    topic = client.post(
        "/api/topics",
        json={"title": "闸门前看得见东西吗", "description": "验证产物清单", "column_id": column_id},
    )
    response = client.post(
        "/api/research-tasks",
        json={
            "title": "产物清单验收",
            "objective": "看看这一步到底做出了什么",
            "topic_id": topic.json().get("id"),
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_api_lists_artifacts_and_reports_the_gate(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(task_artifacts, "DEFAULT_ROOT", tmp_path)
    task_id = _make_task(client)
    _write(tmp_path / f"task-{task_id}" / "discovery.md", "# 选题拆解\n- 净息差的来源结构")

    detail = client.get(f"/api/research-tasks/{task_id}").json()
    listing = client.get(f"/api/research-tasks/{task_id}/artifacts")

    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["task_id"] == task_id
    assert [item["name"] for item in body["files"]] == ["discovery.md"]
    assert "净息差的来源结构" in body["files"][0]["text"]
    # 新任务刚建好还没跑，没有闸门可报
    assert detail["gate"] is None


def test_unknown_task_has_no_artifacts(client) -> None:
    response = client.get("/api/research-tasks/999999/artifacts")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
