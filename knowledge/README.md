# knowledge/ —— 金融知识库（文件侧）

数据库（`knowledge_items` 表）是知识主库；本目录存放**与代码一起版本化的知识文稿**，
用于人工撰写、评审与导出。

## 子目录

| 目录 | 内容 | 对应 `knowledge_domain` |
| --- | --- | --- |
| `concepts/` | 概念（如"久期""基差""回购"） | `concepts` |
| `instruments/` | 金融工具（股票、债券、期货、期权、互换…） | `financial_instruments` |
| `products/` | 结构化与资管产品（ABS、可转债、雪球…） | `financial_products` |
| `institutions/` | 机构与商业模式（银行、券商、交易所…） | `institutions` |
| `industries/` | 行业与产业链 | `industries` |
| `football_business/` | 足球商业、俱乐部财务、转会金融 | `football_business` |
| `cases/` | 金融事件与商业案例 | `financial_cases` |
| `research_reports/` | 研究报告的 Markdown 版本 | —（对应 `research_reports` 表） |

### 知识域 ≠ 内容合集（不要混用）

- `knowledge_domain` 是**知识条目**的学科归属（上表右列，对应 `knowledge_items.domain`）。
- **内容合集**（金融知识 / 财经时事 / 产业链研究 / 公司商业解剖 / 足球金融与商业 /
  金融与商业案例）是**内容产品**的栏目归属，定义在 `config/content_taxonomy.yaml`。
- 同一篇文稿可以被多个合集引用：例如"可转债"条目（`instruments/`）既服务
  `financial_knowledge`（讲机制）也服务 `market_briefing`（讲某次发行热）。
  知识库是资产，合集是货架。

已知缺口：`concepts/` 目录目前**没有对应的枚举取值**（`KnowledgeDomain` 只有六域 +
`general`），概念类条目请使用 `domain: general`，并在正文首行注明所属概念族。
这是数据库枚举变更，需二期决定，不要在本阶段手改枚举。

## 文稿格式约定

每篇知识文稿使用 YAML front matter，字段与数据库列一一对应：

```markdown
---
title: 可转债
slug: convertible-bond
domain: financial_instruments
jurisdiction: CN
as_of_date: 2026-06-30
version: 1
verification_status: draft
source_refs: []
---

## 定义

## 机制

## 参与方与利润模型

## 定价逻辑

## 风险结构

## 足球类比

## 类比的边界
```

### 撰写纪律

- **事实、推断、观点分栏写**，不要混在同一段里。
- 每个数字标注口径（币种、单位、时点、辖区）。
- 足球类比必须两节都写：`足球类比` 与 `类比的边界`，缺一节即不完整。
- `verification_status` 与 `version` 必须真实反映状态，不允许手写 `verified`
  来绕过审核。

## 与数据库的同步

第一阶段**没有**自动同步脚本：文稿与数据库记录通过 API 手工写入
（`POST /api/knowledge`）。将来可在 `backend/app/services/` 增加导入服务，
但本阶段不做，避免出现"看起来已同步"的假功能。
