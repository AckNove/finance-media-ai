"""ORM 模型统一导出。

Alembic 的 target_metadata 依赖本模块导入全部模型，
因此任何新增模型都必须在此登记，否则迁移会漏表。

M2.0 新增（按迁移批次）：
- B1 ``taxonomy``: Tag / TopicCollection / TopicTag / TopicScoreSnapshot
- B2 ``execution``: ResearchTask / ResearchSubtask / ResearchSubtaskDependency / TaskEvent
- B3 ``metering``: ModelCall / ToolCall
"""

from backend.app.database.base import Base
from backend.app.models.agent import AgentRun
from backend.app.models.cases import FinancialCase, FootballCase
from backend.app.models.evidence import Evidence
from backend.app.models.execution import (
    ResearchSubtask,
    ResearchSubtaskDependency,
    ResearchTask,
    TaskEvent,
)
from backend.app.models.finance import FinancialInstrument, FinancialProduct
from backend.app.models.industry import Industry
from backend.app.models.institution import Institution
from backend.app.models.knowledge import KnowledgeItem, KnowledgeRelation
from backend.app.models.media import MediaAsset
from backend.app.models.metering import ModelCall, ToolCall
from backend.app.models.publication import Publication
from backend.app.models.research import FindingCheck, ResearchReport
from backend.app.models.review import ReviewRecord
from backend.app.models.script import Script
from backend.app.models.source import Source
from backend.app.models.taxonomy import (
    Tag,
    TopicCollection,
    TopicScoreSnapshot,
    TopicTag,
)
from backend.app.models.topic import Topic, TopicColumn

__all__ = [
    "Base",
    "AgentRun",
    "Evidence",
    "FindingCheck",
    "FinancialCase",
    "FootballCase",
    "FinancialInstrument",
    "FinancialProduct",
    "Industry",
    "Institution",
    "KnowledgeItem",
    "KnowledgeRelation",
    "MediaAsset",
    "ModelCall",
    "Publication",
    "ResearchReport",
    "ResearchSubtask",
    "ResearchSubtaskDependency",
    "ResearchTask",
    "ReviewRecord",
    "Script",
    "Source",
    "Tag",
    "TaskEvent",
    "ToolCall",
    "Topic",
    "TopicCollection",
    "TopicColumn",
    "TopicScoreSnapshot",
    "TopicTag",
]
