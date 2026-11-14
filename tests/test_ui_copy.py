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


def test_video_page_runs_preflight_before_offering_a_render(source: str) -> None:
    """渲染要十几分钟，缺素材这种问题必须在点按钮之前就说——预检卡不能是摆设。"""
    assert "function preflightCard(pf, jobId)" in source
    assert "/preflight" in source
    # 预检有硬问题时渲染按钮必须是禁用状态，而不是让人点下去再等失败
    render_button = [line for line in source.splitlines() if 'id="render-film"' in line]
    assert render_button, "找不到渲染按钮"
    assert any("preflightBlocked(preflight)" in line for line in render_button), render_button[0].strip()[:160]
    assert 'title="先解决预检里的硬问题"' in source

def test_publish_prompt_checks_the_link_before_sending(source: str) -> None:
    """标已发布前先在本地拦一道：`abc` 这种值不该发出去，更不该被存下来当链接。"""
    assert "function publishLinkComplaint(url)" in source
    publish_block = source.split("[data-publish]", 1)[1].split("});", 1)[0]
    assert "publishLinkComplaint(url)" in publish_block, publish_block[:200]
    # 本地预检拦住时直接返回，不能继续 PATCH
    assert "if (complaint)" in publish_block
    assert "http:// 或 https://" in source


def test_task_detail_shows_the_artifact_before_the_gate(source: str) -> None:
    """闸门要求"看完再放行"，那页面上就得真有东西可看，而且要有一条放行路径。"""
    assert "function taskGateCard(gate, taskId)" in source
    assert "function taskArtifactsCard(bundle, taskId)" in source
    assert "/artifacts`" in source
    # 闸门卡上的按钮先把任务恢复再推进，不是只发一个 resume 就完事
    gate_block = source.split("getElementById('gate-pass')", 1)[1][:1500]
    assert "/resume" in gate_block and "/run" in gate_block


def test_task_detail_polls_by_id_not_by_card_index(source: str) -> None:
    """自动刷新曾经按 `.card` 下标取卡片——前面多插一张卡就会写错地方。"""
    assert 'id="progress-card"' in source
    assert 'id="events-card"' in source
    assert "querySelectorAll('.card')[1]" not in source
    assert "querySelectorAll('.card')[2]" not in source

def test_task_form_offers_the_pipeline_catalogue(source: str) -> None:
    """产线曾经在界面上写死 research_to_script，想"只研究不写脚本"没有入口。"""
    assert "api('/pipelines')" in source
    assert '<select name="pipeline">' in source
    form_block = source.split('id="task-form"', 1)[1][:1200]
    assert 'name="pipeline"' in form_block
    # 提交时必须把选中的产线带上，否则后端还是走默认那条
    submit_block = source.split("document.getElementById('task-form')", 1)[1][:1500]
    assert "pipeline: form.get('pipeline')" in submit_block
    # 列表上要能看出这一条跑的是哪条产线（planner = pipeline:<name>）
    assert ".replace(/^pipeline:/, '')" in source


def test_unknown_pipeline_error_is_actionable(source: str) -> None:
    """422 时后端把可选产线放在 details.available，界面要照着它说，而不是丢一坨 JSON。"""
    assert "function pipelineFailure(result, pipeline)" in source
    error_block = source.split("function pipelineFailure", 1)[1][:900]
    assert "available" in error_block
    assert "可选" in error_block


def test_the_scripts_page_only_offers_a_film_for_approved_scripts(source: str) -> None:
    """审核是道关：草稿/被驳回的脚本不该出现「做片子」按钮。"""
    assert "const FILM_READY_STATUSES = ['approved', 'storyboard_ready', 'produced'];" in source
    guard = source.split("const filmButton", 1)[1][:220]
    assert "FILM_READY_STATUSES.includes(row.status)" in guard
    assert "按这份脚本做片子" in guard
    # 点了要真去开任务，并且把"分镜没排出来"的原因显示出来
    handler = source.split("button[data-film]", 1)[1][:700]
    assert "/video-job" in handler
    assert "blocked_reason" in handler


def test_the_calendar_form_has_its_own_tags_field(source: str) -> None:
    """标签曾经是从「备注」里按空格切出来的：中文备注没空格，整段会变成一个标签。"""
    assert 'id="cal-tags"' in source
    create_block = source.split("view.querySelector('#cal-create')", 1)[1][:900]
    assert "tags" in create_block and "cal-tags" in create_block
    # 列表里要能看出这条排期有几个标签，没填要显眼
    assert "data-tags=" in source and "改标签" in source
    assert "平台建议：抖音 3~5 个、小红书 5~10 个" in source

def test_the_storyboard_and_assets_pages_point_at_each_other(source: str) -> None:
    """核对一张素材要来回翻两个页面：分镜表只写素材号，素材表只写镜头号，都不成链接。"""
    # 分镜页：素材号要能点回素材页，并且带着"看哪一条"
    assert "function assetLink(jobId, assetId)" in source
    sb = source.split("async function pageStoryboard", 1)[1][:2000]
    assert "assetLink(jobId" in sb
    assert "focusFromQuery('shot')" in sb
    # 素材页：镜头号要能点回分镜页，并且带着"看哪一镜"
    assert "function shotLinks(jobId, usedInShots)" in source
    assets = source.split("async function pageAssets", 1)[1][:2600]
    assert "shotLinks(jobId, row.used_in_shots)" in assets
    assert "focusFromQuery('asset')" in assets
    # 跳过去要标出来，否则等于没跳
    assert 'class="focus-row"' in source
    styles = APP_JS.parent / "styles.css"
    assert "tbody tr.focus-row" in styles.read_text(encoding="utf-8")
    # 目标不在表里时不许假装找到了
    assert "没有镜头" in sb and "素材清单里没有" in assets


def test_the_task_page_shows_the_budget_before_it_blocks_anything(source: str) -> None:
    """预算闸门只在被拒时留一条事件；人得先看见上限、已用、剩余。"""
    assert "function budgetCard(budget)" in source
    card = source.split("function budgetCard", 1)[1][:1800]
    assert "budget.exhausted" in card and "已经撞线" in card and "没设上限" in card
    assert "budget.calls_remaining" in card and "budget.cost_remaining_cny" in card
    # 没设上限的项不许编一个数出来（token 那行要写"不限"）
    assert "不限" in card
    # 撞线时要把原因码亮出来，别只说"不能用了"
    assert "budget.blocked_reason" in card
    detail = source.split("async function pageTaskDetail", 1)[1][:4000]
    assert "budgetCard(task.budget)" in detail


def test_money_does_not_print_six_useless_zeros(source: str) -> None:
    """0.5 元曾显示成「¥0.500000」；分以下的零头要留，多余的后导零要砍。"""
    block = source.split("function money", 1)[1][:420]
    assert "toFixed(6)" in block and "replace(/0+$/, '')" in block
