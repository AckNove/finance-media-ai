# 统一选题研究系统 —— 架构设计总说明

> **文档性质**：第二阶段**架构设计**产出，不含任何可执行功能。
> **代码状态**：本次未修改 `backend/`、数据库模型、Alembic 迁移与 API 行为。
> 文中所有流程、示例数据、评分、耗时、成本均为**设计说明或估算**，不是真实执行结果。

---

## 1. 这份文档集要解决什么问题

第一阶段已经交付：可运行的 FastAPI 服务、19 张表的数据库、Alembic 初始迁移、服务层、
10 类 API、9 个 Agent 角色定义与统一协议、内容与视频流程骨架、96 个通过的测试。

但第一阶段**没有任何真实的"研究"能力**：

- 没有调用过任何大模型；
- 没有搜索过任何网页；
- 没有解析过任何 PDF/Excel；
- `RecordingDispatcher` 只是把任务写进 `agent_runs`（`status=pending`），然后停在那里。

因此第二阶段要回答的核心问题是：

> **如何把「选题 → 资料 → 证据 → 分析 → 报告 → 内容 → 发布」变成一条真实可执行、
> 可追溯、可控制成本、不会把推测写成事实的生产线？**

本目录的 17 份文档就是这条生产线的完整设计（M1 出设计，M1.1 做审查，M1.2 做修订与决策固化，M1.2-final 关闭 Q5–Q9 并补齐证据定位规范）。

---

## 2. 系统一句话定位

一个 **本地优先、单机部署、单体架构** 的 AI 研究工作台：

- 人负责方向、观点、事实复核与最终发布；
- AI 负责搜集、解析、提取、分析、起草；
- 所有关键结论都能回溯到"某个资料的某个片段"；
- 所有 AI 输出都带身份标签（谁生成、用哪个模型、花了多少钱）。

---

## 3. 七大模块与四个基础层

### 3.1 七个业务模块

| # | 模块 | 中文名 | 核心职责 | 主要数据表（现有 / 新增） |
|---|---|---|---|---|
| 1 | Topic Hub | 选题中心 | 发现、评分、去重、合并、审阅选题 | `topics`、`topic_columns` / `topic_collections`、`topic_tags`、`tags`、`topic_score_snapshots` |
| 2 | Research Hub | 研究任务中心 | 拆解问题、编排 Agent、跟踪进度、成本控制 | `research_tasks`、`research_subtasks`、`research_subtask_dependencies`、`task_events` / `agent_runs` |
| 3 | Source Hub | 资料中心 | 搜索、抓取、上传、解析、留存快照 | `sources` / `source_snapshots`、`source_extractions`、`source_summaries`、`research_task_sources` |
| 4 | Evidence Hub | 证据中心 | 事实提取、片段定位、交叉验证、冲突标记 | `evidence`（扩列）、`finding_evidence` |
| 5 | Analysis Hub | 分析中心 | 分档结论、研究框架、报告生成 | `research_reports` / `report_findings`、`research_reports.findings` |
| 6 | Knowledge Hub | 知识中心 | 长期知识沉淀与检索 | `knowledge_items`、`knowledge_relations`、`institutions`、`industries`、`financial_*` / `companies` |
| 7 | Content Hub | 内容生产中心 | 报告 → 多形态内容、脚本、审核、发布 | `scripts`、`media_assets`、`publications` / `content_items`、`script_evidence_links` |

### 3.2 四个横切基础层

| 层 | 职责 | 关键组件 |
|---|---|---|
| 模型适配层 | 屏蔽供应商差异、计量 token 与费用 | `ModelGateway` + `model_calls` |
| 工具层 | 让 Agent 真正能"做事"而不是"说它能做" | `ToolRegistry` + `tool_calls` |
| 执行层 | 进程内任务队列、租约、重试、人工暂停 | `TaskRuntime` + `research_tasks` 状态机 |
| 审计层 | 状态流转留痕、成本可查、结论可追溯 | `task_events`、`review_records`、证据链 |

---

## 4. 文档地图

| 文档 | 内容 | 什么时候读它 |
|---|---|---|
| [`m1_delivery_checklist.md`](m1_delivery_checklist.md) | **M1 交付记录**：Goal 能力自检 14 项、任务书逐条对照、实测证据、风险 | 想知道"M1 做完了没有、哪些是设计" |
| [`m1_1_architecture_review.md`](m1_1_architecture_review.md) | **M1.1 架构审查报告**：2 P0 + 8 P1 + 16 P2 + 5 P3、31 条整改清单、M2 启动判断 | 想知道"设计有哪些硬伤" |
| [`m1_2_decision_register.md`](m1_2_decision_register.md) | **M1.2 决策登记表**：D1–D12 决策、每条的依据、代价、待人工确认项 | 想知道"为什么这么定" |
| [`m1_2_delivery_report.md`](m1_2_delivery_report.md) | **M1.2 交付报告**：31 条整改逐项状态、跨文档复检结果、M2 判断 | 想知道"整改到底改完没有" |
| [`m1_2_final_review.md`](m1_2_final_review.md) | **M1.2-final 收尾报告**：Q5–Q9 人工决策如何落地、Q7 批次冲突修正、证据定位规范、文件与 Git 核对 | 想知道"最后这轮改了什么、还剩什么" |
| [`current_state_audit.md`](current_state_audit.md) | **现状审计**：逐项标记真实实现 / 仅数据结构 / 仅 Prompt / 未实现 | 想确认"现在到底有什么" |
| [`system_architecture.md`](system_architecture.md) | 总体架构、分层、运行时、部署形态、技术选型与理由 | 想理解系统骨架 |
| [`business_workflow.md`](business_workflow.md) | 端到端业务流程 +《商业银行是如何赚钱的》完整推演 | 想知道"一条内容是怎么做出来的" |
| [`database_design.md`](database_design.md) | 现有 ER 图、目标 ER 图、19 张新表字段、**六批次迁移**、删除语义、quote_hash 算法 | 要动数据库之前必读 |
| [`module_design.md`](module_design.md) | 七大模块（A–G）逐条能力：现状 → 目标设计 → 表/端点 → 阶段 | 想知道某个功能归哪张表、哪个阶段 |
| [`agent_architecture.md`](agent_architecture.md) | 9 个 Agent 的边界、输入输出、权限、失败处理、**task_type 单一注册表** | 要写 Agent 代码之前必读 |
| [`tooling_architecture.md`](tooling_architecture.md) | 10 个工具的**现状分级**、接口契约、**CallerContext 权限**、错误、费用、逐工具测试方法 | 要接工具之前必读 |
| [`api_design.md`](api_design.md) | 新增 API 清单、请求/响应示例、**CallerContext 权限与错误码总表** | 要写后端接口之前必读 |
| [`state_machine.md`](state_machine.md) | 八套状态机（含 `model_calls` 记账态）与守卫条件 | 要写状态流转代码之前必读 |
| [`implementation_roadmap.md`](implementation_roadmap.md) | 2.0–2.9 分阶段开发计划、依赖、迁移、验收、成本 | 要排期之前必读 |
| [`acceptance_criteria.md`](acceptance_criteria.md) | 每个阶段的客观验收标准（可测试） | 每阶段结束时对照打勾 |
| [`m2_0_implementation_plan.md`](m2_0_implementation_plan.md) | **M2.0 实施计划**：现状审查、G-01–G-19 差距、技术裁决、T01–T12 任务卡、依赖链、验收命令、风险、待批项 | 要开工 2.0 之前必读 |
| [`m2_0_delivery_report.md`](m2_0_delivery_report.md) | **M2.0 交付报告**：T01–T12 完成情况、文件清单、调用关系、测试与迁移演练证据、开发库未改证明、剩余风险 | 想知道"2.0 到底交付了什么" |
| [`first_video_pipeline_report.md`](first_video_pipeline_report.md) | **V1 首条视频闭环**：MarkItDown/Anthropic Skills 复用、真实 PDF·Excel·CSV 解析、SAPI+FFmpeg 成片 | 想知道引擎最初怎么搭起来的 |
| [`video_pipeline_v2_1_report.md`](video_pipeline_v2_1_report.md) | **V2.1 引擎能力演示**：OpenKatana 接入、动态画板、时间轴、QC、模型候选与价格 | 想知道"能不能做出动态视觉" |
| [`video_pipeline_v2_2_report.md`](video_pipeline_v2_2_report.md) | **V2.2 Phase 1**：FFmpeg 8.1.2 独立安装、26 项能力自检、12 步真机验证 | 想知道媒体工具链的可信基线 |
| [`phase2_media_adapter_report.md`](phase2_media_adapter_report.md) … [`phase2_decision_register.md`](phase2_decision_register.md) | **V2.2 Phase 2 八份文档**：适配层、导入测试、分镜 schema、时间轴集成、素材溯源、演示片、质检、决策登记 | 要接手视频管线之前必读 |
| 本文 `README.md` | 总说明与索引 | 从这里开始 |

---

## 5. 三条不可妥协的设计底线

1. **可追溯**：报告里的每一条"事实"，必须能落到 `report_findings → finding_evidence → evidence → source_snapshots → 原始字节文件`。
   只存一个 URL 不算证据。
2. **不伪装**：AI 摘要、AI 推断、AI 起草必须与原始资料、已验证事实在**数据层面**分开（不同表 / 不同字段 / 不同枚举值），
   不允许出现"AI 摘要被当成来源"的情况。
3. **不越权**：任何内容都不能自动发布；任何个股买卖建议都不得生成；涉及人工复核触发器时必须停下等人工。

---

## 6. 与第一阶段文档的关系

- 第一阶段的 `docs/architecture.md`、`docs/database_schema.md`、`docs/agent_manual.md`、`docs/workflow.md`
  描述的是**已实现的现状**，保持有效，不回改。
- 本目录描述的是**目标架构**（第二阶段的建设目标），与现状的差异集中在
  [`current_state_audit.md`](current_state_audit.md) 与 [`implementation_roadmap.md`](implementation_roadmap.md)。
- 品牌、合集、合规规则以 `config/brand.yaml`、`config/content_taxonomy.yaml`、`config/content_rules.yaml` 为准。

---

## 7. 明确不做（本阶段与架构设计范围内）

- 不实现任何功能代码（本次只出设计）；
- 不引入向量数据库、消息队列、K8s、微服务；
- 不接付费搜索 / 付费云服务（只给出可选项与成本，不采购）；
- 不做多用户、不做权限体系（单用户本地系统，鉴权留到需要时）；
- 不做视频渲染（内容中心的视频只到"分镜/素材/脚本"为止）。
