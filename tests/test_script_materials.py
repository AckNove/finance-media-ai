"""口播稿 → 镜头素材计划 的测试（不联网、不花钱）。

守的是任务书里的三条底线：
1. 拆稿与时长以实测为准；
2. 匹配分不够就报缺口，不许硬塞不相关画面；
3. 模型答坏了要能退回规则，而不是抛一堆看不懂的异常。
"""

from __future__ import annotations

import json

import pytest

from backend.app.video import script_materials as sm


# ---------------------------------------------------------------- 拆稿


def test_split_whole_text_by_sentence_marks():
    lines = sm.split_narration("发电量在掉。电费收入却还在涨！钱从哪来？")
    assert [line.index for line in lines] == [1, 2, 3]
    assert lines[0].text == "发电量在掉。"
    assert lines[2].text == "钱从哪来？"


def test_split_rows_use_measured_seconds():
    rows = [{"text": "第一句", "seconds": 4.2}, {"narration": "第二句"}]
    lines = sm.split_narration(rows)
    assert lines[0].seconds == 4.2
    assert lines[0].spoken_seconds == 4.2
    # 第二句没有实测，按字数估且不少于最短镜头
    assert lines[1].seconds == 0.0
    assert lines[1].spoken_seconds >= sm.MIN_SHOT_SECONDS


def test_empty_script_is_rejected():
    with pytest.raises(sm.MaterialPlanError) as error:
        sm.split_narration("   ")
    assert error.value.reason == "empty_script"


# ---------------------------------------------------------------- 规则意图


def test_rule_intent_marks_mechanism_as_animation():
    line = sm.ScriptLine(index=1, text="上游涨价怎么传导到下游的利润分配？")
    intent = sm.rule_intent(line)
    assert intent.kind == "animation"
    assert intent.source == "rules"
    assert intent.queries


def test_rule_intent_marks_numbers_as_data():
    line = sm.ScriptLine(index=2, text="2026 年上半年利润同比降 6.2%")
    assert sm.rule_intent(line).kind == "data"


# ---------------------------------------------------------------- 模型意图


class _Reply:
    def __init__(self, text: str, ok: bool = True, error_code: str = "") -> None:
        self.text = text
        self.ok = ok
        self.error_code = error_code


def _complete_factory(text: str, *, ok: bool = True, error_code: str = ""):
    calls: list[dict] = []

    def complete(**kwargs):
        calls.append(kwargs)
        return _Reply(text, ok=ok, error_code=error_code)

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


def test_model_intents_parse_json_and_keep_order():
    lines = sm.split_narration("第一句。第二句。")
    reply = json.dumps(
        [
            {"index": 1, "kind": "real_video", "visual_goal": "看电厂", "queries": ["火电厂", "冷却塔"], "shot_hint": "航拍"},
            {"index": 2, "kind": "animation", "visual_goal": "画传导", "queries": ["产业链"], "shot_hint": "俯视"},
        ],
        ensure_ascii=False,
    )
    complete = _complete_factory(f"```json\n{reply}\n```")
    intents = sm.model_intents(lines, complete=complete)
    assert [intent.index for intent in intents] == [1, 2]
    assert intents[0].kind == "real_video"
    assert intents[0].queries == ("火电厂", "冷却塔")
    assert intents[1].kind == "animation"
    assert intents[0].source == "model"
    # 调用参数要对得上：purpose 固定、不缓存
    assert complete.calls[0]["purpose"] == "video.visual_plan"
    assert complete.calls[0]["cacheable"] is False


def test_model_intents_fall_back_per_line_when_index_missing():
    lines = sm.split_narration("第一句。第二句。")
    reply = json.dumps([{"index": 1, "kind": "real_image", "visual_goal": "g", "queries": ["变电"]}], ensure_ascii=False)
    intents = sm.model_intents(lines, complete=_complete_factory(reply))
    assert intents[0].source == "model"
    assert intents[1].source == "rules"


def test_model_failure_raises_stable_reason():
    lines = sm.split_narration("一句话。")
    with pytest.raises(sm.MaterialPlanError) as error:
        sm.model_intents(lines, complete=_complete_factory("", ok=False, error_code="pricing_missing"))
    assert error.value.reason == "model_unavailable"


def test_model_bad_json_raises_stable_reason():
    lines = sm.split_narration("一句话。")
    with pytest.raises(sm.MaterialPlanError) as error:
        sm.model_intents(lines, complete=_complete_factory("我拒绝输出 JSON"))
    assert error.value.reason == "model_bad_reply"


# ---------------------------------------------------------------- 素材库与匹配


def _asset(asset_id: str, title: str, *, kind: str = "video", **kwargs) -> sm.LibraryAsset:
    return sm.LibraryAsset(
        asset_id=asset_id,
        kind=kind,
        title=title,
        tags=(title,),
        local_path=f"D:/tmp/{asset_id}.mp4",
        credit="测试",
        license_name="CC BY 4.0",
        width=1080,
        height=1920,
        **kwargs,
    )


def test_match_prefers_relevant_asset_and_never_repeats():
    lines = sm.split_narration([{"text": "火电厂的冷却塔在冒白汽"}, {"text": "输电铁塔沿着山脊排开"}])
    intents = [sm.VisualIntent(index=1, kind="real_video", visual_goal="电厂", queries=("火电厂", "冷却塔")),
               sm.VisualIntent(index=2, kind="real_video", visual_goal="铁塔", queries=("输电铁塔",))]
    library = [
        _asset("V1", "火电厂 冷却塔 白汽"),
        _asset("V2", "输电铁塔 山脊 黄昏"),
    ]
    shots = sm.match_shots(lines, intents, library)
    assert shots[0].asset_id == "V1"
    assert shots[1].asset_id == "V2"
    assert shots[0].score >= sm.MIN_MATCH_SCORE


def test_match_reports_hole_instead_of_forcing_irrelevant_asset():
    lines = sm.split_narration("半导体光刻机的国产化进度")
    intents = [sm.VisualIntent(index=1, kind="real_video", visual_goal="光刻机", queries=("光刻机", "半导体"))]
    library = [_asset("V9", "风电场 风机 转动")]
    shots = sm.match_shots(lines, intents, library)
    assert shots[0].asset_id == ""
    assert shots[0].fallback == "manual"
    assert "素材库没有够贴近的画面" in shots[0].note


def test_animation_intent_is_not_forced_to_use_footage():
    lines = sm.split_narration("上游涨价怎么传到下游利润")
    intents = [sm.VisualIntent(index=1, kind="animation", visual_goal="传导", queries=("产业链",))]
    shots = sm.match_shots(lines, intents, [_asset("V1", "火电厂")])
    assert shots[0].asset_id == ""
    assert shots[0].fallback == "animation"
    assert shots[0].ok is True


def test_summary_counts_coverage_and_holes():
    lines = sm.split_narration("火电厂冷却塔。半导体光刻机。")
    intents = [sm.rule_intent(line) for line in lines]
    library = [_asset("V1", "火电厂 冷却塔 白汽")]
    shots = sm.match_shots(lines, intents, library)
    summary = sm.summarise(shots, library)
    assert summary["shots"] == 2
    assert summary["library_size"] == 1
    assert 0.0 <= summary["coverage"] <= 1.0
    assert summary["total_seconds"] > 0


def test_write_plan_roundtrip(tmp_path):
    lines = sm.split_narration("火电厂冷却塔在冒白汽。")
    intents = [sm.rule_intent(line) for line in lines]
    shots = sm.match_shots(lines, intents, [_asset("V1", "火电厂 冷却塔 白汽")])
    path = sm.write_plan(shots, tmp_path / "materials_plan.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["shots"][0]["index"] == 1
    assert payload["shots"][0]["intent"]["queries"]
    assert "ok" in payload["shots"][0]


def test_load_library_skips_non_commercial_and_missing_files(tmp_path):
    import csv

    job = tmp_path / "job"
    (job / "assets").mkdir(parents=True)
    good = job / "assets" / "good.mp4"
    good.write_bytes(b"x")
    register = job / "source_register.csv"
    with register.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "asset_id", "source_type", "publisher_or_subject", "local_path",
                "source_url", "license", "commercial_use", "note", "original_file", "duration_s",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "asset_id": "V1", "source_type": "video", "publisher_or_subject": "火电厂 冷却塔",
            "local_path": str(good), "source_url": "https://example.org/1", "license": "CC BY 4.0",
            "commercial_use": "allowed", "note": "白汽", "original_file": "good.mp4", "duration_s": "9",
        })
        writer.writerow({
            "asset_id": "V2", "source_type": "video", "publisher_or_subject": "不可商用",
            "local_path": str(good), "source_url": "", "license": "CC BY-NC 4.0",
            "commercial_use": "forbidden", "note": "", "original_file": "good.mp4", "duration_s": "9",
        })
        writer.writerow({
            "asset_id": "V3", "source_type": "video", "publisher_or_subject": "文件不存在",
            "local_path": str(job / "assets" / "missing.mp4"), "source_url": "", "license": "CC0",
            "commercial_use": "allowed", "note": "", "original_file": "missing.mp4", "duration_s": "9",
        })

    library = sm.load_library([tmp_path])
    assert [asset.asset_id for asset in library] == ["V1"]
    assert library[0].kind == "video"
    assert library[0].duration_s == 9.0
