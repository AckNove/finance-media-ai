"""一稿三版：同一份脚本改成三个平台的成稿。

这里只测「编排规则」：长度、分行、标签、警告。
**不测「好不好看」**——那是人看的事。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.content import PLATFORMS, SPECS, adapt, adapt_one, adapt_payload

LONG_TITLE = "商业银行到底靠什么赚钱：一张图看懂净息差是怎么被吃掉的，以及为什么 2024 年这么难"
BODY = (
    "净息差从 2020 年的 2.10% 降到 2024 年的 1.52%。这意味着银行每放 100 元贷款，赚的差价少了 0.58 元。\n\n"
    "第二段：手续费及佣金净收入占比约 19.3%，投资收益约 10.5%，利息净收入仍是基本盘。"
)


def _one(platform: str, *, title: str = LONG_TITLE, body: str = BODY, tags=()):
    return adapt_one(spec=SPECS[platform], title=title, body=body, tags=tags)


def _topic(client: TestClient, title: str = "净息差为什么一直在降") -> int:
    response = client.post("/api/topics", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _script(client: TestClient) -> int:
    created = client.post(
        "/api/scripts",
        json={"topic_id": _topic(client), "title": LONG_TITLE, "body": BODY},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_three_platforms_have_the_spec_every_caller_relies_on() -> None:
    assert PLATFORMS == ("douyin", "xiaohongshu", "wechat")
    for key in PLATFORMS:
        spec = SPECS[key]
        assert spec.key == key
        assert spec.title_max > 0
        assert spec.name
    # 抖音与小红书要分行（竖屏阅读），公众号要保留段落
    assert SPECS["douyin"].break_lines is True
    assert SPECS["xiaohongshu"].break_lines is True
    assert SPECS["wechat"].break_lines is False
    assert SPECS["wechat"].line_max == 0


def test_douyin_title_is_cut_to_its_limit() -> None:
    item = _one("douyin")
    assert len(item.title) <= SPECS["douyin"].title_max
    assert item.title.startswith("商业银行到底靠什么赚钱")
    assert any("标题" in warning and "截断" in warning for warning in item.warnings)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_line_breaking_never_splits_a_number(platform: str) -> None:
    item = _one(platform)
    joined = "".join(line.strip() for line in item.body.splitlines())
    for number in ("2.10%", "1.52%", "0.58", "19.3%", "10.5%", "100"):
        assert number in joined, f"{number} 被切断了"
    if not SPECS[platform].break_lines:
        assert "\n\n" in item.body
        return
    for line in item.body.splitlines():
        stripped = line.strip()
        assert stripped, "不该有空行"
        # 行尾既不能断在小数点后，也不能断成半截数字（单位必须跟着数字走）
        assert not stripped.endswith("."), f"行尾切出了小数点：{stripped}"
        tail = stripped[-4:]
        if stripped[-1].isdigit():
            assert any(unit in tail for unit in ("%", "％", "元")), f"行尾像半截数字：{stripped}"


def test_hashtags_are_never_invented() -> None:
    bare = _one("douyin", title="银行怎么赚钱", tags=())
    assert bare.hashtags == ()
    assert any("标签" in warning for warning in bare.warnings)
    given = _one("douyin", title="银行怎么赚钱", tags=("银行", "净息差"))
    assert given.hashtags[:2] == ("银行", "净息差")


def test_tags_are_deduped_and_capped() -> None:
    tags = [f"标签{i}" for i in range(12)] + ["标签0"]
    item = _one("xiaohongshu", title="银行怎么赚钱", tags=tags)
    assert len(item.hashtags) == SPECS["xiaohongshu"].hashtag_max
    assert len(set(item.hashtags)) == len(item.hashtags)


def test_risky_call_to_action_words_are_flagged() -> None:
    item = _one("wechat", body="想了解细节，点击链接加微信。净息差降到了 1.52%。")
    assert any("导流" in warning for warning in item.warnings)
    # 只提示，不代删
    assert "加微信" in item.body


def test_wechat_keeps_paragraphs_and_douyin_flattens_them() -> None:
    wechat = _one("wechat")
    douyin = _one("douyin")
    assert "\n\n" in wechat.body
    assert "\n\n" not in douyin.body
    lines = [line for line in douyin.body.splitlines() if line]
    # 分行器允许整句跨行时略超一点点，但不能离谱
    assert all(len(line) <= SPECS["douyin"].line_max + 6 for line in lines)


def test_data_free_body_gets_a_gentle_nudge() -> None:
    item = _one("douyin", body="银行靠存贷款利差和中间业务赚钱，说起来简单。")
    assert any("数据" in warning for warning in item.warnings)


def test_unknown_platform_is_refused() -> None:
    with pytest.raises(ValueError, match="不认识的平台"):
        adapt(title=LONG_TITLE, body=BODY, platforms=("kuaishou",))


def test_payload_reports_platforms_and_counts() -> None:
    envelope = adapt_payload(title=LONG_TITLE, body=BODY, platforms=("douyin", "wechat"))
    assert envelope["platforms"] == ["douyin", "wechat"]
    assert len(envelope["variants"]) == 2
    assert envelope["note"]
    # 不传平台就是三个都要
    assert len(adapt(title=LONG_TITLE, body=BODY)) == 3


def test_summary_never_exceeds_its_limit() -> None:
    for key in PLATFORMS:
        item = _one(key)
        assert item.summary
        assert len(item.summary) <= SPECS[key].summary_max


def test_numbers_are_collected_for_the_data_card() -> None:
    item = _one("douyin")
    assert "1.52%" in item.numbers
    assert "19.3%" in item.numbers


def test_api_returns_three_variants(client: TestClient) -> None:
    script_id = _script(client)
    adapted = client.post(f"/api/scripts/{script_id}/adapt", json={})
    assert adapted.status_code == 200, adapted.text
    data = adapted.json()
    assert data["script_id"] == script_id
    assert data["platforms"] == ["douyin", "xiaohongshu", "wechat"]
    assert len(data["variants"]) == 3
    assert data["note"]
    for variant in data["variants"]:
        assert variant["title"]
        assert variant["body"]
        assert isinstance(variant["hashtags"], list)
        assert isinstance(variant["warnings"], list)
        assert variant["numbers"]


def test_api_can_restrict_platforms_and_pass_tags(client: TestClient) -> None:
    script_id = _script(client)
    adapted = client.post(
        f"/api/scripts/{script_id}/adapt",
        json={"platforms": ["wechat"], "tags": ["银行"]},
    )
    assert adapted.status_code == 200, adapted.text
    data = adapted.json()
    assert data["platforms"] == ["wechat"]
    assert data["variants"][0]["hashtags"] == ["银行"]


def test_api_rejects_unknown_platform(client: TestClient) -> None:
    script_id = _script(client)
    bad = client.post(f"/api/scripts/{script_id}/adapt", json={"platforms": ["kuaishou"]})
    assert bad.status_code == 422


def test_api_returns_404_for_unknown_script(client: TestClient) -> None:
    missing = client.post("/api/scripts/999999/adapt", json={})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
