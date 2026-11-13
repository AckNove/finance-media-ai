"""产业链示意图自动接进分镜的测试（不联网、不花钱、不碰引擎）。"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.media.storyboard import load_storyboard
from backend.app.research.digest import DataPoint, Digest, SourceRef
from backend.app.video import handoff
from backend.app.video.chain import insert_chain_shot
from backend.app.video.outline import OutlineError, build_outline


def _digest() -> Digest:
    return Digest(
        title="光伏产业链利润为什么会从中游流向上下游两端",
        objective="看利润怎么在链条上流动",
        sources=(
            SourceRef(source_id="S01", title="甲", url="https://example.com/a",
                      site_name="", author="", published_at="", sha256="",
                      snapshot_path="", char_count=10, query=""),
            SourceRef(source_id="S02", title="乙", url="https://example.com/b",
                      site_name="", author="", published_at="", sha256="",
                      snapshot_path="", char_count=10, query=""),
        ),
        data_points=(
            DataPoint(source_id="S01", text="硅料价格下降了30%"),
            DataPoint(source_id="S02", text="组件价格跌到0.68元/W"),
        ),
    )


def _job(tmp_path: Path) -> Path:
    build_outline(_digest(), job_root=tmp_path, job_id="chain_case")
    return tmp_path / "storyboard" / "storyboard.csv"


CHAIN_STAGES = {
    "upstream": ["硅料价格下跌三成"],
    "midstream": ["组件价格跌破现金成本线"],
    "downstream": ["电站装机增速放缓"],
}


def test_chain_shot_goes_right_after_the_opening(tmp_path: Path) -> None:
    path = _job(tmp_path)
    before = load_storyboard(path)

    info = insert_chain_shot(tmp_path, title="光伏产业链利润流向", stages=CHAIN_STAGES)

    after = load_storyboard(path)
    assert info["inserted_at"] == 1
    assert info["shot_id"] == "sh02"
    assert len(after.shots) == len(before.shots) + 1
    assert after.shots[0].narrative_purpose == "开场"
    assert after.shots[1].shot_id == "sh02"
    assert after.shots[1].asset_type == "composition"
    assert after.shots[1].asset_id == "CHAIN_01"


def test_chain_shot_keeps_the_storyboard_legal(tmp_path: Path) -> None:
    path = _job(tmp_path)
    insert_chain_shot(tmp_path, title="产业链", stages=CHAIN_STAGES)

    board = load_storyboard(path)
    # 镜头号唯一、时间连续不重叠、总时长等于各段之和——分镜校验就是这么要求
    ids = [shot.shot_id for shot in board.shots]
    assert ids == [f"sh{index:02d}" for index in range(1, len(ids) + 1)]
    for left, right in zip(board.shots, board.shots[1:]):
        assert right.start_time == pytest.approx(left.end_time, abs=1e-6)
    total = sum(shot.duration for shot in board.shots)
    assert board.duration == pytest.approx(total, abs=1e-6)


def test_chain_asset_is_registered_so_the_renderer_accepts_it(tmp_path: Path) -> None:
    _job(tmp_path)
    info = insert_chain_shot(tmp_path, title="产业链", stages=CHAIN_STAGES)

    asset_file = tmp_path / "assets" / "compositions" / f"{info['asset_id'].lower()}.json"
    assert asset_file.is_file()
    manifest = (tmp_path / "assets_manifest.csv").read_text(encoding="utf-8")
    assert "CHAIN_01" in manifest
    assert "composition" in manifest
    # 登记进清单的路径必须和分镜里写的一致，否则渲染阶段会找不到文件
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")
    shot = board.shot(info["shot_id"])
    assert (tmp_path / shot.asset_source).is_file()


def test_chain_shot_without_a_storyboard_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        insert_chain_shot(tmp_path, title="产业链", stages=CHAIN_STAGES)


def test_handoff_skips_the_chain_when_only_one_stage_has_nodes(tmp_path: Path) -> None:
    report = SimpleNamespace(
        title="只讲上游",
        findings=[
            {"section": "upstream", "text": "硅料价格下跌三成"},
            {"section": "risks", "text": "库存减值风险"},
        ],
    )
    result = handoff._try_chain_shot(report, tmp_path)
    assert result["ok"] is False
    assert result["reason"] == "not_a_chain_story"
    assert "1 段" in result["message"]
    assert not (tmp_path / "assets" / "compositions").exists()


def test_handoff_inserts_the_chain_when_two_stages_have_nodes(tmp_path: Path) -> None:
    _job(tmp_path)
    report = SimpleNamespace(
        title="光伏产业链利润流向",
        findings=[
            {"section": "upstream", "text": "硅料价格下跌三成，产能过剩"},
            {"section": "midstream", "text": "组件价格跌破现金成本线"},
            {"section": "risks", "text": "库存减值风险"},
        ],
    )
    result = handoff._try_chain_shot(report, tmp_path)

    assert result["ok"] is True
    assert result["inserted_at"] == 1
    assert result["stages"]["upstream"] == ["硅料价格下跌三成"]
    assert result["stages"]["downstream"] == []
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")
    assert board.shots[1].asset_id == "CHAIN_01"


def test_handoff_outline_payload_carries_the_chain_result(tmp_path: Path) -> None:
    report = SimpleNamespace(
        id=1,
        title="光伏产业链利润流向",
        summary="利润在中游被挤掉",
        findings=[
            {"section": "upstream", "text": "硅料价格下跌三成"},
            {"section": "downstream", "text": "电站装机增速放缓"},
        ],
    )
    evidence = [
        SimpleNamespace(source_id=1, claim="硅料价格下降了30%"),
        SimpleNamespace(source_id=2, claim="电站装机增速放缓到5%"),
    ]
    sources = [
        SimpleNamespace(id=1, title="甲", url="https://example.com/a", publisher="",
                        published_at="", content_hash=""),
        SimpleNamespace(id=2, title="乙", url="https://example.com/b", publisher="",
                        published_at="", content_hash=""),
    ]
    payload = handoff._try_outline(report, evidence, sources, tmp_path, "chain_case")

    assert payload["ok"] is True
    assert payload["chain"]["ok"] is True
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")
    assert board.shots[1].asset_id == "CHAIN_01"


def test_broken_chain_never_breaks_the_outline(tmp_path: Path) -> None:
    _job(tmp_path)
    report = SimpleNamespace(
        title="光伏产业链",
        findings=[
            {"section": "upstream", "text": "硅料价格下跌三成"},
            {"section": "downstream", "text": "电站装机增速放缓"},
        ],
    )
    # 分镜表被人为删掉：示意图插不进去，但交接不能因此整体失败
    (tmp_path / "storyboard" / "storyboard.csv").unlink()
    result = handoff._try_chain_shot(report, tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "chain_failed"
    assert "storyboard.csv" in result["message"]


def test_outline_still_refuses_a_single_source(tmp_path: Path) -> None:
    """回归：示意图不能成为绕过"至少 2 个来源"的理由。"""
    digest = dataclasses.replace(_digest(), sources=_digest().sources[:1])
    with pytest.raises(OutlineError) as error:
        build_outline(digest, job_root=tmp_path, job_id="one_source")
    assert error.value.reason == "insufficient_sources"
