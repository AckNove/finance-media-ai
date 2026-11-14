"""前端文案与接口字段的一致性护栏。

为什么要有这个文件：页面上的说明文字是"给人看的承诺"，它们最容易在功能演进后
悄悄变成假话（比如分镜早就可能按脚本排了，说明里还写着"纯规则"）。这里只做
两件成本极低的事：文案里不许残留 Markdown 记号、以及分镜来源这句话必须跟着
`outline.source` 分叉。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"


@pytest.fixture(scope="module")
def source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_no_markdown_emphasis_leaks_into_page_text(source: str) -> None:
    """页面用 innerHTML 直出字符串，`**加粗**` 会原样显示成两个星号。"""
    offenders: list[str] = []
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        # 注释里写 Markdown 无所谓，只查真正会进页面的模板字符串与字符串字面量
        if stripped.startswith(("*", "//", "/*")):
            continue
        if "**" in line:
            offenders.append(f"{number}: {stripped[:80]}")
    assert offenders == [], "页面文案里残留了 Markdown 记号：\n" + "\n".join(offenders)


def test_storyboard_source_sentence_follows_the_payload(source: str) -> None:
    """分镜说明必须按 outline.source 分叉——早期写死"纯规则"，脚本驱动时就是假话。"""
    assert "outline.source === 'script'" in source
    assert "按你确认过的那版脚本排的" in source
    assert "分镜是按规则排的草稿" in source


def test_video_job_card_links_to_the_preview_page(source: str) -> None:
    """建完任务、渲染完都要能一脚走到视频页，不用回去记任务号。"""
    assert "#/video?job=${encodeURIComponent(job.job_id)}" in source


def test_render_finished_links_to_the_film(source: str) -> None:
    """渲染跑完那一行必须挂一个"去看成片"的链接（同一个模板字符串里带 done 判断）。"""
    lines = [line for line in source.splitlines() if "去看成片" in line]
    assert lines, "渲染完成的提示里没有通往成片的链接"
    assert any("data.status === 'done'" in line for line in lines), lines[0].strip()[:120]


def test_timestamps_are_read_as_utc_and_shown_in_local_time(source: str) -> None:
    """后端存的是 naive UTC：直接 new Date(str) 会按本地解释，北京时间差 8 小时。"""
    assert "function parseTs(value)" in source
    assert "'Z'" in source, "缺时区的时间要补 Z 当 UTC 解析"
    # when() 必须走 parseTs，而不是把原始字符串截断显示
    when_block = source.split("function when(value) {", 1)[1].split("\n}", 1)[0]
    assert "parseTs(value)" in when_block
    assert "slice(0, 19)" not in when_block
    # 排期相关的比较一律不许再拿原始字符串构造 Date
    assert "new Date(row.scheduled_for)" not in source


def test_home_shows_what_to_publish_today(source: str) -> None:
    """工作台一打开就该知道今天欠哪几条，而不是去日历里翻。"""
    assert "function publishQueueCard(result)" in source
    assert "今天该发什么" in source
    assert "api('/calendar?limit=100')" in source
    assert "系统不会自己发布" in source
