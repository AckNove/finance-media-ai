"""「从报告建视频任务」的交接测试。

重点不是"能建出目录"，而是三件事：

1. 研究材料**真的搬过去了**（正文、证据行、来源清单，正文带 SHA-256）；
2. 脚本与分镜**确实是空的**——没有可用模型时不许塞模板文字冒充脚本；
3. 同名任务目录**不会被覆盖**（人改过的分镜不能被第二次点击吃掉）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _confirmed(*indexes: int, evidence_ids: tuple[int, ...] = (1,)) -> list[dict[str, Any]]:
    """人工确认过的结论（成稿闸门要求至少一条）。"""
    return [
        {
            "finding_index": index,
            "check_id": 900 + index,
            "verdict": "supported",
            "evidence_ids": list(evidence_ids),
            "source_ids": ["S01"],
            "text": "营业收入 4.28 万亿元",
        }
        for index in indexes
    ]


def test_handoff_copies_research_and_leaves_script_empty(tmp_path: Path, no_model: None) -> None:
    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "营业收入 4.28 万亿元")],
        sources=[_FakeSource()],
        root=tmp_path,
        confirmed=_confirmed(0),
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
    assert manifest["counts"]["evidence"] == 1
    assert manifest["counts"]["sources"] == 1
    # 成稿闸门：只有人工确认过的结论才进稿，这里必须记下确认了几条
    assert manifest["counts"]["confirmed_findings"] == 1
    assert manifest["draft_gate"]["check_ids"] == [900]
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
    scaffold = create_video_job(_FakeReport(), root=tmp_path, confirmed=_confirmed(0))
    assert scaffold.blocked is False
    assert scaffold.blocked_reason == ""
    manifest = json.loads((tmp_path / scaffold.job_id / "job.json").read_text(encoding="utf-8"))
    assert manifest["blocked"] is False
    # 即使模型可用，也没有替人写脚本
    assert list((tmp_path / scaffold.job_id / "script").iterdir()) == []


def test_handoff_refuses_to_overwrite_an_existing_job(tmp_path: Path, no_model: None) -> None:
    first = create_video_job(
        _FakeReport(), root=tmp_path, job_id="fixed_job", confirmed=_confirmed(0)
    )
    target = tmp_path / first.job_id / "script" / "script.md"
    target.write_text("人写的脚本", encoding="utf-8")
    with pytest.raises(VideoHandoffError) as error:
        create_video_job(
            _FakeReport(), root=tmp_path, job_id="fixed_job", confirmed=_confirmed(0)
        )
    assert error.value.reason == "job_exists"
    assert target.read_text(encoding="utf-8") == "人写的脚本"


def _confirm_all(db_session: Any, report_id: int, count: int) -> None:
    """把报告的前 count 条结论标成人工已确认（模拟人在核查页点过）。"""
    from backend.app.models.research import FindingCheck

    for index in range(count):
        db_session.add(
            FindingCheck(
                research_report_id=report_id,
                finding_index=index,
                claim=f"结论 {index}",
                verdict="supported",
                reason="人工已核对原文",
                human_review_status="confirmed",
            )
        )
    db_session.commit()


def test_video_job_api_refuses_until_a_conclusion_is_confirmed(client, tmp_path: Path) -> None:
    """成稿闸门：一条结论都没人工确认时，不许建视频任务。"""
    created = client.post(
        "/api/research-reports",
        json={
            "title": "没确认就不许成稿",
            "summary": "这是摘要。",
            "limitations": "样本有限。",
            "findings": [{"statement": "净息差降至 1.52%", "nature": "fact"}],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    readiness = client.get(f"/api/research-reports/{report_id}/draft-readiness")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["allowed"] is False
    assert readiness.json()["reason"] == "no_checks"

    refused = client.post(f"/api/research-reports/{report_id}/video-job")
    assert refused.status_code == 409, refused.text
    details = refused.json()["error"]["details"]
    assert details["reason"] == "no_checks"
    assert details["allowed"] is False


def test_video_job_api_creates_directory_and_reports_blocked(
    client, db_session: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
            "findings": [{"statement": "营业收入 4.28 万亿元", "nature": "fact"}],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    _confirm_all(db_session, report_id, 1)

    readiness = client.get(f"/api/research-reports/{report_id}/draft-readiness")
    assert readiness.json()["allowed"] is True
    assert readiness.json()["confirmed"] == 1

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
    # 报告副本里必须写清"哪几条能进稿"
    report_md = (Path(body["path"]) / "research" / "report.md").read_text(encoding="utf-8")
    assert "可进成稿的结论（人工已确认）" in report_md
    assert "营业收入 4.28 万亿元" in report_md


def test_video_job_api_accepts_an_explicit_job_id(
    client, db_session: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
        json={
            "title": "重试也要能建",
            "summary": "摘要。",
            "limitations": "样本有限。",
            "findings": [{"statement": "净息差 1.52%", "nature": "fact"}],
        },
    )
    report_id = created.json()["id"]
    _confirm_all(db_session, report_id, 1)

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


def _topic_id(db_session: Any) -> int:
    """脚本必须挂在选题上（`ScriptService.create` 会校验选题存在）。"""
    from backend.app.models.topic import Topic

    topic = Topic(title="接口按脚本排分镜", slug="api-script-storyboard")
    db_session.add(topic)
    db_session.commit()
    return topic.id


def test_video_job_api_returns_404_for_unknown_report(client, tmp_path: Path) -> None:
    response = client.post("/api/research-reports/999999/video-job")
    assert response.status_code == 404


def test_video_job_api_uses_the_latest_script_when_one_exists(
    client, db_session: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """报告已经有脚本时，接口建出来的分镜必须是**脚本**那条，而不是规则模板。"""
    from backend.app.services.script_service import ScriptService

    monkeypatch.setattr(handoff_module, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(
        model_providers,
        "provider_status",
        lambda **_: {"deepseek": {"status": "missing_api_key"}},
    )
    created = client.post(
        "/api/research-reports",
        json={
            "title": "接口按脚本排分镜",
            "summary": "这是摘要。",
            "limitations": "样本有限。",
            "findings": [{"statement": "净息差降至 1.52%", "nature": "fact"}],
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    _confirm_all(db_session, report_id, 1)

    ScriptService(db_session).create(
        topic_id=_topic_id(db_session),
        title="脚本写出来的标题",
        body="2024年第四季度净息差降到1.52%。\n\n贷款利息收得少了，存款还得照样付息。",
        research_report_id=report_id,
        hook="一句话说清银行靠什么赚钱。",
        segments=[
            {
                "text": "2024年第四季度净息差降到1.52%。",
                "role": "evidence",
                "source_ids": ["S01"],
                "numbers": ["1.52%"],
            },
            {
                "text": "贷款利息收得少了，存款还得照样付息。",
                "role": "mechanism",
                "source_ids": ["S01"],
                "numbers": [],
            },
        ],
        disclaimer="本片为合规资料整理，不构成投资建议。",
        estimated_duration=12,
    )

    response = client.post(f"/api/research-reports/{report_id}/video-job")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outline"]["source"] == "script"
    assert body["outline"]["script_id"]
    assert body["stage"] == "storyboard_ready"
    board = (Path(body["path"]) / "storyboard" / "storyboard.csv").read_text(encoding="utf-8")
    assert "贷款利息收得少了" in board


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
        confirmed=_confirmed(0, evidence_ids=(1, 2)),
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
        confirmed=_confirmed(0, evidence_ids=(1, 2)),
    )
    assert list((tmp_path / scaffold.job_id / "storyboard").iterdir()) == []
    manifest = json.loads((tmp_path / scaffold.job_id / "job.json").read_text(encoding="utf-8"))
    assert manifest["outline"]["reason"] == "outline_disabled"
    assert manifest["blocked"] is True


class _FakeScript:
    """`scripts` 表里的一行（`content.script` 让模型写出来的那份稿子）。"""

    id = 12
    title = "净息差为什么重要"
    hook = "银行赚钱，就靠那一点点利差。"
    disclaimer = "本片为合规资料整理，不构成投资建议。"
    segments = [
        {
            "text": "2024年第四季度净息差降到1.52%。",
            "role": "evidence",
            "source_ids": ["S01"],
            "numbers": ["1.52%"],
        },
        {
            "text": "贷款利息收得少了，存款还得照样付息。",
            "role": "mechanism",
            "source_ids": ["S01"],
            "numbers": [],
        },
    ]


def test_a_script_turns_into_the_storyboard_instead_of_the_rule_outline(
    tmp_path: Path, no_model: None
) -> None:
    """脚本在手时，分镜必须按脚本排——不然模型写的那份稿子永远进不了片子。"""
    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "营业收入 4.28 万亿元")],
        sources=[_FakeSource()],
        root=tmp_path,
        confirmed=_confirmed(0),
        script=_FakeScript(),
        script_id=_FakeScript.id,
    )
    job = tmp_path / scaffold.job_id
    board = (job / "storyboard" / "storyboard.csv").read_text(encoding="utf-8")

    assert "银行赚钱，就靠那一点点利差。" in board
    assert "贷款利息收得少了" in board
    # 来源跟着句子走：内容质检靠它判断"这一镜能不能回溯"
    assert "S01" in board
    assert (job / "storyboard" / "FROM_SCRIPT.md").is_file()
    # 规则分镜那套说明不该出现
    assert not (job / "storyboard" / "OUTLINE.md").exists()

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["outline"]["source"] == "script"
    assert manifest["outline"]["script_id"] == 12
    assert manifest["outline"]["shot_count"] >= 4
    # 有分镜就能出片，所以即使没配模型也不算阻塞（分镜不再依赖模型）
    assert manifest["blocked"] is False
    assert scaffold.stage == "storyboard_ready"


def test_a_broken_script_is_reported_not_silently_replaced(
    tmp_path: Path, no_model: None
) -> None:
    """脚本排不出分镜时，不许悄悄换成规则分镜——那等于骗人"片子就是脚本那条"。"""

    class _Empty(_FakeScript):
        segments: list[dict[str, Any]] = []

    scaffold = create_video_job(
        _FakeReport(),
        evidence=[_FakeEvidence(1, "a"), _FakeEvidence(2, "b")],
        sources=[_FakeSource(), _FakeSource()],
        root=tmp_path,
        confirmed=_confirmed(0, evidence_ids=(1, 2)),
        script=_Empty(),
        script_id=13,
    )
    job = tmp_path / scaffold.job_id
    assert list((job / "storyboard").iterdir()) == []

    manifest = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert manifest["outline"]["ok"] is False
    assert manifest["outline"]["reason"] == "empty_script"
    assert manifest["outline"]["source"] == "script"
