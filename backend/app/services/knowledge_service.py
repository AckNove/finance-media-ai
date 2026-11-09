"""知识条目与知识关联服务（含检索）。"""

from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.core.utils import utcnow
from backend.app.models.enums import VerificationStatus
from backend.app.models.knowledge import KnowledgeItem, KnowledgeRelation
from backend.app.schemas.knowledge import (
    KnowledgeItemCreate,
    KnowledgeItemUpdate,
    KnowledgeMatch,
    KnowledgeRelationCreate,
    KnowledgeSearchResult,
)
from backend.app.services.base import BaseService, make_unique_slug

# (字段名, 列, 权重)：权重用于给命中排序，标题命中优先级最高
SEARCH_FIELDS: tuple[tuple[str, object, float], ...] = (
    ("title", KnowledgeItem.title, 3.0),
    ("definition", KnowledgeItem.definition, 2.0),
    ("mechanism", KnowledgeItem.mechanism, 1.5),
    ("risk_structure", KnowledgeItem.risk_structure, 1.0),
    ("pricing_logic", KnowledgeItem.pricing_logic, 1.0),
    ("profit_model", KnowledgeItem.profit_model, 1.0),
    ("participants", KnowledgeItem.participants, 1.0),
    ("football_analogy", KnowledgeItem.football_analogy, 0.5),
    ("analogy_limitations", KnowledgeItem.analogy_limitations, 0.5),
)


class KnowledgeService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    # ------------------------------------------------------------------ 写入
    def create(self, payload: KnowledgeItemCreate) -> KnowledgeItem:
        slug = payload.slug or make_unique_slug(self.db, KnowledgeItem, payload.title)
        data = payload.model_dump(exclude={"slug"})
        item = KnowledgeItem(slug=slug, **data)
        if payload.verification_status is not VerificationStatus.UNVERIFIED:
            item.last_verified_at = utcnow()
        self.db.add(item)
        return self._commit(item, f"知识条目写入冲突：slug={slug} 可能已存在")

    def update(self, item_id: int, payload: KnowledgeItemUpdate) -> KnowledgeItem:
        item = self.get(item_id)
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(item, field, value)
        if data.get("verification_status") not in (None, VerificationStatus.UNVERIFIED):
            item.last_verified_at = utcnow()
        # 内容被修改后版本号自增，保留"这条知识改过几次"的痕迹
        if data:
            item.version = (item.version or 1) + 1
        self.db.add(item)
        return self._commit(item, "知识条目更新冲突")

    # ------------------------------------------------------------------ 读取
    def get(self, item_id: int) -> KnowledgeItem:
        item = self.db.get(KnowledgeItem, item_id)
        if item is None:
            raise NotFoundError(
                f"知识条目 id={item_id} 不存在", details={"knowledge_item_id": item_id}
            )
        return item

    def get_by_slug(self, slug: str) -> KnowledgeItem:
        item = self.db.scalar(select(KnowledgeItem).where(KnowledgeItem.slug == slug))
        if item is None:
            raise NotFoundError(f"知识条目 slug={slug!r} 不存在", details={"slug": slug})
        return item

    def list(
        self,
        *,
        knowledge_domain: str | None = None,
        verification_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[KnowledgeItem], int]:
        stmt = select(KnowledgeItem)
        if knowledge_domain:
            stmt = stmt.where(KnowledgeItem.knowledge_domain == knowledge_domain)
        if verification_status:
            stmt = stmt.where(KnowledgeItem.verification_status == verification_status)
        return self._paginate(
            stmt,
            limit=limit,
            offset=offset,
            order_by=(KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc()),
        )

    # ------------------------------------------------------------------ 检索
    def search(
        self,
        query: str,
        *,
        knowledge_domain: str | None = None,
        verification_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> KnowledgeSearchResult:
        """基于关键词的知识检索。

        当前实现是 SQL LIKE 多字段匹配 + 加权排序（SQLite 可直接运行，
        PostgreSQL 亦兼容）。没有引入向量库：第一阶段不伪装语义检索能力。
        """
        keyword = (query or "").strip()
        if not keyword:
            raise DomainValidationError("检索关键词 q 不能为空", details={"field": "q"})
        if len(keyword) > 200:
            raise DomainValidationError("检索关键词过长（上限 200 字符）", details={"field": "q"})

        pattern = f"%{keyword}%"
        clauses = [column.ilike(pattern) for _, column, _ in SEARCH_FIELDS]
        stmt = select(KnowledgeItem).where(
            KnowledgeItem.is_current.is_(True), or_(*clauses)
        )
        if knowledge_domain:
            stmt = stmt.where(KnowledgeItem.knowledge_domain == knowledge_domain)
        if verification_status:
            stmt = stmt.where(KnowledgeItem.verification_status == verification_status)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        title_first = case((KnowledgeItem.title.ilike(pattern), 1), else_=0)
        rows = list(
            self.db.scalars(
                stmt.order_by(
                    title_first.desc(), KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc()
                )
                .limit(limit)
                .offset(offset)
            )
        )

        needle = keyword.lower()
        matches: list[KnowledgeMatch] = []
        for row in rows:
            matched_fields: list[str] = []
            score = 0.0
            for field, _column, weight in SEARCH_FIELDS:
                value = getattr(row, field)
                if value and needle in str(value).lower():
                    matched_fields.append(field)
                    score += weight
            matches.append(
                KnowledgeMatch(
                    item=row,
                    matched_fields=matched_fields,
                    score=round(score, 2),
                )
            )
        return KnowledgeSearchResult(
            query=keyword,
            items=matches,
            total=int(total),
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------ 知识关联
    def create_relation(self, payload: KnowledgeRelationCreate) -> KnowledgeRelation:
        self.get(payload.source_item_id)
        self.get(payload.target_item_id)
        relation = KnowledgeRelation(**payload.model_dump())
        self.db.add(relation)
        return self._commit(
            relation, "知识关联已存在（同一组 source/target/relation_type 只能建一次）"
        )

    def list_relations(self, item_id: int) -> list[KnowledgeRelation]:
        self.get(item_id)
        stmt = (
            select(KnowledgeRelation)
            .where(
                or_(
                    KnowledgeRelation.source_item_id == item_id,
                    KnowledgeRelation.target_item_id == item_id,
                )
            )
            .order_by(KnowledgeRelation.id.asc())
        )
        return list(self.db.scalars(stmt))
