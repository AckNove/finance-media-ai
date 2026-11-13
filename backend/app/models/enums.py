"""领域枚举。

所有枚举以 str 为基类，便于 Pydantic 直接序列化，
数据库中以 value（小写字符串）存储并附加 CHECK 约束。
"""

from __future__ import annotations

from enum import Enum


class VerificationStatus(str, Enum):
    """事实/知识条目的核验状态。"""

    UNVERIFIED = "unverified"
    IN_REVIEW = "in_review"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REFUTED = "refuted"


class StatementNature(str, Enum):
    """区分事实、推断与观点（硬性合规要求）。"""

    FACT = "fact"
    INFERENCE = "inference"
    OPINION = "opinion"


class SupportType(str, Enum):
    """证据对事实主张的支持关系。"""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXTUAL = "contextual"
    AMBIGUOUS = "ambiguous"


class KnowledgeDomain(str, Enum):
    """知识域，与金融专业数据域一一对应。"""

    FINANCIAL_INSTRUMENTS = "financial_instruments"
    FINANCIAL_PRODUCTS = "financial_products"
    INSTITUTIONS = "institutions"
    INDUSTRIES = "industries"
    FOOTBALL_BUSINESS = "football_business"
    FINANCIAL_CASES = "financial_cases"
    GENERAL = "general"


class AudienceLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ResearchStatus(str, Enum):
    """选题的研究状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentStatus(str, Enum):
    """选题的内容生产状态（覆盖研究→发布全链路）。"""

    IDEA = "idea"
    RESEARCH = "research"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    STORYBOARD = "storyboard"
    PRODUCING = "producing"
    QA = "qa"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    OFFICIAL_STATISTICS = "official_statistics"
    REGULATOR_FILING = "regulator_filing"
    EXCHANGE_DISCLOSURE = "exchange_disclosure"
    ACADEMIC_PAPER = "academic_paper"
    INDUSTRY_REPORT = "industry_report"
    FINANCIAL_NEWS = "financial_news"
    COMPANY_REPORT = "company_report"
    BOOK = "book"
    INTERVIEW = "interview"
    OTHER = "other"


class CredibilityLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class LicenseStatus(str, Enum):
    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    LICENSED = "licensed"
    RESTRICTED = "restricted"
    UNCLEAR = "unclear"


class RelationType(str, Enum):
    """知识条目之间的关系类型。"""

    PREREQUISITE = "prerequisite"
    RELATED = "related"
    CONTRASTS = "contrasts"
    COMPOSED_OF = "composed_of"
    APPLIED_IN = "applied_in"
    SUPERSEDES = "supersedes"


class AgentStatus(str, Enum):
    """Agent 执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewerType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


class ReviewTargetType(str, Enum):
    KNOWLEDGE_ITEM = "knowledge_item"
    RESEARCH_REPORT = "research_report"
    SCRIPT = "script"


class ReviewStage(str, Enum):
    PROFESSIONAL = "professional"
    COMPLIANCE = "compliance"
    FINAL = "final"


class ReviewVerdict(str, Enum):
    PASS = "pass"
    NEEDS_REVISION = "needs_revision"
    REJECT = "reject"


class ScriptStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    STORYBOARD_READY = "storyboard_ready"
    PRODUCED = "produced"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class VideoTemplate(str, Enum):
    """规划中的四类视频模板。"""

    FOOTBALL_EXPLAINER = "football_explainer"
    FINANCIAL_MECHANISM = "financial_mechanism"
    INDUSTRY_RESEARCH = "industry_research"
    CASE_DOCUMENTARY = "case_documentary"


class MediaAssetType(str, Enum):
    IMAGE = "image"
    CHART = "chart"
    DIAGRAM = "diagram"
    AUDIO = "audio"
    VOICEOVER = "voiceover"
    SUBTITLE = "subtitle"
    VIDEO = "video"
    OTHER = "other"


class PublicationPlatform(str, Enum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT = "wechat"
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    OTHER = "other"


class InstrumentClass(str, Enum):
    EQUITY = "equity"
    BOND = "bond"
    FUND = "fund"
    FUTURE = "future"
    OPTION = "option"
    FORWARD = "forward"
    SWAP = "swap"
    CREDIT_DERIVATIVE = "credit_derivative"
    STRUCTURED_PRODUCT = "structured_product"
    OTHER = "other"


class ProductWrapper(str, Enum):
    PUBLIC_FUND = "public_fund"
    PRIVATE_FUND = "private_fund"
    TRUST = "trust"
    INSURANCE = "insurance"
    STRUCTURED_NOTE = "structured_note"
    DEPOSIT = "deposit"
    OTHER = "other"


class InstitutionType(str, Enum):
    CENTRAL_BANK = "central_bank"
    REGULATOR = "regulator"
    COMMERCIAL_BANK = "commercial_bank"
    INVESTMENT_BANK = "investment_bank"
    SECURITIES_FIRM = "securities_firm"
    INSURER = "insurer"
    ASSET_MANAGER = "asset_manager"
    FUND = "fund"
    EXCHANGE = "exchange"
    CLEARING_HOUSE = "clearing_house"
    FINTECH = "fintech"
    FOOTBALL_CLUB = "football_club"
    LEAGUE_BODY = "league_body"
    OTHER = "other"


class IndustryChainStage(str, Enum):
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"
    SERVICE = "service"
    CROSS_CUTTING = "cross_cutting"


class FootballCaseType(str, Enum):
    TRANSFER = "transfer"
    CLUB_FINANCE = "club_finance"
    OWNERSHIP_TAKEOVER = "ownership_takeover"
    BROADCASTING_RIGHTS = "broadcasting_rights"
    SPONSORSHIP = "sponsorship"
    FINANCIAL_FAIR_PLAY = "financial_fair_play"
    OTHER = "other"


class FinancialCaseType(str, Enum):
    RISK_EVENT = "risk_event"
    CAPITAL_OPERATION = "capital_operation"
    BUSINESS_MODEL = "business_model"
    MARKET_EVENT = "market_event"
    FRAUD_CASE = "fraud_case"
    OTHER = "other"


class TagNamespace(str, Enum):
    """标签命名空间：把同名标签按语义隔离（"银行"既可能是机构类型也可能是行业）。"""

    INSTRUMENT = "instrument"
    INSTITUTION_TYPE = "institution_type"
    INDUSTRY = "industry"
    GEOGRAPHY = "geography"
    PERIOD = "period"
    MECHANISM = "mechanism"
    OTHER = "other"


class ScoredBy(str, Enum):
    """选题评分来源：规则 / 模型 / 人工。"""

    RULE = "rule"
    MODEL = "model"
    HUMAN = "human"


class TopicOrigin(str, Enum):
    """选题来源渠道（M2.0 B1 新增，用于区分人工与技术发现）。"""

    MANUAL = "manual"
    RSS = "rss"
    NEWS = "news"
    KNOWLEDGE_GAP = "knowledge_gap"
    AI_SUGGESTED = "ai_suggested"


class TopicStatus(str, Enum):
    """选题生命周期主状态机（docs/research_hub/state_machine.md §2）。"""

    CANDIDATE = "candidate"
    SCORED = "scored"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    IN_RESEARCH = "in_research"
    RESEARCHED = "researched"
    CONTENT_PRODUCED = "content_produced"
    REJECTED = "rejected"
    PARKED = "parked"
    MERGED = "merged"
    ARCHIVED = "archived"


class ResearchTaskStatus(str, Enum):
    """研究任务状态机（docs/research_hub/state_machine.md §3）。"""

    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskStatus(str, Enum):
    """子任务状态机（含 attempt 语义）。"""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskEventType(str, Enum):
    """任务事件流的事件类型（只追加表）。"""

    CREATED = "created"
    QUEUED = "queued"
    CLAIMED = "claimed"
    PLAN_CREATED = "plan_created"
    SUBTASK_STARTED = "subtask_started"
    SUBTASK_SUCCEEDED = "subtask_succeeded"
    SUBTASK_FAILED = "subtask_failed"
    RETRIED = "retried"
    PAUSED = "paused"
    RESUMED = "resumed"
    DIRECTION_CHANGED = "direction_changed"
    BUDGET_EXCEEDED = "budget_exceeded"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallStatus(str, Enum):
    """模型调用状态（model_calls）。"""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    BUDGET_BLOCKED = "budget_blocked"


class ToolCallStatus(str, Enum):
    """工具调用状态（tool_calls）。"""

    OK = "ok"
    DENIED = "denied"
    ERROR = "error"
    TIMEOUT = "timeout"


class CalendarStatus(str, Enum):
    """内容日历里一条排期走到哪一步（运营层）。"""

    PLANNED = "planned"
    SCRIPTED = "scripted"
    RENDERED = "rendered"
    EXPORTED = "exported"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class PublishState(str, Enum):
    """发布状态。系统不自动发布，这里只记录人做过什么。"""

    NOT_PUBLISHED = "not_published"
    PUBLISHED = "published"
    REMOVED = "removed"


class Platform(str, Enum):
    """排期与数据回填用的平台标识（比 PublicationPlatform 更宽松）。"""

    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT = "wechat"
    BILIBILI = "bilibili"
    VIDEO_ACCOUNT = "video_account"
    YOUTUBE = "youtube"
    OTHER = "other"
