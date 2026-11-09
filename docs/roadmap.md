# 项目路线图

> 更新时间：2026-11-09。上一版路线图信息散落在 `docs/phase1_acceptance.md`
> 的"下一阶段建议任务"里，本文件把它收敛为唯一版本；验收报告仍保留为历史记录。

## 阶段总览

| 阶段 | 名称 | 状态 | 交付物 |
| --- | --- | --- | --- |
| P0 | 品牌与战略定义 | **DONE（本次）** | 品牌、六大合集、标签规范、商业模式、路线图文档 |
| P1 | 基础设施 | **DONE（已验证）** | 18 表数据库 + 迁移、服务层、14 个 API、九 Agent 协议、工作流骨架、76 个测试 |
| P2 | 内容生产链路打通 | 未开始 | 脚本/审核/发布接口、研究报告 → 内容产品的可执行流程 |
| P3 | 模型与搜索接入 | 未开始 | 真实研究执行器（LLM + 受限搜索），取代 `RecordingDispatcher` |
| P4 | 视频生产 | 未开始 | 图表、配音、字幕、合成、自动质检 |
| P5 | 会员与课程 | 未开始 | 付费产品交付与运营 |

**硬性前提**：P2 之后每一步都需要用户明确批准；P3 涉及费用与外部账号，
不得擅自购买或注册。

---

## P0 品牌与战略（本次，DONE）

- 品牌：个人 IP「电力之王AK」+ 内容品牌「AK带你拆解世界」+ 主张/定位/使命/个性。
- 内容：六大合集、多标签规范、8 种内容形式、研究报告与内容产品的关联链路。
- 商业：四条收入线、合规红线、合作披露机制。
- 落地文件：`config/brand.yaml`、`config/content_taxonomy.yaml`、
  `config/content_rules.yaml`、`docs/brand_strategy.md`、`docs/content_strategy.md`、
  `docs/business_model.md`、`docs/roadmap.md`（本文件）。
- **本阶段未做**：任何数据库/API/业务逻辑改动、任何模型或搜索接入。

## P1 基础设施（已完成，历史记录）

18 张表 + Alembic 迁移 `de8148733a9b`、服务层、14 个 API、九角色提示词与统一协议、
内容/视频目录与规范、76 个 pytest 用例。详见 `docs/phase1_acceptance.md`。

---

## P2 内容生产链路（下一步，等你批准）

目标：让"选题 → 研究 → 脚本 → 审核 → 发布记录"在系统里真正跑通（仍不接模型）。

| 任务 | 交付物 | 验收 |
| --- | --- | --- |
| 2.1 选题多合集支持 | 迁移新增 `topic_collections(topic_id, collection_id, is_primary)`、`topic_tags(topic_id, dimension, value)` + 接口 | 一个选题能挂 2 个合集并检索出来（**需批准改库**） |
| 2.2 脚本接口 | `POST/PATCH /api/scripts`、版本自增、`topic_id` + `research_report_id` 关联 | 脚本能落库并被 `publications` 引用 |
| 2.3 审核接口 | `POST /api/reviews` 写入 `review_records`（professional/compliance/final） | 未 `pass` 的脚本无法进入 `approved` |
| 2.4 发布记录接口 | `POST /api/publications`（仅记录，不调平台 API） | 校验 `script_version` 已审批 |
| 2.5 知识文稿导入 | Markdown front matter → `knowledge_items` 的服务层 import | 双向一致，不允许"看起来已同步" |
| 2.6 内容台账 | `content/topics/<slug>.md` 与数据库字段的映射校验脚本（只读检查） | 差异可报告，不自动覆盖 |

首个验证用例：用 `financial_knowledge` 合集写一篇"可转债"内容，走完全链路。

## P3 模型与搜索接入（需费用与账号审批）

| 任务 | 说明 |
| --- | --- |
| 3.1 `LLMTaskDispatcher(TaskDispatcher)` | 组装 `prompts/*.md` + 角色 `PROMPT.md` → 调用模型 → 校验输出结构 → 写 `output_payload` |
| 3.2 受限来源抓取器 | 白名单 + `accessed_at` + `content_hash` 落库 |
| 3.3 `fact_checker` 结论落库 | 自动生成 `review_records` 草稿，仍需人工确认 |
| 3.4 成本与审计 | 每次调用记录 token/耗时/费用，配置在 `.env`（不入库） |

约束：密钥只通过环境变量；无 Key 时降级为 `UnavailableDispatcher`（503），
不允许返回假结果。

## P4 视频生产（P2/P3 之后）

| 任务 | 说明 |
| --- | --- |
| 4.1 图表生成 | Matplotlib（可选依赖 `charts`）生成产业链图、资金流向图 |
| 4.2 配音与字幕 | TTS + 时间戳 → SRT（规范见 `video_engine/subtitles/README.md`） |
| 4.3 渲染 | FFmpeg 合成 1080×1920/30fps（模板见 `video_engine/templates/`） |
| 4.4 自动质检 | 落地 `qc_checks.yaml` 的 12 项，`auto: true` 项先自动化 |

## P5 会员与课程（有稳定产能后）

先做会员研究报告，再做课程；上线前必须完成合规自查（见 `docs/business_model.md` §6）。

---

## 明确的非目标

- 不做独立 App、不做多账号矩阵、不做 SaaS 工具对外售卖。
- 不引入 Kubernetes / 微服务 / 独立向量数据库。
- 不做自动发布（任何平台都不自动推送）；发布永远由人工执行。
- 不做荐股、代客理财、个性化投资咨询（永久排除，不属于阶段问题）。

## 待你决定（阻塞后续排期）

1. P2 是否批准做数据库变更（多合集 + 标签两张表）。
2. P3 之前是否先手工跑 5–10 条内容，验证流程与风格再自动化。
3. 六大合集是否注册为 `topic_columns` 记录（改动小）还是新建 `collections` 表（语义清晰）。
4. 是否需要为"概念类知识条目"在 `knowledge_domain` 中新增 `concepts` 取值。
