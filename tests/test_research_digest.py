"""研究底稿汇编与原文核对：只做程序能确定的事，不假装成分析。

这些用例固定三件事：

1. 来源编号稳定、按 URL 去重，字段照抄不补；
2. 候选数据点是**原样摘录**，带数字与单位才算，且有数量上限；
3. 核对只判"原话是否出现在摘录里"，找不到就标 missing 交人工，不判真伪。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.research.digest import (
    Digest,
    DataPoint,
    SourceRef,
    build_digest,
    extract_data_points,
    sources_from_collect,
    write_digest,
)
from backend.app.research.verify import verify_points


def collect_output(query: str, pages: list[dict]) -> dict:
    return {
        "planner": "keywords",
        "queries": [query],
        "results": [{"query": query, "hit_count": len(pages), "hits": [], "fetched": pages}],
        "fetched_pages": len(pages),
        "errors": [],
    }


def page(url: str, excerpt: str, *, title: str = "标题", published: str = "") -> dict:
    return {
        "url": url,
        "title": title,
        "published_at": published,
        "author": "",
        "site_name": "example.com",
        "sha256": "a" * 64,
        "snapshot_path": "data/snapshots/example.com/a.html",
        "char_count": len(excerpt),
        "excerpt": excerpt,
    }


# --- 数据点抽取 -----------------------------------------------------------


def test_extract_data_points_keeps_sentences_with_numbers_and_units():
    text = (
        "商业银行净息差降至1.53%，同比收窄17个基点。"
        "这句话没有任何数字。"
        "不良贷款率环比持平，为1.56%。"
    )
    points = extract_data_points(text, source_id="S01")
    assert [point.text for point in points] == [
        "商业银行净息差降至1.53%，同比收窄17个基点。",
        "不良贷款率环比持平，为1.56%。",
    ]
    assert points[0].numbers == ("1.53", "17")


def test_extract_data_points_ignores_bare_numbers_without_units():
    points = extract_data_points("2024 年第三季度数据已经发布完毕。", source_id="S01")
    assert points == []


def test_extract_data_points_respects_limit_and_dedupes():
    sentence = "净息差为1.53%，环比下降3个基点。"
    points = extract_data_points(sentence * 5, source_id="S01", limit=2)
    assert len(points) == 1, "同一句话反复出现只算一条"


# --- 来源清单 -------------------------------------------------------------


def test_sources_from_collect_dedupes_by_url_and_numbers_them():
    outputs = [
        collect_output("净息差", [page("https://a.example/1", "正文" * 100)]),
        collect_output("银行", [page("https://a.example/1", "正文" * 100), page("https://b.example/2", "正文" * 100)]),
    ]
    sources = sources_from_collect(outputs)
    assert [source.source_id for source in sources] == ["S01", "S02"]
    assert [source.url for source in sources] == ["https://a.example/1", "https://b.example/2"]
    assert sources[0].query == "净息差", "第一条来自第一个检索词"


def test_source_ref_dict_marks_unverified_and_keeps_empty_fields():
    ref = SourceRef(source_id="S01", title="", url="https://a.example/1")
    payload = ref.as_dict()
    assert payload["verification_status"] == "unverified"
    assert payload["published_at"] == "", "页面没给发布时间就留空，不猜"


# --- 底稿 -----------------------------------------------------------------


def test_build_digest_marks_itself_as_rules_and_carries_excerpts():
    outputs = [
        collect_output(
            "净息差",
            [page("https://a.example/1", "净息差为1.53%，同比收窄17个基点。" * 3)],
        )
    ]
    digest = build_digest(
        title="商业银行赚钱方式",
        objective="搞清楚净息差怎么影响利润",
        upstream=[{"sequence": 1, "task_type": "research.collect", "output": outputs[0]}],
        queries=["净息差"],
        now=datetime(2026, 5, 20, 9, 0, tzinfo=UTC),
    )
    assert digest.generated_by == "rules"
    assert digest.source_count == 1
    assert digest.data_points, "应该抽到带数字的句子"
    assert digest.meta["excerpts"]["S01"].startswith("净息差为1.53%")
    assert digest.generated_at == "2026-05-20T09:00:00+00:00"

    markdown = digest.to_markdown()
    assert "规则汇编（未使用模型）" in markdown
    assert "| 编号 | 标题 |" in markdown
    assert "候选数据点（原文摘录，未核验）" in markdown
    assert "这一版没有做的事" in markdown


def test_digest_handles_no_sources_honestly():
    digest = build_digest(title="空任务", upstream=[])
    assert digest.source_count == 0
    assert digest.data_points == ()
    assert "没有任何来源被抓取成功" in digest.to_markdown()


def test_write_digest_creates_markdown_and_json(tmp_path: Path):
    digest = build_digest(title="落盘", upstream=[])
    paths = write_digest(digest, tmp_path)
    assert paths["markdown"].exists() and paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["generated_by"] == "rules"
    assert payload["schema"] == 1
    assert paths["markdown"].read_text(encoding="utf-8").startswith("# 落盘")


# --- 原文核对 -------------------------------------------------------------


def _digest_with(excerpt: str, point_text: str) -> Digest:
    return Digest(
        title="核对",
        objective="",
        sources=(SourceRef(source_id="S01", title="t", url="https://a.example/1"),),
        data_points=(DataPoint(source_id="S01", text=point_text, numbers=("1.53",)),),
        meta={"excerpts": {"S01": excerpt}},
    )


def test_verify_points_finds_a_verbatim_quote_even_with_spacing():
    digest = _digest_with("净息差为 1.53%，同比收窄。", "净息差为1.53%，同比收窄。")
    report = verify_points(digest)
    assert report["checked"] == 1
    assert report["found"] == 1
    assert report["results"][0]["status"] == "found"


def test_verify_points_flags_missing_quote_for_human_review():
    digest = _digest_with("完全不相关的一段话。", "净息差为1.53%。")
    report = verify_points(digest)
    assert report["missing"] == 1
    assert report["results"][0]["status"] == "missing"
    assert "人工复核" in report["results"][0]["note"]


def test_verify_points_without_excerpts_reports_everything_missing():
    digest = Digest(
        title="无摘录",
        objective="",
        sources=(),
        data_points=(DataPoint(source_id="S01", text="净息差为1.53%。"),),
    )
    report = verify_points(digest)
    assert report["checked"] == 1
    assert report["found"] == 0


@pytest.mark.parametrize("text", ["", "   "])
def test_extract_data_points_tolerates_blank_text(text):
    assert extract_data_points(text, source_id="S01") == []
