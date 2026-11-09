# content/ —— 内容生产工作区

存放从选题到发布之间的所有产出物。数据库记录（`topics`/`scripts`/`publications`）
保存结构化字段与状态；本目录保存可人工编辑的正文文件。

## 子目录

| 目录 | 内容 |
| --- | --- |
| `topics/` | 选题卡（问题、合集、受众层次、类比要求、优先级） |
| `scripts/` | 脚本 Markdown（与 `scripts` 表版本对应） |
| `storyboards/` | 分镜表（每个镜头的时间、画面、屏上文字） |
| `assets/` | 图表、图示、音视频中间素材（大文件不入库，见 `.gitignore`） |
| `published/` | 已发布内容的快照与效果数据 |

## 命名约定

```text
topics/{topic_slug}.md
scripts/{topic_slug}/{template}-v{version}.md
storyboards/{topic_slug}/{template}-v{version}.yaml
published/{platform}/{YYYY-MM-DD}-{topic_slug}.md
```

`{template}` 取四类视频模板之一（`football_explainer` / `financial_mechanism` /
`industry_research` / `case_documentary`），与 `video_engine/templates/` 同名。
内容形式（60–120s 短视频 / 2–5min 知识视频 / 5–15min 纪录片 / 图文 / 报告 / 系列 /
会员 / 课程）见 `config/content_taxonomy.yaml#forms`——**形式决定用哪个模板，模板不是形式本身**。

## 选题卡必须包含的分类信息

第一阶段数据库还不支持多合集（`topics.column_id` 是单选），因此**分类信息写在选题卡
front matter 里**，以文件为准、数据库为索引：

```yaml
---
title: 转会费摊销为什么能让俱乐部"财务合规"
slug: transfer-amortisation-ffp
collections: [football_finance, financial_knowledge]   # 1–3 个，第一个是 primary
primary_collection: football_finance
audience_level: learner          # mass | learner | researcher
tags: {instrument: [], industry: [], geography: [EU], period: [], mechanism: [摊销]}
forms: [knowledge_video, article]
agents: [football_researcher, fact_checker, scriptwriter]
research_report_id: null
---
```

- 合集 id 与标签维度定义见 `config/content_taxonomy.yaml`，不得自造 id。
- `column_id` 在数据库里填 `primary_collection` 对应的栏目（二期改为关联表）。

## 状态流转（以代码枚举为准）

```text
idea → research → drafting → reviewing → approved
     → storyboard → producing → qa → published        （archived 为终态旁支）
```

上表与 `backend/app/models/enums.py: ContentStatus` 一致，
`config/content_rules.yaml#workflow_states` 是同一份状态机的人类可读版本。
**枚举是唯一真相**，任何文档/脚本不得自造状态名（例如 `researching`、`qc_passed` 都不存在）。

硬规则：

- `approved` 之前不得进入分镜制作。
- `published` 只能由人工操作，任何 Agent 不得写发布状态。
- 同一脚本版本的 `approved_at` / `approved_by` 必须记录在 `scripts` 表中。

## 素材与 Git

`assets/` 下的媒体大文件默认被 `.gitignore` 忽略（只保留目录结构与本说明）。
需要版本化的素材请放在外部存储，并在 `media_assets` 表中登记 `checksum` 与 `file_path`。
