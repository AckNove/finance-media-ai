"""选题服务。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainValidationError, NotFoundError
from backend.app.models.enums import ContentStatus, ResearchStatus, TagNamespace
from backend.app.models.taxonomy import Tag, TopicCollection, TopicTag
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
        tag_id: int | None = None,
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
        if tag_id is not None:
            # 语义是"这个标签下的选题"：内连接而非 EXISTS，方便一次查询拿到计数。
            stmt = stmt.join(TopicTag, TopicTag.topic_id == Topic.id).where(
                TopicTag.tag_id == tag_id
            )
        return self._paginate(
            stmt,
            limit=limit,
            offset=offset,
            order_by=(Topic.priority.asc(), Topic.created_at.desc(), Topic.id.desc()),
        )

    def find_tag(self, *, name: str, namespace: TagNamespace | None = None) -> Tag:
        """按 `namespace(+name)` 找标签；没指定 namespace 时按名字找，找到多个就算歧义。"""
        stmt = select(Tag).where(Tag.name == name.strip())
        if namespace is not None:
            stmt = stmt.where(Tag.namespace == namespace)
        rows = list(self.db.scalars(stmt.limit(2)))
        if not rows:
            raise NotFoundError(
                f"标签 {name!r} 不存在",
                details={"name": name, "namespace": namespace.value if namespace else None},
            )
        if len(rows) > 1:
            raise DomainValidationError(
                f"标签名 {name!r} 在多个命名空间下都存在，请指定 namespace",
                details={
                    "field": "namespace",
                    "candidates": sorted(row.namespace.value for row in rows),
                },
            )
        return rows[0]

    def list_tagged_topics(
        self,
        *,
        name: str | None = None,
        namespace: TagNamespace | None = None,
        tag_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Topic], int, Tag | None]:
        """按标签反查选题：可以给 `tag_id`，也可以给名字（+可选命名空间）。

        顺序刻意是"先解析标签再查选题"：标签不存在时给 404，而不是静默返回空列表
        ——空列表会被误读成"这个标签下没有选题"。
        """
        tag: Tag | None = None
        if tag_id is not None:
            tag = self.db.get(Tag, tag_id)
            if tag is None:
                raise NotFoundError(f"标签 id={tag_id} 不存在", details={"tag_id": tag_id})
        elif name:
            tag = self.find_tag(name=name, namespace=namespace)
        topics, total = self.list(
            tag_id=tag.id if tag is not None else None, limit=limit, offset=offset
        )
        return topics, total, tag


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

    def list_collections(self, topic_id: int) -> list[TopicCollection]:
        """选题所在的合集（按主合集优先、权重降序）。"""
        self.get(topic_id)
        stmt = (
            select(TopicCollection)
            .where(TopicCollection.topic_id == topic_id)
            .order_by(
                TopicCollection.is_primary.desc(),
                TopicCollection.weight.desc(),
                TopicCollection.column_id.asc(),
            )
        )
        return list(self.db.scalars(stmt))

    def attach_collection(
        self,
        topic_id: int,
        *,
        column_id: int,
        is_primary: bool = False,
        weight: float = 1.0,
    ) -> TopicCollection:
        """把选题放进一个合集。

        一个选题最多一个主合集（表上有条件唯一索引兜底），所以这里设主合集时
        会先把同选题的其他主合集降级——否则手工和并发两条路径都会撞唯一索引。
        重复挂同一个合集视为更新权重/主次，不报错。
        """
        self.get(topic_id)
        if self.db.get(TopicColumn, column_id) is None:
            raise DomainValidationError(
                f"column_id={column_id} 不存在", details={"field": "column_id"}
            )
        if is_primary:
            for other in self.list_collections(topic_id):
                if other.is_primary and other.column_id != column_id:
                    other.is_primary = False
                    self.db.add(other)
            self.db.flush()

        link = self.db.scalar(
            select(TopicCollection).where(
                TopicCollection.topic_id == topic_id,
                TopicCollection.column_id == column_id,
            )
        )
        if link is None:
            link = TopicCollection(topic_id=topic_id, column_id=column_id)
            self.db.add(link)
        link.is_primary = is_primary
        link.weight = weight
        return self._commit(link, "选题合集写入冲突")

    def detach_collection(self, topic_id: int, column_id: int) -> None:
        """把一个合集从选题上摘下来；本来就没挂则报 404，避免界面以为删成功。"""
        self.get(topic_id)
        link = self.db.scalar(
            select(TopicCollection).where(
                TopicCollection.topic_id == topic_id,
                TopicCollection.column_id == column_id,
            )
        )
        if link is None:
            raise NotFoundError(
                f"选题 {topic_id} 没有挂在合集 {column_id} 上",
                details={"topic_id": topic_id, "column_id": column_id},
            )
        self.db.delete(link)
        self.db.commit()

    # --- 标签（tags / topic_tags）------------------------------------------

    def list_tags(self, topic_id: int) -> list[TopicTag]:
        """选题上的标签，按权重降序、标签名升序。"""
        self.get(topic_id)
        stmt = (
            select(TopicTag)
            .join(Tag, Tag.id == TopicTag.tag_id)
            .where(TopicTag.topic_id == topic_id)
            .order_by(TopicTag.weight.desc(), Tag.name.asc())
        )
        return list(self.db.scalars(stmt))

    def attach_tag(
        self,
        topic_id: int,
        *,
        name: str,
        namespace: TagNamespace = TagNamespace.OTHER,
        weight: float = 1.0,
        create_missing: bool = True,
    ) -> TopicTag:
        """给选题打一个标签。

        `name` 在 `namespace` 内唯一（表上 `uq_tag_namespace_name` 兜底）；
        标签不存在时按名字现造一个（`create_missing=False` 则报 422），
        已经打过就是更新权重，不重复插入。
        """
        self.get(topic_id)
        cleaned = name.strip()
        if not cleaned:
            raise DomainValidationError("标签名不能为空", details={"field": "name"})

        tag = self.db.scalar(
            select(Tag).where(Tag.namespace == namespace, Tag.name == cleaned)
        )
        if tag is None:
            if not create_missing:
                raise DomainValidationError(
                    f"标签 {namespace.value}:{cleaned} 不存在，且本次不允许新建",
                    details={"field": "name", "namespace": namespace.value},
                )
            tag = Tag(
                namespace=namespace,
                name=cleaned,
                slug=make_unique_slug(self.db, Tag, cleaned),
            )
            self.db.add(tag)
            self.db.flush()

        link = self.db.scalar(
            select(TopicTag).where(
                TopicTag.topic_id == topic_id,
                TopicTag.tag_id == tag.id,
            )
        )
        if link is None:
            link = TopicTag(
                topic_id=topic_id, tag_id=tag.id, created_at=datetime.now(UTC)
            )
            self.db.add(link)
            tag.usage_count = (tag.usage_count or 0) + 1
        link.weight = weight
        return self._commit(link, "选题标签写入冲突")

    def detach_tag(self, topic_id: int, tag_id: int) -> None:
        """把一个标签从选题上摘下来；没打过则报 404，不假装删成功。"""
        self.get(topic_id)
        link = self.db.scalar(
            select(TopicTag).where(
                TopicTag.topic_id == topic_id, TopicTag.tag_id == tag_id
            )
        )
        if link is None:
            raise NotFoundError(
                f"选题 {topic_id} 没有打过标签 {tag_id}",
                details={"topic_id": topic_id, "tag_id": tag_id},
            )
        tag = self.db.get(Tag, tag_id)
        if tag is not None and (tag.usage_count or 0) > 0:
            tag.usage_count -= 1
        self.db.delete(link)
        self.db.commit()

    def create_column(
        self, *, code: str, name: str, description: str | None = None, display_order: int = 0
    ) -> TopicColumn:
        column = TopicColumn(
            code=code, name=name, description=description, display_order=display_order
        )
        self.db.add(column)
        return self._commit(column, f"栏目 code={code!r} 已存在")

