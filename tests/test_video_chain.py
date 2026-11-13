"""产业链示意图的测试。

要证明的四件事：
1. 三行固定顺序（上游 → 中游 → 下游），空的环节也画出来并写「（本环缺证据）」；
2. 只认挂到产业链骨架节点上的结论，挂不上的句子不上图；
3. 画板契约不能破：easing 只有三种、关键帧落在 0..frames-1、**最后一帧仍在变化**；
4. 落盘后能在素材清单里查到（B 类、授权 allowed），重复写是幂等的。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.video import chain
from backend.app.video.chain import (
    CHAIN_STAGES,
    MAX_NODE_CHARS,
    MAX_NODES_PER_STAGE,
    chain_document,
    chain_path,
    nodes_from_findings,
    write_chain,
)

FINDINGS = [
    {"section": "upstream", "text": "硅料价格下跌三成，挤压上游一体化企业的利润空间"},
    {"section": "upstream", "text": "硅料价格下跌三成，挤压上游一体化企业的利润空间"},  # 重复
    {"section": "midstream", "text": "组件价格跌破现金成本线，中游率先出现亏损"},
    {"section": "downstream", "text": "电站装机增速放缓，下游议价能力反而上升"},
    {"section": "", "text": "没有挂到骨架上的结论不该出现在图里"},
    {"section": "risks", "text": "行业级风险也不属于这三天行"},
]


def test_stage_order_is_fixed_and_starts_upstream() -> None:
    assert [key for key, _ in CHAIN_STAGES] == ["upstream", "midstream", "downstream"]


def test_each_stage_gets_its_own_node_and_duplicates_are_dropped() -> None:
    stages = nodes_from_findings(FINDINGS)
    assert stages["upstream"] == ["硅料价格下跌三成"]
    assert stages["midstream"] == ["组件价格跌破现金成本线"]
    assert stages["downstream"] == ["电站装机增速放缓"]
    assert all("骨架" not in "".join(items) for items in stages.values())


def test_findings_without_a_section_never_reach_the_diagram() -> None:
    stages = nodes_from_findings([{"section": "", "text": "随便一句话"}, {"text": "连 section 都没有"}])
    assert stages == {"upstream": [], "midstream": [], "downstream": []}


def test_每行最多三个节点且最长十二个字() -> None:
    many = [
        {"section": "upstream", "text": f"上游第{index}个很长的节点名称需要被截断掉"}
        for index in range(6)
    ]
    stages = nodes_from_findings(many)
    assert len(stages["upstream"]) == MAX_NODES_PER_STAGE
    assert all(len(item) <= MAX_NODE_CHARS for item in stages["upstream"])


def test_empty_stage_is_drawn_not_hidden() -> None:
    document = chain_document(
        title="光伏产业链", stages={"upstream": ["硅料"], "midstream": [], "downstream": ["电站"]}, frames=60
    )
    texts = json.dumps(document, ensure_ascii=False)
    assert "（本环缺证据）" in texts
    assert "上游" in texts and "中游" in texts and "下游" in texts


def test_每行从上到下依次出现() -> None:
    document = chain_document(
        title="t",
        stages={"upstream": ["a"], "midstream": ["b"], "downstream": ["c"]},
        frames=90,
    )
    starts = {}
    for track in document["tracks"]:
        if track["property"] == "opacity" and track["node"].endswith("_row"):
            starts[track["node"]] = track["keys"][0][0]
    assert starts["upstream_row"] < starts["midstream_row"] < starts["downstream_row"]


def test_画板契约不破() -> None:
    frames = 100
    document = chain_document(
        title="t", stages={"upstream": ["a"], "midstream": ["b"], "downstream": ["c"]}, frames=frames
    )
    assert document["schema"] == 1
    assert document["canvas"]["frames"] == frames
    assert document["fonts"][0]["src"] == "fonts/NotoSansSC-VF.ttf"
    seen: set[tuple[str, str]] = set()
    for track in document["tracks"]:
        assert track["easing"] in ("linear", "smooth", "step")
        for frame, _value in track["keys"]:
            assert 0 <= frame <= frames - 1
        key = (track["node"], track["property"])
        assert key not in seen, f"同一节点同一属性只能一条轨道：{key}"
        seen.add(key)


def test_最后一帧仍在变化否则会被判成冻结画面() -> None:
    frames = 120
    document = chain_document(
        title="t", stages={"upstream": ["a"], "midstream": ["b"], "downstream": ["c"]}, frames=frames
    )
    moving = [
        track
        for track in document["tracks"]
        if track["keys"][-1][0] == frames - 1 and track["keys"][-1][1] != track["keys"][0][1]
    ]
    assert moving, "没有任何一条轨道持续到最后一帧"


def test_frames_must_be_at_least_two() -> None:
    with pytest.raises(ValueError):
        chain_document(title="t", stages={}, frames=1)


def test_write_chain_lands_in_the_job_and_registers_a_b_class_asset(tmp_path: Path) -> None:
    document = write_chain(
        tmp_path,
        asset_id="chain_01",
        title="光伏产业链利润流向",
        stages={"upstream": ["硅料"], "midstream": ["组件"], "downstream": ["电站"]},
        frames=80,
    )
    assert document.path == chain_path(tmp_path, "chain_01")
    assert document.path.is_file()
    assert document.relative_path.startswith("assets/compositions/")
    manifest = (tmp_path / "assets_manifest.csv").read_text(encoding="utf-8-sig")
    assert "chain_01" in manifest
    assert "allowed" in manifest

    first = document.path.read_bytes()
    write_chain(
        tmp_path,
        asset_id="chain_01",
        title="光伏产业链利润流向",
        stages={"upstream": ["硅料"], "midstream": ["组件"], "downstream": ["电站"]},
        frames=80,
    )
    assert document.path.read_bytes() == first  # 幂等


def test_register_can_be_skipped_for_dry_runs(tmp_path: Path) -> None:
    write_chain(
        tmp_path,
        asset_id="chain_dry",
        title="t",
        stages={"upstream": ["a"]},
        frames=40,
        register=False,
    )
    assert not (tmp_path / "assets_manifest.csv").exists()
    assert chain.chain_path(tmp_path, "chain_dry").is_file()
