# 数据库设计（第一阶段）

- 数据库：SQLite（`data/finance_media.db`），保留 PostgreSQL 兼容能力。
- 表数量：**18 张业务表**（另有 `alembic_version`），初始迁移 revision `de8148733a9b`。
- 模型：`backend/app/models/*.py`；迁移：`backend/migrations/versions/20261109_1754_initial_schema.py`。
- 索引：103 个（含唯一索引与外键索引）。

## 1. 表清单

| 域 | 表 | 模型文件 | 说明 |
| --- | --- | --- | --- |
| 选题 | `topic_columns` | `models/topic.py` | 栏目 |
| 选题 | `topics` | `models/topic.py` | 选题与流程状态 |
| 知识 | `knowledge_items` | `models/knowledge.py` | 知识条目（含机制/定价/风险/足球类比与边界） |
| 知识 | `knowledge_relations` | `models/knowledge.py` | 知识条目之间的有向关系 |
| 来源 | `sources` | `models/source.py` | 原始资料来源与内容指纹 |
| 证据 | `evidence` | `models/evidence.py` | 事实主张 ↔ 来源 |
| 研究 | `research_reports` | `models/research.py` | 研究报告（findings + limitations） |
| Agent | `agent_runs` | `models/agent.py` | Agent 执行记录（输入/上下文/输出/错误） |
| 审核 | `review_records` | `models/review.py` | 专业/合规/终审记录 |
| 脚本 | `scripts` | `models/script.py` | 视频脚本（版本化、审批） |
| 素材 | `media_assets` | `models/media.py` | 图像/图表/音频/字幕/视频素材 |
| 发布 | `publications` | `models/publication.py` | 发布记录与效果数据 |
| 金融域 | `institutions` | `models/institution.py` | 金融机构、俱乐部、监管机构 |
| 金融域 | `financial_instruments` | `models/finance.py` | 金融工具（债券、期货、期权…） |
| 金融域 | `financial_products` | `models/finance.py` | 可售产品/结构性产品 |
| 金融域 | `industries` | `models/industry.py` | 产业链节点（自引用树） |
| 金融域 | `football_cases` | `models/cases.py` | 足球商业案例 |
| 金融域 | `financial_cases` | `models/cases.py` | 金融事件/企业案例 |

## 2. ER 关系（真实外键，全部已在数据库中生效）

```mermaid
erDiagram
    topic_columns ||--o{ topics : column_id
    topics ||--o{ research_reports : topic_id
    topics ||--o{ scripts : topic_id
    topics ||--o{ agent_runs : topic_id
    research_reports ||--o{ evidence : research_report_id
    research_reports ||--o{ scripts : research_report_id
    research_reports ||--o{ football_cases : research_report_id
    research_reports ||--o{ financial_cases : research_report_id
    knowledge_items ||--o{ evidence : knowledge_item_id
    sources ||--o{ evidence : source_id
    sources ||--o{ football_cases : source_id
    sources ||--o{ financial_cases : source_id
    knowledge_items ||--o{ knowledge_relations : source_item_id
    knowledge_items ||--o{ knowledge_relations : target_item_id
    agent_runs ||--o{ research_reports : agent_run_id
    scripts ||--o{ media_assets : script_id
    scripts ||--o{ publications : script_id
    knowledge_items ||--o{ financial_instruments : knowledge_item_id
    knowledge_items ||--o{ financial_products : knowledge_item_id
    knowledge_items ||--o{ institutions : knowledge_item_id
    knowledge_items ||--o{ industries : knowledge_item_id
    knowledge_items ||--o{ football_cases : knowledge_item_id
    knowledge_items ||--o{ financial_cases : knowledge_item_id
    institutions ||--o{ financial_products : issuer_institution_id
    financial_instruments ||--o{ financial_products : instrument_id
    institutions ||--o{ football_cases : club_institution_id
    institutions ||--o{ football_cases : league_institution_id
    institutions ||--o{ institutions : parent_id
    industries ||--o{ industries : parent_industry_id
```

`review_records` 是多态表：`(target_type, target_id)` 指向 `knowledge_item` / `research_report` /
`script` / `publication`，**故意不加外键**（跨表多态无法用干净外键表达），由服务层与测试保证一致性。

## 3. 核心实体关键字段

### 3.1 `knowledge_items`（22 列）

`id, slug(唯一), title, knowledge_domain, definition, mechanism, participants, profit_model,
pricing_logic, risk_structure, football_analogy, analogy_limitations, jurisdiction(默认 global),
as_of_date, currency, unit, verification_status, last_verified_at, version, is_current,
created_at, updated_at`

设计要点：

- `football_analogy` 与 `analogy_limitations` **必须成对**（Schema 层校验）：有类比就必须写清
  不成立的部分，这是品牌与合规的硬规则。
- `currency / unit / as_of_date / jurisdiction` 四件套保证"数字脱离语境就是错误数字"不会被写进来。
- `version + is_current` 支持条目迭代而不丢历史；更新时服务层自动 `version += 1`。

### 3.2 `sources`（15 列）

`id, title, url, publisher, source_type, published_at, accessed_at, jurisdiction,
credibility_level, license_status, content_hash, raw_excerpt, language, created_at, updated_at`

- `accessed_at` 必填（默认当前 UTC），`content_hash` 是 `raw_excerpt` 的 SHA-256，
  可用于去重与"内容是否已变更"的判断（服务层计算，不由客户端传入）。
- `credibility_level` 与 `license_status` 是枚举：来源可信度与版权状态必须可筛选。

### 3.3 `evidence`（14 列）

`id, source_id(必填), claim, original_excerpt, page_number, quote_locator, claim_nature,
support_type, verification_status, verified_at, knowledge_item_id, research_report_id,
created_at, updated_at`

- CHECK `has_claim_target`：`knowledge_item_id IS NOT NULL OR research_report_id IS NOT NULL`
  —— 证据不允许悬空。
- `support_type`：`supports / refutes / contextual / ambiguous` —— 证据可以**反驳**主张，
  不是只用来"支持"结论。
- `claim_nature`：`fact / inference / opinion` —— 事实、推断、观点的区分落在数据层，而不是文案层。

### 3.4 `topics`（14 列）

`id, slug(唯一), title, description, column_id, knowledge_domain, audience_level, priority,
football_analogy_required, research_status, content_status, estimated_duration,
created_at, updated_at`

- CHECK：`priority` 限定 1..5，`estimated_duration >= 0`。
- `research_status` 与 `content_status` 分开，研究进度与内容进度是两条独立流水线。

### 3.5 `research_reports`（17 列）

`id, title, topic_id, summary, findings(JSON), limitations(必填), verification_status,
jurisdiction, currency, author_agent, agent_run_id, version, reading_time_minutes,
as_of_date, created_at, updated_at`

- `findings` 是 JSON 列表：`[{"statement", "nature", "evidence_ids"}]`，每条结论都带性质标注
  与证据引用。
- `limitations` 非空是**硬约束**（DB `NOT NULL` + Schema 校验），不允许"没有局限"的研究。
- `reading_time_minutes > 0` CHECK。

### 3.6 `agent_runs`（21 列）

`id, task_id(唯一), agent_name, task_type, status, input_payload(JSON), context_payload(JSON),
output_payload(JSON), sources(JSON), evidence(JSON), error_payload(JSON),
requested_at, started_at, finished_at, duration_ms, topic_id, notes, created_at, updated_at, ...`

与统一协议一一对应（见 `agents/PROTOCOL.md`）。第一阶段只写 `pending` 记录，
`output_payload` 永远为空——**没有执行就不产出内容**。

### 3.7 `scripts`（17 列）/ `publications`（20 列）

- `scripts`：`topic_id, research_report_id, template, version, title, hook, body, segments(JSON),
  status, disclaimer, word_count, approved_at, approved_by`；唯一约束
  `uq_script_version(topic_id, template, version)`。
- `publications`：唯一约束 `uq_publication_per_platform(script_id, script_version, platform)`
  —— 同一个已审批的脚本版本在同一平台只能有一条发布记录；`uq_platform_item` 防止同一平台
  内容 ID 重复。效果字段：`views/likes/comments/shares/saves/watch_time_seconds/completion_rate/metrics`。
- 语义：`publications.script_version` 必须是**已审批的脚本版本**（服务层与流程文档约束，
  当前没有发布接口，因此没有自动化校验点）。

## 4. 金融专业数据域：设计选择与理由

需求要求覆盖 `financial_instruments / financial_products / institutions / industries /
football_cases / financial_cases`。第一阶段采用**独立表 + `knowledge_items` 桥接**，理由：

| 选择 | 理由 | 代价 |
| --- | --- | --- |
| 工具与产品**分表**（`financial_instruments` / `financial_products`） | 工具是机制与定价逻辑（如"可转债"），产品是可售/已发行的具体标的（如某只转债、某结构性产品）。定价、风险、参与者在工具层，发行人与条款在产品层。合成一张表会让"未来合约"和"某银行发行的挂钩产品"混在一起 | 需要一次 join 才能拿到产品的工具属性 |
| 六个域都用 `knowledge_item_id` 关联 | 公众号/视频内容需要的是"讲解口径"（定义、机制、类比、边界），这正是 `knowledge_items` 的职责；专业表只补充结构化事实，避免两套讲解内容漂移 | 写入时需保证知识条目存在（当前为可空外键，允许先建专业实体再补条目） |
| `institutions` 自引用 `parent_id` | 监管集团—子公司、俱乐部—母公司、集团—分支机构天然是树 | 深层递归查询需要应用层处理 |
| `industries` 自引用 `parent_industry_id` | 产业链是树/图，上下游与"利润分配"节点可复用同一表 | 跨链关系需另建表（未做） |
| `football_cases` 与 `financial_cases` **分表** | 足球案例有俱乐部/联赛/转会费口径，金融案例有监管处罚/资金流向口径，字段差异大；共享 `FinancialCaseType`/`FootballCaseType` 枚举会造成大量 NULL | 跨域统一检索需 UNION 或依赖 `knowledge_items` |

扩展方式：

1. 新域（如"衍生品交易所规则"）→ 新建模型文件 → 在 `backend/app/models/__init__.py` 注册 →
   `alembic revision --autogenerate` → 人工审阅迁移 → `alembic upgrade head`。
2. 新字段 → 直接改模型 + 生成迁移；SQLite 下 `render_as_batch=True` 已开启，可以 ALTER。
3. 结构化程度不确定的数据 → 先放 `knowledge_items` 的文本字段或 `findings` JSON，
   **不要**为了"齐全"提前造空表。

## 5. 必须支持的八条能力 → 落地位置

| 需求 | 落地 |
| --- | --- |
| 每个重要事实可追溯到来源 | `evidence.source_id`（必填）+ `sources.url/publisher/accessed_at` |
| 证据可支持或反驳主张 | `evidence.support_type ∈ {supports, refutes, contextual, ambiguous}` |
| 区分事实/推断/观点 | `evidence.claim_nature`、`research_reports.findings[].nature` |
| 记录数据日期/币种/单位/辖区 | `knowledge_items.as_of_date/currency/unit/jurisdiction`、`research_reports` 同名字段、`sources.jurisdiction` |
| 记录审核状态与版本 | `verification_status + last_verified_at + version + is_current`，另有 `review_records` |
| 记录 Agent 执行状态/输入/输出/错误 | `agent_runs.task_id/status/input_payload/context_payload/output_payload/error_payload/timings` |
| 脚本关联选题与研究报告 | `scripts.topic_id`（必填）+ `scripts.research_report_id` |
| 发布记录关联已审批脚本版本 | `publications.script_id + script_version` + 唯一约束 |

## 6. 约束与索引清单（节选，已在 SQLite 中核验）

- `ck_topics_priority_range`：`priority BETWEEN 1 AND 5`
- `ck_topics_duration_non_negative`：`estimated_duration IS NULL OR estimated_duration >= 0`
- `ck_evidence_has_claim_target`：证据必须挂在知识条目或研究报告上
- `ck_knowledge_relations_no_self_relation`：`source_item_id <> target_item_id`
- `ck_*_confidence_range`：关系置信度 0..1
- `uq_knowledge_relations_triple`：`(source_item_id, target_item_id, relation_type)` 唯一
- `uq_publication_per_platform`：`(script_id, script_version, platform)` 唯一
- `uq_platform_item`：`(platform, platform_item_id)` 唯一
- 枚举列均带 CHECK 约束（`native_enum=False` + `create_constraint=True`）

## 7. PostgreSQL 迁移注意

1. 环境变量 `DATABASE_URL=postgresql+psycopg://user:pass@host/db`，安装可选依赖
   `pip install -e ".[postgres]"`。
2. 模型未使用任何 SQLite 专有类型（JSON 用 SQLAlchemy `JSON`，金额用 `Numeric`），
   但 `render_as_batch` 只对 SQLite 生效，PG 上应关闭（`env.py` 已按方言判断）。
3. 迁移前先在空库执行 `alembic upgrade head` 验证，再做数据迁移；不要直接对生产库 autogenerate。
4. 时间字段是 `DateTime(timezone=True)`：PG 会存 `timestamptz`，SQLite 存字符串，行为差异需注意。
5. `LIKE` 检索在 PG 上是大小写敏感的（`ILIKE` 已用于搜索），迁移后需回归知识检索测试。
