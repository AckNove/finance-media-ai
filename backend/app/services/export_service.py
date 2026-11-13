"""把一条排期导出成可发布文件，并留记录。

为什么要有这一步：运营表里 `export_path` 之前只能手工填。手工填的问题是——
「导出过了」这句话没有证据。这里做的事很朴素：真把文件写出来，把路径写进库里，
文件里带一稿三版的成果和一份 ``meta.json``（谁、什么时候、按哪些平台、用了哪份脚本）。

导出不是发布。发布仍然必须人工填链接（`publish_state=published` 需要 `published_url`）。
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

#: 导出根目录（相对仓库根）
EXPORT_ROOT = PROJECT_ROOT / "content" / "exports"

#: 每个平台一个文件，扩展名统一 .md（人可以直接粘到平台后台）
_SUFFIX = ".md"


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
) -> dict[str, Any]:
    """执行导出，返回 ``{ok, entry_id, export_path, files, platforms, script_id, warnings}``。"""
    entry = session.get(ContentCalendar, entry_id)
    if entry is None:
        raise ExportError("unknown_entry", f"没有这条排期：{entry_id}")
    script = _script_of(session, entry.script_id)

    wanted = [str(item) for item in (platforms or PLATFORMS)]
    unknown = [item for item in wanted if item not in PLATFORMS]
    if unknown:
        raise ExportError("unknown_platform", f"不认识的平台：{'、'.join(unknown)}")

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

    meta = {
        "entry_id": entry.id,
        "title": entry.title,
        "platform": entry.platform,
        "script_id": script.id,
        "script_version": getattr(script, "version", None),
        "exported_at": stamp,
        "platforms": wanted,
        "files": files,
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
        "files": files,
        "platforms": wanted,
        "script_id": script.id,
        "warnings": warnings,
        "note": meta["note"],
    }


__all__ = ["EXPORT_ROOT", "ExportError", "export_entry"]
