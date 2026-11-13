"""字幕字号自适应 + 按标点收口的回归测试（全离线）。"""

from __future__ import annotations

from pathlib import Path

from backend.app.content.stages import DISCLAIMER, ScriptDraft, ScriptLine
from backend.app.media.storyboard import load_storyboard
from backend.app.video.render import CAPTION_WIDTH_BUDGET, _display_width, caption_font_size
from backend.app.video.script_board import MAX_CAPTION_CHARS, _caption, storyboard_from_draft


def _draft(*lines: ScriptLine) -> ScriptDraft:
    return ScriptDraft(title="光伏", hook="开场", lines=lines)


def test_long_captions_are_cut_at_a_sentence_end_without_ellipsis() -> None:
    caption = _caption(DISCLAIMER)

    assert caption == "本片为合规资料整理，不构成投资建议"
    assert not caption.endswith("…")


def test_mid_sentence_cut_keeps_the_ellipsis_and_enough_words() -> None:
    caption = _caption("全产业链亏损：2024年三季度起主链毛利率持续为负，硅料硅片电池组件普遍亏损。")

    assert len(caption) <= MAX_CAPTION_CHARS
    assert caption.endswith("…")
    # 不能因为句子开头有个冒号就把整句砍成"全产业链亏损…"
    assert len(caption) >= 12


def test_short_caption_is_left_alone() -> None:
    assert _caption("供给过剩把报价压到成本线以下。") == "供给过剩把报价压到成本线以下。"


def test_caption_font_size_shrinks_only_when_needed() -> None:
    class _Shot:
        def __init__(self, subtitle: str) -> None:
            self.subtitle = subtitle

    class _Board:
        def __init__(self, *subtitles: str) -> None:
            self.shots = [_Shot(text) for text in subtitles]
            self.width = 1080

    short = _Board("资料汇编 · 不是投资建议")
    assert caption_font_size(short, base=46, width=1080) == 46

    long = _Board("本片为合规资料整理，不构成投资建议")
    size = caption_font_size(long, base=46, width=1080)
    assert size < 46
    assert size >= 32
    assert _display_width("本片为合规资料整理，不构成投资建议") * size <= 1080 * CAPTION_WIDTH_BUDGET


def test_storyboard_captions_fit_the_frame(tmp_path: Path) -> None:
    draft = _draft(
        ScriptLine(
            text="全产业链亏损：2024年三季度起主链毛利率持续为负，硅料硅片电池组件普遍亏损。",
            role="evidence",
            source_ids=("S01",),
        ),
        ScriptLine(
            text=DISCLAIMER,
            role="closing",
            source_ids=("S02",),
        ),
    )
    storyboard_from_draft(draft, job_root=tmp_path)
    board = load_storyboard(tmp_path / "storyboard" / "storyboard.csv")

    subtitles = [shot.subtitle for shot in board.shots if shot.subtitle]
    assert subtitles
    assert max(len(text) for text in subtitles) <= MAX_CAPTION_CHARS
    size = caption_font_size(board, base=46, width=board.width)
    widest = max(_display_width(text) for text in subtitles)
    assert widest * size <= board.width, "字幕宽度必须落在画面内"
