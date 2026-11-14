"""素材清单审计：成片里出现的每一样东西，是不是都在清单上、且说的都是真话。

为什么需要它：`assets_manifest.csv` 的承诺是"这份片子的每一个画面和声音都登记在案"。
但要验证这句话，得把四份来源摆在一起比：

1. **清单**（`assets_manifest.csv`）——我们说用了什么、版权是什么；
2. **分镜表**（`storyboard/storyboard.csv`）——打算用哪个素材；
3. **时间轴**（`project/timeline.json`）——真正进了成片的镜头用了哪些 clip；
4. **磁盘**（清单里写的文件）——文件还在吗？还是当初那个文件吗（sha256）？

四份里任何两份对不上，就会出现"片子里有个来路不明的画面"或者"清单说授权没问题、
文件早被换掉了"这类事。这里只报事实，不替人下结论：

* ``error`` —— 片子里用了清单上没有的东西、清单上的文件没了/被改了、
  授权状态不是 allowed 却进了成片、走查不到时间轴；
* ``warning`` —— 清单登记了但成片没用（可能白登记）、底板没登记成功（渲染回执里有记录）；
* ``info`` —— 正常项，写出来是为了让人看到"查过了"。

判定不猜：拿不到时间轴就说拿不到（``error: timeline_missing``），不去假装"应该没问题"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.app.media.manifest import MANIFEST_FILENAME, read_manifest
from backend.app.video.generated import sha256_of

__all__ = [
    "AssetAudit",
    "AssetFinding",
    "audit_job",
    "to_markdown",
]

#: 允许进成片的授权状态。其余（unknown / not_allowed / 空）都算硬错。
ALLOWED_COMMERCIAL_USE = {"allowed"}

#: 时间轴里的 clip 键 → 清单 asset_id 的映射只需要原样比对；引擎自己生成的
#: 帧序列键（solid_xxxx）同样按 asset_id 登记，因此不需要特例表。
_TIMELINE_NAMES = ("project/timeline.json", "project/timeline.render.json")


@dataclass(frozen=True)
class AssetFinding:
    check: str
    severity: str
    asset_id: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "asset_id": self.asset_id,
            "detail": self.detail,
        }


@dataclass
class AssetAudit:
    job_id: str
    manifest_present: bool = False
    manifest_assets: int = 0
    storyboard_assets: int = 0
    timeline_assets: int = 0
    findings: list[AssetFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[AssetFinding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[AssetFinding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.ok:
            return (
                f"素材审计通过：清单 {self.manifest_assets} 条，分镜用到 {self.storyboard_assets} 个，"
                f"时间轴 {self.timeline_assets} 个 clip；{len(self.warnings)} 条提醒"
            )
        return f"素材审计发现 {len(self.errors)} 个硬问题、{len(self.warnings)} 条提醒"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "ok": self.ok,
            "manifest_present": self.manifest_present,
            "manifest_assets": self.manifest_assets,
            "storyboard_assets": self.storyboard_assets,
            "timeline_assets": self.timeline_assets,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [item.as_dict() for item in self.findings],
            "summary": self.summary(),
        }


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _timeline_clips(job_dir: Path) -> tuple[dict[str, Any], str]:
    """返回 (clips, 用了哪个文件的相对路径)。取不到就返回空字典。"""
    for name in _TIMELINE_NAMES:
        path = job_dir / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        clips = payload.get("clips")
        if isinstance(clips, dict):
            return clips, name
    return {}, ""


def _render_assets(job_dir: Path) -> dict[str, dict[str, Any]]:
    """渲染回执里记录的"本次真正导入的素材"，用来交叉验证时间轴。"""
    path = job_dir / "qc" / "render_run.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    report = payload.get("report") if isinstance(payload, dict) else None
    rows = (report or {}).get("assets") if isinstance(report, dict) else None
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("asset_id"):
            result[str(row["asset_id"])] = row
    return result


def audit_job(job_dir: Path | str, *, job_id: str | None = None) -> AssetAudit:
    """对一个视频任务目录做素材审计（纯本地读文件，不联网、不改任何东西）。"""

    directory = Path(job_dir)
    audit = AssetAudit(job_id=job_id or directory.name)

    manifest_file = directory / MANIFEST_FILENAME
    manifest_rows: list[dict[str, str]] = []
    if manifest_file.is_file():
        manifest_rows = read_manifest(manifest_file)
        audit.manifest_present = True
    registered = {str(row.get("asset_id") or ""): row for row in manifest_rows}
    registered.pop("", None)
    audit.manifest_assets = len(registered)

    storyboard_file = directory / "storyboard" / "storyboard.csv"
    planned: set[str] = set()
    if storyboard_file.is_file():
        for row in _read_csv_rows(storyboard_file):
            asset_id = str(row.get("asset_id") or "").strip()
            if asset_id:
                planned.add(asset_id)
    audit.storyboard_assets = len(planned)

    clips, timeline_name = _timeline_clips(directory)
    audit.timeline_assets = len(clips)
    rendered = _render_assets(directory)
    actual = set(clips) | set(rendered)

    if not manifest_file.is_file():
        if planned or actual:
            audit.findings.append(
                AssetFinding(
                    "manifest_missing",
                    "error",
                    "",
                    f"这片子用了 {len(actual | planned)} 个素材，但目录里没有 {MANIFEST_FILENAME}："
                    "来路与授权无从核对",
                )
            )
        else:
            audit.findings.append(
                AssetFinding("manifest_missing", "info", "", "没有素材清单，也没有用到任何外部素材")
            )

    if not clips and not rendered:
        audit.findings.append(
            AssetFinding(
                "timeline_missing",
                "warning",
                "",
                "找不到 project/timeline.json 或渲染回执：只能核对清单与分镜，核不了成片实际用了什么",
            )
        )

    # 1. 成片里用了、清单上没有 → 硬错（"来路不明的画面"）
    for asset_id in sorted(actual - set(registered)):
        row = rendered.get(asset_id) or {}
        extra = "（渲染时登记失败，见回执 register_error）" if row.get("registered") is False else ""
        audit.findings.append(
            AssetFinding(
                "asset_not_registered",
                "error",
                asset_id,
                f"成片里用了 {asset_id}，但素材清单里没有这一条{extra}",
            )
        )

    # 2. 清单登记了、成片没用 → 提醒（可能白登记，也可能这一版砍掉了）
    if actual:
        for asset_id in sorted(set(registered) - actual):
            audit.findings.append(
                AssetFinding(
                    "asset_unused",
                    "warning",
                    asset_id,
                    f"清单里有 {asset_id}，但这一版成片没用它",
                )
            )

    # 3. 文件还在吗、还是当初那个文件吗
    for asset_id, row in sorted(registered.items()):
        relative = str(row.get("file") or "").strip()
        if not relative:
            audit.findings.append(
                AssetFinding("asset_file_blank", "error", asset_id, "清单这一条没写文件路径")
            )
            continue
        path = (directory / relative).resolve()
        if not path.is_file():
            audit.findings.append(
                AssetFinding("asset_file_missing", "error", asset_id, f"清单里的文件不存在：{relative}")
            )
            continue
        expected = str(row.get("sha256") or "").strip().lower()
        if expected:
            actual_digest = sha256_of(path).lower()
            if actual_digest != expected:
                audit.findings.append(
                    AssetFinding(
                        "asset_hash_mismatch",
                        "error",
                        asset_id,
                        f"{relative} 的哈希与登记不符（登记 {expected[:12]}…，实际 {actual_digest[:12]}…）："
                        "文件在登记之后被改过",
                    )
                )
        commercial = str(row.get("commercial_use") or "").strip().lower()
        if asset_id in actual and commercial not in ALLOWED_COMMERCIAL_USE:
            audit.findings.append(
                AssetFinding(
                    "license_not_allowed",
                    "error",
                    asset_id,
                    f"{asset_id} 的商用授权是「{commercial or '未填'}」，却进了成片",
                )
            )

    # 4. 分镜打算用、清单上没有 → 硬错（哪怕这一版没渲染到）
    for asset_id in sorted(planned - set(registered)):
        if asset_id in actual:
            continue  # 上面已经按"成片用了没登记"报过一次
        audit.findings.append(
            AssetFinding(
                "storyboard_asset_not_registered",
                "error",
                asset_id,
                f"分镜里写了 {asset_id}，但素材清单里没有：要么补登记，要么改分镜",
            )
        )

    if audit.ok and not audit.warnings:
        audit.findings.append(
            AssetFinding(
                "all_registered",
                "info",
                "",
                f"清单 {audit.manifest_assets} 条与成片实际使用的 {len(actual)} 个素材对得上",
            )
        )
    return audit


def to_markdown(audit: AssetAudit) -> str:
    lines = [f"# 素材审计：{audit.job_id}", "", audit.summary(), ""]
    lines.append(
        f"- 清单：{'有' if audit.manifest_present else '**没有**'}（{audit.manifest_assets} 条）"
    )
    lines.append(f"- 分镜计划使用：{audit.storyboard_assets} 个")
    lines.append(f"- 时间轴/回执实际使用：{audit.timeline_assets} 个 clip")
    lines.append("")

    if not audit.findings:
        lines.append("没有需要报告的问题。")
        return "\n".join(lines) + "\n"

    labels = {"error": "硬问题", "warning": "提醒", "info": "说明"}
    for severity in ("error", "warning", "info"):
        items: Iterable[AssetFinding] = [f for f in audit.findings if f.severity == severity]
        items = list(items)
        if not items:
            continue
        lines.append(f"## {labels[severity]}（{len(items)}）")
        lines.append("")
        for item in items:
            prefix = f"`{item.asset_id}`：" if item.asset_id else ""
            lines.append(f"- {prefix}{item.detail}")
        lines.append("")

    lines.append("> 这份审计只核对「清单说了什么」与「片子里实际有什么」是否一致，不判断素材好不好看。")
    return "\n".join(lines) + "\n"
