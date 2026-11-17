"""口播稿 → 镜头素材计划。

V2.0 任务书里这条链路是重点：

    口播稿（已人工确认） → 逐段理解语义 → 决定这一句要"看见什么"
    → 生成检索词 / 素材类型 / 时长预算 → 在已授权的素材库里匹配
    → 匹配不上就换词、换场景，再不行退回程序化动画

三条硬规矩（写在这里，代码照着做）：

1. **模型只用来"想"，不用来"编素材"**。它输出的是每段要什么画面、检索词是什么；
   真正的素材必须来自已登记、可商用、有出处的库。
2. **不许拿不相关画面凑时长**。匹配分低于阈值就明说"这段没素材"，
   由上层决定是用动画解释还是让用户补素材，绝不静默塞一张图。
3. **时长以实测为准**。口播音频实测秒数优先于字数估算，估算值只在没有音频时兜底。

素材库这条线目前有两个真实来源：
- 已登记的真实影像/图片（`source_register.csv`，CC0 / PD / CC BY / Pexels License）；
- Pexels 官方 API（没有 Key 时 BLOCKED，见 `capabilities/media/pexels.py`）。
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# 每段最多给几个检索词（多了模型会乱撒，少了容易搜不到）
MAX_QUERIES = 4
# 匹配分低于这个就当"没有可用素材"，宁可报缺口也不塞错图
MIN_MATCH_SCORE = 0.34
# 没有实测音频时，按中文口播语速估时长（字/秒）
CHARS_PER_SECOND = 4.6
# 单个镜头最短/最长（任务书：普通镜头 2–5 秒，解释镜头可放宽）
MIN_SHOT_SECONDS = 2.0
MAX_SHOT_SECONDS = 12.0

# 需要"用动画解释"的语义信号（产业链、资金流、机制类）
ANIMATION_HINTS = (
    "产业链", "上下游", "上游", "中游", "下游", "传导", "资金流", "现金流",
    "收入结构", "成本结构", "利润分配", "商业模式", "机制", "原理", "流程",
    "怎么赚钱", "如何赚钱", "定价", "结算",
)
# 需要"数据层"的信号
DATA_HINTS = ("%", "％", "亿元", "万元", "倍", "同比", "环比", "占比", "增速", "利润", "营收")

_TAG_SPLIT = re.compile(r"[\s,，、;；/|=]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_PUNCT = re.compile(r"[，。！？；：、,.!?;:\s“”‘’（）()《》\"'—-]+")


class MaterialPlanError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ScriptLine:
    """口播稿里的一行（一句）。"""

    index: int
    text: str
    seconds: float = 0.0  # 实测；0 表示还没测

    @property
    def spoken_seconds(self) -> float:
        """用于排时间轴的口播时长：实测优先，没有就按字数估。"""

        if self.seconds > 0:
            return round(self.seconds, 3)
        chars = len(_CJK.findall(self.text))
        estimate = chars / CHARS_PER_SECOND if chars else 1.5
        return round(min(max(estimate, MIN_SHOT_SECONDS), MAX_SHOT_SECONDS), 3)


@dataclass(frozen=True)
class VisualIntent:
    """模型（或规则兜底）给出的"这一段要看见什么"。"""

    index: int
    kind: str  # real_video | real_image | data | animation
    visual_goal: str
    queries: tuple[str, ...]
    shot_hint: str = ""
    source: str = "model"  # model | rules


@dataclass(frozen=True)
class LibraryAsset:
    """素材库里的一条（都是已登记、可商用的）。"""

    asset_id: str
    kind: str  # video | image
    title: str
    tags: tuple[str, ...]
    local_path: str
    page_url: str = ""
    credit: str = ""
    license_name: str = ""
    width: int = 0
    height: int = 0
    duration_s: float = 0.0

    @property
    def orientation(self) -> str:
        if self.height > self.width:
            return "portrait"
        if self.width > self.height:
            return "landscape"
        return "square"


@dataclass
class MatchedShot:
    index: int
    text: str
    seconds: float
    intent: VisualIntent
    asset_id: str = ""
    score: float = 0.0
    fallback: str = ""  # 没匹配上时的处理：animation | manual | ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.asset_id) or self.fallback in {"animation", "manual"}

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intent"] = asdict(self.intent)
        payload["ok"] = self.ok
        return payload


# --------------------------------------------------------------------------- #
# 1. 拆稿
# --------------------------------------------------------------------------- #


def split_narration(text_or_rows: str | Sequence[str] | Sequence[dict[str, Any]]) -> list[ScriptLine]:
    """把口播稿拆成逐句。

    支持三种输入：整段文本（按句号/问号切）、字符串列表、脚本表行（`text`/`narration` 字段）。
    """

    lines: list[str] = []
    if isinstance(text_or_rows, str):
        pieces = re.split(r"(?<=[。！？!?])", text_or_rows)
        lines = [piece.strip() for piece in pieces if piece and piece.strip()]
    else:
        for row in text_or_rows:
            if isinstance(row, str):
                lines.append(row.strip())
            elif isinstance(row, dict):
                value = row.get("text") or row.get("narration") or row.get("body") or ""
                seconds = float(row.get("seconds") or 0.0)
                if value.strip():
                    lines.append((value.strip(), seconds))
    result: list[ScriptLine] = []
    for position, item in enumerate(lines, start=1):
        if isinstance(item, tuple):
            result.append(ScriptLine(index=position, text=item[0], seconds=item[1]))
        else:
            result.append(ScriptLine(index=position, text=item))
    if not result:
        raise MaterialPlanError("empty_script", "口播稿是空的，没法排镜头。")
    return result


# --------------------------------------------------------------------------- #
# 2. 语义 → 视觉意图
# --------------------------------------------------------------------------- #


def _bigrams(text: str) -> set[str]:
    chars = [ch for ch in text if _CJK.match(ch)]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def rule_intent(line: ScriptLine) -> VisualIntent:
    """不靠模型也能给出一个及格线意图（模型不可用时的兜底，不是主路径）。"""

    text = line.text
    has_data = any(mark in text for mark in DATA_HINTS)
    wants_animation = any(word in text for word in ANIMATION_HINTS)
    kind = "animation" if wants_animation else ("data" if has_data else "real_video")
    # 检索词：去掉标点后的实词片段，取最长两段
    fragments = [part for part in _PUNCT.split(text) if len(part) >= 2]
    queries = tuple(fragments[:2]) if fragments else (text[:12],)
    return VisualIntent(
        index=line.index,
        kind=kind,
        visual_goal="按原句语义找最贴近的画面" if kind != "animation" else "把这句话里的关系画出来",
        queries=queries,
        source="rules",
    )


def _extract_json(text: str) -> list[dict[str, Any]]:
    """模型可能带 ```json 包壳，这里只取第一段 JSON 数组。"""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = cleaned.replace("```", "").strip()
    if cleaned.startswith("{"):
        try:
            payload_obj = json.loads(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
        except json.JSONDecodeError as error:
            raise MaterialPlanError("model_bad_reply", f"模型返回的 JSON 解析失败：{error}") from error
        shots = payload_obj.get("shots") if isinstance(payload_obj, dict) else None
        if not isinstance(shots, list):
            raise MaterialPlanError("model_bad_reply", "模型返回的对象里没有 shots 数组。")
        return [item for item in shots if isinstance(item, dict)]

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise MaterialPlanError("model_bad_reply", "模型没有返回 JSON 数组。")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise MaterialPlanError("model_bad_reply", f"模型返回的 JSON 解析失败：{error}") from error
    if not isinstance(payload, list):
        raise MaterialPlanError("model_bad_reply", "模型返回的不是数组。")
    return [item for item in payload if isinstance(item, dict)]


PLAN_PROMPT = """你是财经纪录片的视觉导演。下面是一条视频的口播稿，逐句给你。

请为**每一句**决定"观众该看见什么"。只输出一个 JSON 对象：{"shots": [ ... ]}，不要解释、不要代码块。每项字段：
- index：句子编号（整数，必须和给你的编号一致）
- kind：real_video / real_image / data / animation 之一
  · real_video、real_image：**能用真实拍摄画面讲清楚的优先用这两个**（工厂、设备、城市、人物、现场、天气、施工）
  · data：这句话的主角是数字、比例、增速，需要数据层叠在真实画面上
  · animation：只有当这句话讲的是"关系/流程/机制/资金怎么走/利润怎么分"、实拍拍不出来时才用
- 判据：如果一句画面可以"找一段真实影像就说明白"，就不要选 animation。
- visual_goal：一句话说清这一镜要让观众看懂什么
- queries：1–4 个中文检索词，写给素材库/图库用（要具体：写成"火电厂 冷却塔"而不是"电力"）
- shot_hint：镜头提示（景别/机位/运动），一句话

规矩：
- 不要为了有画面而编造事实；某句确实只是过渡，就把 kind 设成 real_video 并给一个不误导的检索词。
- queries 必须是能直接拿去图库/素材库搜索的具体词（2–8 个字），不要写整句。
- 输出必须是最小合法 JSON：双引号、无注释、无尾逗号。
"""


def model_intents(
    lines: Sequence[ScriptLine],
    *,
    complete,
    caller=None,
    budget=None,
) -> list[VisualIntent]:
    """用模型（本项目统一 deepseek-4.1-Flash）生成视觉意图。失败抛 MaterialPlanError。"""

    numbered = "\n".join(f"{line.index}. {line.text}" for line in lines)
    prompt = f"{PLAN_PROMPT}\n\n口播稿：\n{numbered}\n"
    try:
        reply = complete(
            prompt=prompt,
            purpose="video.visual_plan",
            max_tokens=2600,
            response_format="json_object",
            caller=caller,
            budget=budget,
            cacheable=False,
        )
    except Exception as error:  # noqa: BLE001 上层要能区分"模型不可用"与"模型答坏了"
        raise MaterialPlanError("model_unavailable", f"视觉意图模型调用失败：{error}") from error

    text = getattr(reply, "text", "") or ""
    if not getattr(reply, "ok", False):
        raise MaterialPlanError("model_unavailable", f"模型没有成功返回：{getattr(reply, 'error_code', '') or text[:80]}")

    items = _extract_json(text)
    by_index = {int(item.get("index", 0) or 0): item for item in items}
    intents: list[VisualIntent] = []
    for line in lines:
        item = by_index.get(line.index)
        if item is None:
            intents.append(rule_intent(line))
            continue
        kind = str(item.get("kind", "")).strip()
        if kind not in {"real_video", "real_image", "data", "animation"}:
            kind = rule_intent(line).kind
        queries = item.get("queries")
        if isinstance(queries, str):
            queries = [queries]
        queries = tuple(str(q).strip() for q in (queries or []) if str(q).strip())[:MAX_QUERIES]
        if not queries:
            queries = rule_intent(line).queries
        intents.append(
            VisualIntent(
                index=line.index,
                kind=kind,
                visual_goal=str(item.get("visual_goal", "")).strip(),
                queries=queries,
                shot_hint=str(item.get("shot_hint", "")).strip(),
                source="model",
            )
        )
    return intents


# --------------------------------------------------------------------------- #
# 3. 素材库索引与匹配
# --------------------------------------------------------------------------- #


def load_library(roots: Iterable[Path]) -> list[LibraryAsset]:
    """从已登记的 `source_register.csv` 建素材索引（只认可商用的）。"""

    assets: list[LibraryAsset] = []
    keywords_map: dict[str, str] = {}
    for root in roots:
        sidecar = Path(root).rglob("library_keywords.json")
        for path in sidecar:
            try:
                keywords_map.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue

    for root in roots:
        for csv_path in sorted(Path(root).rglob("source_register.csv")):
            try:
                rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
            except (OSError, csv.Error):
                continue
            for row in rows:
                if str(row.get("commercial_use", "")).strip().lower() not in {"allowed", "yes", "true"}:
                    continue
                local = str(row.get("local_path", "")).strip()
                if not local or not Path(local).is_file():
                    continue
                asset_id = str(row.get("asset_id", "")).strip()
                title = str(row.get("publisher_or_subject", "") or "").strip()
                note = str(row.get("note", "") or "").strip()
                kind_raw = (str(row.get("source_type", "")) + " " + str(row.get("original_file", ""))).lower()
                kind = "video" if (".mp4" in kind_raw or ".webm" in kind_raw or ".mov" in kind_raw) else "image"
                keywords = ""
                for piece in re.split(r"[;；]", note):
                    if piece.strip().lower().startswith("keywords="):
                        keywords = piece.split("=", 1)[1]
                extra = keywords_map.get(asset_id, "")
                tag_text = f"{title} {note} {keywords} {extra} {row.get('original_file', '')} {asset_id}"
                tags = tuple(t for t in _TAG_SPLIT.split(tag_text) if t)
                assets.append(
                    LibraryAsset(
                        asset_id=asset_id,
                        kind=kind,
                        title=title or Path(local).stem,
                        tags=tags,
                        local_path=local,
                        page_url=str(row.get("source_url", "") or ""),
                        credit=str(row.get("publisher_or_subject", "") or ""),
                        license_name=str(row.get("license", "") or ""),
                        duration_s=float(row.get("duration_s") or 0.0),
                    )
                )
    return assets


def score_asset(intent: VisualIntent, line: ScriptLine, asset: LibraryAsset) -> float:
    """语义匹配分：检索词命中 + 正文二元组重合 + 类型贴合。0–1。"""

    queries_text = " ".join(intent.queries)
    asset_text = " ".join((asset.title, *asset.tags))
    query_grams = _bigrams(queries_text)
    line_grams = _bigrams(line.text)
    asset_grams = _bigrams(asset_text)
    if not asset_grams:
        lexical = 0.0
    else:
        query_hit = len(query_grams & asset_grams) / max(1, len(query_grams))
        line_hit = len(line_grams & asset_grams) / max(1, len(line_grams))
        lexical = 0.65 * query_hit + 0.35 * line_hit
    # 类型贴合：要"视频"就给视频，要静态就静态都行；要动画那不需要真实素材
    kind_bonus = 0.0
    if intent.kind == "real_video":
        kind_bonus = 0.2 if asset.kind == "video" else 0.05
    elif intent.kind == "real_image":
        kind_bonus = 0.12
    elif intent.kind == "data":
        kind_bonus = 0.04
    return round(min(1.0, lexical + kind_bonus), 3)


def match_shots(
    lines: Sequence[ScriptLine],
    intents: Sequence[VisualIntent],
    library: Sequence[LibraryAsset],
    *,
    min_score: float = MIN_MATCH_SCORE,
) -> list[MatchedShot]:
    """逐段匹配素材；不够分就标缺口，绝不硬塞。"""

    intent_by_index = {intent.index: intent for intent in intents}
    used: set[str] = set()
    shots: list[MatchedShot] = []
    for line in lines:
        intent = intent_by_index.get(line.index) or rule_intent(line)
        shot = MatchedShot(index=line.index, text=line.text, seconds=line.spoken_seconds, intent=intent)

        if intent.kind == "animation":
            shot.fallback = "animation"
            shot.note = "这一段讲的是关系/机制，交给程序化动画解释，不硬找真实素材。"
            shots.append(shot)
            continue

        best: tuple[float, LibraryAsset | None] = (0.0, None)
        for asset in library:
            if asset.asset_id in used:
                continue
            value = score_asset(intent, line, asset)
            if value > best[0]:
                best = (value, asset)

        if best[1] is not None and best[0] >= min_score:
            shot.asset_id = best[1].asset_id
            shot.score = best[0]
            used.add(best[1].asset_id)
        else:
            shot.fallback = "animation" if intent.kind == "data" else "manual"
            shot.score = best[0]
            shot.note = (
                f"素材库没有够贴近的画面（最高分 {best[0]:.2f} < {min_score}）。"
                + ("改用数据动画解释。" if intent.kind == "data" else "需要补素材或人工指定。")
            )
        shots.append(shot)
    return shots


# --------------------------------------------------------------------------- #
# 4. 汇总
# --------------------------------------------------------------------------- #


def summarise(shots: Sequence[MatchedShot], library: Sequence[LibraryAsset]) -> dict[str, Any]:
    matched = [shot for shot in shots if shot.asset_id]
    holes = [shot for shot in shots if not shot.asset_id]
    return {
        "shots": len(shots),
        "matched": len(matched),
        "animated": sum(1 for shot in shots if shot.fallback == "animation"),
        "manual_needed": sum(1 for shot in shots if shot.fallback == "manual"),
        "total_seconds": round(sum(shot.seconds for shot in shots), 2),
        "library_size": len(library),
        "coverage": round(len(matched) / max(1, len(shots)), 3),
        "holes": [shot.index for shot in holes],
        "min_score": MIN_MATCH_SCORE,
    }


def write_plan(shots: Sequence[MatchedShot], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"shots": [shot.as_dict() for shot in shots]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
