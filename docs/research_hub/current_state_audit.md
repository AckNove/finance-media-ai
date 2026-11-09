# 现状审计：当前系统到底有什么

> **审计方式**：直接读取仓库真实代码、`Base.metadata` 反射、`app.openapi()` 导出、Alembic `ScriptDirectory`、
> `pytest --collect-only`，以及开发库 `data/finance_media.db` 的实际行数。
> **审计基线**：`main` 分支，commit `438aba3af974eaf1970b32d7ec5d4bb7b1acc1cc`。
> **审计时间**：2026-11-09。以下每一行都可核对到具体文件。

---

## 0. 状态图例

| 标记 | 含义 | 判定标准 |
|---|---|---|
| **DONE** | 真实实现，有测试覆盖 | 有代码 + 有测试 + 实跑通过 |
| **DATA-ONLY** | 只有数据结构（表/字段/枚举），没有执行逻辑 | 能读写库，但没有任何业务流程使用它 |
| **PROMPT-ONLY** | 只有 Prompt / 角色定义 / 配置文本 | 人读得懂，代码不消费 |
| **NOT IMPL** | 尚未实现 | 无代码、无结构 |
| **NEEDS ADJ** | 已存在但设计需要调整 | 能被使用，但不能满足第二阶段需求 |

---

## 1. 运行形态

| 项 | 实测值 | 证据 |
|---|---|---|
| 应用入口 | `backend/app/main.py:create_app()` → `app` | DONE |
| 启动命令 | `uvicorn backend.app.main:app` | DONE（真实起过进程，`/health` 200） |
| 数据库 | SQLite `data/finance_media.db`，19 张表 | DONE |
| 迁移 | `de8148733a9b`（唯一 head，初始 schema） | DONE |
| 开发库数据量 | 除 `alembic_version`（1 行）外**全部为 0 行** | 实测 `select count(*)` |
| 外部依赖 | 无。`config/settings.yaml` 的 `llm_providers`/`web_search`/`vector_store`/`video_rendering` 四项全为 `false` | DONE（如实声明） |
| 测试 | 96 passed / 1 warning（`httpx` 与 `starlette.testclient` 的已知无害告警） | DONE（实跑） |

> **重要**：开发库没有任何业务数据，因此第二阶段的数据库迁移**不存在历史数据包袱**，
> 只需考虑"表结构兼容"与"未来真实数据"的正确性。

---

## 2. 数据库：19 张表逐项审计

表名、列数、外键来自 `Base.metadata` 实测（详见 [`database_design.md`](database_design.md) 的现有 ER 图）。

| # | 表 | 列数 | 关键外键 | 状态 | 说明 |
|---|---|---|---|---|---|
| 1 | `topic_columns` | 7 | — | **DATA-ONLY** | 栏目定义表，可建可读；但**语义上应承担"六大内容合集"主数据**，当前无任何代码把 `content_taxonomy.yaml` 的 6 个合集灌进去 |
| 2 | `topics` | 14 | `column_id → topic_columns.id` | **NEEDS ADJ** | 只能关联**一个**合集（`column_id` 单选），无法满足"一个选题多合集 + 多标签" |
| 3 | `knowledge_items` | 22 | — | **DONE**（存储）/ **DATA-ONLY**（业务使用） | 22 个字段齐全（定义/机制/参与方/盈利模式/定价逻辑/风险结构/足球类比/类比边界/辖区/币种/单位/版本/`is_current`），可写可检索 |
| 4 | `knowledge_relations` | 8 | `source_item_id`、`target_item_id` → `knowledge_items.id` | **DONE**（存储） | 6 种关系类型、禁自环、三元组唯一 |
| 5 | `sources` | 15 | — | **NEEDS ADJ** | 有 `url`/`content_hash`/`credibility_level`，但**没有"快照"概念**：网页改版后无法保留当时的证据 |
| 6 | `evidence` | 14 | `source_id`、`knowledge_item_id`、`research_report_id` | **NEEDS ADJ** | 有 `claim`/`original_excerpt`/`page_number`/`support_type`/`claim_nature`/`verification_status`；**缺** `source_snapshot_id`、字符级片段定位（`char_start/char_end`）、引文哈希 |
| 7 | `research_reports` | 17 | `topic_id`、`agent_run_id` | **NEEDS ADJ** | `findings` 是 JSON 数组，只有 fact/inference/opinion 三档；**无法表达**"已验证事实/有证据判断/合理推测/未确认/存在争议"五档，也无法按档查询统计 |
| 8 | `agent_runs` | 21 | `topic_id` | **DONE**（记录）/ **DATA-ONLY**（执行） | 字段完整（输入/上下文/输出/来源/证据/错误/耗时/模型）；**但没有任何执行器写入 running/succeeded** |
| 9 | `review_records` | 14 | 弱多态 `target_type + target_id` | **DATA-ONLY** | 表能存审核记录，但**没有任何 API 或服务写它**，脚本/报告审核流程未实现 |
| 10 | `scripts` | 17 | `topic_id`、`research_report_id` | **DATA-ONLY** | 有 `template`/`version`/`status`/`segments`/免责声明；**无写入入口**，也无"脚本段落 ↔ 研究依据"关联 |
| 11 | `media_assets` | 19 | `script_id` | **DATA-ONLY** | 素材登记表，无生成器 |
| 12 | `publications` | 20 | `script_id` | **DATA-ONLY** | 有平台/版本唯一约束与效果字段，无采集器 |
| 13 | `institutions` | 13 | 自引用 `parent_id` | **DATA-ONLY** | 覆盖央行/监管/银行/券商/保险/交易所/俱乐部/联赛，**无"非金融公司"语义**（见 `companies` 设计理由） |
| 14 | `industries` | 11 | 自引用 `parent_industry_id` | **DATA-ONLY** | 有层级与 `chain_stage`（上/中/下游），可建产业链树 |
| 15 | `financial_instruments` | 17 | `knowledge_item_id` | **DATA-ONLY** | 10 类工具，含合约乘数/保证金比例 |
| 16 | `financial_products` | 17 | `instrument_id`、`issuer_institution_id` | **DATA-ONLY** | 与工具分表（理由见 `docs/database_schema.md`） |
| 17 | `football_cases` | 19 | `knowledge_item_id`、`research_report_id`、`source_id`、`club/league_institution_id` | **DATA-ONLY** | 含 `analogy_scope`/`analogy_limitations` |
| 18 | `financial_cases` | 17 | 同上三个 | **DATA-ONLY** | 含 `fund_flow_summary`/`risk_notes` |
| 19 | `alembic_version` | 1 | — | DONE | 当前 `de8148733a9b` |

**结论**：19 张表里，**真正被业务逻辑写入并通过 API 验证的只有 5 张**
（`topics`、`topic_columns`、`knowledge_items`、`knowledge_relations`、`sources`、`evidence`、`research_reports` —— 精确说是 7 张，
经 `POST` 接口 + 服务层 + 测试验证）；其余是"结构就绪、无生产者"。

---

## 3. API 审计（21 个端点，来自 `app.openapi()`）

| 方法 | 路径 | 状态 | 说明 |
|---|---|---|---|
| GET | `/health` | **DONE** | 不碰数据库，返回 `{"status":"ok","app":"finance-media-ai","version":"0.1.0"}` |
| GET | `/api/system/status` | **DONE** | 真实探测：`SELECT 1` + 表数量 + 迁移版本 + 脱敏 URL；`agents.discovered=9` |
| POST | `/api/topics` | **DONE** | 创建选题；`column_id` 不存在 → 422 `domain_validation_error` |
| GET | `/api/topics` | **DONE** | 分页 + 3 种过滤 |
| GET | `/api/topics/columns` | **DONE** | 只读，无创建端点（栏目只能用 `TopicService.create_column`） |
| GET/PATCH | `/api/topics/{topic_id}` | **DONE** | 部分更新流程状态 |
| POST | `/api/knowledge` | **DONE** | 建模校验：类比与类比边界必须成对出现 |
| GET | `/api/knowledge` | **DONE** | 分页 + 过滤 |
| GET | `/api/knowledge/search` | **DONE（但为 LIKE 匹配）** | 加权多字段子串匹配，**不是语义检索**，代码中已注释说明 |
| POST | `/api/knowledge/relations`、GET `/{id}/relations` | **DONE** | 禁自环、三元组唯一 409 |
| GET/PATCH | `/api/knowledge/{item_id}` | **DONE** | 更新即 `version+1` |
| POST/GET | `/api/sources` | **DONE** | `raw_excerpt` 由服务端算 `sha256` 作 `content_hash` |
| POST | `/api/evidence` | **DONE** | 必须且只能挂 `knowledge_item_id` 或 `research_report_id` 之一（CHECK + 422） |
| GET | `/api/evidence` | **DONE** | 返回附来源摘要的 `EvidenceDetailOut` |
| POST/GET | `/api/research-reports`、GET `/{id}` | **DONE** | `limitations` 必填非空白 |

**没有任何接口**涉及：研究任务、任务进度、Agent 执行、抓取、解析、审核、脚本、发布。

---

## 4. Agent 审计

| 项 | 状态 | 证据 |
|---|---|---|
| 9 个角色定义（`agents/roster.yaml` v2） | **DONE** | `load_roster()` 返回 9 项，`roster_keys_missing_prompts() == []` |
| 9 份角色 Prompt（`agents/*/PROMPT.md`） | **PROMPT-ONLY** | 共约 3.5–4.3 KB/份，含 task_type 表与 JSON 输出约定 |
| 统一协议 `AgentTaskEnvelope`（10 字段） | **DONE**（结构） | `agent_registry.py:44`，`to_dict()` 与 `agent_runs` 列一一对应 |
| 协议文档 `agents/PROTOCOL.md`（含 §6 品牌规则） | **PROMPT-ONLY** | 人读，代码不解析 |
| `TaskDispatcher` 抽象 + `UnavailableDispatcher` | **DONE**（接口） | 后者显式抛 503，**不伪造结果** |
| `RecordingDispatcher` | **DATA-ONLY** | 只写 `agent_runs(status=pending)`，`output_payload=NULL`；**这不是研究，只是登记** |
| 协作/编排/路由 | **NOT IMPL** | 无调度循环、无 DAG、无重试、无并发、无 Agent 间交接 |
| 工具能力 | **NOT IMPL** | 无搜索、无抓取、无解析、无模型调用 |
| 成本计量 | **NOT IMPL** | `agent_runs` 只有 `duration_ms`，无 token / 费用 |

---

## 5. 内容与视频基础

| 项 | 状态 | 证据 |
|---|---|---|
| 目录骨架（`content/`、`video_engine/`、`knowledge/`、`prompts/`） | **DONE（仅目录与规范文档）** | 17 个 `.gitkeep` |
| 4 个视频模板 YAML | **PROMPT-ONLY / CONFIG-ONLY** | `video_engine/templates/*.yaml`，无代码消费 |
| 12 项质检清单 | **CONFIG-ONLY** | `video_engine/quality_control/qc_checks.yaml` |
| 视频渲染、字幕、配音 | **NOT IMPL** | `video_engine/renderers/README.md` 明确写了 NOT IMPLEMENTED |
| 品牌/合集/合规配置 | **DONE（配置）** | `config/brand.yaml`、`content_taxonomy.yaml`、`content_rules.yaml`，被 `tests/test_strategy_docs.py` 断言 |

---

## 6. 测试覆盖审计（96 passed）

| 测试文件 | 用例数 | 覆盖什么 | 明确**不**覆盖 |
|---|---|---|---|
| `test_health.py` | 6 | 存活、OpenAPI 路径存在性 | — |
| `test_system_status.py` | 4 | 状态真实探测、URL 脱敏、9 个 Agent | — |
| `test_topics.py` | 11 | 创建/读取/分页/过滤/更新/404/422/栏目 | 多合集、评分、去重 |
| `test_knowledge.py` | 7 | 字段回读、类比成对、检索排序、版本+1、关系约束 | 语义检索 |
| `test_sources_evidence.py` | 6 | 来源登记、哈希、证据二选一、FK 不存在 → 422 | 快照、片段定位 |
| `test_research_reports.py` | 4 | 报告创建、limitations 校验、列表过滤 | 五档结论、findings 落表 |
| `test_models_constraints.py` | 12 | 唯一索引/CHECK/命名约定/SQLite 实态 | — |
| `test_agent_contracts.py` | 10 | 协议字段、调度器落库、Prompt 文件存在 | **真实执行** |
| `test_config.py` | 11 | 5 份 YAML 完整性、密钥标记、模板与质检数量 | — |
| `test_strategy_docs.py` | 20 | 品牌/合集/形式/合规/名册绑定/文档交叉引用 | — |
| `test_migrations.py` | 5 | upgrade/downgrade/幂等/离线 SQL | 数据迁移脚本 |

**测试红线**：没有任何一个测试触及"模型调用""网络请求""文件解析"，
因为这些能力**尚未存在**，也没有用 mock 假装它们存在。

---

## 7. 与第二阶段设计冲突、必须调整的 8 处

| # | 现状 | 为什么不够 | 调整方向（详见设计文档） |
|---|---|---|---|
| 1 | `topics.column_id` 单选合集 | 一个选题要属于多个合集 | 新增 `topic_collections`（M2M）+ `is_primary`；`column_id` 暂留作兼容 |
| 2 | 无标签体系 | 需求要求多标签、可复用、六维标签 | 新增 `tags` + `topic_tags` |
| 3 | 无选题评分 | 需要六维评分与人工调整 | 新增 `topic_score_snapshots` |
| 4 | 无研究任务实体 | `agent_runs` 是"单次 Agent 执行"，不是"一次研究" | 新增 `research_tasks` / `research_subtasks` / `research_subtask_dependencies` |
| 5 | `sources` 无快照 | 网页变化后证据失效 | 新增 `source_snapshots`（不可变字节 + 哈希）+ `source_extractions`（解析结果）+ `source_summaries`（AI 摘要，单独标识） |
| 6 | `evidence` 无片段定位 | 只存 URL 不算可追溯 | 扩列：`source_snapshot_id`、`char_start`、`char_end`、`quote_hash`、`strength` |
| 7 | 报告结论只有三档 | 需求要求五档且禁把推测写事实 | 新增 `report_findings`（含 `tier` 枚举）+ `finding_evidence`；`findings` JSON 保留为兼容镜像 |
| 8 | 无成本与调用记录 | 无法控制费用、无法复盘 | 新增 `model_calls` / `tool_calls` / `task_events` |

---

## 8. 现状能力一句话总结

> **现在拥有的是一间装修完毕、水电到位、但没有工人、没有原料、也没有生产线的工厂。**
>
> - 库房（19 张表）已建好，货架标签贴了一半；
> - 工人手册（9 份 Prompt + 协议）写好了，但没人读过；
> - 传送带（`RecordingDispatcher`）会把工单扔进收件箱，然后就没有然后了；
> - 门口的招牌（品牌与合规定义）已经挂上，且确实生效于测试。
>
> 第二阶段要做的是：**先装模型与工具的"手"，再装编排的"大脑"，最后把整条线接上电。**
