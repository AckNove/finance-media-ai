# M1.1 架构设计审查报告

- 审查对象：AK Research Hub 统一选题研究系统 M1 设计（提交 `0029c3e`，`docs/research_hub/` 13 份文档）
- 审查方式：只读审查（读代码 + 读真实数据库 schema + 读文档 + 跑现有测试），不做任何修改
- 审查日期：2026-11-10
- 审查结论一句话：**设计方向正确、结构完整、没有虚报实现，但存在 2 个 P0 与 8 个 P1 设计缺陷，其中 4 项必须在 M2 第一次迁移或第一次真实模型调用之前解决。M2 不建议直接开工写模型接入。**

---

## 0. 阅读说明：证据分级

本报告中每一个判断都标注证据等级，避免把"文档里写着"当成"已经验证"。

| 标记 | 含义 | 本次如何取得 |
|---|---|---|
| **[V]** | 已由代码、真实数据库 schema 或实跑测试验证 | 直接 dump `data/finance_media.db`、grep 源码、跑 `pytest` |
| **[d]** | 仅从设计文档中发现（文档内部或文档之间） | 逐份通读 + 跨文档关键词交叉比对 |
| **[u]** | 尚未验证，需后续实测 | 依赖真实网络/模型/OAuth 的能力，本轮禁止触发 |

本次实测环境（只读）：

```
git HEAD = origin/main = 0029c3e  工作区 clean  分支 main
alembic heads  = de8148733a9b (head)
alembic current = de8148733a9b (head)
alembic check  = "No new upgrade operations detected."
pytest         = 96 passed（详见 §12 复现命令）
真实库 data/finance_media.db = 19 张表，行数除 alembic_version=1 外全部为 0
sqlite 3.49.1  journal_mode=delete  busy_timeout=0  page_size=4096
```

---

## 1. 总体评价

### 1.1 做得对的地方（先说结论，不是客套）

1. **没有把设计伪装成实现**。21 个端点、19 张表、7 个服务、9 个 Agent Prompt 都明确区分了 DONE / DATA-ONLY / PROMPT-ONLY / NOT IMPL，`current_state_audit.md` 的分类与本次实测**完全一致 [V]**——这是本次审查中最重要的正面发现，因为它意味着后续所有"完成度"判断都有可信基线。
2. **架构选型克制**。数据库队列 + 租约拒绝引入 Celery/Redis [d]，五层分层（API → 领域服务 → 执行层 → 能力层 → 存储）与现有代码结构可兼容，没有为了"看起来专业"上微服务。
3. **能力层默认空实现**（`Null*` 返回 `not_configured`）[d] 是对的方向：它保证"没有密钥时系统不会假装能搜网"，与 `integrations.*.enabled: false` 的真实配置一致 [V]。
4. **证据不可变 + 快照 + 字符偏移**（`source_snapshots` / `char_start` / `quote_hash`）[d] 是整套设计中最有价值的部分，也是"金融内容可追溯"的真正地基。
5. **`FindingTier` 五档**（verified_fact / supported_judgment / reasonable_speculation / unconfirmed / disputed）[d] 正面回应了"事实、推断、观点必须分开"这条硬规则，比现有 `StatementNature` 三档更贴近研究场景。

### 1.2 主要问题分布

| 严重程度 | 数量 | 含义 |
|---|---|---|
| **P0** | 2 | 不解决会导致 M2 返工或直接跑不通，或造成不可控成本 |
| **P1** | 8 | 应在 M2 前修改设计，或必须在 2.0–2.2 内修复 |
| **P2** | 13 | 不阻塞，但会造成数据不一致、查询错误或返工 |
| **P3** | 5 | 文档一致性、可维护性问题 |

### 1.3 一句话定性

M1 交出的是一份**可信、可执行、但尚未闭合的施工图**。它的缺口不在篇幅，而在**三个"交接缝"**：

1. **约束缝**：既有数据库 CHECK / CASCADE 约束与新的流水线顺序互相打架（P0-1、P1-3、P1-4）。
2. **时点缝**：记账与预算能力排在首次真实模型调用之后（P0-2）。
3. **执行缝**：权限、成本、调用者身份目前 100% 停留在文档里（P1-7）。

这三条缝都是"文档看不出来、一写代码立刻撞墙"的类型，必须在开工前补上。

---

## 2. 数据库设计审查

### 2.1 现有 19 张表是否被完整盘点

**结论：清查完整，但 ER 图与计数有漂移。**

`database_design.md` §1 的标题写"19 张表"，实际 Mermaid 图只画了 **17 张**：缺 `review_records`（另有 `alembic_version` 属基础设施，可略）。本次 dump 的真实表清单 [V]：

```
agent_runs, alembic_version, evidence, financial_cases, financial_instruments,
financial_products, football_cases, industries, institutions, knowledge_items,
knowledge_relations, media_assets, publications, research_reports, review_records,
scripts, sources, topic_columns, topics
```

三分法结论：

| 类别 | 表 | 说明 |
|---|---|---|
| **现有真实表（18 业务表 + 1 版本表）** | 见上 | 均有真实 DDL，行数除 alembic_version 外为 0 |
| **计划新增（19 张）** | `tags` `topic_collections` `topic_tags` `topic_score_snapshots` `research_tasks` `research_subtasks` `research_subtask_dependencies` `task_events` `source_snapshots` `source_extractions` `source_summaries` `research_task_sources` `report_findings` `finding_evidence` `model_calls` `tool_calls` `companies` `content_items` `script_evidence_links` | 全部未建，仅文档描述 |
| **明确不建** | 独立 `collections` 表、`topics_v2`、`knowledge_item_sources`、向量表、额外审核表 | 理由已写在 §3，接受 |

> 注意：新增的 19 张表里**没有一张**在真实数据库中，也没有对应迁移文件。`backend/migrations/versions/` 只有 `20261109_1754_initial_schema.py` 一个 revision [V]。任何"38 张表"的表述都必须读作"19 张真实 + 19 张计划"。

### 2.2 新增模型是否确有业务必要

逐表核对的结论：**17 张有必要，2 张存在更轻的替代方案**。

- **必要且不可替代**（4 张核心）：`research_tasks`（没有它就没有可恢复的长任务）、`research_subtasks`（没有它 orchestrator 无法分工）、`source_snapshots`（没有它"网页改版后证据失效"无法发现）、`model_calls`（没有它就没有成本与调用可审计性）。
- **必要但可合并**：
  - `topic_score_snapshots` 与 `topics.score_total` 是"快照 + 当前值"关系，合理；但六维分值同时存在于 `research_tasks.plan` JSON 与快照表中时需明确权威源 [d]。
  - `tool_calls` 与 `model_calls` 分表合理（成本模型不同），但第一阶段只有 3 个真工具，`tool_calls` 可能长期空表 → 保留但标注 `P3-5`。
- **重复风险最高的一对**：`companies` vs `institutions`。`institutions` 现有 `institution_type` 枚举已包含非金融机构类型 [V]，而设计未给出"某主体该进哪张表"的判定规则 → **P2-10**。
- **过度设计嫌疑最小化处理**：`research_task_sources.purpose`（background / data_point / cross_check / counter_evidence）是有价值的，它直接支撑"必须找反方证据"这条规则，不算过度。

### 2.3 是否存在重复存储

| 重复点 | 判定 | 建议 |
|---|---|---|
| `research_reports.findings` (JSON, 真实存在 [V]) vs `report_findings` (计划表) | **真重复** | 见 P2-6：必须声明权威源（建议表为权威、JSON 为渲染快照），否则 2.5 之后两份会漂移 |
| `agent_runs.model_provider` / `model_name` [V] vs `model_calls` | 重叠 | 见 P2-7：agent_runs 保留汇总，细粒度归 model_calls |
| `evidence.page_number` / `quote_locator` [V] vs 新增 `char_start`/`char_end`/`quote_hash` | 重叠 | 见 P2-5：需定义共存与迁移规则 |
| `evidence.claim` vs `report_findings.statement` | 非重复 | 前者是"这句话出自哪里"，后者是"我们得出的判断"，语义不同，保留 |
| `source_summaries` vs `research_reports.summary` | 非重复 | 单源摘要 vs 综合结论，保留（且已禁止 evidence 指向 summaries，很好） |

### 2.4 `topic_columns` 是否被正确复用为六大合集的唯一主数据表

**复用决定（D2）在技术上是成立的，但"唯一主数据"目前只成立一半。[V]**

- 真实 `topic_columns` 列只有：`id` `code VARCHAR(32)` `name VARCHAR(64)` `description` `display_order` `created_at` `updated_at`，**当前 0 行** [V]。
- `config/content_taxonomy.yaml` 里每个合集还有 `mission` `scope` `boundary` `typical` `agents{primary,support}` `templates` `forms` `risk` —— 这些**没有任何一列存进 DB**，且 §5 明确写 `topic_columns` 属"明确不改" [V/d]。
- 六个合集 id 最长 `financial_knowledge` = 19 字符，`VARCHAR(32)` 够用 [V]；但新增合一 `code` 的长度上限未在文档声明（P3-4）。

于是出现**身份与政策分裂**：

```
DB:   topic_columns(code, name)          ← 只有身份
YAML: content_taxonomy.yaml:collections  ← 全部政策（使命/边界/模板/负责 Agent）
```

两者靠 `code` 对齐，**没有外键、没有校验、没有停用机制**。seed 脚本按 `code` upsert，§6.4 校验只查 `select count(*) from topic_columns >= 6` [V]——如果有人改了 YAML 里的合集 id，旧行会残留成"孤儿合集"，历史 `topic_collections.column_id` 仍指向它，而校验仍然通过。→ **P1-8**。

### 2.5 `topics` 与 `topic_columns` 的历史关联是否兼容

**兼容方案（双写 + 回填 + SET NULL）是对的**，回填 SQL 幂等、`WHERE NOT EXISTS` 防重复 [d]。但缺三点：

1. **未定义双写冲突时的权威源**（`column_id` 与 `topic_collections(is_primary=1)` 打架时谁赢）→ 建议服务层单点写入 + 定期一致性断言（P2 级，合并进 P1-8 的整改）。
2. **未规定 `is_primary` 数量上限的强制位置**：部分唯一索引 `WHERE is_primary = 1` 保住了"最多一个"，但没保住"至少一个"（选题可以 0 合集）→ 与 taxonomy `per_topic_limits.collections: 1–3` 矛盾（**新发现，P2-14**）。
3. **退场计划（2.10 评估删除）没有前置条件**：应写明"连续两个阶段无双写不一致 + 所有读路径切走"才算达标。

### 2.6 `topic_collections` 是否支持一个选题对应多个合集

**支持。** `uq_topic_collection(topic_id, column_id)` + M:N 结构正确 [d]；`is_primary` 用部分唯一索引表达"每选题至多一个主合集"，SQLite/PostgreSQL 均支持（MySQL 不支持，本项目不用 MySQL）[V/d]。

**但级联规则错误（P1-3）**：`topic_collections.column_id` 声明为 `ON DELETE CASCADE`，而真实库中 `topics.column_id → topic_columns.id` 是 **SET NULL** [V]。也就是说：**删掉一个合集，会静默删除所有选题与它的归属关系，而不是把选题置空**——这与"合集是主数据、可以停用但不该带走数据"的语义正好相反。

### 2.7 `tags` / `topic_tags` 是否支持多标签

**支持，约束设计得相当完整 [d]**：`uq_tag_namespace_name(namespace, name)`、`ix_tags_slug(unique)`、`uq_topic_tag(topic_id, tag_id)`、`CHECK weight 0..1`、六维 namespace 与 `content_taxonomy.yaml:multi_label.tag_dimensions` 一致。

两点保留意见：

- `slug` 全局唯一（`ix_tags_slug`）与 namespace 分组存在张力：不同 namespace 下同名 slug（如 `bank`）会冲突。需明确 slug 生成规则是否含 namespace 前缀（**P3-6**）。
- `per_topic_limits.tags: 0–6` 的数量限制只写在文档、放服务层 [d]，数据库无约束——这是合理取舍，但必须在 2.7 的验收里有一条"第 7 个标签被拒绝"的用例。

### 2.8 外键 / 唯一约束 / 索引 / 级联规则是否缺失

已声明的唯一约束齐全 [d]：`uq_topic_collection`、`uq_topic_primary_collection`、`uq_topic_tag`、`uq_topic_current_score`、`uq_subtask_sequence`、`uq_subtask_dependency`、`uq_snapshot_content`、`uq_extraction_variant`、`uq_task_source`、`uq_finding_ordinal`、`uq_finding_evidence`、`uq_company_name_country`、`uq_tag_namespace_name`。

**仍然缺的四类（对应下方问题编号）**：

| 缺失项 | 位置 | 编号 |
|---|---|---|
| `task_events` 无序号列，事件顺序依赖 `created_at`（同秒并列不可判定） | §4.8 | P2-11 |
| `script_evidence_links` 无唯一约束（同一片段可重复挂同一证据） | §4.19 | P2-11 |
| 既有 CASCADE 链未评估（删选题连带删脚本/发布数据） | 真实库 | P1-4 |
| 部分唯一索引 Alembic 不可见（会静默漂移） | §6.2/§7 | P2-9 |

索引方面：真实库 19 张表的**外键列 100% 有前导索引** [V]（脚本检查 "FK COLUMNS WITHOUT A LEADING INDEX: 空"），这一点做得好；新增表的索引清单也已逐表列出 [d]。

### 2.9 Source → Evidence → Claim → Fact → Analysis → Report 能否完整追溯

**设计的追溯链是完整的 [d]**：

```
sources → source_snapshots(字节+hash) → source_extractions(解析产物)
        → source_summaries(AI 摘要，禁止被 evidence 引用)
evidence(source_snapshot_id + extraction_id + char_start/char_end + quote_hash)
        → finding_evidence(role: supports/refutes/contextual)
report_findings(tier: 五档) → research_reports ← research_tasks
```

**但链条在最关键的一步被现有 CHECK 约束掐断（P0-1）**，详见 §8。

### 2.10 是否支持来源版本、网页快照与证据原文定位

**支持，且设计正确 [d]**：快照不可变（改版新增行、旧行 `is_current=false`、旧字节永不删除）、`uq_snapshot_content(source_id, content_hash)` 防重复存、`span_offset_base` 声明偏移基准、`quote_hash` 可检测"原文是否变过"。

唯一欠缺：**没有实现层**（`backend/app/capabilities/parsers/` 目录不存在 [V]），且 `quote_hash` 的校验算法（是否含空白归一化、是否含 HTML 实体解码）未定义 → 属"待实测（u）"项，须在 2.3 明确算法并写死测试向量。

### 2.11 是否能区分事实 / 分析 / 推测 / 未确认

**能，且优于现状。** 现有 `evidence.claim_nature` 只有 fact/inference/opinion 三档 [V]；设计中 `FindingTier` 五档 [d] 增加了 `reasonable_speculation`、`unconfirmed`、`disputed`，并用 CHECK 强制"disputed 必须带 counter_evidence、reasonable_speculation 必须带 invalid_if"——这是**用数据库约束表达方法学纪律**，是本设计里最漂亮的一处。

缺口：`evidence.claim_nature` 与 `FindingTier` 的**映射规则未定义**（同一句主张在证据层是 inference、在发现层是 supported_judgment 时，谁决定？）→ 建议在 `agent_architecture.md` 的 fact_checker 定义里写死映射表（P2 级）。

### 2.12 是否支持 Agent 执行记录、失败记录、重试与成本记录

| 需求 | 现状 |
|---|---|
| 执行记录 | `agent_runs` 真实存在（21 列，含 input/context/output/sources/evidence/error_payload + 时间戳）[V]，但**无 `subtask_id`、无 `cost_estimate`**（计划在 §5 加列）[V/d] |
| 失败记录 | `agent_runs.error_payload` + 计划 `task_events.failure_reason` [d] |
| 重试 | `research_subtasks.attempts` / `max_attempts=2` [d]，**无实现** |
| 成本记录 | `model_calls.cost_estimate` + `pricing_unknown` [d]，**表在 2.5 才建**（P0-2） |

结论：**结构够用，时点危险**。

### 2.13 SQLite 是否支撑现阶段本地使用与并发需求

分两段回答：

- **容量：够。** 数据量估算十万级行，`page_size=4096`、库文件当前 540 KB [V]，瓶颈在 `data/raw/` 字节而非数据库。
- **并发：当前配置不安全 [V]。** 实测 `journal_mode=delete`、`busy_timeout=0`；`backend/app/database/session.py` 的 connect 事件只设 `PRAGMA foreign_keys=ON`，未设 WAL 与 busy_timeout [V]。2.4 引入进程内 worker 后，"API 写 + worker 写"并发时 SQLite 会**立即**返回 `database is locked`（默认 busy_timeout=0 不重试）→ **P1-6**。

### 2.14 备份、迁移、回滚是否有可执行方案

| 项目 | 结论 |
|---|---|
| 增量迁移 | ✅ 已声明两段式/多阶段；但**节奏自相矛盾**（P1-2） |
| 旧数据兼容 | ✅ 开发库除版本表外全空 [V]，回填 SQL 幂等 [d]；**但生产/知识库数据尚未产生，实际风险低** |
| 迁移前备份 | ⚠️ §6.5 给了 `sqlite3 .backup` 命令，但 `data/backups/` **目录不存在** [V]，命令**从未实测**（P2-12） |
| 升级后完整性检查 | ✅ 清单齐全（期望 38 表、pytest、`PRAGMA foreign_key_check`、topic_columns≥6、downgrade 往返）[d]；缺 code 与 YAML 一致性校验（P1-8） |
| 失败回滚 | ✅ 三层（git revert + downgrade / 备份还原 / 功能开关）逻辑完整 [d]；缺"迁移执行到一半中断"的具体演练（P2-15） |
| 是否一次性变更过多 | ❌ 见 P1-2：19 张表 + 5 表 ALTER 放在一个 revision 里，与"分五次"的推荐直接冲突 |

---

## 3. 系统架构审查

**结论：五层分层合理，运行时选型保守正确，但"执行层"与"能力层"目前是**零行代码的目录名**。**

### 3.1 分层与现状对照 [V]

| 设计层 | 设计中的模块 | 代码现状 |
|---|---|---|
| L0 API | `api/routes/*` | 只有 5 个路由文件（health/topics/knowledge/sources/research_reports） |
| L1 领域服务 | `services/*` | 7 个文件，无 research_task/finding/script/content 服务 |
| L2 执行层 | `execution/`（TaskRuntime/Orchestrator/AgentExecutor） | **目录不存在** |
| L3 能力层 | `capabilities/`（ModelGateway/ToolRegistry/Parser） | **目录不存在** |
| L4 存储 | SQLite + `data/raw` + `knowledge/*.md` | SQLite 19 表 + 空目录（有 .gitkeep） |

这不是缺陷（M1 只允许设计），但**必须在 2.0 落地为可 import 的空壳带测试**，否则 2.1 起会边写边改结构。

### 3.2 数据库队列 + 租约的取舍（决策 A1）

**认可。** 理由链完整：单机、单用户、无 Redis、拒绝 K8s/微服务；租约用 `claimed_by` + `lease_until`，崩溃恢复靠租约过期重扫。三点必须补：

1. **心跳/续租未定义**：长任务（搜网 + 多次模型调用）超过租约时长会被第二个 worker 抢走 → 必须写明续租周期（建议租约 120s / 每 30s 续租）与"重入时幂等"要求（**P1 级，并入 P1-6 的整改**）。
2. **`execution.enabled=false` + dev tick** 是好的安全默认 [d]，但 API 端点 `POST /api/_dev/worker/tick` 在生产必须返回 404（`api_design.md` §2.3 已写 [d]）——需要代码层实现，目前不存在（P1-7）。
3. **单 worker 并发 = 1** 与 SQLite 写锁天然匹配，但 WAL 未开（P1-6）会让"读请求撞写事务"成为日常故障。

### 3.3 成本闸门位置

设计了三层（单次 / 任务 / 日），逻辑正确 [d]。但**强制点缺失**：`ModelGateway` 尚不存在，且 `research_tasks.budget_*` 所在表在 2.0 才建、`model_calls` 在 2.5 才建 → 2.1–2.4 期间无表可记、无闸可挡 → **P0-2**。

### 3.4 其他系统级观察

- `config/models.yaml` 价目表 + `pricing_unknown=true` 不猜价 [d] —— 正确且诚实。
- `request_hash` 24h 缓存 [d] —— 需要明确"缓存命中是否计入 budget"（建议计入 calls、不计 cost，并在 `model_calls.cache_hit` 体现）。
- 扩展点表与 5 条技术债已列出 [d]，其中"单进程"与"无鉴权"两条应在 2.4 前升级为显式风险登记。

---

## 4. Agent 架构审查

### 4.1 职责与边界

**9 个角色的定位、输入、输出、写库权限、可否调用他人、失败处理都写了，质量在同类设计里属上乘 [d]。** 能力矩阵 9×15、routing 白名单、handoff 五型（request_subtask / request_revision / need_human / insufficient_evidence / request_asset）都能对应到表与事件。

真正的问题只有两类：

1. **一个任务类型凭空出现（P1-5）**：`module_design.md:45` 使用 `topic.discover`（RSS / 知识缺口扫描），但九种 `task_type` 目录与 routing 白名单里都没有它 [V/d] → 真按文档实现，Agent 路由会以"未知 task_type"拒绝该任务。
2. **"仅有 Prompt、没有执行能力"的现状必须显式保留（不是缺陷，是事实）**：`RecordingDispatcher.dispatch()` 只写 `agent_runs(status=pending)` 且**不写 output** [V]；`get_dispatcher()` 固定返回它 [V]。这意味着**当前没有任何 Agent 真的在工作**。`current_state_audit.md` 已如实标注 PROMPT-ONLY [V]，M2 起必须在验收里继续用"output_payload 是否有值"作为 Agent 真跑过的判据——`acceptance_criteria.md` 的反面清单已包含这一条 [d]，很好。

### 4.2 状态管理 / 重试 / 超时 / 失败 / 幂等

| 机制 | 设计 | 现状 |
|---|---|---|
| 任务状态机 | ResearchTaskStatus 含 created/queued/claimed/running/paused/… | 表未建 [V] |
| 子任务状态机 | SubtaskStatus + attempts/max_attempts | 表未建 [V] |
| 超时 | 工具层 timeout + 任务层租约 | 未实现 [V] |
| 幂等 | `request_hash`、`uq_*` 唯一约束、`idempotency_key` | 部分设计 [d]，无代码 |
| 失败传播 | 失败矩阵覆盖 model_unavailable / schema_invalid / budget_exceeded / tool_failed / quote_mismatch / DAG 环 / 进程崩溃 | 文档完整 [d]，无实现 |

**缺一项关键设计**：`quote_mismatch`（引用与原文字节不符）的处置策略只写了"标记"，未写"是否阻断报告生成"。建议：`verified_fact` 级发现若出现 `quote_mismatch=1`，必须 fail 该 finding 而不是降档（**P2-16**）。

### 4.3 按任务动态调度

设计正确：orchestrator 只按 subtask.task_type 路由到单一 assignee，不在一次调用里串所有 Agent [d]。这是成本与可控性的关键，认可。

### 4.4 数据库访问权限控制

**设计有三层（工具白名单 / db_scopes / 运行时上下文），代码为零（P1-7）[V/d]。** 也就是说当前状态下，任何能调用服务层函数的代码都能写任意表——只是目前还没有 agent 代码可写。

---

## 5. 工具能力审查

### 5.1 三分法（真实 / 已设计未实现 / 仅接口占位）

| 工具 | 设计 | 实现状态 | 判定 |
|---|---|---|---|
| `web_search` | ✅ 契约完整（含 provider 抽象、错误码） | 无任何 provider 实现；`config/settings.yaml: integrations.web_search.enabled=false` [V] | **仅接口** |
| `fetch_url` | ✅ 契约 + 快照落盘规则 | 无 `httpx`/`requests` 调用 [V] | **仅接口** |
| `parse_pdf` / `parse_xlsx` | ✅（`parse_pdf` 含页偏移基准） | 无 `capabilities/parsers/` 目录 [V]；无 PDF 库依赖 | **仅接口** |
| `llm_complete` | ✅（含 pricing_unknown、request_hash 缓存） | 无模型 SDK 依赖、无 `ModelGateway` [V] | **仅接口** |
| `db_query` | ✅（只读 + 域白名单） | 无实现 | **仅接口** |
| `db_write` | ✅（写库白名单） | 无实现 | **仅接口** |
| `task_update` | ✅ | 无实现（状态机也只有文档） | **仅接口** |
| `handoff` | ✅ 五型 | 无实现 | **仅接口** |
| `fact_check` | ✅（只能用 verify 拉已知 URL，禁止搜索） | 无实现 | **仅接口** |
| 文件存储（`data/raw`） | ✅ 路径规范 | 目录存在但为空（`.gitkeep`）[V] | **仅规范** |

**结论：10 个工具，0 个真实可用。** 文档对此是诚实的（`tooling_architecture.md` 明写默认 `Null*` 返回 `not_configured`）[d]，但必须在 M2 的报告里继续这样写，不能出现"已具备搜索能力"的表述。

### 5.2 逐工具测试方法

§2.11 已给 10 个工具各自的成功用例、特有失败边界与离线方式（`FakeSearchProvider`、本地 `http.server`、`FakeModelGateway`、`dry_run`）[d]，质量足够开工。补一条：`fetch_url` 必须有一个"同一 URL 两次抓取得到相同字节 → 只产生一条快照"的用例（验证 `uq_snapshot_content`），另有"内容变化 → 新增快照且旧快照 `is_current=0`"的用例。

### 5.3 搜索摘要 vs 原始资料

设计要求"搜索摘要不能冒充原始资料" [d]，落点正确：`source_summaries.is_ai_generated` 恒 true，且 `evidence` 禁止指向摘要（422）。**建议再加一条数据库级保护**：`source_summaries` 不出现在任何 FK 指向 `evidence` 的路径上（当前设计无此 FK，已是隐式保证）——保持即可。

---

## 6. API 与权限审查

### 6.1 端点设计

现有 21 个端点（14 路径 / 21 操作）[V]，计划端点按 A–G 七组组织 [d]。有三处不闭合：

| 问题 | 证据 | 编号 |
|---|---|---|
| `POST /api/sources/upload` 只在权限表与 module_design 出现，未列入 §2.2 端点目录 | [V] grep：`sources/upload` 仅 `module_design.md:86` | P2-4 |
| `/api/topics/{id}/approve` 写的是 `idea → approved`，而状态机是 `candidate → scored → awaiting_review → approved` | [V] `api_design.md:68` vs `state_machine.md` | P2-2 |
| research task 示例响应 `"status": "draft"`，而 DB/状态机默认 `created` | [V] `api_design.md:211` | P2-2 |

另：`module_design.md:30` 说 Topic Hub 计划 11 个端点，`api_design.md` A 组 6 + B 组 8 = 14 个 [V] → 计数漂移（P3-1）。

### 6.2 三类调用者权限

设计写了 `human` / `agent` / `dev-only` 三类 + 逐端点组权限表 + "approve/publish/pause/merge/split 必须由人发起" [d]。**这层设计是正确的，但完全没有代码落点 [V]**：

- 全仓无 `Security(`、`HTTPBearer`、`OAuth2PasswordBearer`、`get_current_user`、`APIKeyHeader` 任何符号 [V]；
- 21 个端点全部无鉴权依赖 [V]；
- 无 `CallerContext`、无 `assert_scope`、无 `db_scopes` 校验函数 [V]。

后果：M2 若先写模型接入再补权限，则所有写库路径都先长成"无身份"的样子，后期再加会改动每个服务签名 → **P1-7，必须在 M2 前解决**。

另需明确一个产品决策：服务默认只监听 `127.0.0.1` [V，`config/settings.yaml`]，因此"无鉴权"在单机自用下可接受；但一旦要手机访问/局域网访问，必须先补鉴权——建议写成硬前置条件（风险 R6）。

### 6.3 是否存在只在文档中声明、代码无保护的接口

**全部计划端点都是这种情况（因为它们还不存在）[V]。** 需要区分两类危险：

1. 不危险的：计划端点未实现。
2. 危险的：**如果 M2 按 roadmap 先实现 `db_write` 工具与写库端点，而权限仍是文档** → 任何 agent 都能写任何表。这正是 P1-7 的实质。

---

## 7. 成本控制审查

### 7.1 设计层面

三层闸门 + 价目表 + `pricing_unknown` + `request_hash` 缓存 [d]，思路完整。`business_workflow.md` 的《商业银行是如何赚钱的？》估算 42–59 次调用、¥0.5–0.9，并明确标注"估算、非实测" [d]——诚实，认可。

### 7.2 执行层面（这是本节的结论）

**成本控制在代码执行层完全不存在，且按当前 roadmap 的时间顺序，最早也要到 2.5 才具备记账能力 [V/d]。**

- `model_calls` 表：roadmap 2.5 才建，`database_design` 却写"2.0 建 19 表"[V/d] → 两处冲突；
- `research_tasks.budget_max_cost` 字段：2.0 建表时有列，但**没有任何代码读它**（`backend/app/` 无 execution 目录）[V]；
- 日预算 `settings.cost.daily_cap`：`config/settings.yaml` 目前**没有 cost 段** [V/d] → 需要新增配置项；
- 唯一能立刻生效的"闸门"是 `integrations.llm.enabled=false` 与 `NullModelGateway` 抛 `not_configured` [d]——这只能防"没密钥时误调用"，防不了"有密钥时失控"。

**P0-2 的本质**：不是"设计不好"，而是"记账能力排在第一次真实花钱之后"。M2 一开工（2.1 接模型）就会产生无法审计的调用记录。

### 7.3 建议的最小成本闭环（M2 前必须写进设计）

1. `model_calls` 表提前到 **2.1 的第一次迁移**；
2. `ModelGateway.call()` 内强制三段：**预算检查 → 调用 → 记账（即使失败也记 error 行）**；
3. 预算不可用（无表/无配置）时**硬失败**（`budget_blocked`），不允许"先跑再说"；
4. 日预算按 `date(created_at)` 汇总 `model_calls.cost_estimate`，超限拒绝并写 `task_events(budget_exceeded)`；
5. 三条测试：单次超限被拒、任务超限被拒、日超限被拒（都要断言"没有发生真实调用"）。

---

## 8. 数据追溯与证据体系审查

### 8.1 追溯链完整性

见 §2.9 的链路图。**设计闭合，实现为零，且存在一个 P0 阻断。**

### 8.2 P0-1：证据 CHECK 约束阻断流水线顺序（本次最重要的发现）

**证据：**

```
真实库 [V]：
  CONSTRAINT ck_evidence_has_claim_target
  CHECK (knowledge_item_id IS NOT NULL OR research_report_id IS NOT NULL)
```

**冲突：** 设计的 DAG 是

```
research.collect → research.extract（产出证据）→ research.analyze → research.synthesize（产出报告）
```

也就是说，**证据必须在报告存在之前落库**。但 `evidence` 表要求每一行必须挂到 `knowledge_item` 或 `research_report` 上；此时报告还没生成，knowledge_item 更不会为一次临时研究而创建 → **INSERT 必然失败**。

而且 `database_design.md` §5 对 `evidence` 的加列清单里**没有**放松该 CHECK 的计划（grep 全文，`evidence` 只在第 575 行 ALTER 列表与第 657 行数据量估算中出现 [V]）——说明这不是"后面会补"，而是**遗漏**。

**可能后果：**

- 2.4 起，Agent 抽取到的证据无处可放，只能：① 先建空报告壳（但 `research_reports.summary/limitations` NOT NULL，见 P2-13，得写假内容）；② 把证据塞进 `agent_runs.output_payload` JSON（等于退回到"没有证据表"）；③ 被迫在 2.4 就改约束（届时已写过一批代码）。
- 三条路都会污染"证据可追溯"这个根基。

**建议解决方案（需人工选定，见待确认 Q2）：**

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A（推荐）** | `evidence` 增 `research_task_id`，CHECK 改为"三选一：knowledge_item / research_report / research_task" | 与流水线顺序天然吻合；一次 batch ALTER 完成 | SQLite 需重建表（`render_as_batch` 已配置，可支持）；CHECK 变更需手写迁移 |
| B | 允许 `evidence` 全空目标（去掉 CHECK） | 最简单 | 失去"证据必须属于某事"的纪律，孤儿证据不可查 |
| C | 保留 CHECK，强制"先建报告壳" | 不改 schema | 需放宽 `summary/limitations` 为可空（另一次 schema 变更），且语义颠倒 |

**是否必须在 M2 前解决：是。** 这是 2.0 第一次迁移就要定的事（同表加列 + 改 CHECK 一起做最省）。

### 8.3 引用能否定位原始来源与证据片段

设计上用 `source_snapshot_id + extraction_id + span_offset_base + char_start/char_end + quote_hash` 定位到字节级 [d]，方向正确。两项空缺：

- `quote_hash` 的归一化算法未定义（空白折叠？HTML 实体解码？全角半角？）→ 待实测（u），须在 2.3 写死并给测试向量；
- 既有 `page_number` / `quote_locator` 与新列如何共存无规定 → **P2-5**。

### 8.4 事实 / 分析 / 推测的区分

见 §2.11：结构优于现状，但**映射规则缺失**，且 `review_records` 无法承载针对单个 finding 的裁决（P1-1）。

---

## 9. 端到端流程可实现性审查

以"用户输入一个选题 → 研究报告 → 内容生产"逐步判定。判定口径：四要素（数据结构 / 工具 / API / 执行机制）齐全才算"可实现"。

| # | 步骤 | 数据结构 | 工具 | API | 执行机制 | 判定 |
|---|---|---|---|---|---|---|
| 1 | 选题录入 | ✅ `topics` [V]（缺 status/origin/dedupe，2.0 加） | — | ✅ `POST /api/topics` [V] | 人 | **PARTIAL**（缺评分/立项，2.7） |
| 2 | 合集/标签归类 | ❌ `topic_collections`/`topic_tags`/`tags` 未建 | — | ❌ | — | **NOT IMPL**（2.0/2.7） |
| 3 | 立项与建研究任务 | ❌ `research_tasks` 未建 | — | ❌ | ❌ | **NOT IMPL**（2.4） |
| 4 | 任务拆解（DAG） | ❌ `research_subtasks`/依赖未建 | ❌ | ❌ | ❌ | **NOT IMPL**（2.4） |
| 5 | 搜索资料 | — | ❌ 无 provider | ❌ | ❌ | **NOT IMPL**（2.2） |
| 6 | 抓取并保存原始字节 | ❌ `source_snapshots` 未建；`sources` 只能存元数据 | ❌ | ❌ | ❌ | **NOT IMPL**（2.2） |
| 7 | 解析（HTML/PDF/XLSX） | ❌ `source_extractions` 未建 | ❌ | ❌ | ❌ | **NOT IMPL**（2.3） |
| 8 | 证据抽取落库 | ⚠️ `evidence` 存在但被 **P0-1 阻断** | ❌ | ✅ `POST /api/evidence` [V]（但需先有报告/knowledge_item） | ❌ | **BLOCKED** |
| 9 | 分析（8 类框架） | ❌ `report_findings` 未建 | ❌ | ❌ | ❌ | **NOT IMPL**（2.5） |
| 10 | 事实核查 | ✅ `review_records` [V]（但 target_type 仅 3 类，P1-1） | ❌ `fact_check` | ❌ | ❌（agent 未跑） | **PARTIAL** |
| 11 | 研究报告 | ⚠️ `research_reports` [V]，`findings` 是 JSON；`report_findings` 未建 | ❌ | ✅ `POST /api/research-reports` [V] | ❌ | **PARTIAL** |
| 12 | 内容生产（脚本） | ✅ `scripts` [V]；`content_items`/`script_evidence_links` 未建 | ❌ | ❌（无 scripts 路由） | ❌ | **PARTIAL→NOT IMPL**（2.9） |
| 13 | 视频渲染 | ✅ `media_assets` [V] | ❌ 无 renderer/FFmpeg | ❌ | ❌ | **NOT IMPL**（M10） |

**判定结论：**

- 链路两端（1 选题、11 报告、12 脚本）**有真实表与真实 API**；
- 中间四段（3 任务、6 快照、7 解析、8 证据）**全部未建，且第 8 段被 P0-1 阻断**；
- 因此**端到端不可运行——这符合 M1"只设计"的边界，不是缺陷**；但 **P0-1 会让 2.4→2.5 之间必然返工**，这才是问题。

另一条必须写进 M2 验收的硬规则：**搜索摘要不得冒充原始资料**（第 5→6 步），**内容作品不得覆盖研究报告**（第 11→12 步）；两者目前靠设计纪律，建议在 2.5 加数据库级断言（`evidence.source_snapshot_id NOT NULL` 一旦启用，摘要冒充在结构上就不可能）。

---

## 10. 现阶段主要风险

| 编号 | 风险 | 等级 | 依据 |
|---|---|---|---|
| R1 | 首次真实模型调用发生在记账能力之前 → 费用不可审计、不可限流 | **高** | P0-2 [V/d] |
| R2 | 证据无法按流水线顺序落库 → 2.4 起被迫绕道，污染追溯根基 | **高** | P0-1 [V] |
| R3 | 删选题/删合集触发 CASCADE，连带删除脚本与发布效果数据（不可恢复） | **高** | P1-3、P1-4 [V] |
| R4 | 无 WAL / busy_timeout=0，2.4 引入 worker 后频繁 `database is locked` | 中高 | P1-6 [V] |
| R5 | 权限只有文档，Agent 一旦能写库即等于全表可写 | 中高 | P1-7 [V] |
| R6 | 无鉴权 + 若监听 0.0.0.0 → 局域网可写入 | 中 | P1-7 [V] |
| R7 | 38 表一次性迁移失败且回滚未演练 | 中 | P1-2、P2-12、P2-15 [d] |
| R8 | 文档漂移（状态名/量纲/字段名/计数不一致）→ 后续按文档实现必然出错 | 中 | P2-1/2/3、P3-1 [V] |
| R9 | `topic_columns` 与 `content_taxonomy.yaml` 双源无校验 → 合集身份漂移 | 中 | P1-8 [V] |
| R10 | 设计中的新目录（execution/capabilities/parsers）不存在，2.0 若不落地空壳，2.1 起边写边改结构 | 低中 | §3.1 [V] |

---

## 11. 问题整改清单

> 每条包含：编号 / 严重程度 / 位置 / 依据 / 后果 / 建议 / 是否 M2 前必须解决。证据等级见 §0。

### P0（2 项）

**P0-1 证据落库顺序被 CHECK 约束阻断** — [V]
- 位置：真实库 `ck_evidence_has_claim_target`；`docs/research_hub/database_design.md` §5（未包含该约束变更）
- 依据：`CHECK (knowledge_item_id IS NOT NULL OR research_report_id IS NOT NULL)`；DAG 为 extract → synthesize
- 后果：2.4 抽取到的证据无法落库，被迫建空报告或塞 JSON，追溯体系失效
- 建议：采用方案 A（`evidence` 增 `research_task_id`，CHECK 改三选一），在 2.0 同一次 batch 迁移内完成
- **M2 前必须解决：是**

**P0-2 记账与预算强制点晚于首次真实模型调用** — [V/d]
- 位置：`implementation_roadmap.md`（2.5 才建 `model_calls`）vs `database_design.md` §6.2（2.0 建 19 表）；`config/settings.yaml` 无 `cost` 段
- 后果：2.1 起产生不可审计、不可限流的真实调用；成本失控无法事后追认
- 建议：`model_calls` 提到 2.1 的第一次迁移；`ModelGateway.call()` 强制"预算检查 → 调用 → 记账"；预算不可用时硬失败 `budget_blocked`；补三条预算拒绝测试
- **M2 前必须解决：是**

### P1（8 项）

**P1-1 `review_records` 在数据库设计中完全缺席，且 target_type 只支持 3 类** — [V/d]
- 位置：真实库 `ck_review_records_target_type IN ('knowledge_item','research_report','script')`；`database_design.md` 全文 **0 次**提及 `review_records`（grep 验证），§1 ER 图也未画
- 后果：Fact Checker 对单个 `finding` 的裁决、Video Producer 的 QC 记录、内容成品的审核记录**无处可存**；一旦写入 content_item 目标会触发 CHECK 失败
- 建议：① 在 `database_design.md` 补一节说明 `review_records` 的定位（弱多态审核流水）；② 2.7/2.9 前把 target_type 扩为 `('knowledge_item','research_report','script','content_item','media_asset','finding')`（batch ALTER）；③ 或改用强 FK 分表（见 P3-2）
- **M2 前必须解决：否（2.5 前必须）**

**P1-2 迁移节奏三处口径冲突** — [V/d]
- 位置：`database_design.md:556`「单次 revision，两段式」 vs `m1_delivery_checklist.md:108/120`「分五次」 vs `acceptance_criteria.md:163`「推荐分 5 次」 vs `implementation_roadmap.md`「2.0 迁库 ❌，2.2/2.3/2.4/2.5/2.7/2.9 各迁一次」
- 后果：2.0 到底建 19 表还是 0 表无人能定，迁移风险敞口（19 表 + 5 ALTER 一次做）不可控
- 建议：采用 roadmap 版本（**分 5 次**，2.0 只建骨架不迁库），把 `database_design.md` §6.2 改写为"按阶段切片"，并给出每次迁移的表清单与回滚点
- **M2 前必须解决：是**

**P1-3 `topic_collections.column_id` 使用 CASCADE，与主数据语义相反** — [V/d]
- 位置：`database_design.md:187-188`；对照真实库 `topics.column_id → topic_columns.id ON DELETE SET NULL` [V]
- 后果：删一个合集 → 所有选题的合集归属被静默删除（且不可恢复）
- 建议：改为 `ON DELETE RESTRICT`（推荐，主数据不允许删除、只允许停用 `is_active`）或 `SET NULL`；并补"删除合集必须先停用"的守卫
- **M2 前必须解决：是（在 2.0 迁移写死，否则后期改要重建表）**

**P1-4 既有 CASCADE 链未评估，与"SET NULL 保命"原则矛盾** — [V/d]
- 位置：真实库 `scripts.topic_id`、`publications.script_id`、`media_assets.script_id`、`evidence.knowledge_item_id`/`research_report_id` 全部 CASCADE [V]；`database_design.md:644` 宣称"不做物理级联删除 publications/scripts，用 SET NULL 保命"
- 后果：删一个选题 → 连带删脚本 → 连带删已发布记录与全部效果数据（`publications.metrics`），历史效果数据永久丢失；文档承诺与事实不符
- 建议：2.0 一并评估把 `scripts.topic_id`、`publications.script_id` 改为 `RESTRICT`（或软删除 `is_archived`）；至少 `publications` 必须改为 RESTRICT/SET NULL，因为它承载不可再生的效果数据。**属既有表约束变更，需用户明确批准**
- **M2 前必须解决：是（需批准）**

**P1-5 `topic.discover` 不在 task_type 目录中** — [V/d]
- 位置：`module_design.md:45`（唯一出现处，grep 验证 [V]）；九种 task_type 与 routing 白名单均无
- 后果：选题发现（RSS / 知识缺口）在 Agent 层被拒；M8 选题中心前置能力缺失
- 建议：二选一并在 `agent_architecture.md` 写死：① 新增 `topic.discover`（由 financial_researcher/industry_researcher 承接）；② 明确它是 orchestrator 的内部函数（不产生 agent_runs）
- **M2 前必须解决：否（2.7 前必须）**

**P1-6 SQLite 未开 WAL、busy_timeout=0** — [V]
- 位置：`backend/app/database/session.py` connect 事件仅 `PRAGMA foreign_keys=ON`；实测 `journal_mode=delete`、`busy_timeout=0`
- 后果：2.4 引入进程内 worker 后，API 写与 worker 写并发时立即 `database is locked`；读请求也可能被写事务阻塞
- 建议：connect 事件补 `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`；2.4 验收加"并发写不报锁"测试；同时定义租约续租周期（120s / 30s 续租）与幂等要求
- **M2 前必须解决：否（2.4 前必须）**

**P1-7 权限与调用者身份无代码落点** — [V/d]
- 位置：`backend/app/api/routes/*` 无任何鉴权依赖；无 `CallerContext`/`assert_scope`/`db_scopes` 实现；`api_design.md` §2.3 只有文档
- 后果：M2 一旦实现 `db_write`，Agent 即可写任意表；human/agent/dev-only 的区分无法执行
- 建议：2.0 先落地 `CallerContext`（header `X-Caller` + 服务端白名单）与 `assert_scope(caller, table)`，所有写端点强制依赖；dev 端点在 `app_env=production` 时 404
- **M2 前必须解决：是**

**P1-8 合集身份双源无校验** — [V/d]
- 位置：`topic_columns` 真实列只有 code/name/description/display_order [V]；政策字段全在 `config/content_taxonomy.yaml`；§5 声明 topic_columns「明确不改」；§6.4 校验只查 `count(*) >= 6`
- 后果：YAML 改合集 id → 旧行残留成孤儿，历史 `topic_collections` 指向孤儿；M8 选题中心按合集筛选会漏
- 建议：① 明确"DB 存身份、YAML 存政策"的分工并写入文档；② seed 脚本增加"YAML 有但 DB 无 → 新建；DB 有但 YAML 无 → `is_active=0`（不删）"；③ §6.4 校验改为"DB code 集合 == YAML id 集合"
- **M2 前必须解决：否（2.0 迁移时一并做最省）**

### P2（13 项）

| 编号 | 问题 | 位置 | 建议 | 证据 |
|---|---|---|---|---|
| P2-1 | 评分量纲矛盾：六维 0–1、权重和 1.0，但阈值写 `3.5/5` | `state_machine.md:52` vs `database_design.md` §4.4 / `module_design.md:55` | 统一为 0–1 且阈值写 `0.70`（=3.5/5）；`topics.score_total` 加 `CHECK (0..1)` | [V] |
| P2-2 | 状态名不一致：`idea → approved`、research task `"draft"` | `api_design.md:68,211` | 改为 `candidate → … → approved`、`created` | [V] |
| P2-3 | 预算字段命名不一致且无映射 | `api_design.md:202,214` `budget_max_cost_cny`/`budget_used_calls` vs `database_design.md:243,244` `budget_max_cost`/`calls_spent` | 统一为 DB 名，API 层不重命名；币种字段单独 `currency` | [V] |
| P2-4 | `POST /api/sources/upload` 未进端点目录 | `module_design.md:86`（唯一处） | 补入 `api_design.md` D 组并写明 multipart 与大小上限 | [V] |
| P2-5 | 证据定位字段新旧重叠，无共存规则 | 真实库 `page_number`/`quote_locator` [V] vs 新增 char_start/char_end/quote_hash | 明确"新列优先、旧列 deprecated、写入时双写、读取时新列优先" | [V/d] |
| P2-6 | `research_reports.findings`(JSON) 与 `report_findings` 表双写，权威源未定义 | `database_design.md` §4.13 vs 真实 `findings JSON NOT NULL` [V] | 声明表为权威、JSON 为渲染快照；2.5 加一致性校验 | [V/d] |
| P2-7 | `agent_runs.model_provider/model_name` 与 `model_calls` 重叠 | 真实库 [V] | agent_runs 只留汇总，细粒度归 model_calls | [V] |
| P2-8 | `uq_platform_item(platform, platform_item_id)` 因列可空而失效（NULL 互不相等） | 真实库 [V] | 用部分唯一索引 `WHERE platform_item_id IS NOT NULL` | [V] |
| P2-9 | 部分唯一索引 Alembic 不可识别 → `alembic check` 静默漂移 | `database_design.md` §7 | 手写 `op.create_index(..., sqlite_where=sa.text("is_primary = 1"))`；校验清单加"部分索引存在"断言 | [d] |
| P2-10 | `companies` 与 `institutions` 语义重叠，无判定规则 | `database_design.md` §4.17 vs 真实 `institutions` [V] | 二选一或明规则：`institutions`=金融机构/联赛/俱乐部，`companies`=一般经营主体；或合并并加 `is_commercial_entity` | [V/d] |
| P2-11 | `task_events` 无序号列（顺序靠 created_at）；`script_evidence_links` 无唯一约束 | `database_design.md` §4.8/§4.19 | 加 `event_seq` + `uq(research_task_id, event_seq)`；加 `uq_script_evidence(script_id, segment_index, finding_id, evidence_id)` | [d] |
| P2-12 | `data/backups/` 不存在，备份命令未实测 | `database_design.md` §6.5；实测目录缺失 [V] | 2.0 前实跑一次 `sqlite3 .backup` 并记录命令与产物大小 | [V] |
| P2-13 | `summary`/`limitations` NOT NULL 迫使报告一次写全，无法建壳 | 真实库 [V] | 若采纳 P0-1 方案 A 则无需建壳；否则需放宽为可空 + 完成态校验 | [V] |

（另含 P2-14、P2-15、P2-16 三条，见下）

| 编号 | 问题 | 建议 | 证据 |
|---|---|---|---|
| P2-14 | 部分唯一索引保住了"至多一个 primary"，但没保住"至少一个"合集，与 taxonomy 要求的 1–3 个矛盾 | 服务层强制 + 迁移后校验"0 合集选题数 == 0" | [d] |
| P2-15 | "迁移执行到一半中断"的恢复路径未演练 | 2.0 在临时库演练一次中断 + 恢复，并记录 `alembic_version` 状态 | [d] |
| P2-16 | `quote_mismatch` 只标记不阻断；`evidence.claim_nature` 与 `FindingTier` 映射未定义 | `verified_fact` 出现 quote_mismatch 必须 fail 该 finding；在 fact_checker 定义里写死映射表 | [d] |

### P3（5 项）

| 编号 | 问题 | 位置 | 建议 |
|---|---|---|---|
| P3-1 | 文档计数漂移：19 表 vs ER 画 17 张；"10 份文档"实为 11 份而目录已有 13 份；`sources` 14 vs 15 字段；Topic Hub 11 vs 14 端点；`current_state_audit.md:65-67` "5 张…精确说 7 张" 自相矛盾 | 多份 | 一次性对齐；并在 `acceptance_criteria.md` §4 补入新增两份文档 | [V] |
| P3-2 | `review_records` 弱多态（target_type + target_id）无 FK → 孤儿记录 | 真实库 [V] | 保留弱多态但在 2.5 加"孤儿审核记录"巡检查询 | [V] |
| P3-3 | `evidence` 无"被引用次数"反向统计，规模上来后 `finding_evidence` 反查变慢 | 设计 [d] | 先在 2.5 观察，十万行后再考虑 `usage_count` | [d] |
| P3-4 | 新增合集 `code` 的 `VARCHAR(32)` 上限与新合集命名约束未声明 | `database_design.md` §4.2 / taxonomy `extension_policy` | 在 taxonomy 里写死"code ≤ 32 字符、小写下划线" | [V/d] |
| P3-5 | `tool_calls` 第一阶段只有 3 个真工具，可能长期空表 | 设计 [d] | 保留但在文档标注"预期前两阶段近乎空表" | [d] |

---

## 12. 推荐整改顺序

原则：**先定不可逆的，再定便宜的，最后才写代码。**

| 步骤 | 内容 | 覆盖问题 | 产物 | 预估 |
|---|---|---|---|---|
| **R1 决策包** | 四项待人工批准：① 迁移分 5 次（roadmap 版）是否确认；② P0-1 采用方案 A/B/C；③ 既有 CASCADE 是否改 RESTRICT（涉已发布数据）；④ 是否顺带修 `backend/app/main.py:27` 旧文案 | P0-1、P1-2、P1-4 | 决策记录（写入 `docs/research_hub/README.md` 决策区） | 需人工 |
| **R2 术语与量纲统一** | 纯文档修订：状态名、评分量纲、预算字段名、计数漂移 | P2-1/2/3、P3-1、P3-4 | `state_machine.md`/`api_design.md`/`acceptance_criteria.md` 修订 | 0.5 天 |
| **R3 数据库设计修订** | 写入 P0-1、P1-1、P1-3、P1-4、P1-8、P2-5/6/8/9/11/13/14 的处理结论；§6.2 改为阶段切片 | 数据库类全部 | `database_design.md` v2 | 0.5–1 天 |
| **R4 路线图与成本设计修订** | `model_calls` 与预算闭环提前到 2.1；迁移编号重排；补 `settings.yaml: cost` 段设计 | P0-2、P1-2 | `implementation_roadmap.md` v2 | 0.5 天 |
| **R5 2.0 骨架落地**（需批准后） | `execution/`、`capabilities/` 空壳带测试；`CallerContext`+`assert_scope`；WAL/busy_timeout；seed 脚本 + 一致性校验；备份实跑 | P1-6/7/8、P2-12/15 | 代码 + 测试 | 1–2 天 |
| **R6 2.1 模型接入**（需批准后） | `ModelGateway` + `model_calls` + 三层预算硬失败 + 三条拒绝测试 | P0-2 收尾 | 代码 + 测试 | 1–2 天 |
| R7 | 之后按 2.2 起逐阶段推进 | 其余 | — | — |

**不要做的事**：不要在 R1–R4 完成前先写 2.1 的 `ModelGateway`。那会让 P0-2 从"设计问题"变成"代码返工"。

---

## 13. 对 M2 是否具备启动条件的判断

### 13.1 判断

**结论：有条件启动 —— 当前不建议直接进入 M2 编码；建议先完成 R1–R4（约 1.5–2 天，几乎全是文档修订 + 一次人工决策），再进 R5/R6。**

理由分三层：

1. **能力层面已具备**：M1 文档 13 份、覆盖 12 个必答问题、无虚报、无占位冒充、96 测试全绿、代码零改动 [V]。作为"施工图"它够格。
2. **约束层面未闭合**：2 个 P0 中有 1 个（P0-1）是**数据库 CHECK 与流水线顺序的硬冲突**，另 1 个（P0-2）是**记账能力排在花钱之后**。这两个都在"第一次迁移"和"第一次真实调用"的路径上，属于不可延后项。
3. **执行层面空白**：权限、成本、调用者身份目前 0 行代码（P1-7）。M2 若先写能力再补权限，将改动每个服务签名。

### 13.2 M2 启动前的最小关闭清单（Must-Close List）

| # | 项 | 对应 | 关闭判据 |
|---|---|---|---|
| 1 | 迁移节奏定案（分 5 次） | P1-2 | 三份文档措辞一致，且 `database_design.md` §6.2 按阶段切片 |
| 2 | 证据归属方案定案 | P0-1 | 选 A/B/C 并写入 §4/§5 |
| 3 | `model_calls` + 预算闭环前移 | P0-2 | roadmap 2.1 含该表与三条拒绝测试 |
| 4 | 既有 CASCADE 处置定案 | P1-4 | 明确哪些表改 RESTRICT 并获批准 |
| 5 | `CallerContext` 设计定稿 | P1-7 | `api_design.md` 给出可实现的签名与依赖注入方式 |
| 6 | WAL/busy_timeout 写入 2.0 任务 | P1-6 | 2.0 验收含"并发写不报锁" |

以上 6 项全部有明确判据，且**除第 4 项外都不需要写业务代码**。

### 13.3 明确不该做的事（本轮之后仍然有效）

- ❌ 不要在 M2 启动时就一次性建 19 张表；
- ❌ 不要在没有 `model_calls` 的情况下做第一次真实模型调用；
- ❌ 不要把 `RecordingDispatcher` 的 `agent_runs(status=pending)` 当作"Agent 已运行"的证据；
- ❌ 不要把 `NullSearchProvider` 的 `not_configured` 当作"搜索已接通"；
- ❌ 不要在权限落地前实现 `db_write`。

---

## 附录 A：本次审查实跑记录（只读）

```
$ git status --porcelain                 -> 空
$ git rev-parse HEAD origin/main         -> 0029c3e2... / 0029c3e2...
$ git rev-list --left-right --count ...  -> 0  0
$ alembic heads                           -> de8148733a9b (head)
$ alembic current                         -> de8148733a9b (head)
$ alembic check                           -> No new upgrade operations detected.
$ pytest -q                               -> 96 passed, 1 warning
$ sqlite3 data/finance_media.db "select count(*) from sqlite_master where type='table'"
                                          -> 19
$ 各表行数                                 -> 除 alembic_version=1 外全部 0
$ PRAGMA journal_mode                     -> delete
$ PRAGMA busy_timeout                     -> 0
```

外部依赖（模型 / 搜索 / OAuth）**本轮全部未触发**，因此本报告中所有"能力可用性"结论均为 [d] 或 [u]，没有一条声称"实测通过"。

## 附录 B：复现命令

```powershell
# 1. 只读核对
git rev-parse --show-toplevel; git status --porcelain; git log --oneline -3
.\.venv\Scripts\alembic.exe heads; .\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe check

# 2. 库结构（只读）
.\.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/finance_media.db');print([r[0] for r in c.execute(\"select name from sqlite_master where type='table' order by name\")])"

# 3. 测试
.\.venv\Scripts\python.exe -m pytest -q

# 4. 约束原文
.\.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/finance_media.db');print(c.execute(\"select sql from sqlite_master where name='evidence'\").fetchone()[0])"
```

## 附录 C：本轮未做（按纪律）

- 未修改任何业务代码、数据库结构、迁移文件、配置文件
- 未执行任何 Alembic 迁移
- 未写入任何业务数据
- 未调用任何付费 API 或外部服务
- 未修改密钥/Token/环境变量/远程配置
- 未提交、未推送（`git status` 只有本报告一个未跟踪文件）
- 未进入 M2
