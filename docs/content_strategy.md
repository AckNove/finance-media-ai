# 内容战略（正式版 v2.0）

> 生效日期：2026-11-09。机器可读配置：`config/content_taxonomy.yaml`（分类、标签、形式）
> 与 `config/content_rules.yaml`（证据、风格、审核硬规则）。位置：本文件负责"做什么内容、怎么分类"。

## 1. 内容主线：金融与商业并重

- **金融线**：金融知识、财经时事、金融案例 → 讲"世界怎么运转"。
- **商业线**：产业链、公司解剖、商业案例 → 讲"钱在哪里、为什么在那里"。
- 足球线（足球金融与商业）既是独立商业研究领域，也是金融教学的类比场景。

账号不做"新闻搬运"，也不做"财经八卦"。每条内容必须回答：
**这里发生了什么 → 为什么发生 → 谁受益谁付代价 → 这个机制还能用在哪。**

## 2. 六大长期内容合集

合集是**内容组织方式，不是独立账号**。一个账号、六类内容；合集可增加，不设上限。

| # | id | 合集 | 使命（为什么存在） | 主责 Agent | 适配模板 | 适配形式 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `financial_knowledge` | 金融知识 | 建立概念底座：货币、利率、汇率、产品、机构、市场机制 | `financial_researcher` | `financial_mechanism` | 知识视频 / 热点短视频 / 图文 / 系列 / 课程 |
| 2 | `market_briefing` | 财经时事 | 把新闻从"发生了什么"推进到"为什么、谁受益、往哪走" | `financial_researcher` | `financial_mechanism` | 热点短视频 / 知识视频 / 图文 |
| 3 | `industry_research` | 产业链研究 | 回答"钱在链条哪一段被赚走" | `industry_researcher` | `industry_research` | 知识视频 / 纪录片 / 报告 / 系列 / 会员 |
| 4 | `company_teardown` | 公司商业解剖 | 拆开公司：靠什么赚钱、靠什么活着、靠什么长大 | `industry_researcher` | `industry_research` / `case_documentary` | 知识视频 / 纪录片 / 报告 / 会员 |
| 5 | `football_finance` | 足球金融与商业 | 用高关注场景讲金融；足球本身也是独立研究领域 | `football_researcher` | `football_explainer` | 热点短视频 / 知识视频 / 纪录片 / 系列 |
| 6 | `business_cases` | 金融与商业案例 | 用真实事件证明规律如何运作、人如何犯错 | `case_analyst` | `case_documentary` | 纪录片 / 知识视频 / 报告 / 系列 / 会员 |

各合集的 `scope`（覆盖范围）、`boundary`（明确不做什么）、`typical`（典型选题示例）、
`risk`（特有风险）在 `config/content_taxonomy.yaml` 中逐条定义，这里不重复。

**边界的三个典型例子**

- 金融知识不做"哪个产品更好"的结论；
- 财经时事不做短期点位预测、不做新闻搬运；
- 公司解剖不做个股推荐、不把管理层叙事当事实。

## 3. 多标签分类规范

一个选题可以同时属于多个合集（例如"某俱乐部财务造假"同时属于 `football_finance` 与
`business_cases`），因此系统必须支持多标签。

**规则**

1. 每个选题 1–3 个合集，且必须指定一个 `primary`（主合集）。
2. 标签 0–6 个，取自六个维度：`instrument`、`institution_type`、`industry`、
   `geography`、`period`、`mechanism`（详见 taxonomy 文件）。
3. 标签用于检索与串联，**不得替代合集**（"足球"不是合集，`football_finance` 才是）。
4. 合集 id 稳定，不随中文改名变化；新增合集不得删除已有合集。

**当前实现状况（诚实说明）**

| 项 | 现状 |
| --- | --- |
| 数据库 | `topics` 表只有 `column_id`（指向 `topic_columns`，单选）与 `knowledge_domain`（单选） |
| 是否已支持多合集 | **否**。第一阶段数据库不做改动（本次任务明确禁止改库） |
| 临时替代做法 | `column_id` 填 `primary` 合集对应的栏目；其余合集与标签写在选题描述与 `content/topics/<slug>.md` 的 front matter 中 |
| 二期方案 | 新增 `topic_collections(topic_id, collection_id, is_primary)` 与 `topic_tags(topic_id, dimension, value)`，属数据库变更，**需用户确认后执行** |

## 4. 内容形式矩阵

| 形式 | 时长 | 模板 | 平台 | 要求（摘要） |
| --- | --- | --- | --- | --- |
| `short_news` 热点短视频 | 60–120s（默认 90） | financial_mechanism / football_explainer | 抖音、小红书 | 8 秒钩子；只讲一个机制或一个数字；必须给数据时点 |
| `knowledge_video` 金融知识视频 | 120–300s（默认 180） | financial_mechanism / football_explainer / industry_research | 抖音、小红书、bilibili | 讲完一个完整机制（参与者/资金流/定价/风险） |
| `documentary` 深度纪录片 | 300–900s（默认 480） | case_documentary / industry_research | bilibili、youtube | 时间线 + 关键数字 + 争议点 + 启示 |
| `article` 图文 | — | — | 微信公众号、小红书 | 图表与来源齐全，可转成脚本 |
| `research_report` 研究报告 | — | — | — | 落库 `research_reports`，含 findings/limitations/来源 |
| `series` 系列专题 | — | — | — | ≥3 集，单集可看、整体递进 |
| `member_research` 会员深度研究 | — | — | — | 数据可复核，含免责声明与利益披露 |
| `course` 系统课程 | — | — | — | 有学习路径与作业，不含个股建议 |

**选择原则**：不要求每个选题都产出所有形式。选题价值决定产品形态——
有时效性的做短视频，有机制深度的做知识视频，有叙事张力的做纪录片，
有数据积累的做报告与会员内容。

## 5. 研究报告与内容产品的关联

内容生产不允许"直接开写"。必须走的链路：

```
选题(topic, 含 collections)
  → 原始资料(sources) + 事实证据(evidence, supports/refutes)
  → 研究报告(research_reports: findings + limitations, 标注 fact/inference/opinion)
  → 审核(review_records: professional → compliance → final)
  → 脚本(scripts: topic_id + research_report_id)
  → 素材(media_assets) → 发布(publications: script_id + script_version)
```

- **一份报告可以派生多条内容**（短视频 / 长视频 / 图文 / 课程一节），
  但每条内容都必须引用同一 `report_id` 与同一批证据，口径不得各版本不一致。
- 数据库字段：`research_reports.topic_id`、`scripts.topic_id`、`scripts.research_report_id`、
  `publications.script_id`、`publications.script_version`（已存在于第一阶段迁移
  `de8148733a9b`，本次未改动）。
- 追溯要求：`publications` 能一路回查到 `evidence` 与 `sources`。

## 6. 知识库与内容合集的关系（两个不同维度）

| 维度 | 取值来源 | 用途 |
| --- | --- | --- |
| `knowledge_domain` | `financial_instruments` / `financial_products` / `institutions` / `industries` / `football_business` / `financial_cases` / `general` | **知识条目**的学科归属（`knowledge_items` 表） |
| `collection` | 六大合集（taxonomy） | **内容产品**的栏目归属（选题与脚本层面） |

两者不是一一对应：一篇"可转债"知识条目（`financial_instruments`）可能同时被
`financial_knowledge`（讲机制）与 `market_briefing`（讲某次发行热）两个合集引用。
知识库是资产，合集是货架。

## 7. 生产原则（与 `docs/workflow.md` 配套）

1. 研究优先于制作：没有研究报告与证据，不进入脚本阶段。
2. 事实优先于观点：观点必须标注为 `opinion` 并有证据支撑。
3. AI 负责研究与制作，**人负责方向、观点、重要事实审核与最终发布审批**。
4. 不允许 AI 伪造用户表达过的观点；AI 生成的观点必须标注来源与不确定性。
5. 成果必须沉淀为可复用知识资产（`knowledge_items` + `knowledge/` 文稿）。

## 8. 与旧策略的差异

| 项目 | 旧 | 新 | 理由 |
| --- | --- | --- | --- |
| 合集数量 | 无正式合集概念（只有 8 个知识域 + 栏目表） | 六大长期合集，可扩展 | 需要稳定的内容组织骨架 |
| 商业线 | 未独立（产业链作为知识域之一） | 产业链 + 公司解剖 + 商业案例三个合集 | 商业研究升级为一等公民 |
| 足球 | 教学隐喻 | 独立合集 `football_finance` + 教学场景 | 足球商业本身有受众与研究价值 |
| 分类能力 | 单一 `column_id` | 多合集 + 6 维标签（规范已定，库待二期） | 选题天然跨合集 |
| 内容形式 | 只有四类视频模板 | 8 种形式，模板只是视频实现方式 | 形式与产品形态解耦 |
| 报告与内容关系 | 未明确定义 | 强制链路：报告 → 审核 → 脚本 → 发布，一报告多产品 | 保证可追溯、口径一致 |

## 9. 尚未解决的冲突（需要你确认）

1. **多标签分类需要数据库变更**：当前 `topics.column_id` 是单选，无法真正支持
   "一个选题多个合集"。本次按"不改库"的要求只做到规范与临时替代，二期需新增两张表。
2. **新旧合集 id 与 `topic_columns` 的关系**：是把六大合集注册成 `topic_columns` 记录，
   还是新建独立 `collections` 表？两种都可行，前者改动小、后者语义更清。
   建议二期做（需要一次迁移）。
3. **`knowledge_domain` 是否需要拆出 `concepts` 域**：`knowledge/README.md` 里有
   `concepts/` 目录但枚举只有 6 域 + `general`，目前概念类条目只能落在 `general`。
   这属于数据库枚举变更，本次未动，留待二期决定。
