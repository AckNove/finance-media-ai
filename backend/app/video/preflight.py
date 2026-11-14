"""渲染前预检：把「渲染两分钟后才报错」提前到「点按钮之前」。

为什么要有这一层
----------------
`render_job` 会在真正开渲之后才逐项失败：缺素材清单、分镜引用了未登记的素材、
素材文件被删了、组合画板 JSON 坏了、要配音但一句旁白都没有……这些都要等引擎
起来、`prepare_clips` 跑到那一条时才炸，慢一点的片子白等两三分钟，而且报错位置
离真正的原因很远（例如「empty_clip」其实是画板 JSON 少了 root）。

所以这里做一遍**纯文件与 schema 的检查**：不连引擎、不开 ffmpeg、不联网、不写任何
文件。查出来的问题按 `error` / `warning` 分级：

* `error`   —— 现在开渲必然失败，`render_job` 会直接拒绝，并沿用渲染器自己的原因码；
* `warning` —— 能渲，但结果可能不是你想要的（例如组合画板画布与分镜不一致）。

刻意不做的事
------------
- 不判断素材好不好看、数字对不对（那是内容质检 `content_qc` 的事）；
- 不猜路径：清单里没写的素材一律算「未登记」，不做模糊匹配；
- 不替人补文件：缺了就是缺了，写清楚缺哪一条、分镜里哪几个镜头用到它。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from backend.app.media.manifest import MANIFEST_FILENAME, read_manifest
from backend.app.media.storyboard import Storyboard, StoryboardError, load_storyboard

Severity = Literal["error", "warning"]

#: 清单里表示「可以商用」的取值；其它非空取值一律视为不许商用。
COMMERCIAL_ALLOWED = "allowed"
#: 需要明确授权才敢发出去的素材类别（A 类＝真实素材）。
LICENCE_STRICT_CLASSES = frozenset({"A"})
#: 纯色底的合法写法。
_SOLID_COLOUR_PREFIX = "#"

STORYBOARD_REL = Path("storyboard") / "storyboard.csv"


class PreflightError(RuntimeError):
    """预检自己出了问题（不是任务的问题），例如任务目录都不存在。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class PreflightFinding:
    """一条预检结论。`check` 与渲染器的原因码保持一致，方便排查时对得上。"""

    check: str
    severity: Severity
    shot_ids: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "shot_ids": list(self.shot_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PreflightReport:
    """一份分镜在开渲之前能查出来的所有事。"""

    job_id: str
    storyboard_present: bool
    shot_count: int = 0
    duration_seconds: float = 0.0
    asset_count: int = 0
    findings: tuple[PreflightFinding, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[PreflightFinding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[PreflightFinding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if not self.storyboard_present:
            return "预检没通过：还没有分镜文件"
        if self.errors:
            head = "；".join(item.detail for item in self.errors[:3])
            more = f"（另有 {len(self.errors) - 3} 条）" if len(self.errors) > 3 else ""
            return f"预检没通过（{len(self.errors)} 个硬问题）：{head}{more}"
        if self.warnings:
            return (
                f"预检通过，{self.shot_count} 个镜头、{self.duration_seconds:g} 秒，"
                f"另有 {len(self.warnings)} 条提醒"
            )
        return f"预检通过：{self.shot_count} 个镜头、{self.duration_seconds:g} 秒，未发现问题"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "storyboard_present": self.storyboard_present,
            "shot_count": self.shot_count,
            "duration_seconds": self.duration_seconds,
            "asset_count": self.asset_count,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
            "summary": self.summary(),
            "facts": dict(self.facts),
        }


def _manifest_rows(job_root: Path) -> dict[str, dict[str, str]]:
    path = job_root / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for row in read_manifest(path):
        asset_id = str(row.get("asset_id") or "").strip()
        if asset_id:
            rows[asset_id] = row
    return rows


def _lookup(asset_id: str, rows: dict[str, dict[str, str]]) -> tuple[dict[str, str] | None, str]:
    """按 asset_id 找清单行，允许退回到「去掉末尾大写字母」的登记项。

    这条退回规则是从 `prepare_clips` 抄过来的：同一份素材常被拆成 S03A / S03B
    两个入点使用。**只允许这一种退回**，别的模糊匹配一律不做。
    """
    if asset_id in rows:
        return rows[asset_id], asset_id
    base = asset_id.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if base and base != asset_id and base in rows:
        return rows[base], base
    return None, asset_id


def _inside(job_root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(job_root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _shots_by_asset(storyboard: Storyboard) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for shot in storyboard.shots:
        if shot.asset_type == "solid" or not shot.asset_id:
            continue
        grouped.setdefault(shot.asset_id, []).append(shot.shot_id)
    return grouped


def preflight_job(
    job_root: Path | str,
    *,
    narration: bool = False,
) -> PreflightReport:
    """只读地检查一个任务目录能不能开渲。不建工程、不碰引擎、不写文件。"""
    root = Path(job_root).resolve()
    if not root.is_dir():
        raise PreflightError("unknown_job", f"任务目录不存在：{root}")
    job_id = root.name

    storyboard_path = root / STORYBOARD_REL
    if not storyboard_path.is_file():
        return PreflightReport(
            job_id=job_id,
            storyboard_present=False,
            findings=(
                PreflightFinding(
                    check="missing_storyboard",
                    severity="error",
                    shot_ids=(),
                    detail=f"没有分镜文件 {STORYBOARD_REL.as_posix()}，先让人（或模型）把分镜写出来",
                ),
            ),
        )

    try:
        storyboard = load_storyboard(storyboard_path)
    except StoryboardError as error:
        return PreflightReport(
            job_id=job_id,
            storyboard_present=True,
            findings=(
                PreflightFinding(
                    check="invalid_storyboard",
                    severity="error",
                    shot_ids=(),
                    detail=f"分镜不合法（{error.code}）：{error}",
                ),
            ),
        )

    findings: list[PreflightFinding] = []
    rows = _manifest_rows(root)
    by_asset = _shots_by_asset(storyboard)
    all_shots = tuple(sorted({sid for ids in by_asset.values() for sid in ids}))

    if not rows and by_asset:
        # 清单整个没有时只报这一条：再逐条说「这个素材没登记」是同一个原因的噪音，
        # 反而让人以为要去补七八条登记。
        findings.append(
            PreflightFinding(
                check="missing_manifest",
                severity="error",
                shot_ids=all_shots,
                detail=(
                    f"缺少素材清单 {MANIFEST_FILENAME}：分镜里有 {len(by_asset)} 个非纯色素材，"
                    "但没有清单说明它们从哪来、能不能商用"
                ),
            )
        )

    checked_kinds: dict[str, str] = {}
    for asset_id, shot_ids in sorted(by_asset.items() if rows else ()):
        row, registered_as = _lookup(asset_id, rows)
        if row is None:
            findings.append(
                PreflightFinding(
                    check="unknown_asset",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"分镜用了未登记的素材：{asset_id}（清单里没有这一条）",
                )
            )
            continue

        relative = (row.get("file") or "").strip()
        if not relative:
            findings.append(
                PreflightFinding(
                    check="missing_asset_file",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"{asset_id}：清单里这一条没写文件路径",
                )
            )
            continue

        path = (root / relative).resolve()
        if not _inside(root, path):
            findings.append(
                PreflightFinding(
                    check="asset_outside_job",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"{asset_id}：清单里的路径跑到了任务目录之外（{relative}），不允许渲染",
                )
            )
            continue
        if not path.is_file():
            findings.append(
                PreflightFinding(
                    check="missing_asset_file",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"{asset_id}：文件不存在 {relative}",
                )
            )
            continue
        if path.stat().st_size <= 0:
            findings.append(
                PreflightFinding(
                    check="empty_asset_file",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"{asset_id}：文件是空的（0 字节）{relative}",
                )
            )
            continue

        # 授权：真实素材（A 类）必须有明确的「可商用」，其余类别只要不是明确禁止就行。
        asset_class = (row.get("asset_class") or "").strip().upper()
        commercial = (row.get("commercial_use") or "").strip()
        if commercial and commercial != COMMERCIAL_ALLOWED:
            findings.append(
                PreflightFinding(
                    check="license_denied",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=f"{asset_id}：清单里写的是「{commercial}」，不是可商用，不许进成片",
                )
            )
        elif not commercial and asset_class in LICENCE_STRICT_CLASSES:
            findings.append(
                PreflightFinding(
                    check="license_unclear",
                    severity="error",
                    shot_ids=tuple(shot_ids),
                    detail=(
                        f"{asset_id}：A 类真实素材没写能不能商用（commercial_use 空），"
                        "授权不明一律不渲"
                    ),
                )
            )
        elif not commercial:
            findings.append(
                PreflightFinding(
                    check="license_unclear",
                    severity="warning",
                    shot_ids=tuple(shot_ids),
                    detail=(
                        f"{asset_id}：清单里没写商用授权（commercial_use 空）；"
                        "自绘素材通常没问题，但发出去之前最好补上"
                    ),
                )
            )

        kind = (row.get("kind") or "").strip()
        if kind == "composition":
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                findings.append(
                    PreflightFinding(
                        check="bad_composition",
                        severity="error",
                        shot_ids=tuple(shot_ids),
                        detail=f"{asset_id}：组合画板不是合法 JSON（{error}）",
                    )
                )
                continue
            canvas = document.get("canvas") if isinstance(document, dict) else None
            if not isinstance(canvas, dict) or not {"w", "h"} <= set(canvas):
                findings.append(
                    PreflightFinding(
                        check="bad_composition",
                        severity="error",
                        shot_ids=tuple(shot_ids),
                        detail=f"{asset_id}：组合画板缺 canvas.w / canvas.h",
                    )
                )
                continue
            try:
                canvas_w, canvas_h = int(canvas["w"]), int(canvas["h"])
            except (TypeError, ValueError):
                findings.append(
                    PreflightFinding(
                        check="bad_composition",
                        severity="error",
                        shot_ids=tuple(shot_ids),
                        detail=f"{asset_id}：组合画板的 canvas 宽高不是数字",
                    )
                )
                continue
            if (canvas_w, canvas_h) != (storyboard.width, storyboard.height):
                findings.append(
                    PreflightFinding(
                        check="composition_canvas_mismatch",
                        severity="warning",
                        shot_ids=tuple(shot_ids),
                        detail=(
                            f"{asset_id}：画板画布 {canvas_w}×{canvas_h} 与分镜 "
                            f"{storyboard.width}×{storyboard.height} 不一致，会被缩放后使用"
                        ),
                    )
                )
            checked_kinds[asset_id] = "composition"
        else:
            checked_kinds[asset_id] = kind or "unknown"

    # 纯色底的写法检查：写错颜色不会失败（渲染器有默认色），但结果和你想的不一样。
    bad_solids: list[str] = []
    for shot in storyboard.shots:
        if shot.asset_type != "solid":
            continue
        colour = (shot.asset_source or "").strip()
        if not colour.startswith(_SOLID_COLOUR_PREFIX) or len(colour) != 7:
            bad_solids.append(f"{shot.shot_id}({colour or '空'})")
    if bad_solids:
        findings.append(
            PreflightFinding(
                check="bad_solid_colour",
                severity="warning",
                shot_ids=tuple(item.split("(")[0] for item in bad_solids),
                detail=(
                    "这些纯色底镜头的颜色不是 #rrggbb，会退回默认深色："
                    + "、".join(bad_solids[:5])
                ),
            )
        )

    # 配音：要配音就得有词。
    if narration:
        with_text = [shot.shot_id for shot in storyboard.shots if (shot.narration or "").strip()]
        without = [shot.shot_id for shot in storyboard.shots if not (shot.narration or "").strip()]
        if not with_text:
            findings.append(
                PreflightFinding(
                    check="no_narration_text",
                    severity="error",
                    shot_ids=tuple(shot.shot_id for shot in storyboard.shots),
                    detail="要求配音，但分镜里一句旁白都没有（narration 列全是空的）",
                )
            )
        elif without:
            findings.append(
                PreflightFinding(
                    check="missing_narration_text",
                    severity="warning",
                    shot_ids=tuple(without),
                    detail=(
                        f"{len(without)} 个镜头没有旁白，这些镜头会是静音画面："
                        + "、".join(without[:5])
                    ),
                )
            )

    return PreflightReport(
        job_id=job_id,
        storyboard_present=True,
        shot_count=len(storyboard.shots),
        duration_seconds=float(storyboard.duration),
        asset_count=len(by_asset),
        findings=tuple(findings),
        facts={
            "fps": storyboard.fps,
            "width": storyboard.width,
            "height": storyboard.height,
            "assets": sorted(checked_kinds.items()),
            "solid_shots": sum(1 for shot in storyboard.shots if shot.asset_type == "solid"),
            "narration_shots": sum(1 for shot in storyboard.shots if (shot.narration or "").strip()),
            "manifest_present": bool(rows),
        },
    )


def to_markdown(report: PreflightReport) -> str:
    lines: list[str] = [f"# 渲染前预检：{report.job_id}", "", report.summary(), ""]
    if report.storyboard_present:
        lines.extend(
            [
                "## 分镜",
                "",
                f"- 镜头数：{report.shot_count}",
                f"- 时长：{report.duration_seconds:g} 秒",
                f"- 画布：{report.facts.get('width')}×{report.facts.get('height')} @ "
                f"{report.facts.get('fps')}fps",
                f"- 非纯色素材：{report.asset_count} 个",
                "",
            ]
        )
    for title, items in (("硬问题（不解决渲不出来）", report.errors), ("提醒（能渲，但看一眼）", report.warnings)):
        if not items:
            continue
        lines.extend([f"## {title}", ""])
        for item in items:
            where = f"（镜头 {', '.join(item.shot_ids)}）" if item.shot_ids else ""
            lines.append(f"- `{item.check}` {item.detail}{where}")
        lines.append("")
    lines.append("这份检查只回答「文件齐不齐、登记对不对、授权清不清楚」，不判断内容好不好看。")
    return "\n".join(lines) + "\n"


__all__ = [
    "COMMERCIAL_ALLOWED",
    "PreflightError",
    "PreflightFinding",
    "PreflightReport",
    "STORYBOARD_REL",
    "preflight_job",
    "to_markdown",
]
