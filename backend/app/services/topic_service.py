"""选题服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.models.enums import ContentStatus, ResearchStatus
from backend.app.models.topic import Topic, TopicColumn
from backend.app.schemas.topic import TopicCreate, TopicUpdate
from backend.app.services.base import BaseService, make_unique_slug


class TopicService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create(self, payload: TopicCreate) -> Topic:
        if payload.column_id is not None:
            column = self.db.get(TopicColumn, payload.column_id)
            if column is None:
                raise DomainValidationError(
                    f"column_id={payload.column_id} 不存在",
                    details={"field": "column_id"},
                )

        slug = payload.slug or make_unique_slug(self.db, Topic, payload.title)
        topic = Topic(
            slug=slug,
            title=payload.title,
            description=payload.description,
            column_id=payload.column_id,
            knowledge_domain=payload.knowledge_domain,
            audience_level=payload.audience_level,
            priority=payload.priority,
            football_analogy_required=payload.football_analogy_required,
            estimated_duration=payload.estimated_duration,
            research_status=ResearchStatus.PENDING,
            content_status=ContentStatus.IDEA,
        )
        self.db.add(topic)
        return self._commit(topic, f"选题写入冲突：slug={slug} 可能已存在")

    def get(self, topic_id: int) -> Topic:
        topic = self.db.get(Topic, topic_id)
        if topic is None:
            raise NotFoundError(f"选题 id={topic_id} 不存在", details={"topic_id": topic_id})
        return topic

    def get_by_slug(self, slug: str) -> Topic:
        topic = self.db.scalar(select(Topic).where(Topic.slug == slug))
        if topic is None:
            raise NotFoundError(f"选题 slug={slug!r} 不存在", details={"slug": slug})
        return topic

    def list(
        self,
        *,
        knowledge_domain: str | None = None,
        research_status: str | None = None,
        content_status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Topic], int]:
        stmt = select(Topic)
        if knowledge_domain:
            stmt = stmt.where(Topic.knowledge_domain == knowledge_domain)
        if research_status:
            stmt = stmt.where(Topic.research_status == research_status)
        if content_status:
            stmt = stmt.where(Topic.content_status == content_status)
        return self._paginate(
            stmt,
            limit=limit,
            offset=offset,
            order_by=(Topic.priority.asc(), Topic.created_at.desc(), Topic.id.desc()),
        )

    def update(self, topic_id: int, payload: TopicUpdate) -> Topic:
        topic = self.get(topic_id)
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            if field == "priority" and value is None:
                continue
            setattr(topic, field, value)
        self.db.add(topic)
        return self._commit(topic, "选题更新冲突")

    def list_columns(self) -> list[TopicColumn]:
        return list(
            self.db.scalars(select(TopicColumn).order_by(TopicColumn.display_order.asc()))
        )

    def create_column(
        self, *, code: str, name: str, description: str | None = None, display_order: int = 0
    ) -> TopicColumn:
        column = TopicColumn(
            code=code, name=name, description=description, display_order=display_order
        )
        self.db.add(column)
        return self._commit(column, f"栏目 code={code!r} 已存在")
