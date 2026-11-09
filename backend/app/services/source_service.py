"""资料来源与事实证据服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.core.utils import sha256_hex, utcnow
from backend.app.models.enums import VerificationStatus
from backend.app.models.evidence import Evidence
from backend.app.models.knowledge import KnowledgeItem
from backend.app.models.research import ResearchReport
from backend.app.models.source import Source
from backend.app.schemas.source import EvidenceCreate, SourceCreate
from backend.app.services.base import BaseService


class SourceService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create(self, payload: SourceCreate) -> Source:
        data = payload.model_dump(exclude={"raw_excerpt"})
        content_hash = data.pop("content_hash", None)
        if content_hash is None and payload.raw_excerpt:
            content_hash = sha256_hex(payload.raw_excerpt)
        if data.get("accessed_at") is None:
            data["accessed_at"] = utcnow()
        source = Source(content_hash=content_hash, **data)
        self.db.add(source)
        return self._commit(source, "资料来源写入失败（可能违反唯一约束）")

    def get(self, source_id: int) -> Source:
        source = self.db.get(Source, source_id)
        if source is None:
            raise NotFoundError(f"资料来源 id={source_id} 不存在", details={"source_id": source_id})
        return source

    def list(
        self,
        *,
        source_type: str | None = None,
        credibility_level: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Source], int]:
        stmt = select(Source)
        if source_type:
            stmt = stmt.where(Source.source_type == source_type)
        if credibility_level:
            stmt = stmt.where(Source.credibility_level == credibility_level)
        return self._paginate(
            stmt, limit=limit, offset=offset, order_by=(Source.accessed_at.desc(), Source.id.desc())
        )

    def find_by_hash(self, content_hash: str) -> list[Source]:
        stmt = select(Source).where(Source.content_hash == content_hash)
        return list(self.db.scalars(stmt))


class EvidenceService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create(self, payload: EvidenceCreate) -> Evidence:
        # 来源必须真实存在：证据脱离来源就等于没有证据
        if self.db.get(Source, payload.source_id) is None:
            raise DomainValidationError(
                f"source_id={payload.source_id} 不存在，证据必须挂在真实来源上",
                details={"field": "source_id"},
            )
        if payload.knowledge_item_id is not None:
            if self.db.get(KnowledgeItem, payload.knowledge_item_id) is None:
                raise DomainValidationError(
                    f"knowledge_item_id={payload.knowledge_item_id} 不存在",
                    details={"field": "knowledge_item_id"},
                )
        if payload.research_report_id is not None:
            if self.db.get(ResearchReport, payload.research_report_id) is None:
                raise DomainValidationError(
                    f"research_report_id={payload.research_report_id} 不存在",
                    details={"field": "research_report_id"},
                )

        evidence = Evidence(**payload.model_dump())
        if evidence.verification_status is not VerificationStatus.UNVERIFIED:
            evidence.verified_at = utcnow()
        self.db.add(evidence)
        return self._commit(evidence, "证据写入冲突")

    def get(self, evidence_id: int) -> Evidence:
        evidence = self.db.get(Evidence, evidence_id)
        if evidence is None:
            raise NotFoundError(
                f"证据 id={evidence_id} 不存在", details={"evidence_id": evidence_id}
            )
        return evidence

    def list_for_target(
        self,
        *,
        knowledge_item_id: int | None = None,
        research_report_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Evidence], int]:
        if (knowledge_item_id is None) == (research_report_id is None):
            raise DomainValidationError(
                "必须且只能指定 knowledge_item_id 或 research_report_id 之一"
            )
        stmt = select(Evidence)
        if knowledge_item_id is not None:
            stmt = stmt.where(Evidence.knowledge_item_id == knowledge_item_id)
        else:
            stmt = stmt.where(Evidence.research_report_id == research_report_id)
        return self._paginate(
            stmt, limit=limit, offset=offset, order_by=(Evidence.created_at.asc(), Evidence.id.asc())
        )
