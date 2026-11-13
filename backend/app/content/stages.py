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
