"""`backend/app/video/script_board.py` 的回归测试（全离线：不渲染、不联网、不调模型）。

动机：系统原来有两条互不相干的喉舌——`content.script` 让模型把**人工确认过的结论**写成脚本，
建视频任务时却只按报告数据点排规则分镜，模型写的稿子进不了片子。
这一层把脚本排成分镜（带数据卡），让"脚本 → 分镜 → 成片"真的接上。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.content.stages import ScriptDraft, ScriptLine, plan_shots
from backend.app.media.storyboard import load_storyboard
from backend.app.video.script_board import (
    ScriptStoryboardError,
    draft_from_script,
    script_storyboard,
    storyboard_from_draft,
)

LINES = [
    ScriptLine(
        text="净息差已经降到1.52%，这是2024年第四季度的数。",
        role="evidence",
        source_ids=("S01",),
        numbers=("1.52%",),
    ),
    ScriptLine(
        text="贷款利息收得少了，存款还得照样付息。",
        role="mechanism",
        source_ids=("S02",),
    ),
    ScriptLine(
        text="中间业务只能补一部分。",
        role="closing",
        source_ids=("S03",),
    ),
]


def _draft() -> ScriptDraft:
    return ScriptDraft(
        title="商业银行是怎么赚钱的",
        hook="银行赚钱靠什么？",
        lines=tuple(LINES),
        disclaimer="本片为合规资料整理，不构成投资建议。",
    )


def _script_row(**overrides):
    data = {
        "id": 7,
        "title": "商业银行是怎么赚钱的",
        "hook": "银行赚钱靠什么？",
        "segments": [
            {
                "text": line.text,
                "role": line.role,
                "source_ids": list(line.source_ids),
                "numbers": list(line.numbers),
            }
            for line in LINES
        ],
        "disclaimer": "本片为合规资料整理，不构成投资建议。",
    }
    data.update(overrides)
    return data


def test_draft_from_script_round_trips_segments() -> None:
    draft = draft_from_script(_script_row())

    assert draft.title == "商业银行是怎么赚钱的"
    assert draft.hook == "银行赚钱靠什么？"
    assert len(draft.lines) == 3
    assert draft.lines[0].source_ids == ("S01",)
    assert draft.lines[0].numbers == ("1.52%",)
    assert draft.generated_by == "script"


def test_a_script_without_segments_is_refused_plainly() -> None:
    with pytest.raises(ScriptStoryboardError) as caught:
        draft_from_script(_script_row(segments=[]))
    assert caught.value.reason == "empty_script"


def test_one_line_is_not_enough_for_a_film() -> None:
    draft = ScriptDraft(title="t", hook="h", lines=(LINES[0],))
    with pytest.raises(ScriptStoryboardError) as caught:
        storyboard_from_draft(draft, job_root=Path("unused"))
    assert caught.value.reason == "too_few_lines"


def test_storyboard_keeps_the_script_sentences_and_their_sources(tmp_path: Path) -> None:
    result = storyboard_from_draft(_draft(), job_root=tmp_path)

    assert result["ok"] is True
    assert result["source"] == "script"
    assert result["shot_count"] == 5  # 开场 + 3 句 + 收尾
    assert result["card_count"] == 1  # 只有带单位那句做成数据卡

    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")
    narrations = [shot.narration for shot in board.shots]
    assert narrations[0] == "银行赚钱靠什么？"
    assert LINES[0].text in narrations
    assert narrations[-1].startswith("本片为合规资料整理")

    # 来源要落在可追溯列上：内容质检靠它判"这一镜能不能回溯"
    assert board.shots[1].source_ids == "S01"
    assert board.shots[2].source_ids == "S02"


def test_numeric_sentence_becomes_a_data_card_and_registers_as_an_asset(tmp_path: Path) -> None:
    result = storyboard_from_draft(_draft(), job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")
    card_shot = board.shots[1]

    assert card_shot.asset_type == "composition"
    assert card_shot.asset_id.startswith("CARD_sh")
    assert (tmp_path / card_shot.asset_source).is_file()
    # 数据卡上已经写了说明，底部不再重复叠字幕
    assert card_shot.subtitle == ""

    manifest = (tmp_path / "assets_manifest.csv").read_text(encoding="utf-8")
    assert card_shot.asset_id in manifest
    assert result["card_count"] == 1


def test_sentences_without_units_become_headline_cards(tmp_path: Path) -> None:
    """没有数字的句子也要有画面。

    早期成片这里是"纯色底 + 底部字幕"，抽帧后整片就是会动的 PPT（用户原话
    "做出来的质量极差"）。现在每句都画成一块整句大字画板，字幕不再重复。
    """
    board_draft = ScriptDraft(
        title="t",
        hook="开场",
        lines=(
            ScriptLine(text="第一条没有数字。", role="evidence", source_ids=("S01",)),
            ScriptLine(text="第二条也没有数字。", role="evidence", source_ids=("S02",)),
        ),
    )
    result = storyboard_from_draft(board_draft, job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")

    assert all(shot.asset_type == "composition" for shot in board.shots)
    assert all(shot.asset_id.startswith("TEXT_") for shot in board.shots)
    assert all(shot.subtitle == "" for shot in board.shots)
    assert all((tmp_path / shot.asset_source).is_file() for shot in board.shots)
    assert result["text_count"] == len(board.shots)
    assert result["card_count"] == 0


def test_timeline_is_contiguous_and_starts_at_zero(tmp_path: Path) -> None:
    storyboard_from_draft(_draft(), job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")

    assert board.shots[0].start_time == 0
    for previous, current in zip(board.shots, board.shots[1:]):
        assert abs(previous.end_time - current.start_time) < 1e-6
    assert board.duration > 0


def test_plan_shots_and_storyboard_agree_on_shot_count(tmp_path: Path) -> None:
    draft = _draft()
    storyboard_from_draft(draft, job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")

    assert len(plan_shots(draft)) == len(board.shots)


def test_capacity_units_also_become_data_cards(tmp_path: Path) -> None:
    """光伏/储能类研究满篇 GW、千瓦时——漏掉这些单位，一份报告一张卡都做不出来。"""
    draft = ScriptDraft(
        title="光伏",
        hook="先看装机。",
        lines=(
            ScriptLine(
                text="2024年全球光伏组件产能约920GW。",
                role="evidence",
                source_ids=("S01",),
                numbers=("920GW",),
            ),
            ScriptLine(
                text="新增装机对应的用电量约1200千瓦时。",
                role="evidence",
                source_ids=("S02",),
                numbers=("1200千瓦时",),
            ),
        ),
    )
    result = storyboard_from_draft(draft, job_root=tmp_path)

    assert result["card_count"] == 2
    board = (tmp_path / "storyboard" / "storyboard.csv").read_text(encoding="utf-8")
    assert "920GW" in board
    assert "1200千瓦时" in board


def test_headline_never_scales_the_text_block(tmp_path: Path) -> None:
    """整块文字缓慢放大 = 引擎判"倒着放一遍不一致"。

    实测事故：`lines` 节点上挂一条跨整片 0.97→1.03 的 `scale` 轨道，渲染时报
    `ContractError: Composition is not deterministic when seeking backwards`，
    表现是这条分镜渲染直接失败。改法是让非文字色条去动（`accent` 的 scale），
    文字只做入场动画。
    """

    draft = ScriptDraft(
        title="t",
        hook="开场",
        lines=(
            ScriptLine(text="这一句没有数字，只能用大字说。", role="hook", source_ids=("S01",)),
            ScriptLine(text="这一句同样没有数字，也要有画面。", role="evidence", source_ids=("S02",)),
        ),
    )
    storyboard_from_draft(draft, job_root=tmp_path)

    for path in sorted((tmp_path / "assets" / "compositions").glob("text_*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        scaled_text = [
            track
            for track in document["tracks"]
            if track["property"] == "scale" and track["node"] in {"lines", "line1", "line2", "line3"}
        ]
        assert scaled_text == [], f"{path.name} 又给文字块加了缩放轨道"
        assert any(track["property"] == "scale" for track in document["tracks"]), (
            f"{path.name} 没有任何持续运动，成片会被判成冻结画面"
        )


def test_every_composition_obeys_the_engine_track_rules(tmp_path: Path) -> None:
    """把引擎的轨道规则写成断言，省得下次再靠渲染失败才发现。

    规则来自 `tools/openkatana/docs/COMPOSITIONS.md`：属性只有六个、缓动只有三种、
    关键帧必须落在 `0..frames-1`、同一节点同一属性只能有一条轨道。
    """

    allowed = {"opacity", "reveal", "translateX", "translateY", "rotate", "scale"}
    easings = {"linear", "smooth", "step"}
    result = storyboard_from_draft(_draft(), job_root=tmp_path)
    assert result["shot_count"] >= 2

    for path in sorted((tmp_path / "assets" / "compositions").glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        frames = document["canvas"]["frames"]
        seen: set[tuple[str, str]] = set()
        for track in document["tracks"]:
            assert track["property"] in allowed, f"{path.name}: 未知属性 {track['property']}"
            assert track["easing"] in easings, f"{path.name}: 未知缓动 {track['easing']}"
            key = (track["node"], track["property"])
            assert key not in seen, f"{path.name}: {key} 有两条轨道"
            seen.add(key)
            for frame, _value in track["keys"]:
                assert 0 <= frame <= frames - 1, f"{path.name}: 关键帧 {frame} 越界（0..{frames - 1}）"


def test_script_storyboard_accepts_a_script_row(tmp_path: Path) -> None:
    result = script_storyboard(_script_row(), job_root=tmp_path)

    assert result["shot_count"] == 5
    notes = (tmp_path / "storyboard" / "FROM_SCRIPT.md").read_text(encoding="utf-8")
    assert "分镜来自脚本" in notes
    assert "不要在这里改字" in notes


def test_captions_obey_the_same_screen_limit_as_the_rule_storyboard(tmp_path: Path) -> None:
    """字幕是 46px 中文，一屏只放得下 18 个字；两套分镜必须用同一个上限。

    实测事故：脚本分镜曾经自己写 24 字上限，成片里"全产业链亏损：2024年三季度起…"
    直接顶到画面左右边缘。
    """

    from backend.app.video import outline, script_board

    assert script_board.MAX_CAPTION_CHARS == outline.MAX_CAPTION_CHARS == 18

    draft = ScriptDraft(
        title="光伏",
        hook="光伏为什么亏",
        lines=(
            ScriptLine(
                text="全产业链亏损：2024年三季度起主链毛利率持续为负，硅料硅片电池组件普遍亏损。",
                role="evidence",
                source_ids=("S01",),
            ),
            ScriptLine(
                text="供给过剩把报价压到成本线以下。",
                role="mechanism",
                source_ids=("S02",),
            ),
        ),
    )
    storyboard_from_draft(draft, job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")

    # 现在每句都有画板，字幕不再兜底；但只要有字幕，就必须守同一个上限。
    subtitles = [shot.subtitle for shot in board.shots if shot.subtitle]
    if subtitles:
        assert max(len(text) for text in subtitles) <= script_board.MAX_CAPTION_CHARS
    assert all(shot.asset_type == "composition" for shot in board.shots)

