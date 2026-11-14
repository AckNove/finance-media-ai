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
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.content.platform_adapt import PLATFORMS, SPECS, adapt_payload
from backend.app.core.config import PROJECT_ROOT
from backend.app.models.enums import CalendarStatus
from backend.app.models.operations import ContentCalendar
from backend.app.models.script import Script
from backend.app.services.calendar_readiness import READINESS_LABELS, job_readiness
from backend.app.services.content_tags import format_hashtags, split_tags, tag_note

#: 导出根目录（相对仓库根）
EXPORT_ROOT = PROJECT_ROOT / "content" / "exports"

#: 每个平台一个文件，扩展名统一 .md（人可以直接粘到平台后台）
_SUFFIX = ".md"

#: 发布清单的文件名
CHECKLIST_NAME = "publish_checklist.md"

#: 交付包首页（一页纸：发什么、复制什么、发完填什么）
README_NAME = "README.md"

#: 标签速查（每个平台一行，复制即可）
TAGS_NAME = "tags.txt"

#: 片子放哪（硬链接优先，跨盘时退回复制）
FILM_DIR_NAME = "film"

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


def _attach_film(base: Path, readiness: dict[str, Any] | None) -> dict[str, Any]:
    """把片子放进交付包。

    人拿着手机去平台上传时，最烦的是"文案在这个目录、片子在另一个目录"。
    这里直接把片子放进 ``film/``：同盘优先用**硬链接**（不占额外空间、删掉临时目录
    也不影响原件），跨盘或没有权限时退回复制。

    拿不到片子（没绑任务 / 文件不在）**不算导出失败**——“只有文案”也是一种交付，
    但必须在包里说清楚，不能让人以为片子在里面。
    """
    film = (readiness or {}).get("film") or {}
    source = str(film.get("path") or "")
    if not source:
        return {"status": "none", "note": "这条排期没绑视频任务：交付包里只有文案"}
    src = Path(source)
    if not src.is_file():
        return {"status": "missing", "source": str(src), "note": "记录里的片子文件不在了"}
    target_dir = base / FILM_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / src.name
    method = "copy"
    try:
        os.link(src, target)
        method = "hardlink"
    except OSError:
        # 跨盘、文件系统不支持硬链接、权限不足——都退回复制，并把方式记进回执。
        shutil.copy2(src, target)
    return {
        "status": "copied",
        "source": str(src),
        "target": str(target),
        "relative": f"{FILM_DIR_NAME}/{src.name}",
        "method": method,
        "byte_size": target.stat().st_size,
    }


def _tags_text(variants: list[dict[str, Any]], tags: list[str]) -> str:
    """标签速查：每个平台一行，复制即可。"""
    lines = [f"# 话题标签速查 · 共 {len(tags)} 个（来源：排期的「标签」字段）", ""]
    if not tags:
        lines += ["（这条排期没有标签：平台建议抖音 3~5 个、小红书 5~10 个，请手工补，这里不替你编）", ""]
    lines.append(f"全部：{format_hashtags(tags) if tags else '（空）'}")
    lines.append("")
    for item in variants:
        current = item.get("hashtags") or []
        lines.append(f"[{item['platform']}] {format_hashtags(current) if current else '（没有）'}")
    return "\n".join(lines).rstrip() + "\n"


def _readme(
    *,
    entry: ContentCalendar,
    files: list[dict[str, str]],
    film: dict[str, Any],
    tags: list[str],
    readiness: dict[str, Any] | None,
) -> str:
    """交付包首页：一页纸看完"发什么、复制什么、发完填什么"。"""
    lines = [f"# 交付包 · {entry.title}", ""]
    lines.append(f"- 平台：{entry.platform}")
    lines.append(f"- 视频任务：{entry.video_job_id or '（没绑）'}")
    lines.append(f"- 底片就绪度：{READINESS_LABELS.get(str((readiness or {}).get('status') or ''), '（没绑视频任务）')}")
    lines.append("")
    lines.append("## 一、要发的东西")
    lines.append("")
    if film.get("status") == "copied":
        method = "硬链接（不占额外空间）" if film.get("method") == "hardlink" else "复制"
        lines.append(f"- 片子：`{film['relative']}`（{method}）")
    elif film.get("status") == "missing":
        lines.append(f"- 片子：**找不到**（记录里写的是 `{film.get('source')}`）")
    else:
        lines.append("- 片子：这条排期没绑视频任务，只有文案")
    lines.append("")
    lines.append("## 二、复制到平台后台的文案")
    lines.append("")
    for item in files:
        lines.append(f"- `{Path(item['path']).name}`")
    lines.append("")
    lines.append(f"## 三、话题标签（速查表在 `{TAGS_NAME}`）")
    lines.append("")
    lines.append(f"- {tag_note(tags, **_platform_limits(entry.platform))}")
    lines.append("")
    lines.append("## 四、发完回来填什么")
    lines.append("")
    lines.append(f"照着 `{CHECKLIST_NAME}` 第三节做：标已发布（**链接必填**）、推进度、回填数据。")
    lines.append("")
    lines.append("> 系统不会替你发布。这个包里没有「封面」——封面要在平台后台上传或自己做一个，")
    lines.append("> 导出只负责把文案、标签、片子凑到一处。")
    return "\n".join(lines).rstrip() + "\n"


def _platform_limits(platform: str) -> dict[str, int]:
    """平台的标签个数建议（来自 ``platform_adapt.SPECS``，不另写一份）。"""
    spec = SPECS.get(platform)
    if spec is None:
        return {}
    return {"platform_min": spec.hashtag_min, "platform_max": spec.hashtag_max}


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
    tags: list[str] | None = None,
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
    tag_list = tags or []
    lines.append(f"- 标签：共 {len(tag_list)} 个，速查表在 `{TAGS_NAME}`")
    if not tag_list:
        lines.append("  —— **没填标签**：平台建议抖音 3~5 个、小红书 5~10 个；这里不替你编，请回排期填上再导出")
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
        tags=split_tags(getattr(entry, "tags", "")),
        platforms=tuple(wanted),
    )

    files: list[dict[str, str]] = []
    warnings: list[str] = []
    tags = split_tags(getattr(entry, "tags", ""))
    if not tags:
        warnings.append(
            "这条排期没有标签：平台建议抖音 3~5 个、小红书 5~10 个。这里不替你编标签，"
            "请到「内容日历」把标签填上再导出"
        )
    for variant in payload["variants"]:
        target = base / f"{variant['platform']}{_SUFFIX}"
        target.write_text(_render(variant), encoding="utf-8")
        files.append({"platform": variant["platform"], "path": str(target), "bytes": str(target.stat().st_size)})
        warnings.extend(f"[{variant['platform']}] {item}" for item in (variant.get("warnings") or []))

    film = _attach_film(base, readiness)
    tags_path = base / TAGS_NAME
    tags_path.write_text(_tags_text(payload["variants"], tags), encoding="utf-8")
    readme_path = base / README_NAME
    readme_path.write_text(
        _readme(entry=entry, files=files, film=film, tags=tags, readiness=readiness),
        encoding="utf-8",
    )

    facts = _film_facts(Path(readiness["path"]) if readiness and readiness.get("path") else None)
    checklist_path = base / CHECKLIST_NAME
    checklist_path.write_text(
        _checklist(entry=entry, readiness=readiness, facts=facts, files=files, warnings=warnings, tags=tags),
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
        "readme": str(readme_path),
        "tags_file": str(tags_path),
        "tags": tags,
        "film": facts,
        "film_file": film,
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
        "readme_path": str(readme_path),
        "tags_path": str(tags_path),
        "tags": tags,
        "film": facts,
        "film_file": film,
        "files": files,
        "platforms": wanted,
        "script_id": script.id,
        "readiness": (readiness or {}).get("status") or "",
        "warnings": warnings,
        "note": meta["note"],
    }


__all__ = [
    "BLOCKING_STATUSES",
    "CHECKLIST_NAME",
    "EXPORT_ROOT",
    "FILM_DIR_NAME",
    "README_NAME",
    "TAGS_NAME",
    "ExportError",
    "export_entry",
]
