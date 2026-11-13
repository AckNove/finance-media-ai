"""视频脚本服务：研究报告 → 脚本，并管住状态机。

状态机（刻意做窄，只在必要处开口子）：

    draft ──submit──▶ in_review ──approve──▶ approved ──produce──▶ produced
       ▲                   │                     │
       └────reject─────────┘                     └──archive──▶ archived

- 只有 `approved` 才写 `approved_at`/`approved_by`——"谁批的"必须能查。
- 版本号在 `(topic_id, template)` 维度内自增，与表上 `uq_script_version` 一致。
- 审核通过不等于可以发布：发布另有 `publications` 流程，这里不做。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import ConflictError, DomainValidationError, NotFoundError
from backend.app.core.utils import utcnow
from backend.app.models.enums import ScriptStatus, VideoTemplate
from backend.app.models.research import ResearchReport
from backend.app.models.script import Script
from backend.app.models.topic import Topic
from backend.app.research.draft_gate import readiness_of
from backend.app.services.base import BaseService

#: 允许的状态迁移。表上没有触发器，所以这层就是唯一的闸门。
SCRIPT_TRANSITIONS: dict[ScriptStatus, frozenset[ScriptStatus]] = {
    ScriptStatus.DRAFT: frozenset({ScriptStatus.IN_REVIEW}),
    ScriptStatus.IN_REVIEW: frozenset({ScriptStatus.APPROVED, ScriptStatus.REJECTED}),
    ScriptStatus.REJECTED: frozenset({ScriptStatus.DRAFT}),
    ScriptStatus.APPROVED: frozenset({ScriptStatus.STORYBOARD_READY, ScriptStatus.PRODUCED}),
    ScriptStatus.STORYBOARD_READY: frozenset({ScriptStatus.PRODUCED}),
    ScriptStatus.PRODUCED: frozenset({ScriptStatus.PUBLISHED, ScriptStatus.ARCHIVED}),
    ScriptStatus.PUBLISHED: frozenset({ScriptStatus.ARCHIVED}),
    ScriptStatus.ARCHIVED: frozenset(),
}


class ScriptService(BaseService):
    def __init__(self, db: Session) -> None:
        super().__init__(db)

    def create(
        self,
        *,
        topic_id: int,
        title: str,
        body: str,
        research_report_id: int | None = None,
        template: VideoTemplate = VideoTemplate.FINANCIAL_MECHANISM,
        hook: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        disclaimer: str | None = None,
        estimated_duration: int | None = None,
    ) -> Script:
        """建一版脚本。版本号按 (topic_id, template) 自增。"""
        if self.db.get(Topic, topic_id) is None:
            raise DomainValidationError(
                f"topic_id={topic_id} 不存在", details={"field": "topic_id"}
            )
        if research_report_id is not None:
            report = self.db.get(ResearchReport, research_report_id)
            if report is None:
                raise DomainValidationError(
                    f"research_report_id={research_report_id} 不存在",
                    details={"field": "research_report_id"},
                )
            if report.topic_id is not None and report.topic_id != topic_id:
                raise DomainValidationError(
                    "研究报告与选题不是同一个，脚本不能跨选题引用",
                    details={"field": "research_report_id"},
                )
            # 成稿闸门：脚本就是成稿本身，所以和"建视频任务"用同一条判据——
            # 报告里必须有结论被人工确认过，模型判 supported 不算数。
            gate = readiness_of(self.db, report)
            if not gate.allowed:
                raise ConflictError(gate.note, details=dict(gate.as_dict()))
        if not (body or "").strip():
            raise DomainValidationError("body 不能为空", details={"field": "body"})

        latest = self.db.scalar(
            select(func.max(Script.version)).where(
                Script.topic_id == topic_id, Script.template == template
            )
        )
        script = Script(
            topic_id=topic_id,
            research_report_id=research_report_id,
            title=title,
            template=template,
            version=int(latest or 0) + 1,
            status=ScriptStatus.DRAFT,
            hook=hook,
            body=body,
            segments=segments,
            disclaimer=disclaimer,
            word_count=len("".join(body.split())),
            estimated_duration=estimated_duration,
        )
        self.db.add(script)
        return self._commit(script, "脚本写入冲突（同一选题同一模板版本号重复）")

    def get(self, script_id: int) -> Script:
        script = self.db.get(Script, script_id)
        if script is None:
            raise NotFoundError(
                f"脚本 id={script_id} 不存在", details={"script_id": script_id}
            )
        return script

    def latest_for_report(self, research_report_id: int) -> Script | None:
        """这份报告最新的一版脚本；没有就返回 None（调用方据此退回规则分镜）。"""
        return self.db.scalars(
            select(Script)
            .where(Script.research_report_id == research_report_id)
            .order_by(Script.version.desc(), Script.id.desc())
            .limit(1)
        ).first()

    def list(
        self,
        *,
        topic_id: int | None = None,
        status: ScriptStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Script], int]:
        stmt = select(Script)
        if topic_id is not None:
            stmt = stmt.where(Script.topic_id == topic_id)
        if status is not None:
            stmt = stmt.where(Script.status == status)
        return self._paginate(
            stmt,
            limit=limit,
            offset=offset,
            order_by=(Script.updated_at.desc(), Script.id.desc()),
        )

    def set_status(
        self, script_id: int, target: ScriptStatus, *, actor: str = "human"
    ) -> Script:
        """迁移状态；非法迁移返回 409 并列出允许的下一步。"""
        script = self.get(script_id)
        current = script.status
        if current == target:
            return script
        allowed = SCRIPT_TRANSITIONS[current]
        if target not in allowed:
            raise ConflictError(
                f"脚本状态不能从 {current.value} 直接变成 {target.value}",
                details={
                    "status": current.value,
                    "requested": target.value,
                    "allowed": sorted(item.value for item in allowed),
                },
            )
        script.status = target
        if target is ScriptStatus.APPROVED:
            script.approved_at = utcnow()
            script.approved_by = (actor or "human")[:64]
        elif current is ScriptStatus.APPROVED:
            # 从 approved 回到别的状态（如归档）时清掉批注，避免"批过的版本"到处留痕
            script.approved_at = None
            script.approved_by = None
        self.db.add(script)
        return self._commit(script, "脚本状态更新冲突")
