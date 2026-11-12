"""把一次研究的抓取结果与研究结论落进库，形成可追溯的证据链。

为什么需要这一层：
`TaskRuntime` 只知道"哪一格跑完了"，产物还停留在 JSON 里。用户要的是能翻回去
核对的资料库——哪篇文章、什么机构发的、什么时候抓到、原文摘要是什么、这句话
用它来支撑还是反驳。这些必须在库里，而不是散落在 agent 输出的 dict 里。

设计取舍：
- **不存全文**（版权与体积），只存标题/URL/发布方/时间/内容哈希/快照路径；
  全文在 `data/snapshots/` 的本地快照里，库里留哈希指向它。
- 同一 URL 再次抓到只更新元数据、不产生新行（按 `url` 去重）；URL 缺失时退回
  按 `content_hash` 去重，避免同一篇转载刷出多条"来源"。
- 证据必须挂在报告上（`evidence` 表有 CHECK 约束），所以 `record_evidence`
  要求先有 `ResearchReport`。
- 抓到的数字默认 `claim_nature=fact` + `verification_status=verified` 只在逐字
  核对命中时成立；没命中一律 `unverified`，**不因为"看起来对"就标已验证**。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.utils import utcnow
from backend.app.models.enums import (
    CredibilityLevel,
    LicenseStatus,
    SourceType,
    StatementNature,
    SupportType,
    VerificationStatus,
)
from backend.app.models.evidence import Evidence
from backend.app.models.research import ResearchReport
from backend.app.models.source import Source
from backend.app.research.digest import Digest, SourceRef

#: 域名 → 来源类型。只写有把握的；其余一律 `other`，由人后续在资料库里改。
_DOMAIN_KINDS: tuple[tuple[str, SourceType], ...] = (
    ("pbc.gov.cn", SourceType.OFFICIAL_STATISTICS),
    ("stats.gov.cn", SourceType.OFFICIAL_STATISTICS),
    ("customs.gov.cn", SourceType.OFFICIAL_STATISTICS),
    ("nfra.gov.cn", SourceType.REGULATOR_FILING),
    ("csrc.gov.cn", SourceType.REGULATOR_FILING),
    ("cninfo.com.cn", SourceType.EXCHANGE_DISCLOSURE),
    ("sse.com.cn", SourceType.EXCHANGE_DISCLOSURE),
    ("szse.cn", SourceType.EXCHANGE_DISCLOSURE),
    ("sec.gov", SourceType.REGULATOR_FILING),
)

#: 官方与交易所站点默认给"高"可信度，其余先按未知处理——不替人做判断。
_HIGH_TRUST = ("pbc.gov.cn", "stats.gov.cn", "nfra.gov.cn", "csrc.gov.cn", "sec.gov")


@dataclass(frozen=True)
class RecordedSources:
    """落库结果：谁能对上哪个 source_id，一次说清楚。"""

    mapping: Mapping[str, int]
    created: int
    updated: int

    def ids(self) -> list[int]:
        return sorted(set(self.mapping.values()))


def classify_source_type(url: str | None) -> SourceType:
    """按域名判断来源类型；判断不出来就是 ``other``。"""
    host = _host_of(url)
    for suffix, kind in _DOMAIN_KINDS:
        if host.endswith(suffix):
            return kind
    return SourceType.OTHER


def credibility_for(url: str | None) -> CredibilityLevel:
    host = _host_of(url)
    if any(host.endswith(suffix) for suffix in _HIGH_TRUST):
        return CredibilityLevel.HIGH
    return CredibilityLevel.UNKNOWN


def _host_of(url: str | None) -> str:
    if not url:
        return ""
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url.strip())
    if not match:
        return ""
    host = match.group(1).lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    return host.split(":", 1)[0]


def record_sources(session: Session, refs: Iterable[SourceRef]) -> RecordedSources:
    """把研究用到的来源写进 ``sources``；已存在的只补空字段。"""
    mapping: dict[str, int] = {}
    created = 0
    updated = 0
    for ref in refs:
        existing = _find_source(session, ref)
        if existing is None:
            row = Source(
                title=ref.title or ref.url or f"来源 {ref.source_id}",
                url=ref.url or None,
                publisher=ref.site_name or None,
                source_type=classify_source_type(ref.url),
                published_at=_parse_dt(ref.published_at),
                accessed_at=utcnow(),
                credibility_level=credibility_for(ref.url),
                license_status=LicenseStatus.UNCLEAR,
                content_hash=ref.sha256 or None,
                language="zh",
                notes=_source_note(ref),
            )
            session.add(row)
            session.flush()
            mapping[ref.source_id] = row.id
            created += 1
            continue
        # 已存在：只补空白，不覆盖人手工填过的内容（例如可信度、许可）
        if not existing.publisher and ref.site_name:
            existing.publisher = ref.site_name
        if not existing.content_hash and ref.sha256:
            existing.content_hash = ref.sha256
        if existing.published_at is None:
            existing.published_at = _parse_dt(ref.published_at)
        existing.accessed_at = utcnow()
        mapping[ref.source_id] = existing.id
        updated += 1
    session.flush()
    return RecordedSources(mapping=dict(mapping), created=created, updated=updated)


def _find_source(session: Session, ref: SourceRef) -> Source | None:
    if ref.url:
        found = session.scalar(select(Source).where(Source.url == ref.url))
        if found is not None:
            return found
    if ref.sha256:
        return session.scalar(select(Source).where(Source.content_hash == ref.sha256))
    return None


def _source_note(ref: SourceRef) -> str:
    bits = []
    if ref.query:
        bits.append(f"检索词：{ref.query}")
    if ref.snapshot_path:
        bits.append(f"本地快照：{ref.snapshot_path}")
    if ref.char_count:
        bits.append(f"正文 {ref.char_count} 字")
    if ref.author:
        bits.append(f"作者：{ref.author}")
    return "；".join(bits)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len("2024-01-01T00:00:00")], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def record_report(
    session: Session,
    *,
    digest: Digest,
    research_task_id: int | None = None,
    agent_run_id: int | None = None,
    outline: list[dict[str, Any]] | None = None,
    tier_stats: dict[str, Any] | None = None,
    summary_override: str | None = None,
    findings_override: list[dict[str, Any]] | None = None,
    model_label: str = "",
) -> ResearchReport:
    """创建/更新一条研究报告草稿（摘要来自底稿，不是模型编的结论）。

    同一个任务重复跑分析阶段时**复用同一条报告**：一个任务对应一份研究底稿，
    不会因为多跑一次就多出一份"报告"，否则资料库会被草稿塞满。

    ``summary_override`` / ``findings_override`` 是给"模型已给出分析"的场景用的：
    调用方负责保证这些结论**每条都挂了证据编号**（见 ``research.analysis``）。
    ``model_label``（形如 ``deepseek/deepseek-flash``）用于在局限说明里如实写明
    "这一版是模型写的"，而不是沿用底稿里"未使用模型"那句话。
    """
    summary = summary_override or _summary_of(digest)
    findings = findings_override if findings_override is not None else _findings_of(digest)
    limitations = _limitations_of(digest, model_label=model_label)
    stats = tier_stats or _tier_stats_of(digest)
    report = None
    if research_task_id is not None:
        report = session.scalar(
            select(ResearchReport).where(ResearchReport.research_task_id == research_task_id)
        )
    if report is not None:
        report.title = digest.title
        report.summary = summary
        report.findings = findings
        report.limitations = limitations
        report.tier_stats = stats
        report.outline = outline or report.outline or []
        if agent_run_id is not None:
            report.agent_run_id = agent_run_id
        report.reading_time_minutes = max(1, len(summary) // 400)
        session.flush()
        return report
    report = ResearchReport(
        title=digest.title,
        summary=summary,
        findings=findings,
        limitations=limitations,
        verification_status=VerificationStatus.UNVERIFIED,
        author_agent=digest.generated_by,
        research_task_id=research_task_id,
        agent_run_id=agent_run_id,
        outline=outline or [],
        tier_stats=stats,
        reading_time_minutes=max(1, len(summary) // 400),
    )
    session.add(report)
    session.flush()
    return report


def _summary_of(digest: Digest) -> str:
    parts = [
        f"本稿由规则汇编（未使用模型）生成，共 {len(digest.sources)} 个来源、"
        f"{len(digest.data_points)} 条候选数据点。"
    ]
    if digest.objective:
        parts.append(f"研究目标：{digest.objective}")
    if digest.queries:
        parts.append("检索词：" + "、".join(digest.queries))
    return " ".join(parts)


def _findings_of(digest: Digest) -> list[dict[str, Any]]:
    """把数据点原样列出来，不加工成结论——没有模型就不写结论。

    结构是 ``{"text", "source_id", "numbers", "verified"}``：库里这一列是 JSON，
    接口与前端都按"一条发现=一条对象"来读；早先只写字符串，会让
    ``GET /api/research-reports`` 直接 500。
    """
    return [
        {
            "text": point.text,
            "source_id": point.source_id,
            "numbers": list(point.numbers),
            "verified": False,
        }
        for point in digest.data_points[:20]
    ]


def _limitations_of(digest: Digest, *, model_label: str = "") -> str:
    """局限说明：先把"这一版做了什么、没做什么"写清楚，再把交叉比对看到的坑摆出来。

    报告最容易被当成"结论"直接引用，所以单一来源与冲突必须写在最显眼的位置，
    而不是藏在一份附带的 markdown 里。

    ``model_label`` 非空表示这一版摘要/结论是模型写的——此时**必须换掉**底稿里
    「未使用模型、只有规则汇编」那几句，否则报告会写着"没有用模型"而正文是模型写的。
    """
    notes = [note for note in digest.notes]
    if model_label:
        notes = [
            note
            for note in notes
            if not any(marker in note for marker in ("未使用模型", "没有使用模型", "不含分析"))
        ]
        parts = [
            f"本稿的摘要与结论由模型（{model_label}）基于下方列出的证据生成，"
            "每条结论都挂了来源编号；但**模型结论不等于事实核查结论**，引用前必须回原文核对口径与时点"
        ]
    else:
        parts = notes or ["未使用模型，只有规则汇编"]
    cross = digest.corroboration
    if cross is not None:
        if getattr(cross, "conflicts", ()):
            parts.append(
                f"跨来源比对发现 {len(cross.conflicts)} 处同一件事数字不一致，"
                "未判断谁对，引用前必须回原文核对口径与时点"
            )
        if getattr(cross, "single_source", ()):
            parts.append(
                f"另有 {len(cross.single_source)} 条说法只有单一来源，引用前应另找佐证"
            )
        if getattr(cross, "corroborated", ()):
            parts.append(
                f"其中 {len(cross.corroborated)} 个数字被至少两个来源同时说到"
                "（仅表示一致，不等于数据正确）"
            )
    return "；".join(parts)


def _tier_stats_of(digest: Digest) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "sources": len(digest.sources),
        "data_points": len(digest.data_points),
    }
    cross = digest.corroboration
    if cross is not None:
        stats["cross_check"] = {
            "checked_points": getattr(cross, "checked_points", 0),
            "corroborated": len(getattr(cross, "corroborated", ())),
            "conflicts": len(getattr(cross, "conflicts", ())),
            "single_source": len(getattr(cross, "single_source", ())),
        }
    return stats


def record_evidence(
    session: Session,
    *,
    report: ResearchReport,
    digest: Digest,
    source_ids: Mapping[str, int],
    verification: Mapping[str, Any] | None = None,
) -> list[Evidence]:
    """把候选数据点写成证据行，逐字核对命中的才标 verified。"""
    statuses = {
        item["text"]: item["status"] for item in (verification or {}).get("results", [])
    }
    excerpts = {ref.source_id: (digest.meta.get("excerpts") or {}).get(ref.source_id, "") for ref in digest.sources}
    rows: list[Evidence] = []
    for point in digest.data_points:
        source_id = source_ids.get(point.source_id)
        if source_id is None:
            continue
        hit = statuses.get(point.text)
        rows.append(
            Evidence(
                source_id=source_id,
                claim=point.text,
                original_excerpt=excerpts.get(point.source_id, "")[:1000] or None,
                quote_locator=f"{point.source_id}:{point.numbers[0]}" if point.numbers else point.source_id,
                support_type=SupportType.SUPPORTS,
                claim_nature=StatementNature.FACT,
                verification_status=(
                    VerificationStatus.VERIFIED if hit == "found" else VerificationStatus.UNVERIFIED
                ),
                verified_at=utcnow() if hit == "found" else None,
                research_report_id=report.id,
            )
        )
    session.add_all(rows)
    session.flush()
    return rows


def digest_fingerprint(digest: Digest) -> str:
    """底稿指纹：同样的来源与数据点 → 同样的指纹（用于幂等重跑）。"""
    payload = "|".join([ref.url or ref.sha256 for ref in digest.sources])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: 人工复核允许的取值。**模型判定永远不能直接落进这里。**
HUMAN_REVIEW_STATUSES: frozenset[str] = frozenset(
    {"pending", "confirmed", "rejected", "needs_more_evidence"}
)


def record_checks(session: Session, *, report: ResearchReport, checks: Iterable[Any]) -> list[Any]:
    """把一次语义核查的结果写进 ``finding_checks``。

    同一份报告重复核查时**整批替换**（而不是追加）：核查结论是"当时对这批证据的
    判断"，留着旧判定只会让人分不清哪一条是现状。人工复核状态按 (报告, 下标) 继承，
    避免重跑一次模型就把人已经确认过的结论打回 pending。
    """
    from backend.app.models.research import FindingCheck

    previous = {
        row.finding_index: row
        for row in session.execute(
            select(FindingCheck).where(FindingCheck.research_report_id == report.id)
        )
        .scalars()
        .all()
    }
    for row in previous.values():
        session.delete(row)
    session.flush()

    rows: list[FindingCheck] = []
    for check in checks:
        carried = previous.get(int(getattr(check, "index", 0)))
        human_status = carried.human_review_status if carried is not None else "pending"
        human_note = carried.human_note if carried is not None else ""
        human_at = carried.human_reviewed_at if carried is not None else None
        row = FindingCheck(
            research_report_id=report.id,
            finding_index=int(getattr(check, "index", 0)),
            claim=str(getattr(check, "claim", "")),
            claim_kind=str(getattr(check, "claim_kind", "inference") or "inference"),
            evidence_ids=list(getattr(check, "evidence_ids", ()) or ()),
            source_ids=list(getattr(check, "source_ids", ()) or ()),
            verdict=str(getattr(check, "verdict", "unverifiable")),
            reason=str(getattr(check, "reason", "") or ""),
            supporting=list(getattr(check, "supporting", ()) or ()),
            contradicting=list(getattr(check, "contradicting", ()) or ()),
            missing=list(getattr(check, "missing", ()) or ()),
            issues=[item.as_dict() for item in (getattr(check, "issues", ()) or ())],
            provider=str(getattr(check, "provider", "") or ""),
            model=str(getattr(check, "model", "") or ""),
            model_call_id=getattr(check, "model_call_id", None),
            prompt_tokens=int(getattr(check, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(check, "completion_tokens", 0) or 0),
            cost_cny=getattr(check, "cost_cny", 0) or 0,
            checked_at=_parse_dt(str(getattr(check, "checked_at", "") or "")),
            human_review_status=human_status,
            human_note=human_note,
            human_reviewed_at=human_at,
            notes=list(getattr(check, "notes", ()) or ()),
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def set_human_review(
    session: Session,
    *,
    check_id: int,
    status_value: str,
    note: str = "",
) -> Any:
    """人改人工复核状态。非法取值抛 ``ValueError``（由上层翻成 422）。"""
    from backend.app.models.research import FindingCheck

    if status_value not in HUMAN_REVIEW_STATUSES:
        raise ValueError(
            f"human_review_status 只能是 {sorted(HUMAN_REVIEW_STATUSES)}，收到 {status_value!r}"
        )
    row = session.get(FindingCheck, check_id)
    if row is None:
        return None
    row.human_review_status = status_value
    row.human_note = note or ""
    row.human_reviewed_at = None if status_value == "pending" else utcnow()
    session.flush()
    return row


__all__ = [
    "HUMAN_REVIEW_STATUSES",
    "RecordedSources",
    "classify_source_type",
    "credibility_for",
    "digest_fingerprint",
    "record_checks",
    "record_evidence",
    "record_report",
    "record_sources",
    "set_human_review",
]
