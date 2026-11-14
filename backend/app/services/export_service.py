"""把一条排期导出成可发布文件，并留记录。

为什么要有这一步：运营表里 `export_path` 之前只能手工填。手工填的问题是——
「导出过了」这句话没有证据。这里做的事很朴素：真把文件写出来，把路径写进库里，
文件里带一稿三版的成果和一份 ``meta.json``（谁、什么时候、按哪些平台、用了哪份脚本），
再加一份 ``publish_checklist.md``（片子在哪、过没过技术质检、发完回来要填什么）。

导出不是发布。发布仍然必须人工填链接（`publish_state=published` 需要 `published_url`）。

导出还**必须过片子这一关**：排期绑了视频任务，就拿磁盘事实核对它到底能不能发。
结论被撤回的片子（``draft_invalid``）或根本没渲出来（``film_missing``）一律拒绝导出——
否则人会把一个已经作废的片子发出去，而这正是这个系统最不能犯的错。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.content.platform_adapt import PLATFORMS, adapt_payload
from backend.app.core.config import PROJECT_ROOT
from backend.app.models.enums import CalendarStatus
from backend.app.models.operations import ContentCalendar
from backend.app.models.script import Script
from backend.app.services.calendar_readiness import READINESS_LABELS, job_readiness

#: 导出根目录（相对仓库根）
EXPORT_ROOT = PROJECT_ROOT / "content" / "exports"

#: 每个平台一个文件，扩展名统一 .md（人可以直接粘到平台后台）
_SUFFIX = ".md"

#: 发布清单的文件名
CHECKLIST_NAME = "publish_checklist.md"

#: 这些状态下**不许导出**：片子作废了或压根没渲出来
BLOCKING_STATUSES: frozenset[str] = frozenset({"draft_invalid", "film_missing", "job_missing"})

#: 这些状态允许导出，但清单里必须写明为什么它只是"勉强能用"
_WARN_STATUSES: frozenset[str] = frozenset({"no_gate"})


class ExportError(RuntimeError):
    """导出失败。``reason`` 是稳定码。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _script_of(session: Session, script_id: int | None) -> Script:
    if not script_id:
        raise ExportError("no_script", "这条排期还没绑定脚本：先在「脚本」页写一版，再来导出")
    script = session.get(Script, script_id)
    if script is None:
        raise ExportError("no_script", f"排期引用的脚本 #{script_id} 不在了")
    return script


def _film_facts(job_dir: Path | None) -> dict[str, Any]:
    """从渲染回执里读片子的事实（时长、分辨率、质检失败项）。读不到就返回空，不猜。"""
    if job_dir is None:
        return {}
    path = job_dir / "qc" / "render_run.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    report = payload.get("report") if isinstance(payload, dict) else None
    if not isinstance(report, dict):
        return {}
    qc = report.get("qc") if isinstance(report.get("qc"), dict) else {}
    media = qc.get("media") if isinstance(qc.get("media"), dict) else {}
    checks = qc.get("checks") if isinstance(qc.get("checks"), dict) else {}
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "frames": report.get("frames"),
        "duration": media.get("duration"),
        "width": media.get("width"),
        "height": media.get("height"),
        "failed_checks": failed,
        "has_audio": media.get("has_audio"),
    }


def _checklist(
    *,
    entry: ContentCalendar,
    readiness: dict[str, Any] | None,
    facts: dict[str, Any],
    files: list[dict[str, str]],
    warnings: list[str],
) -> str:
    """写一份"照着做就能发"的清单。人拿到导出目录，先看这个文件。"""
    lines = [f"# 发布清单 · {entry.title}", ""]

    lines += ["## 一、片子", ""]
    if readiness is None:
        lines += ["- 这条排期**没绑视频任务**：只有文案，发什么由你决定。", ""]
    else:
        film = readiness.get("film") or {}
        lines.append(f"- 文件：`{film.get('path') or '（没找到）'}`")
        if film.get("byte_size"):
            size = int(film["byte_size"])
            lines.append(f"- 大小：{size / 1024 / 1024:.2f} MB（{size} 字节）")
        if facts:
            shape = ""
            if facts.get("width") and facts.get("height"):
                shape = f"{facts['width']}×{facts['height']}"
            bits = [bit for bit in (shape, f"{facts.get('frames')} 帧" if facts.get("frames") else "") if bit]
            if facts.get("duration"):
                bits.append(f"{float(facts['duration']):.1f} 秒")
            if bits:
                lines.append(f"- 规格：{' · '.join(bits)}")
            if facts.get("has_audio") is False:
                lines.append("- 声音：**这条片子没有音轨**（渲染时没开旁白）")
            failed = facts.get("failed_checks") or []
            if failed:
                lines.append(f"- 技术质检：**未通过** —— {'、'.join(failed)}")
            else:
                lines.append("- 技术质检：通过（黑帧/冻结/静音/分辨率/响度等）")
        else:
            lines.append("- 技术质检：没找到渲染回执（`qc/render_run.json`），请自己看一遍片子")
        label = readiness.get("label") or ""
        lines.append(f"- 底片就绪度：**{label}**")
        if readiness.get("invalidated_reason"):
            lines.append(f"- 失效原因：{readiness['invalidated_reason']}")
        if readiness.get("status") in _WARN_STATUSES:
            lines.append(f"- 注意：{readiness.get('next_step') or ''}")
        lines.append("")

    lines += ["## 二、这几个平台的文案", ""]
    for item in files:
        lines.append(f"- `{Path(item['path']).name}` —— 直接复制到平台后台（标题、正文、标签都在里面）")
    lines.append("")

    lines += ["## 三、发完回来要填的（不填等于没发）", ""]
    lines += [
        "1. 排期表点「标已发布」，**链接必填**：`publish_state=published` 且 `published_url` 不为空。",
        "2. 进度推到「已导出/已发布」，两个字段不会自己同步（工作台会提醒「账目对不上」）。",
        "3. 有数据了就回填：`POST /api/calendar/{id}/metrics` 一条，或用「导入 CSV」批量。",
        "   平台汇总只算**已发布**排期的最新一次快照，没标已发布的数据不会被计入。",
        "",
        "> 系统不会替你发布，也不会替你判断这片子好不好看——技术质检只保证「不是黑屏、没声音、尺寸不对」。",
    ]

    if warnings:
        lines += ["", "## 四、导出时的提醒", ""]
        lines += [f"- {item}" for item in warnings]
    return "\n".join(lines).rstrip() + "\n"


def _render(variant: dict[str, Any]) -> str:
    """把一个平台版本的变体写成 Markdown（标题 + 正文 + 标签）。"""
    lines = [f"# {variant['title']}", ""]
    if variant.get("summary"):
        lines += [variant["summary"], ""]
    lines += [variant.get("body") or "", ""]
    hashtags = variant.get("hashtags") or []
    if hashtags:
        lines.append(" ".join(f"#{tag}" for tag in hashtags))
    warnings = variant.get("warnings") or []
    if warnings:
        lines += ["", "> 导出时发现的提醒（不影响发布，供你复核）："]
        lines += [f"> - {item}" for item in warnings]
    return "\n".join(lines).rstrip() + "\n"


def export_entry(
    session: Session,
    entry_id: int,
    *,
    platforms: Sequence[str] | None = None,
    root: Path | None = None,
    now: datetime | None = None,
    jobs_root: Path | None = None,
) -> dict[str, Any]:
    """执行导出，返回 ``{ok, export_path, checklist_path, files, readiness, ...}``。

    绑了视频任务时，先按磁盘事实核对片子能不能发（``BLOCKING_STATUSES`` 一律拒绝），
    再写平台文案、``meta.json`` 与 ``publish_checklist.md``。
    """
    entry = session.get(ContentCalendar, entry_id)
    if entry is None:
        raise ExportError("unknown_entry", f"没有这条排期：{entry_id}")
    script = _script_of(session, entry.script_id)

    wanted = [str(item) for item in (platforms or PLATFORMS)]
    unknown = [item for item in wanted if item not in PLATFORMS]
    if unknown:
        raise ExportError("unknown_platform", f"不认识的平台：{'、'.join(unknown)}")

    # 片子这一关：结论被撤回、片子没渲出来、任务目录不在——都不许导出。
    # 这一条比"能不能复制文案"重要得多：发出去就收不回来了。
    readiness: dict[str, Any] | None = None
    if (entry.video_job_id or "").strip():
        readiness = job_readiness(entry.video_job_id, jobs_root=jobs_root)
        status = str(readiness.get("status") or "")
        if status in BLOCKING_STATUSES:
            label = READINESS_LABELS.get(status, status)
            raise ExportError(
                status,
                f"这条排期背后的片子不能发（{label}）：{readiness.get('next_step') or ''}",
            )

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    base = (root or EXPORT_ROOT) / f"entry-{entry.id}" / stamp
    base.mkdir(parents=True, exist_ok=True)

    payload = adapt_payload(
        title=script.title,
        body=script.body,
        tags=(entry.notes or "").split() if entry.notes else (),
        platforms=tuple(wanted),
    )

    files: list[dict[str, str]] = []
    warnings: list[str] = []
    for variant in payload["variants"]:
        target = base / f"{variant['platform']}{_SUFFIX}"
        target.write_text(_render(variant), encoding="utf-8")
        files.append({"platform": variant["platform"], "path": str(target), "bytes": str(target.stat().st_size)})
        warnings.extend(f"[{variant['platform']}] {item}" for item in (variant.get("warnings") or []))

    facts = _film_facts(Path(readiness["path"]) if readiness and readiness.get("path") else None)
    checklist_path = base / CHECKLIST_NAME
    checklist_path.write_text(
        _checklist(entry=entry, readiness=readiness, facts=facts, files=files, warnings=warnings),
        encoding="utf-8",
    )

    meta = {
        "entry_id": entry.id,
        "title": entry.title,
        "platform": entry.platform,
        "video_job_id": entry.video_job_id,
        "readiness": (readiness or {}).get("status") or "",
        "film": facts,
        "script_id": script.id,
        "script_version": getattr(script, "version", None),
        "exported_at": stamp,
        "platforms": wanted,
        "files": files,
        "checklist": str(checklist_path),
        "warnings": warnings,
        "note": "导出只是把文件写出来；发布仍需人工到平台操作并把链接填回排期表。",
    }
    meta_path = base / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    entry.export_path = str(base)
    if entry.status in {CalendarStatus.PLANNED.value, CalendarStatus.SCRIPTED.value, CalendarStatus.RENDERED.value}:
        entry.status = CalendarStatus.EXPORTED.value
    session.commit()

    return {
        "ok": True,
        "entry_id": entry.id,
        "export_path": str(base),
        "meta_path": str(meta_path),
        "checklist_path": str(checklist_path),
        "files": files,
        "platforms": wanted,
        "script_id": script.id,
        "readiness": (readiness or {}).get("status") or "",
        "warnings": warnings,
        "note": meta["note"],
    }


__all__ = ["BLOCKING_STATUSES", "CHECKLIST_NAME", "EXPORT_ROOT", "ExportError", "export_entry"]
