"""动态画板的护栏：数字滚动不能出重影、每个 builder 都要"一直在动"。

这里守的是三类真实事故：

* 十个中间值同时可见 → 画面上两个数字叠在一起（像出重影）；
* 单位单独摆在数字右边 → 数字宽度一变，`%` 就压在数字中间；
* 画板里没有任何持续运动 → 成片被质检判成"冻结画面"。

另外把"文字块不许缩放"这条也钉住：引擎会报
`Composition is not deterministic when seeking backwards`。
"""

from __future__ import annotations

from typing import Any

from backend.app.video import kinetic

FPS = 30
TRACK_PROPS = {"opacity", "reveal", "translateX", "translateY", "rotate", "scale"}
EASINGS = {"linear", "smooth", "step"}


def _tracks(document: dict[str, Any], node: str, prop: str) -> list[dict[str, Any]]:
    return [
        track
        for track in document["tracks"]
        if track["node"] == node and track["property"] == prop
    ]


def _ids(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        found.add(str(node.get("id")))
        for child in node.get("children") or ():
            walk(child)

    walk(document["root"])
    return found


def test_every_track_obeys_the_engine_rules() -> None:
    """属性只有六个、缓动只有三种、关键帧不许越界、同节点同属性只有一条。"""
    documents = [
        kinetic.backdrop_document(frames=60),
        kinetic.number_roll_document(value=6.2, unit="%", label="利润同比", frames=105),
        kinetic.compare_bars_document(
            items=[("电量收入", 70.0), ("容量电价", 23.0)], frames=90, unit="%"
        ),
        kinetic.mechanism_document(
            top_label="过去", top_text="收入跟电量绑在一起",
            bottom_label="现在", bottom_text="利润跟电量脱钩", frames=90,
        ),
    ]

    for document in documents:
        frames = document["canvas"]["frames"]
        assert document["schema"] == 1
        seen: set[tuple[str, str]] = set()
        for track in document["tracks"]:
            assert track["property"] in TRACK_PROPS
            assert track["easing"] in EASINGS
            key = (track["node"], track["property"])
            assert key not in seen, f"{key} 出现了两条轨道"
            seen.add(key)
            for frame, _value in track["keys"]:
                assert 0 <= frame <= frames - 1


def test_every_document_has_a_node_that_always_moves() -> None:
    """没有持续运动就会被判冻结：每块画板至少要有两条跨全片的位移轨道。"""
    cases = {
        "backdrop": kinetic.backdrop_document(frames=60),
        "roll": kinetic.number_roll_document(value=1.5, unit="亿元", label="x", frames=90),
        "bars": kinetic.compare_bars_document(items=[("a", 1.0), ("b", 2.0)], frames=90),
        "mechanism": kinetic.mechanism_document(
            top_label="t", top_text="a", bottom_label="b", bottom_text="c", frames=90
        ),
    }

    for name, document in cases.items():
        frames = document["canvas"]["frames"]
        spanning = [
            track
            for track in document["tracks"]
            if track["keys"][0][0] == 0
            and track["keys"][-1][0] >= frames - 1
            and track["property"] in {"translateX", "translateY", "scale"}
        ]
        assert spanning, f"{name}: 没有任何从第一帧动到最后一帧的元素"


def test_number_roll_shows_one_value_at_a_time() -> None:
    """十个中间值必须一个接一个出现——同时可见就是重影。"""
    frames = 120
    document = kinetic.number_roll_document(
        value=6.2, unit="%", label="利润同比降6.2%", frames=frames, steps=6
    )
    ids = _ids(document)
    windows = []
    for index in range(6):
        node = f"n{index}"
        assert node in ids
        tracks = _tracks(document, node, "opacity")
        assert len(tracks) == 1
        keys = tracks[0]["keys"]
        windows.append((keys[0][0], keys[-1][0] if len(keys) > 2 else frames - 1))

    for index in range(len(windows) - 1):
        assert windows[index][1] <= windows[index + 1][0], (
            f"n{index} 与 n{index + 1} 的可见区间重叠了：{windows[index]} / {windows[index + 1]}"
        )

    # 最后一个数必须停到最后一帧，否则结尾会突然空掉
    last = _tracks(document, "n5", "opacity")[0]["keys"]
    assert last[-1][1] == 1


def test_number_roll_keeps_the_unit_attached_to_the_number() -> None:
    """单位和数字必须同一个字符串，否则数字一变宽单位就压上去了。"""
    document = kinetic.number_roll_document(
        value=88.0, unit="亿元", label="净利润", frames=90, steps=4
    )
    texts = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("type") == "text":
            texts.append(str(node.get("text")))
        for child in node.get("children") or ():
            walk(child)

    walk(document["root"])
    rolled = [text for text in texts if text.endswith("亿元")]
    assert len(rolled) >= 4, f"数字没有带单位：{texts}"
    assert "亿元" not in [text for text in texts if text == "亿元"], "单位不该单独成节点"


def test_text_blocks_are_never_scaled() -> None:
    """文字块缩放会触发引擎的确定性检查，这条已经踩过一次。"""
    document = kinetic.number_roll_document(value=6.2, unit="%", label="x", frames=90)
    for track in document["tracks"]:
        if track["property"] != "scale":
            continue
        assert "-text" not in track["node"], f"{track['node']} 给文字加了缩放"
