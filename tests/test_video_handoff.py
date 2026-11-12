"""「从报告建视频任务」的交接测试。

重点不是"能建出目录"，而是三件事：

1. 研究材料**真的搬过去了**（正文、证据行、来源清单，正文带 SHA-256）；
2. 脚本与分镜**确实是空的**——没有可用模型时不许塞模板文字冒充脚本；
3. 同名任务目录**不会被覆盖**（人改过的分镜不能被第二次点击吃掉）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.capabilities.model import providers as model_providers
from backend.app.video import handoff as handoff_module
from backend.app.video.handoff import VideoHandoffError, create_video_job


class _FakeReport:
    id = 7
    title = "商业银行是如何赚钱的"
    summary = "净息差 × 生息资产规模决定利息净收入。"
    findings = [
        {"claim": "42 家 A 股上市银行营业收入 4.28 万亿元", "source_id": 1},
        "净息差收窄是主要压力。",
    ]
    limitations = "样本仅覆盖 A 股上市银行。"
    research_task_id = 3
    author_agent = "规则汇编"
    verification_status = "unverified"


class _FakeEvidence:
    def __init__(self, evidence_id: int, claim: str) -> None:
        self.id = evidence_id
        self.source_id = 1
        self.claim = claim
        self.original_excerpt = "……营业收入 4.28 万亿元……"
        self.quote_locator = "S01:120"
        self.verification_status = "verified"


class _FakeSource:
    id = 1
    title = "某银行 2024 年报"
    url = "https://example.com/annual"
    publisher = "某银行"
    published_at = None
    content_hash = "a" * 64
    credibility_level = "high"


@pytest.fixture()
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "provider_disabled"}},
    )


def test_handoff_copies_research_and_leaves_script_empty(tmp_path: Path, no_model: None) -> None:
    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "营业收入 4.28 万亿元")],
        sources=[_FakeSource()],
        root=tmp_path,
    )
    job = tmp_path / scaffold.job_id
    assert job.is_dir()
    assert (job / "research" / "report.md").read_text(encoding="utf-8").startswith(
        "# 商业银行是如何赚钱的"
    )
    evidence = json.loads((job / "research" / "evidence.json").read_text(encoding="utf-8"))
    assert evidence[0]["claim"] == "营业收入 4.28 万亿元"
    assert evidence[0]["verification_status"] == "verified"
    sources = json.loads((job / "research" / "sources.json").read_text(encoding="utf-8"))
    assert sources[0]["content_hash"] == "a" * 64
    assert sources[0]["credibility"] == "high"

    # 脚本与分镜必须是空的：没有模型就不许生成
    assert list((job / "script").iterdir()) == []
    assert list((job / "storyboard").iterdir()) == []

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["blocked"] is True
    # 这份夹具只有 1 个来源，规则分镜排不出来 —— 卡点是"资料不够"，不是"没配模型"。
    # 两者处理办法不同（配密钥 vs 补抓另一个来源），所以必须分开报。
    assert manifest["blocked_reason"] == "insufficient_sources"
    assert manifest["stage"] == "research_imported"
    assert manifest["counts"] == {"evidence": 1, "sources": 1}
    assert len(manifest["report"]["sha256"]) == 64
    assert any("script/script.md" in item for item in manifest["not_done"])

    handoff_text = (job / "HANDOFF.md").read_text(encoding="utf-8")
    assert "model_not_configured" in handoff_text
    assert "还缺什么" in handoff_text


def test_handoff_marks_unblocked_when_a_provider_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "ready"}},
    )
    scaffold = create_video_job(_FakeReport(), root=tmp_path)
    assert scaffold.blocked is False
    assert scaffold.blocked_reason == ""
    manifest = json.loads((tmp_path / scaffold.job_id / "job.json").read_text(encoding="utf-8"))
    assert manifest["blocked"] is False
    # 即使模型可用，也没有替人写脚本
    assert list((tmp_path / scaffold.job_id / "script").iterdir()) == []


def test_handoff_refuses_to_overwrite_an_existing_job(tmp_path: Path, no_model: None) -> None:
    first = create_video_job(_FakeReport(), root=tmp_path, job_id="fixed_job")
    target = tmp_path / first.job_id / "script" / "script.md"
    target.write_text("人写的脚本", encoding="utf-8")
    with pytest.raises(VideoHandoffError) as error:
        create_video_job(_FakeReport(), root=tmp_path, job_id="fixed_job")
    assert error.value.reason == "job_exists"
    assert target.read_text(encoding="utf-8") == "人写的脚本"


def test_video_job_api_creates_directory_and_reports_blocked(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(handoff_module, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "missing_api_key"}},
    )

    created = client.post(
        "/api/research-reports",
        json={
            "title": "交接接口测试报告",
            "summary": "这是摘要。",
            "limitations": "样本有限。",
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    response = client.post(f"/api/research-reports/{report_id}/video-job")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["report_id"] == report_id
    assert body["blocked"] is True
    # 单来源报告：卡在资料不够，不是卡在模型（见 test_handoff_copies_research_and_leaves_script_empty）
    assert body["blocked_reason"] == "insufficient_sources"
    # 界面靠 outline 字段说明"分镜为什么没排出来"，所以它必须出现在响应里
    assert body["outline"]["ok"] is False
    assert body["outline"]["reason"] == "insufficient_sources"
    assert Path(body["path"]).is_dir()
    assert Path(body["path"]).parent == tmp_path
    assert (Path(body["path"]) / "job.json").is_file()


def test_video_job_api_accepts_an_explicit_job_id(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一天对同一份报告再点一次不该只能撞 409：允许调用方自己起名。"""
    monkeypatch.setattr(handoff_module, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "missing_api_key"}},
    )
    created = client.post(
        "/api/research-reports",
        json={"title": "重试也要能建", "summary": "摘要。", "limitations": "样本有限。"},
    )
    report_id = created.json()["id"]

    first = client.post(f"/api/research-reports/{report_id}/video-job", json={"job_id": "retry_one"})
    assert first.status_code == 201, first.text
    assert first.json()["job_id"] == "retry_one"

    second = client.post(f"/api/research-reports/{report_id}/video-job", json={"job_id": "retry_two"})
    assert second.status_code == 201, second.text
    assert second.json()["job_id"] == "retry_two"

    duplicate = client.post(
        f"/api/research-reports/{report_id}/video-job", json={"job_id": "retry_one"}
    )
    assert duplicate.status_code == 409, "同名目录仍然拒绝覆盖"


def test_video_job_api_returns_404_for_unknown_report(client, tmp_path: Path) -> None:
    response = client.post("/api/research-reports/999999/video-job")
    assert response.status_code == 404


def test_two_sources_are_enough_for_a_rule_made_storyboard(
    tmp_path: Path, no_model: None
) -> None:
    class _Second(_FakeSource):
        id = 2
        title = "监管机构统计口径"
        url = "https://example.org/stats"

    class _SecondEvidence(_FakeEvidence):
        def __init__(self, evidence_id: int, claim: str) -> None:
            super().__init__(evidence_id, claim)
            self.source_id = 2

    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "营业收入 4.28 万亿元"), _SecondEvidence(2, "净息差 1.52%")],
        sources=[_FakeSource(), _Second()],
        root=tmp_path,
    )

    job = tmp_path / scaffold.job_id
    board = job / "storyboard" / "storyboard.csv"
    assert board.is_file()
    assert (job / "storyboard" / "OUTLINE.md").is_file()
    assert scaffold.stage == "storyboard_ready"
    assert scaffold.next_stage == "render"
    # 有分镜就能出片，所以即使没有模型也不算阻塞
    assert scaffold.blocked is False
    assert "render_job" in scaffold.next_step

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["outline"]["ok"] is True
    assert manifest["outline"]["generated_by"] == "rules"
    assert manifest["outline"]["shot_count"] >= 3
    # 正式脚本仍然没有：规则分镜里的口播稿是原文摘引，不是成稿
    assert list((job / "script").iterdir()) == []


def test_outline_can_be_switched_off(tmp_path: Path, no_model: None) -> None:
    class _Second(_FakeSource):
        id = 2

    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "a"), _FakeEvidence(2, "b")],
        sources=[_FakeSource(), _Second()],
        root=tmp_path,
        outline=False,
    )
    assert list((tmp_path / scaffold.job_id / "storyboard").iterdir()) == []
    manifest = json.loads((tmp_path / scaffold.job_id / "job.json").read_text(encoding="utf-8"))
    assert manifest["outline"]["reason"] == "outline_disabled"
    assert manifest["blocked"] is True
