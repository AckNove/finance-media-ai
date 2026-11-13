"""一稿三版：把同一份脚本改写成抖音 / 小红书 / 公众号三个平台的成稿。

边界（写死在实现里，别绕过）：
- **只做排版与长度适配，不新增事实**。任何数字、结论、引文都不从这里产生，
  也不从这里改写口径；要改内容回脚本与核查环节。
- 平台差异只体现在：标题长度、摘要、正文断行方式、话题标签数量、发布注意项。
- 标签只从「脚本自己带的标签 + 命中的长期合集」里取；**不够就报警告，
  不编标签**（编出来的标签会把人带到错误的流量池）。
- 每个平台都给出 ``warnings``，说明哪里是被截断/凑不够的，交付前要人看一眼。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: 平台键（与 ``backend/app/models/enums.py`` 的 ``Platform`` 一致）
PLATFORMS = ("douyin", "xiaohongshu", "wechat")


@dataclass(frozen=True)
class PlatformSpec:
    """一个平台的成稿规格。数字都来自各平台的公开限制，改这里就够。"""

    key: str
    name: str
    title_max: int
    summary_max: int
    line_max: int
    hashtag_min: int
    hashtag_max: int
    notes: tuple[str, ...] = ()
    #: 是否把正文按句子切成短行（短视频口播/字幕友好）
    break_lines: bool = False


SPECS: dict[str, PlatformSpec] = {
    "douyin": PlatformSpec(
        key="douyin",
        name="抖音",
        title_max=30,
        summary_max=55,
        line_max=18,
        hashtag_min=3,
        hashtag_max=5,
        notes=(
            "前 3 秒必须给结论或冲突，不要先铺垫",
            "字幕按短句断行，一行不超过 18 字",
            "不要出现「点击链接」「私信」等导流词",
        ),
        break_lines=True,
    ),
    "xiaohongshu": PlatformSpec(
        key="xiaohongshu",
        name="小红书",
        title_max=20,
        summary_max=55,
        line_max=24,
        hashtag_min=5,
        hashtag_max=10,
        notes=(
            "标题要像人说话，不要写论文标题",
            "正文分段，每段一个意思，段间空一行",
            "标签要贴着内容，不要蹭无关热词",
        ),
        break_lines=True,
    ),
    "wechat": PlatformSpec(
        key="wechat",
        name="公众号",
        title_max=64,
        summary_max=120,
        line_max=0,
        hashtag_min=0,
        hashtag_max=0,
        notes=(
            "标题可以长一些，但要在 64 字内说清主题",
            "开头两段决定读者是否继续，先给钩子再给背景",
            "文末保留免责声明与来源",
        ),
        break_lines=False,
    ),
}

#: 句子边界：中文按这些标点切
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*")
#: 短句边界：切不动时退到逗号顿号
_CLAUSE_SPLIT = re.compile(r"(?<=[，、,])")
#: 平台禁用的导流词（命中只提醒，不改写）
_RISKY_WORDS = ("点击链接", "私信我", "加微信", "扫码", "下方链接", "评论区留言领取")
#: 数字与单位（用于"正文里有没有数据"的提示，不做改写）
_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个百分点|万亿元|亿元|万元|万亿|亿|万|倍|元|美元)")
#: 裸数字（年份、数量等，切行时同样不能被切断）
_BARE_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Adapted:
    """一个平台的成稿。"""

    platform: str
    name: str
    title: str
    summary: str
    body: str
    hashtags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "name": self.name,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "hashtags": list(self.hashtags),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "numbers": list(self.numbers),
        }


def _clean(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", (text or "").replace("\r\n", "\n")).strip()


def sentences_of(text: str) -> list[str]:
    """按句号/问号/感叹号/分号切成句子（保留标点，去掉空段）。"""
    return [piece.strip() for piece in _SENTENCE_SPLIT.split(_clean(text)) if piece.strip()]


def paragraphs_of(text: str) -> list[str]:
    return [block.strip() for block in _clean(text).split("\n\n") if block.strip()]


def _safe_cut_index(text: str, limit: int) -> int:
    """算一个"不切断数字"的切点。

    "占比约 19.3%" 如果在 19 与 .3 之间断开，字幕上就成了"1 / 9.3%"——
    意思全变了。所以切点落在数字或"数字+单位"中间时，往前退到这个数字之前。
    """
    cut = min(limit, len(text))
    if cut <= 0:
        return 0
    for pattern in (_NUMBER, _BARE_NUMBER):
        for match in pattern.finditer(text):
            start, end = match.span()
            if start < cut < end:
                return start
    return cut


def _cut(text: str, limit: int) -> tuple[str, bool]:
    """按字符数截断，尽量切在标点处；返回 (结果, 是否截断)。"""
    text = text.strip()
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    for mark in ("，", "、", "；", "。", "！", "？", " "):
        index = window.rfind(mark)
        if index >= limit * 0.6:
            return window[: index + 1].rstrip("，、；"), True
    safe = _safe_cut_index(text, limit)
    return text[:safe].rstrip() + "…", True


def _break_into_lines(text: str, limit: int) -> list[str]:
    """把一段话切成适合口播/字幕的短行（不改字，只加换行）。"""
    if limit <= 0:
        return [text.strip()] if text.strip() else []
    lines: list[str] = []
    for sentence in sentences_of(text):
        if len(sentence) <= limit:
            lines.append(sentence)
            continue
        buffer = ""
        for clause in _CLAUSE_SPLIT.split(sentence):
            if not clause:
                continue
            if len(buffer) + len(clause) <= limit:
                buffer += clause
                continue
            if buffer:
                lines.append(buffer.strip())
            buffer = clause
            while len(buffer) > limit:  # 单句超长：硬切，但不切断数字
                cut = _safe_cut_index(buffer, limit)
                if cut <= 0:  # 极端情况：整段以数字开头，宁可超一点也不要切开
                    cut = limit
                lines.append(buffer[:cut].strip())
                buffer = buffer[cut:].lstrip()
        if buffer.strip():
            lines.append(buffer.strip())
    return [line for line in lines if line]


def _hashtags(text: str, tags: Iterable[str], *, collections: dict[str, str]) -> tuple[str, ...]:
    """话题标签：脚本自带标签优先，其次命中的长期合集；去重保序。"""
    result: list[str] = []
    for raw in tags:
        word = str(raw).strip().lstrip("#")
        if word and word not in result:
            result.append(word)
    for code, name in collections.items():
        if code in text or name in text:
            if name not in result:
                result.append(name)
    return tuple(result)


def _collection_names() -> dict[str, str]:
    """合集 id → 中文名（从 ``config/content_taxonomy.yaml`` 读，唯一真相）。"""
    try:
        from backend.app.core.config import load_yaml_config

        config = load_yaml_config("content_taxonomy.yaml")
    except Exception:  # 配置读不到不该让适配直接失败
        return {}
    return {
        str(item.get("id")): str(item.get("name"))
        for item in config.get("collections") or []
        if item.get("id") and item.get("name")
    }


def adapt_one(
    *,
    spec: PlatformSpec,
    title: str,
    body: str,
    tags: Sequence[str] = (),
    collections: dict[str, str] | None = None,
) -> Adapted:
    """把一份脚本改写成某个平台的成稿。"""
    warnings: list[str] = []
    names = _collection_names() if collections is None else collections

    clean_title, cut = _cut(title, spec.title_max)
    if cut:
        warnings.append(f"标题超过 {spec.title_max} 字，已截断（原长 {len(title.strip())} 字）")

    pieces = sentences_of(body)
    if not pieces:
        body_text = _clean(body)
    elif spec.break_lines:
        lines: list[str] = []
        for block in paragraphs_of(body) or [body]:
            lines.extend(_break_into_lines(block, spec.line_max))
        body_text = "\n".join(lines)
        over = [line for line in lines if len(line) > spec.line_max]
        if over:
            warnings.append(f"{len(over)} 行超过 {spec.line_max} 字（超长句会把字幕压成两行）")
    else:
        body_text = "\n\n".join(paragraphs_of(body) or [body])

    summary, cut_summary = _cut(pieces[0] if pieces else body_text, spec.summary_max)
    if cut_summary:
        warnings.append(f"摘要超过 {spec.summary_max} 字，已截断")

    hashtags = _hashtags(f"{title}\n{body}", tags, collections=names)
    if spec.hashtag_max:
        if len(hashtags) > spec.hashtag_max:
            warnings.append(f"标签 {len(hashtags)} 个，超过平台上限 {spec.hashtag_max}，已截取")
            hashtags = hashtags[: spec.hashtag_max]
        if len(hashtags) < spec.hashtag_min:
            warnings.append(
                f"标签只有 {len(hashtags)} 个，平台建议 {spec.hashtag_min}~{spec.hashtag_max} 个；"
                "这里不替你编标签，请手工补"
            )

    hits = [word for word in _RISKY_WORDS if word in body or word in title]
    if hits:
        warnings.append(f"出现平台不鼓励的导流词：{'、'.join(hits)}（没有自动删，请自行判断）")

    numbers = tuple(dict.fromkeys(match.group(0).strip() for match in _NUMBER.finditer(body)))
    if spec.key != "wechat" and not numbers:
        warnings.append("正文里没有带单位的数据，短视频很难留住人（要不要补一张数据卡？）")

    return Adapted(
        platform=spec.key,
        name=spec.name,
        title=clean_title,
        summary=summary,
        body=body_text,
        hashtags=hashtags,
        warnings=tuple(warnings),
        notes=spec.notes,
        numbers=numbers,
    )


def adapt(
    *,
    title: str,
    body: str,
    tags: Sequence[str] = (),
    platforms: Sequence[str] = PLATFORMS,
    collections: dict[str, str] | None = None,
) -> list[Adapted]:
    """一次产出多个平台的成稿（默认三平台）。未知平台直接报错，不静默跳过。"""
    unknown = [key for key in platforms if key not in SPECS]
    if unknown:
        raise ValueError(f"不认识的平台：{'、'.join(unknown)}（可用：{'、'.join(PLATFORMS)}）")
    names = _collection_names() if collections is None else collections
    return [
        adapt_one(spec=SPECS[key], title=title, body=body, tags=tags, collections=names)
        for key in platforms
    ]


def adapt_payload(*, title: str, body: str, tags: Sequence[str] = (),
                  platforms: Sequence[str] = PLATFORMS) -> dict[str, Any]:
    """给接口用的信封：一次三版 + 统一说明。"""
    variants = adapt(title=title, body=body, tags=tags, platforms=platforms)
    return {
        "variants": [item.as_dict() for item in variants],
        "platforms": list(platforms),
        "note": (
            "这里只做长度与排版适配，不改事实、不改口径；"
            "warnings 里列的是截断与「凑不够」的地方，发布前请逐条看过。"
        ),
    }
