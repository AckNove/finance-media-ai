"""`fact_check`：核对一条引文是否真的出现在已存证的原文快照里。

这是"不许编数字"这条底线的执行机构，所以它的边界写得很窄：

* **只查已抓到的快照，不发网络请求。** 想核实新资料就先用 `fetch_url` 抓下来，
  再核——否则"核查"会变成"顺手补一条来源"，那正是我们要避免的。
* 比对是**逐字包含**（只忽略空白差异），不做模糊匹配、不做同义改写。模糊匹配会
  让"下降了 1.05%"和"下降了 1.5%"都判命中，那比不核查更危险。
* 命中后返回字符偏移与快照文件本身，人可以点开原文看；不命中只说"未在原文中找到，
  需人工复核"，**不判"造假"**——可能是引文来自表格图片、可能是我们解析漏了。
* 落盘快照的 SHA-256 必须与库里的 `sources.content_hash` 一致，否则报
  `snapshot_hash_mismatch`：文件被动过，这条证据就不能再当证据用。

来源定位支持三种写法：`source_id`（库里的整数 id）、`source_url`（精确匹配）、
`evidence_id`（直接核对某条既有证据的 claim）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.capabilities.tools import ToolResult
from backend.app.core.caller import CallerContext
from backend.app.core.config import PROJECT_ROOT
from backend.app.database.session import get_session_factory
from backend.app.models.source import Source

SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "snapshots"
MIN_QUOTE_CHARS = 6
_WS = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """只抹平空白：换行/全角空格/制表符不应影响"这句话是不是原文"。"""
    return _WS.sub("", text)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_snapshot(content_hash: str, *, root: Path | None = None) -> Path | None:
    """按内容哈希找落盘快照（`data/snapshots/<host>/<sha256>.<ext>`）。"""
    if not content_hash:
        return None
    base = root or SNAPSHOT_ROOT
    if not base.exists():
        return None
    for candidate in base.glob(f"*/{content_hash}.*"):
        if candidate.is_file():
            return candidate
    return None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def check_quote(
    *,
    quote: str,
    snapshot_text: str,
) -> dict[str, Any]:
    """逐字核对。返回 `{found, offset, matched_chars}`。"""
    needle = _normalise(quote)
    haystack = _normalise(snapshot_text)
    index = haystack.find(needle)
    return {
        "found": index >= 0,
        "offset": index,
        "matched_chars": len(needle),
    }


def _session_factory(factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    return factory if factory is not None else get_session_factory()


def make_fact_check(
    *,
    session_factory: sessionmaker[Session] | None = None,
    snapshot_root: Path | None = None,
) -> Any:
    factory = _session_factory(session_factory)
    root = snapshot_root

    def fact_check(
        caller: CallerContext,
        *,
        quote: str,
        source_id: int | None = None,
        source_url: str | None = None,
        evidence_id: int | None = None,
    ) -> ToolResult:
        text = (quote or "").strip()
        if len(_normalise(text)) < MIN_QUOTE_CHARS:
            return ToolResult(
                status="error",
                error_code="bad_arguments",
                error_message=f"引文太短（至少 {MIN_QUOTE_CHARS} 个非空白字符）",
            )
        if source_id is None and not source_url and evidence_id is None:
            return ToolResult(
                status="error",
                error_code="bad_arguments",
                error_message="需要 source_id / source_url / evidence_id 之一来定位原文",
            )

        with factory() as session:
            source: Source | None = None
            if evidence_id is not None:
                from backend.app.models.evidence import Evidence

                evidence = session.get(Evidence, int(evidence_id))
                if evidence is None:
                    return ToolResult(
                        status="error",
                        error_code="unknown_evidence",
                        error_message=f"证据 {evidence_id} 不存在",
                    )
                source = session.get(Source, evidence.source_id)
                if source is None:
                    return ToolResult(
                        status="error",
                        error_code="unknown_source",
                        error_message=f"证据 {evidence_id} 指向的来源不存在",
                    )
            elif source_id is not None:
                source = session.get(Source, int(source_id))
                if source is None:
                    return ToolResult(
                        status="error",
                        error_code="unknown_source",
                        error_message=f"来源 {source_id} 不存在",
                    )
            else:
                source = session.scalars(
                    select(Source).where(Source.url == source_url)
                ).first()
                if source is None:
                    return ToolResult(
                        status="error",
                        error_code="unknown_source",
                        error_message=f"没有登记过这个地址：{source_url}",
                    )

            assert source is not None
            context = {
                "source_id": source.id,
                "title": source.title,
                "url": source.url,
                "content_hash": source.content_hash,
            }
            snapshot = find_snapshot(source.content_hash or "", root=root)
            if snapshot is None:
                return ToolResult(
                    status="error",
                    error_code="no_snapshot",
                    error_message="这条来源没有落盘快照，无法逐字核对（先用 fetch_url 抓取）",
                    data=context,
                )

            actual_hash = _hash_file(snapshot)
            if actual_hash != (source.content_hash or "").lower():
                return ToolResult(
                    status="error",
                    error_code="snapshot_hash_mismatch",
                    error_message="快照文件内容与登记的哈希不一致，这条证据不可用",
                    data={**context, "snapshot": str(snapshot), "actual_hash": actual_hash},
                )

            result = check_quote(quote=text, snapshot_text=_read_text(snapshot))

        data = {
            **context,
            "snapshot": str(snapshot),
            "quote": text,
            **result,
            "note": (
                "逐字命中（仅忽略空白差异）"
                if result["found"]
                else "未在原文快照中逐字找到，需人工复核；不等于判定造假"
            ),
            "checked_by": caller.name,
            "searched_web": False,
        }
        return ToolResult(status="ok", data=data)

    return fact_check


def register_verification_adapters(registry: Any) -> None:
    registry.register("fact_check", make_fact_check())


__all__ = [
    "MIN_QUOTE_CHARS",
    "SNAPSHOT_ROOT",
    "check_quote",
    "find_snapshot",
    "make_fact_check",
    "register_verification_adapters",
]
