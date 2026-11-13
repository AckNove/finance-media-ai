"""内容阶段：脚本草稿、视觉分镜、制作计划。

三件事都在这里，因为它们同属"研究结论变成片子"的中间层，而且共享同一条硬规矩：

**只有人工确认过的结论可以进成稿。** 所以 `draft_script` 拿的是
``draft_gate.confirmed_findings`` 的输出——没确认的结论一条都进不来，不是靠提示词
去"提醒模型不要用"。

另外两条边界也写死在代码里：

- 模型只负责"怎么写"，不许添事实：所有句子必须能对上给定结论；对不上就丢进
  ``dropped`` 并在产物里列出来，绝不静默改写。
- 视觉与制作两阶段**只出计划，不渲染**。谁在报告里说"已生成视频"，那是假的。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.app.core.errors import DomainValidationError

#: 一条结论最多写多长（口播一句 ~3 秒）
MAX_SENTENCE_CHARS = 60
#: 草稿最多用多少条确认结论
MAX_FINDINGS = 8
#: 每个镜头最短/最长秒数（分镜只做计划，改时长在 storyboard.csv 里改）
MIN_SHOT_SECONDS = 2.5
MAX_SHOT_SECONDS = 12.0
#: 中文口播语速（字/秒），用来估时长——只是估算，实测时长以 TTS 为准
CHARS_PER_SECOND = 4.2


class StageError(RuntimeError):
    """内容阶段无法继续。``reason`` 是稳定码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ScriptLine:
    """草稿里的一句。``source_ids`` 是它能追溯到哪几份资料。"""

    text: str
    role: str  # hook / evidence / mechanism / counter / closing
    source_ids: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "role": self.role,
            "source_ids": list(self.source_ids),
            "numbers": list(self.numbers),
        }


@dataclass(frozen=True)
class ScriptDraft:
    title: str
    hook: str
    lines: tuple[ScriptLine, ...]
    disclaimer: str
    generated_by: str = "rules"
    provider: str = ""
    model: str = ""
    degraded_reason: str = ""
    dropped: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def body(self) -> str:
        return "\n\n".join(line.text for line in self.lines)

    @property
    def word_count(self) -> int:
        return sum(len(line.text) for line in self.lines)

    @property
    def estimated_seconds(self) -> float:
        return round(self.word_count / CHARS_PER_SECOND, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "hook": self.hook,
            "lines": [line.as_dict() for line in self.lines],
            "body": self.body,
            "disclaimer": self.disclaimer,
            "word_count": self.word_count,
            "estimated_seconds": self.estimated_seconds,
            "generated_by": self.generated_by,
            "provider": self.provider,
            "model": self.model,
            "degraded_reason": self.degraded_reason,
            "dropped": list(self.dropped),
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", "", f"> 口播开场：{self.hook}", ""]
        for index, line in enumerate(self.lines, start=1):
            cites = "、".join(line.source_ids) or "—"
            lines.append(f"{index}. {line.text}")
            lines.append(f"   （来源 {cites}｜角色 {line.role}）")
        lines += ["", f"> {self.disclaimer}", ""]
        lines.append(f"生成方式：{self.generated_by}" + (f"（{self.degraded_reason}）" if self.degraded_reason else ""))
        if self.dropped:
            lines.append("")
            lines.append("## 被丢弃的句子（对不上确认结论，未进稿）")
            lines += [f"- {item}" for item in self.dropped]
        return "\n".join(lines).rstrip() + "\n"


DISCLAIMER = "本片为合规资料整理，不构成投资建议；所有数字均来自片尾列出的公开来源。"


def _findings_of(report: Any) -> list[dict[str, Any]]:
    raw = getattr(report, "findings", None) or []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            items.append({"index": index, "text": str(item), "source_ids": [], "numbers": []})
            continue
        text = str(item.get("text") or item.get("claim") or item.get("statement") or "").strip()
        if not text:
            continue
        source_ids = [str(x) for x in (item.get("source_ids") or []) if str(x).strip()]
        numbers = [str(x) for x in (item.get("numbers") or []) if str(x).strip()]
        items.append({"index": index, "text": text, "source_ids": source_ids, "numbers": numbers})
    return items


def _sentence(text: str, *, limit: int = MAX_SENTENCE_CHARS) -> str:
    """压成一句口播：去换行、去多余空白，超长在标点处断。"""
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    head = clean[:limit]
    cut = max(head.rfind(mark) for mark in "，、；：。！？")
    if cut >= limit // 2:
        return head[: cut + 1]
    return head.rstrip("，、；：") + "。"


def draft_script(
    *,
    report: Any,
    confirmed: Sequence[Mapping[str, Any]],
    requested_title: str = "",
) -> ScriptDraft:
    """按确认结论排一份草稿（规则版，不调模型）。

    ``confirmed`` 来自 :func:`backend.app.research.draft_gate.confirmed_findings`；
    传空表示"还没有确认的结论"——那是 ``blocked``，不是"随便写一版"。
    """
    if not confirmed:
        raise StageError(
            "no_confirmed_findings",
            "这份报告还没有人工确认过的结论：先到「语义核查」页逐条确认，再生成脚本",
        )

    usable = _findings_of(report)
    by_index = {item["index"]: item for item in usable}

    lines: list[ScriptLine] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in confirmed[:MAX_FINDINGS]:
        index = int(item.get("finding_index", -1))
        source = by_index.get(index, {})
        # 正文以**报告里那一条**为准（核查行只是它的副本，可能被改过备注）
        text = str(source.get("text") or item.get("text") or "").strip()
        if not text:
            continue
        sentence = _sentence(text)
        key = sentence.strip()
        if key in seen:
            continue
        seen.add(key)
        role = "evidence" if index % 2 == 0 else "mechanism"
        lines.append(
            ScriptLine(
                text=sentence,
                role=role,
                source_ids=tuple(str(x) for x in (source.get("source_ids") or item.get("source_ids") or [])),
                numbers=tuple(str(x) for x in (source.get("numbers") or [])),
            )
        )
        if len(sentence) < 8:
            dropped.append(text)

    if not lines:
        raise StageError("empty_draft", "确认结论里没有能写成口播的句子")

    title = (requested_title or str(getattr(report, "title", "")) or "未命名研究").strip()
    hook_line = lines[0]
    hook = f"{hook_line.text}"
    notes: list[str] = []
    if len(confirmed) > MAX_FINDINGS:
        notes.append(f"确认结论共 {len(confirmed)} 条，草稿只用了前 {MAX_FINDINGS} 条")
    notes.append("草稿只用了人工确认过的结论；未确认的结论进不来")

    return ScriptDraft(
        title=title[:80],
        hook=hook,
        lines=tuple(lines),
        disclaimer=DISCLAIMER,
        generated_by="rules",
        dropped=tuple(dropped),
        notes=tuple(notes),
    )


#: 模型改写时每条结论最多给多少字（给模型看，不是最终稿长）
_MODEL_FINDING_CHARS = 200
#: 允许模型写的角色（和规则版同一套词表）
_ROLES = ("hook", "evidence", "mechanism", "counter", "closing")
#: 带单位的数字（长单位在前，避免 "5.2万亿元" 只吃到 "5.2万亿"）
_NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个百分点|万亿元|亿美元|亿元|万元|万亿|美元|亿|万|倍|元|%|％)"
)
_BARE_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers_in(text: str) -> list[str]:
    """抠出文本里的数字（先去空白，好比较 "1.52 %" 和 "1.52%"）。"""
    squeezed = "".join(str(text).split())
    return [match.group(0) for match in _NUMBER_PATTERN.finditer(squeezed)] or [
        match.group(0) for match in _BARE_NUMBER.finditer(squeezed)
    ]


def _digit_runs(text: str) -> set[str]:
    """所有连续数字（"70-75亿元" → {"70", "75"}），用来抓"整段数字被改写"。"""
    return {match.group(0) for match in _BARE_NUMBER.finditer("".join(str(text).split()))}


def _unsupported_numbers(sentence: str, origin: str) -> list[str]:
    """句子里哪些数字在它引的那条结论里找不到。

    两道检查都要过：

    - **带单位的量**要原样出现（"1.52%" 不能变成 "1.5%"、"70-75亿元" 不能变成 "70亿元"）；
    - 其余裸数字也要原样出现（年份、"四个环节"里的 4 之类）。
    """
    origin_squeezed = "".join(str(origin).split())
    missing: list[str] = []
    units = [match.group(0) for match in _NUMBER_PATTERN.finditer("".join(str(sentence).split()))]
    for number in units:
        if number not in origin_squeezed:
            missing.append(number)
    known_digits = _digit_runs(origin)
    for digits in sorted(_digit_runs(sentence)):
        if digits not in known_digits:
            missing.append(digits)
    return missing


def build_script_messages(
    *,
    draft: ScriptDraft,
    confirmed: Sequence[Mapping[str, Any]],
    skill_notes: Sequence[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    """把"确认结论 + 改写要求"写成一次对话。"""
    lines = [
        f"[{index}] {str(item.get('text') or '').strip()[:_MODEL_FINDING_CHARS]}"
        for index, item in enumerate(confirmed, start=1)
    ]
    numbers = sorted({number for item in confirmed for number in _numbers_in(str(item.get("text") or ""))})
    system = (
        "你在为一档中文财经短视频写口播初稿。硬规矩（违反的整句作废）：\n"
        "1. 只能改写给定的编号结论，不许引入任何新事实、新数字、新判断；\n"
        "2. 每一句都要标注它来自哪一条结论（source_index 用给定编号）；\n"
        "3. 不许出现结论里没有的数字；结论里的数字要原样保留，不得四舍五入改口径；\n"
        "4. 一句不超过 45 字，口语化、有节奏感，不要书面对仗与套话；\n"
        "5. 只输出 JSON：{\"title\": str, \"hook\": str, \"lines\": [{\"text\": str, "
        "\"source_index\": int, \"role\": \"hook|evidence|mechanism|counter|closing\"}]}。\n"
        "风格要求：专业、犀利、有趣味，敢下判断但不夸大；不写「震惊」「必看」这类标题党用语。"
    )
    user = [
        f"主题：{draft.title}",
        "",
        "可用的编号结论（人工已确认，别的都不许用）：",
        *lines,
        "",
        f"结论里出现过的数字白名单：{'、'.join(numbers) if numbers else '（没有）'}",
        "",
        "请写成 4-6 句的口播初稿。",
    ]
    if skill_notes:
        blocks = [f"## {name}\n{instructions.strip()[:1200]}" for name, instructions in skill_notes if instructions.strip()]
        if blocks:
            user += ["", "写作风格参考（只约束怎么写，不许据此添事实）：", *blocks]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user)},
    ]


def parse_script_draft(
    text: str,
    *,
    draft: ScriptDraft,
    confirmed: Sequence[Mapping[str, Any]],
) -> tuple[ScriptDraft, tuple[str, ...]]:
    """解析模型输出；对不上结论的句子进 ``dropped``，绝不静默保留。"""
    payload = _first_json_object(text)
    if payload is None:
        raise StageError("bad_model_script", "模型没有返回可解析的 JSON")

    allowed = {
        int(item.get("finding_index", -1)): item for item in confirmed if item.get("finding_index") is not None
    }
    by_number = {index: item for index, item in enumerate(confirmed, start=1)}
    dropped: list[str] = []
    lines: list[ScriptLine] = []
    seen: set[str] = set()

    for raw in payload.get("lines") or []:
        if not isinstance(raw, Mapping):
            continue
        sentence = _sentence(str(raw.get("text") or ""), limit=45)
        if not sentence:
            continue
        index = raw.get("source_index")
        source: Mapping[str, Any] | None = None
        if isinstance(index, int):
            # 提示词里给模型看的是 1、2、3 这样的编号；也容忍它直接回 finding_index
            source = by_number.get(index) or allowed.get(index)
        if source is None:
            dropped.append(f"{sentence}（没说清来自哪条结论）")
            continue
        origin = str(source.get("text") or "")
        unknown = _unsupported_numbers(sentence, origin)
        if unknown:
            dropped.append(f"{sentence}（数字 {'、'.join(unknown)} 不在它所引的结论里）")
            continue
        if sentence in seen:
            continue
        seen.add(sentence)
        role = str(raw.get("role") or "").strip().lower()
        if role not in _ROLES:
            role = "evidence"
        lines.append(
            ScriptLine(
                text=sentence,
                role=role,
                source_ids=tuple(str(x) for x in (source.get("source_ids") or [])),
                numbers=tuple(_numbers_in(sentence)),
            )
        )

    if not lines:
        raise StageError("empty_model_draft", "模型写的句子没有一句能对上确认结论")

    title = str(payload.get("title") or draft.title).strip()[:80] or draft.title
    hook = str(payload.get("hook") or lines[0].text).strip()[:80] or lines[0].text
    return (
        ScriptDraft(
            title=title,
            hook=hook,
            lines=tuple(lines),
            disclaimer=DISCLAIMER,
            generated_by="model",
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            dropped=tuple(dropped),
            notes=(
                "只有人工确认过的结论进了提示词；模型写的每一句都能对回某条结论",
                "数字逐条比对过：结论里没有的数字不会出现",
            ),
        ),
        tuple(dropped),
    )


def _first_json_object(text: str) -> Mapping[str, Any] | None:
    """从可能带代码围栏的回复里取出第一个 JSON 对象。"""
    body = str(text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.endswith("```"):
            body = body[: -3]
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def plan_shots(draft: ScriptDraft) -> list[dict[str, Any]]:
    """把草稿排成镜头计划（只排，不渲染）。"""
    shots: list[dict[str, Any]] = []
    opening_seconds = MIN_SHOT_SECONDS * 2
    shots.append(
        {
            "shot_id": "sh01",
            "purpose": "开场：说清这条讲什么",
            "narration": draft.hook,
            "visual": "标题板（大字，深色底）",
            "asset_type": "title",
            "source_ids": list(draft.lines[0].source_ids) if draft.lines else [],
            "seconds": opening_seconds,
        }
    )
    for index, line in enumerate(draft.lines, start=2):
        seconds = min(MAX_SHOT_SECONDS, max(MIN_SHOT_SECONDS, len(line.text) / CHARS_PER_SECOND))
        visual = "数据卡（把带单位的数字放到画面上）" if line.numbers else "文字卡（一句一句上屏）"
        shots.append(
            {
                "shot_id": f"sh{index:02d}",
                "purpose": {"evidence": "摆证据", "mechanism": "讲机制"}.get(line.role, "补充说明"),
                "narration": line.text,
                "visual": visual,
                "asset_type": "data_card" if line.numbers else "text_card",
                "source_ids": list(line.source_ids),
                "seconds": round(seconds, 2),
            }
        )
    total = round(sum(float(shot["seconds"]) for shot in shots) + MIN_SHOT_SECONDS, 2)
    shots.append(
        {
            "shot_id": f"sh{len(shots) + 1:02d}",
            "purpose": "收尾：来源与免责",
            "narration": draft.disclaimer,
            "visual": "结尾卡（来源清单 + 免责声明）",
            "asset_type": "outro",
            "source_ids": [],
            "seconds": MIN_SHOT_SECONDS,
        }
    )
    for shot in shots:
        shot["estimated_total_seconds"] = total
    return shots


def production_plan(*, draft: ScriptDraft, shots: Sequence[Mapping[str, Any]], job_id: str = "") -> dict[str, Any]:
    """制作计划：谁配音、字幕怎么来、要不要音乐、渲染参数、质检清单。

    **不含"已渲染"这种话**：这里只写给下一步的命令。
    """
    total = round(sum(float(shot["seconds"]) for shot in shots), 2)
    return {
        "job_id": job_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "duration_seconds": total,
        "shot_count": len(shots),
        "voice": {
            "provider": "edge-tts（默认，中文，免费离线兜底用 sapi）",
            "note": "机器音只作技术验证；正式发布请用真人配音或已授权的 TTS",
        },
        "subtitles": {"source": "narration", "note": "字幕只从旁白来，不另行改写"},
        "music": {
            "policy": "默认本地合成配乐（synthesize_score，无版权素材）；要换曲目必须先在 content/assets/bgm 登记授权",
        },
        "assets": {
            "policy": "真实素材必须有可商用授权并登记 assets_manifest.csv；没有就只用程序生成的图表与文字卡",
        },
        "render": {
            "command": "python -m backend.scripts.render_job --job <job_id> --narration --output output/final.mp4",
            "note": "这一步才会真渲染；本阶段只出计划",
        },
        "qc": [
            "技术质检：分辨率/音画等长/黑帧/冻结/静音/响度",
            "内容质检：上屏数字能否在材料里找到、每镜是否有来源、字幕是否来自旁白、素材授权",
        ],
        "not_done": ["渲染", "配音生成", "素材下载", "发布"],
    }


def write_stage_files(
    *,
    out_dir: Path,
    draft: ScriptDraft | None = None,
    shots: Sequence[Mapping[str, Any]] | None = None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """把产物写到 ``out_dir``，返回 ``{名字: 路径}``。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if draft is not None:
        markdown = out_dir / "script_draft.md"
        markdown.write_text(draft.to_markdown(), encoding="utf-8")
        written["script_markdown"] = str(markdown)
        payload = out_dir / "script_draft.json"
        payload.write_text(json.dumps(draft.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        written["script_json"] = str(payload)
    if shots is not None:
        target = out_dir / "visual_plan.json"
        target.write_text(
            json.dumps({"shots": list(shots)}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written["visual_plan"] = str(target)
    if plan is not None:
        target = out_dir / "production_plan.json"
        target.write_text(json.dumps(dict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
        written["production_plan"] = str(target)
    return written


__all__ = [
    "DISCLAIMER",
    "MAX_FINDINGS",
    "ScriptDraft",
    "ScriptLine",
    "StageError",
    "draft_script",
    "plan_shots",
    "production_plan",
    "write_stage_files",
]
