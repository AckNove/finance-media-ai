"""数据卡登记进素材清单时，"用于镜头"必须是镜头号。

踩过的坑：register_cards 过去写 used_in_shots=asset_id（`CARD_sh02`），而镜头号是
`sh02`。于是素材页写"用于镜头 CARD_sh02"、分镜页找不到这一镜，两边对不上；
人在核对授权时最需要这两个页面互相指得准。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend.app.video.cards import CardDocument, register_cards, shot_of_card, write_card


def _rows(job: Path) -> list[dict[str, str]]:
    with (job / "assets_manifest.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _card(job: Path, *, asset_id: str, shot_id: str, frames: int = 30) -> CardDocument:
    return write_card(
        job,
        asset_id=asset_id,
        number="1.52%",
        caption="2024年第四季度净息差降至1.52%",
        source_id="S01",
        frames=frames,
        shot_id=shot_id,
    )


def test_shot_of_card_reads_the_shot_id_out_of_the_asset_id() -> None:
    assert shot_of_card("CARD_sh02") == "sh02"
    assert shot_of_card("card_sh02") == "sh02"
    # 猜不出来就返回空串：不许硬编一个看起来像的
    assert shot_of_card("plate_01") == ""
    assert shot_of_card("") == ""


def test_register_cards_records_the_shot_id_not_the_asset_id(tmp_path: Path) -> None:
    card = _card(tmp_path, asset_id="CARD_sh02", shot_id="sh02")
    assert register_cards(tmp_path, [card]) == ["CARD_sh02"]
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "CARD_sh02"
    assert rows[0]["used_in_shots"] == "sh02", "这一列是给哪个镜头用的，不能填素材号"


def test_register_cards_falls_back_to_the_prefix_when_the_caller_forgot(
    tmp_path: Path,
) -> None:
    """老调用方不传 shot_id 时按前缀兜底，别默默写空。"""
    document = _card(tmp_path, asset_id="CARD_sh07", shot_id="")
    assert document.shot_id == ""
    register_cards(tmp_path, [document])
    assert _rows(tmp_path)[0]["used_in_shots"] == "sh07"


@pytest.mark.parametrize(("asset_id", "expected"), [
    ("CARD_sh01", "sh01"),
    ("CARD_sh12", "sh12"),
    ("solid_1d2a44", ""),
])
def test_shot_fallback_table(asset_id: str, expected: str) -> None:
    assert shot_of_card(asset_id) == expected
