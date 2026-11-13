"""内容质检（V6.0 I 模块）：只看"能不能对得上"，不评判"好不好看"。

技术质检（`backend/app/video/render.py` 的 `QC_THRESHOLDS`）管的是容器与信号：
分辨率、帧率、时长、黑帧、冻结、静音。这里管另一件事——**画面上的内容有没有出处**：

* 每屏出现的带单位数字，必须在报告正文或证据里出现过（防止模型/人手写错一位数）；
* 每镜的结论都要挂来源编号，没挂的镜头等于没有出处；
* 字幕必须来自旁白（不能另写一段）；
* 素材清单里的授权状态必须是允许商用；
* 相邻镜头不重复同一屏文字（那是凑时长）。

**不做的事**：不判断叙事好不好、节奏对不对、观众喜不喜欢。这些只能人看。
所以本模块的输出永远带着一句"这只能说明没发现硬错，不代表内容合格"。
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.app.core.config import PROJECT_ROOT

JOBS_ROOT = PROJECT_ROOT / "content" / "jobs"
QC_DIR = "qc"
CONTENT_QC_JSON = "content_qc.json"
CONTENT_QC_MD = "content_qc.md"

# 只有"带单位的量"才算可核查的数字（与数据卡同一口径：日历数字不算量）
_NUMBER_WITH_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|个百分点|亿美元|亿元|万元|万亿|亿|万|倍|元/[^\s，。；、）)]{1,3})"
)
_UNIT_SPLIT = re.compile(r"^(\d+(?:\.\d+)?)(.*)$")
_ALLOWED_LICENCE = {"allowed", "cc0", "public_domain", "public-domain", "publicdomain"}

#: 框景镜头（开场、收尾）允许不挂来源：它们不落具体数字，只是"这一条讲什么"。
#: 一旦旁白/字幕里出现**带单位的量**，就必须挂来源——规矩靠"有没有可核查的数字"来判，
#: 不靠镜头名糊过去；年份与"以上 2 个数字"这类计数不算数据主张。
FRAMING_PURPOSES = ("开场", "收尾", "opening", "outro", "closing")
#: 字幕允许是旁白的压缩版，只要用字基本重合（默认七成）就算同一句。
_SUBTITLE_OVERLAP = 0.7


class ContentQcError(RuntimeError):
    """内容质检无法进行（缺分镜、缺报告等）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class QcFinding:
    """一条质检结果。`severity` 只有 error / warning 两档。"""

    check: str
    severity: str
    detail: str
    shot_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "detail": self.detail,
            "shot_id": self.shot_id,
        }


@dataclass
class ContentQcReport:
    job_id: str
    checks: list[QcFinding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[QcFinding]:
        return [item for item in self.checks if item.severity == "error"]

    @property
    def warnings(self) -> list[QcFinding]:
        return [item for item in self.checks if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "stats": self.stats,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
            "note": (
                "这里只说明画面上的数字/来源/字幕能对得上材料，"
                "**不判断内容好坏**：叙事、节奏、观感必须人看片。"
            ),
        }

    def to_markdown(self) -> str:
        lines = [f"# 内容质检：{self.job_id}", ""]
        lines.append(
            "技术质检（分辨率/帧率/黑帧/静音）只管容器与信号；这一份管"
            "**画面上的数字与结论有没有出处**。它不能说明内容合格。"
        )
        lines.append("")
        lines.append(f"- 结论：{'通过（没发现硬错）' if self.ok else '未通过'}")
        lines.append(f"- 硬错 {len(self.errors)} 条 / 提醒 {len(self.warnings)} 条")
        for key, value in self.stats.items():
            lines.append(f"- {key}：{value}")
        if self.errors:
            lines += ["", "## 硬错（必须处理）", ""]
            lines += [
                f"- `{item.check}`" + (f"（镜头 {item.shot_id}）" if item.shot_id else "")
                + f"：{item.detail}"
                for item in self.errors
            ]
        if self.warnings:
            lines += ["", "## 提醒（建议看一眼）", ""]
            lines += [
                f"- `{item.check}`" + (f"（镜头 {item.shot_id}）" if item.shot_id else "")
                + f"：{item.detail}"
                for item in self.warnings
            ]
        lines += [
            "",
            "---",
            "",
            "本文件由 `backend/app/video/content_qc.py` 生成：只做可判定的核对，",
            "不调用模型、不评价好坏。发布前仍需要人看完整片。",
            "",
        ]
        return "\n".join(lines) + "\n"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _report_material(job_dir: Path) -> str:
    """把报告正文与证据拼成一段可检索的语料（只用于"数字有没有出处"）。"""

    chunks: list[str] = []
    report = job_dir / "research" / "report.md"
    if report.is_file():
        chunks.append(report.read_text(encoding="utf-8", errors="replace"))
    evidence = job_dir / "research" / "evidence.json"
    if evidence.is_file():
        try:
            payload = json.loads(evidence.read_text(encoding="utf-8"))
        except ValueError:
            payload = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    chunks.append(str(item.get("text") or item.get("claim") or ""))
                    chunks.append(str(item.get("original_excerpt") or ""))
    digest = job_dir / "research" / "digest.md"
    if digest.is_file():
        chunks.append(digest.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _numbers_in(text: str) -> set[str]:
    """抠出带单位的数字，并把全角/半角、空格差异抹平，便于比对。"""

    found: set[str] = set()
    for match in _NUMBER_WITH_UNIT.finditer(str(text or "")):
        found.add(_normalise_number(match.group(0)))
    return found


def _normalise_number(raw: str) -> str:
    text = str(raw or "").replace("％", "%").replace(" ", "")
    return text.strip()


def _decimal_of(raw: str) -> Decimal | None:
    match = re.match(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:  # pragma: no cover - 正则已限定数字
        return None


def _split_unit(raw: str) -> tuple[Decimal | None, str, int]:
    """把 `12.51%` 拆成 (数值, 单位, 小数位数)；拆不出来时数值为 None。"""

    match = _UNIT_SPLIT.match(raw)
    if not match:
        return None, raw, 0
    number, unit = match.group(1), match.group(2)
    decimals = len(number.split(".")[1]) if "." in number else 0
    try:
        return Decimal(number), unit, decimals
    except InvalidOperation:  # pragma: no cover
        return None, unit, decimals


def _mostly_from(part: str, whole: str, *, threshold: float = _SUBTITLE_OVERLAP) -> bool:
    """`part` 的用字有多大比例出现在 `whole` 里（判断"是不是同一句的压缩版"）。

    只比字符集合，不做语义判断：字幕本来就允许截短、加分隔符，但一旦换了一整句
    无关文案，重合率会掉得很明显。
    """

    chars = [ch for ch in part if not ch.isspace()]
    if not chars:
        return True
    inside = sum(1 for ch in chars if ch in whole)
    return inside / len(chars) >= threshold


def _close_enough(shown: str, material: str) -> bool:
    """`shown` 是不是 `material` 的四舍五入写法（单位必须相同）。

    比较的是"精度差"而不是绝对差：材料写 12.5%、屏幕写 12.51%，差 0.01 小于
    材料精度的一半（0.05），就算同源；但差到 1 个百分点就不是舍入了。
    """

    shown_value, shown_unit, shown_decimals = _split_unit(shown)
    material_value, material_unit, material_decimals = _split_unit(material)
    if shown_value is None or material_value is None:
        return False
    if shown_unit.replace(" ", "") != material_unit.replace(" ", ""):
        return False
    if shown_value == material_value:
        return True
    tolerance = Decimal(5) * (Decimal(10) ** -(min(shown_decimals, material_decimals) + 1))
    return abs(shown_value - material_value) <= tolerance


def review_job(job_dir: Path | str, *, job_id: str | None = None) -> ContentQcReport:
    """对一份视频任务目录做内容质检（纯本地、确定性、不联网）。"""

    directory = Path(job_dir)
    storyboard = directory / "storyboard" / "storyboard.csv"
    if not storyboard.is_file():
        raise ContentQcError("missing_storyboard", f"{directory.name} 没有 storyboard/storyboard.csv")

    rows = _read_csv_rows(storyboard)
    if not rows:
        raise ContentQcError("empty_storyboard", f"{directory.name} 的分镜表是空的")

    report = ContentQcReport(job_id=job_id or directory.name)
    material = _report_material(directory)
    material_numbers = _numbers_in(material)

    narration_count = 0
    on_screen_numbers: set[str] = set()
    previous_on_screen = ""
    for index, row in enumerate(rows, start=1):
        shot_id = str(row.get("shot_id") or f"#{index}")
        narration = str(row.get("narration") or "").strip()
        subtitle = str(row.get("subtitle") or "").strip()
        on_screen = str(row.get("on_screen_text") or "").strip()
        sources = str(row.get("source_ids") or "").strip()

        if narration:
            narration_count += 1

        if not sources:
            purpose = str(row.get("narrative_purpose") or "").strip()
            # 判定"这一镜有没有落数字"只看**带单位的量**：年份（2024 年）和"以上 2 个数字"
            # 这类计数不是可核查的数据主张，拿它去要求来源会让每个开场/收尾都过不了。
            carries_figures = any(
                _NUMBER_WITH_UNIT.search(text) for text in (narration, subtitle, on_screen)
            )
            if purpose in FRAMING_PURPOSES and not carries_figures:
                # 框景镜头可以不挂来源，但只能"不落数字"的那种；落了数字就照样算硬错
                report.checks.append(
                    QcFinding(
                        check="framing_shot_without_source",
                        severity="info",
                        shot_id=shot_id,
                        detail="框景镜头没挂来源（它不落具体数字，属允许）",
                    )
                )
            else:
                report.checks.append(
                    QcFinding(
                        check="shot_without_source",
                        severity="error",
                        shot_id=shot_id,
                        detail="这一镜没有挂任何来源编号，画面上的说法无法回溯",
                    )
                )

        if subtitle and narration and subtitle not in narration:
            if _mostly_from(subtitle, narration):
                # 字幕本来就允许是旁白的压缩版（规则分镜会截短），只要字面来源一致就不吵人
                report.checks.append(
                    QcFinding(
                        check="subtitle_condensed_from_narration",
                        severity="info",
                        shot_id=shot_id,
                        detail="字幕是旁白的压缩版（用字基本重合，属允许）",
                    )
                )
            else:
                report.checks.append(
                    QcFinding(
                        check="subtitle_not_from_narration",
                        severity="warning",
                        shot_id=shot_id,
                        detail="字幕不是旁白的片段：可能是另写的文案，需要人确认口播与字幕一致",
                    )
                )

        if on_screen and on_screen == previous_on_screen:
            report.checks.append(
                QcFinding(
                    check="repeated_on_screen",
                    severity="warning",
                    shot_id=shot_id,
                    detail=f"与上一镜上屏文字完全相同（{on_screen[:24]}），可能是在凑时长",
                )
            )
        previous_on_screen = on_screen

        for raw in _numbers_in(on_screen) | _numbers_in(narration):
            on_screen_numbers.add(raw)
            if raw in material_numbers:
                continue
            # 允许四舍五入后的写法：报告里是 12.5%，画面写 12.51%
            if any(_close_enough(raw, item) for item in material_numbers):
                report.checks.append(
                    QcFinding(
                        check="number_rounded",
                        severity="warning",
                        shot_id=shot_id,
                        detail=f"数字 {raw} 与材料里的值接近但不完全相同（可能做了四舍五入），需人确认",
                    )
                )
                continue
            report.checks.append(
                QcFinding(
                    check="number_not_in_material",
                    severity="error",
                    shot_id=shot_id,
                    detail=f"数字 {raw} 在报告与证据里都找不到出处",
                )
            )

    manifest_rows = _read_csv_rows(directory / "assets_manifest.csv")
    for row in manifest_rows:
        licence = str(
            row.get("commercial_use") or row.get("licence") or row.get("license") or ""
        ).strip().lower()
        asset = str(row.get("asset_id") or row.get("path") or row.get("file") or "未命名素材")
        if not licence:
            report.checks.append(
                QcFinding(
                    check="asset_licence_missing",
                    severity="error",
                    detail=f"素材 {asset} 没有登记授权状态，不能用于商用成片",
                )
            )
        elif licence not in _ALLOWED_LICENCE and licence != "allowed_with_credit":
            report.checks.append(
                QcFinding(
                    check="asset_licence_not_commercial",
                    severity="error",
                    detail=f"素材 {asset} 的授权状态是 {licence}，不允许商用",
                )
            )

    report.stats = {
        "shots": len(rows),
        "shots_with_narration": narration_count,
        "distinct_numbers_on_screen": len(on_screen_numbers),
        "numbers_in_material": len(material_numbers),
        "assets_listed": len(manifest_rows),
    }
    if narration_count == 0:
        report.checks.append(
            QcFinding(
                check="no_narration",
                severity="warning",
                detail="整份分镜没有旁白：成片会是无声的，需要确认是不是有意为之",
            )
        )
    return report


def write_report(report: ContentQcReport, job_dir: Path | str) -> dict[str, str]:
    """把质检结果写进任务目录的 `qc/`（md 给人看，json 给程序读）。"""

    directory = Path(job_dir) / QC_DIR
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / CONTENT_QC_JSON
    md_path = directory / CONTENT_QC_MD
    json_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def review_and_write(
    job_id: str, *, root: Path | None = None
) -> tuple[ContentQcReport, dict[str, str]]:
    base = root or JOBS_ROOT
    job_dir = base / job_id
    if not job_dir.is_dir():
        raise ContentQcError("unknown_job", f"任务目录不存在：{job_id}")
    report = review_job(job_dir, job_id=job_id)
    return report, write_report(report, job_dir)


def summarise_numbers(rows: Iterable[dict[str, Any]]) -> list[str]:
    """给上层用的便捷函数：把一份分镜里出现的带单位数字按序返回（去重）。"""

    seen: list[str] = []
    for row in rows:
        for key in ("on_screen_text", "narration"):
            for raw in sorted(_numbers_in(str(row.get(key) or ""))):
                if raw not in seen:
                    seen.append(raw)
    return seen


__all__ = [
    "CONTENT_QC_JSON",
    "CONTENT_QC_MD",
    "ContentQcError",
    "ContentQcReport",
    "QcFinding",
    "QC_DIR",
    "review_and_write",
    "review_job",
    "summarise_numbers",
    "write_report",
]
