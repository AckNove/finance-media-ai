# V5.1 交付报告：模型驱动研究规划 + 语义事实核查

- 本轮起点：`50d97a0`（V5.0 收官，模型闭环第一次跑通）
- 本轮主题：把「规则拆词 + 逐字核对」升级为「模型规划 + 逐条语义核查」，并把两者接进真实执行链
- 验收主题（全新，未复用旧结果）：**「光伏组件价格战为什么会吃掉龙头企业的现金流」**
- 所有真实运行都跑在 `%TEMP%\fmai_model_e2e_*\flow.db` 临时库上；`data/finance_media.db` 全程只读

---

## 0. 一页纸

| 项 | 结果 |
| --- | --- |
| P0 语义事实核查 | ✅ 已实现并在真实链路跑通（7 条结论逐条判定，5 档判定 + 8 类错误码 + 12 项留痕） |
| P1 模型驱动规划 | ✅ 已实现并在真实链路跑通（`planner=model`、6 个子任务、30 个检索词、6 条风险、6 条缺口） |
| P2 工作流整合 | ✅ 规划结果驱动执行、按规划动态补 2 条检索子任务、核查结果回写报告、全部调用进成本审计 |
| P3 前端 | ✅ 新增「语义核查」页 + 任务详情「规划卡」，接的都是真接口（未在浏览器里点过，见 §9） |
| 真实 e2e | ✅ 9/9 子任务 succeeded，24 个来源 / 356 条证据 / 1 份报告 / 7 条核查，任务 `completed` |
| 本轮真实调用 | 4 次模型调用（plan / analyze / synthesize / verify），46,240 tokens，**¥0.155924** |
| 人工态与模型态 | 7 条核查的 `human_review_status` 全部 `pending` —— **没有任何一处把模型判断写成人工确认** |
| 保护边界 | 真实库 SHA-256 未变、19 张表、`de8148733a9b`、`journal_mode=delete`、无 `-wal/-shm`；密钥未入库 |

一句话：**研究和核查这两步，现在真的有模型在判断，而不是"规则跑完盖个章"。**

---

## 1. 接管与安全检查（任务书第一节）

| 检查项 | 实测 |
| --- | --- |
| `HEAD` / `origin/main` | `50d97a0` == `origin/main`，`git status --porcelain` 空 |
| 上一轮报告 | 已读 `docs/research_hub/v5_0_model_integration_report.md`，并独立核对代码（报告里没提到的缺陷下面 §8 有三条） |
| `verify.claims` 现状 | `backend/app/research/verify.py`（63 行）只做逐字核对：`digest.data_points[].text` 是否出现在 `meta["excerpts"][source_id]` 里 |
| `research.plan` 现状 | 规则拆词：`derive_queries()` 按标点断句，`planner="keywords"` |
| 数据结构 | `ResearchReport.findings` 是 JSON 数组（`{text,kind,source_ids,numbers,verified}`）；`Evidence` 有 `claim`／`original_excerpt`／`source_id`／`research_report_id`／`verification_status` |
| 模型配置 | `deepseek` ready、`deepseek-flash` 价目 ¥0.00213 / ¥0.00852 每千 token，来源与核对日期写在 `config/models.yaml` |
| 预算闸门 | `BudgetContext`（任务调用数 / 任务成本 / 日上限 5.0 元 / 单次上限），网关在发请求前判，价目缺失直接拒 |

---

## 2. P0：语义事实核查

### 2.1 代码位置

| 文件 | 作用 |
| --- | --- |
| `backend/app/research/semantic.py`（新建，~700 行） | 核查的全部逻辑：提示词、解析、逐字回查、降级、成本均摊 |
| `backend/app/models/research.py` | 新增 `FindingCheck` 模型（表 `finding_checks`） |
| `backend/migrations/versions/20261113_0100_b5_finding_checks.py`（新建） | 建表 + 4 个索引，`down_revision=b4_cost_precision` |
| `backend/app/research/persistence.py` | `record_checks()`（整批替换但继承人工态）、`set_human_review()` |
| `backend/app/execution/executor.py` | `_verify_claims` 改成两层：先逐字核对，再语义核查；`_load_checks_input`、`_write_semantic` |
| `backend/app/schemas/finding_check.py`（新建） | 出参/入参模型、5 档判定与 4 种人工态常量 |
| `backend/app/api/routes/research_reports.py` | 列表 / 汇总 / 人工复核三个端点 |

### 2.2 判定与留痕（任务书要求的 12 项）

判定只有五档：`supported` / `partially_supported` / `contradicted` / `insufficient_evidence` / `unverifiable`。

每条核查落库 12 个字段（实测库里就是这么存的）：

| 任务书要求 | 字段 |
| --- | --- |
| 原始结论 | `claim`（+ `claim_kind`：fact / inference / hypothesis） |
| 关联证据 ID | `evidence_ids` |
| 来源 ID | `source_ids` |
| 核查状态 | `verdict` |
| 核查理由 | `reason` |
| 支持片段 | `supporting` |
| 反对片段 | `contradicting` |
| 缺失信息 | `missing` |
| 模型名称与版本 | `provider` / `model` |
| 模型调用记录与成本 | `model_call_id`（指向 `model_calls.id`）、`prompt_tokens` / `completion_tokens` / `cost_cny` |
| 核查时间 | `checked_at` |
| 人工复核状态 | `human_review_status`（默认 `pending`）+ `human_note` + `human_reviewed_at` |

### 2.3 八类错误码

`number_unit_mismatch`（数值/单位不一致）、`time_range_mismatch`（时间范围）、`yoy_vs_mom`（同比环比混淆）、`overgeneralized`（个案推广为整体）、`causal_leap`（因果误推）、`partial_support`（只支持一部分）、`source_conflict`（来源互相矛盾）、`stale_evidence`（证据过期/口径过时）。

### 2.4 三条硬规则（防止「模型说行就行」）

1. 模型说 supported / partially_supported，但**没有一条引文能在证据原文里逐字找到** → 降级为 `insufficient_evidence`，并记 `downgraded:no_verifiable_supporting_quote`。
2. 模型说 contradicted 但拿不出逐字反对片段 → 降级为 `unverifiable`。
3. 结论只挂了 S01，却拿 S02 的原文当支持 → **不算**（跨来源张冠李戴会被抓出来）。
4. 短于 6 字的"片段"直接丢弃（那种片段在哪里都能"找到"，等于没核对）。
5. `human_review_status` 永远是 `pending`，任何代码路径都不允许写 `confirmed`。

### 2.5 真实样例（最后一轮 e2e 的 6 条，原文照抄）

跑在全新主题「光伏组件价格战为什么会吃掉龙头企业的现金流」上：真实搜索 + 真实抓取 + 真 DeepSeek。

| # | 判定 | 结论（截断） | 模型给的理由（截断） |
| --- | --- | --- | --- |
| 0 | `partially_supported` | 价格战源于产能扩张远超需求：2023年产能920GW、产量518.1GW… | S16 支持 2025 年末名义产能超 1000GW 与 2026 年需求 550-650GW，但 2023 年的 920GW/518.1GW 在证据里找不到；S14 的摘录全是导航栏文本 |
| 1 | `partially_supported` | 供给过剩把报价压到成本线以下：CPIA 测算全成本 0.824 元/W… | 银价、0.824 元/W、12 家企业低于该线都能在 S05 逐字找到；但「供给过剩把报价压到成本线以下」这个**因果判断**证据里没有 |
| 2 | `partially_supported` | 2026年1月集中式组件 0.65-0.68 元/W，接近 0.68 元/W 现金成本线… | S06 只显示价格区间；证据里**没有** 0.68 元/W 的现金成本线，0.68 是报价区间上沿，不是成本 |
| 3 | `supported` | 全产业链亏损：2024年三季度起主链毛利率持续为负…通威 2024 年预亏 70-75 亿元 | S16 支持毛利率持续为负并列举硅料/硅片/电池/组件；S19 支持通威 70-75 亿 |
| 4 | `partially_supported` | 第一道冲击来自存货与减值占用资金：隆基存货 145 亿→181.58 亿… | 数字逐字有出处；但「第一道冲击」是**推断**，证据没给因果或先后 |
| 5 | `partially_supported` | 龙头通过拉长应付账款维持现金流：晶科 2024 年经营现金流 78.67 亿… | S20/S19 支持现金流数字；「优化应付账款」原文没有，S23 的「账期 600 天」证据未提供 |

这一轮是 `supported 1` + `partially_supported 5`，人工态全部 `pending`。历次 e2e 累计出现过 `supported` / `partially_supported` / `insufficient_evidence` / `unverifiable` 四档（例如 `%TEMP%\fmai_model_e2e_26sys7w0` 那轮是 `insufficient_evidence 2 + partially_supported 4`）；**`contradicted` 至今没有在真实数据里出现过**——不编一个出来凑数，见 §9.3。

---

## 3. P1：模型驱动研究规划

| 项 | 实现 |
| --- | --- |
| 代码 | `backend/app/research/planning.py`（新建，~430 行） |
| 输入 | 研究主题、目标、`config/pipelines.yaml` 的流水线名、显式检索词 |
| 输出（任务书要的 10 项） | 研究目标、核心问题（≤6）、子任务（≤6，含检索意图 / 关键词 / 同义词 / 建议来源类型 / 优先级 / 依赖 / 完成条件）、风险（≤6）、缺口（≤6） |
| 降级 | 无模型或模型报错 → `keyword_plan()`，`planner="keywords"`、`model_used=False`、`degraded_reason` 写明原因；**降级也照常往下跑，不卡链路** |
| 去重 | 关键词为空的子任务丢弃、问题过短的丢弃、跨子任务重复的检索词丢弃、超过上限截断，全部记 `drop_subtask:` / `truncated_subtasks:` 备注 |

真实产出（本轮）：`planner=model`、`model_used=true`、6 个子任务、**30 个检索词**、6 条核心问题、6 条风险、6 条缺口，`degraded_reason` 为空。

样例核心问题（原文）：

> 2024年以来光伏组件价格的实际跌幅与当前价位如何？采用什么权威口径衡量？

样例风险（原文）：

> 组件价格存在含税与不含税、出厂价与到岸价、不同功率段与封装形式的差异，直接比较容易产生错误结论。

---

## 4. P2：工作流整合

1. **规划驱动执行**：`research.plan` 输出的 `queries` 直接成为 `research.collect` 的检索词（`derive_queries()` 优先级：显式 queries > 上游 plan 产出 > 按标点拆句）。
2. **动态补子任务**：`TaskRuntime.append_followups()`（新代码）按规划里 `priority=high` 的其余子任务补检索子任务，最多 2 条，并且**给原本依赖采集阶段的下游（分析）补一条依赖指向新子任务**——否则分析会抢跑、新资料进不了报告。真实运行里补了 2 条，事件表里有 2 条 `direction_changed`。
3. **可追溯**：结论 → `evidence_ids` → `Evidence.source_id` → `sources`；核查行 `model_call_id` → `model_calls`。
4. **核查回写报告**：`record_checks()` 把 7 条核查挂在 `research_report_id` 上；报告 `limitations` 由模型写的版本覆盖，并明确写「模型结论不等于事实核查结论」。
5. **失败/阻塞真实记录**：`_analysis_failed()` / `_verify_claims` 失败时 `status="retry"` + `reason="semantic_check_failed:…"`，下游按 BLOCKED 传染，任务以 `failed` 收尾而不是卡在 `running`。
6. **成本审计**：每次调用都写 `model_calls`（本轮 4 行），任务行同步累加 `calls_spent` / `tokens_spent` / `cost_spent`。

---

## 5. P3：前端工作台

| 页面 | 改动 |
| --- | --- |
| 新页 `#/checks`（「语义核查」） | 报告下拉 → 汇总卡（总条数、模型花费、调用次数、各判定计数）+ 明细表（结论 / 判定+理由 / issue 码 / 支持片段 / 反对片段 / 人工状态+备注 / 三个复核按钮） |
| 任务详情 `#/tasks/{id}` | 新增「规划卡」：模型规划 or 规则拆词、是否真用了模型、检索词数、子任务数、降级原因、核心问题、风险、动态追加的检索任务 |
| 报告列表 `#/reports` | 每行加「看语义核查」入口 |

所有数据都来自 `GET /api/research-reports/{id}/checks`、`/checks/summary`、`POST /api/research-reports/{id}/checks/{check_id}/review`、`GET /api/research-tasks/{id}`；`node --check web/app.js` 通过，OpenAPI 里三条端点齐全。

**注意**：本轮没有在浏览器里点过这些按钮（见 §9 的诚实分类），页面是接好真接口、语法检查通过，但没有真人点过。

---

## 6. 真实端到端验收

命令：`python -m backend.scripts.model_e2e_check`（临时库 + 真 uvicorn :8397 + 真搜索抓取 + 真 DeepSeek）

主题：**光伏组件价格战为什么会吃掉龙头企业的现金流**（全新主题，不复用任何旧任务结果）

| 步骤 | 实测（最后一轮，临时库 `%TEMP%\fmai_model_e2e_4yv0k_x7\flow.db`） |
| --- | --- |
| 迁移临时库 | `alembic upgrade head` rc=0，30 张表（29 业务 + `alembic_version`） |
| 建任务 | `POST /api/research-tasks` → 201，`task_id=1`，9 个子任务 |
| 执行 | worker 推进到 `paused`（人工立项闸门）→ 脚本 `POST /{id}/resume` 放行 → 继续 |
| 子任务 | 9/9 `succeeded`，另有动态补的检索子任务也 `succeeded` |
| 数据 | `sources=27`、`evidence=416`、`research_reports=1`、`finding_checks=6` |
| 规划 | `planner=model`、`model_used=True`、6 个规划子任务、**36 个检索词**、5 条核心问题、5 条风险 |
| 核查 | `total=6`、`by_verdict={"supported":1,"partially_supported":5}`、`by_human_review={"pending":6}`、`model_cost_cny=0.044430` |
| 任务终态 | `status=completed`、`calls_spent=4`、`tokens_spent=47132`、`cost_spent=0.159294` |
| 账对得上 | `/api/system/costs` 的 `task_cost_cny=0.159294` == `SUM(model_calls.cost_estimate)` == 任务行 `cost_spent`（**这一条是本轮修完记账缺陷后才成立的**） |
| 事件 | 54 条（`claimed` / `queued` / `subtask_*` / `paused` / `resumed` / `plan_created` / `direction_changed` / `completed`） |
| 密钥 | `model_calls.prompt_preview LIKE '%sk-%'` → **0 行** |

---

## 7. 费用账

最后一轮（任务 `budget_max_calls=8` / `budget_max_cost=0.5` 元）：

| purpose | prompt | completion | 费用（元） |
| --- | --- | --- | --- |
| `research.plan` | 489 | 1,536 | 0.014128 |
| `research.analyze` | 13,953 | 2,396 | 0.050134 |
| `research.synthesize` | 13,953 | 2,451 | 0.050602 |
| `research.verify` | 9,519 | 2,835 | 0.044430 |
| **合计（4 次调用）** | 37,914 | 9,218 | **0.159294** |

本轮一共跑了 **8 次**真实 e2e（含因截断失败、因证据撑爆提示词、以及修记账前后的对照），累计真实花费 **约 ¥1.48**：

| 次序 | 花费（元） | 结果 |
| --- | --- | --- |
| 1–2 | 0.0687 / 0.0824 | 规划 1800 上限被截断 → 降级关键词 |
| 3 | 0.2105 | 分析 3600 仍截断 |
| 4 | 0.2284 | 核查提示词 15 万 token（¥0.345 那次的前身） |
| 5 | 0.4497 | 核查一次 ¥0.345，暴露证据上限缺失 |
| 6 | 0.1559 | 全链跑通，判定四档里出现三档 |
| 7 | 0.1211 | 修完证据挑选后复核（发现任务账 0.0928 < 账单 0.1211） |
| 8 | 0.159294 | 记账修完，任务账 == 账单，6 条核查 |

- 每次都在任务预算与日上限（5 元）之内；`pricing_unknown_calls=0`（价目都已核对并有 `price_checked_at`）。
- 所有调用前都过预算闸门与单次上限，未出现"超预算才失败"的调用。

> 关于那次 ¥0.345 的单轮：语义核查原本把库里 356 条证据全塞进提示词，一次 prompt 15 万 token。修法是给证据条数设上限（`MAX_EVIDENCE=30`）+ 按结论挂的来源优先挑选 + token 递减重试，同一环节降到 9,519 token / ¥0.0444。**这是真实的成本缺陷，不是被闸门拦下的。**

---

## 8. 这一轮修掉的 9 个真缺陷

| # | 缺陷 | 后果 | 修法 |
| --- | --- | --- | --- |
| 1 | `SubtaskOut.output` 恒为 None | 接口永远看不到阶段产出（含规划结果） | ORM 列名 `output_payload` 与模型字段 `output` 不一致，补 `_subtask_out()` 显式映射 |
| 2 | 模型回复撞 `max_tokens` 被当成正常结果 | 规划直接降级成关键词、分析/核查解析半截 JSON | 网关把 `finish_reason` 写进 `usage`；三层各加截断检测 + 递减重试阶梯 |
| 3 | 核查的来源编号映射必然失败 | 所有证据退化成 `db-N`，引文全被丢弃 → 核查恒为「证据不足」 | 改用报告入库时写下的 `report.outline` 做主映射，URL/标题兜底，对不上就告警 |
| 4 | `CallRecord` 字段名搞错 | `model_call_id` 恒为 None，核查与 `model_calls` 的审计链断掉 | `getattr(record, "model_call_id")` → `getattr(record, "id")` |
| 5 | 上游 BLOCKED 不传染 | 任务卡在 `running`，worker 空转（真跑里连跑 18 轮 completed=0） | `_refresh_readiness()` 的传染集合加入 `BLOCKED` |
| 6 | 核查把 356 条证据全塞进提示词 | 一次核查 ¥0.345（比整条研究链还贵 80%） | 证据上限 30 条、片段 400 字、递减重试 |
| 7 | 各阶段的产物散在三个目录 | 同一份研究的底稿/分析/核查分开三个文件夹 | 目录 key 改用**任务目标**（`_task_objective()`），不再用各阶段的阶段问题 |
| 8 | 核查按 id 取前 30 条证据 | 结论引用的 S16/S19 根本没进候选 → 有据的报告被判「证据不足」（实测 4/6） | 新增 `_select_evidence()`：先按结论自己挂的来源挑（每源 4 条），再补其余 |
| 9 | **规划降级时漏记成本** | `research_tasks.cost_spent` 小于 `SUM(model_calls)`（3 次 vs 4 次、0.0928 vs 0.1211） | 三个错误类带上 `usage`（并累加重试花费），`_usage_kwargs()` 把失败调用的钱也记进任务账 |

第 9 条还有一半：撞上限时**原来直接降级成规则拆词**，等于白花钱还把模型能力丢掉。现在规划也走递减阶梯（少写几条子任务 + 抬 cap 重试），三轮都装不下才降级，并在 `degraded_reason` 里写明 `model_planning_failed:truncated`。

另外补齐了两处诚实性：核查行的 `prompt_tokens/completion_tokens/cost_cny` 之前恒为 0（现在按条均摊，`SUM` 等于真实花费）；报告 `limitations` 之前还留着「本文件由规则汇编生成，没有使用模型」（现在按有没有真用模型来写）。

---

## 9. 交付分类（严格按任务书要求）

### 9.1 已完成且有真实运行证据

- **语义核查**：真模型、6 条真实结论逐条判定、12 项留痕、8 类错误码、人工态恒 `pending`；`finding_checks` 落库、`semantic_check.{md,json}` 落盘
- **模型规划**：真模型、6 个规划子任务、36 个检索词、5 条核心问题、5 条风险；`planner=model`/`model_used=True` 如实标注
- **动态补子任务**：规划里的高优先级子任务真的追加成采集任务（`direction_changed` 事件落库）
- **工作流整合**：9/9 子任务 `succeeded`、27 来源、416 证据、1 报告、6 核查、54 事件
- **成本审计**：4 次调用全进 `model_calls`，任务账 == 账单 == `/api/system/costs`
- **三个核查 API**：列表 / 汇总 / 人工复核（`confirmed` 必须带 note，否则 422）
- **临时库迁移 b5**：30 张表、`alembic check` 无漂移、downgrade/upgrade 往返正常
- **前端**：`#/checks?report=<id>` 页与任务详情里的规划卡已接真接口（接口存在性 + 语法检查通过）

### 9.2 仅通过单元测试（离线，未在真实链路验证）

- `tests/test_semantic_check.py`（12 例）：三条降级硬规则、跨来源张冠李戴、未知判定、模型漏答、截断、无模型、证据挑选
- `tests/test_planning.py`（12 例）：提示词、10 项输出、去重与截断、截断/无模型降级
- `tests/test_execution_pipeline.py` 新增 6 例：模型规划 + followup、垃圾回复降级、worker 补任务、核查落库且人工态 pending、**规划截断重试后仍记账**、**任务花费 == 模型调用合计**
- 人工复核的四种状态（`confirmed` / `rejected` / `needs_more_evidence` / 回 `pending`）只有单测与接口测试，没有真人点过

### 9.3 尚未完成

- **`contradicted`（与证据冲突）至今没有真实样本**，只有单测覆盖；不编造一个凑数
- 前端页面没有在浏览器里真人点过（只做了语法检查与接口存在性验证）
- 语义核查一次最多看 30 条证据；证据更多的报告会漏掉尾部证据（已在报告 `note` 里写明）
- 规划的子任务没有写回 `research_tasks.plan` 的结构化字段（只进 `research.plan` 子任务的 `output_payload`）
- 语义核查与人工复核还没有和成稿联动（`confirmed` 目前只改核查行）

### 9.4 外部依赖阻塞

- 无。模型、搜索、抓取都可用；本轮没有需要用户介入的阻塞。

---

## 10. 保护边界核对（任务书第八节逐条）

| 约束 | 实测 |
| --- | --- |
| 不迁移真实开发库 | `data/finance_media.db` 仍是 `revision=de8148733a9b`、19 张表、无 `model_calls`、无 `finding_checks` |
| 不改真实库文件 | `sha256=2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`（与上轮一致）、540,672 B |
| 不改 WAL 状态 | `journal_mode=delete`，无 `-wal` / `-shm` |
| 不改 FFmpeg 8.1.2 | `ffmpeg version 8.1.2-full_build-www.gyan.dev` |
| 不改 `backend/app/main.py` 第 27 行品牌文字 | 该文件本轮未被修改（`git status` 里没有它） |
| 不打印/提交密钥 | 全仓扫描只有 `.env`（已 gitignore、未被跟踪）含密钥；`git grep "sk-[A-Za-z0-9]{20,}"` 零命中 |
| 不用桌面旧文件/旧报告 | 本轮验收主题是全新的；检索走真实搜索，抓的是线上原文 |
| 不自动发布 | 无任何发布动作 |
| 不 `reset --hard` / `clean -fd` / Force Push | 未执行 |
| 不用 Mock 冒充真实成功 | §9.1 与 §9.2 分开列；Mock 只出现在单测里 |

---

## 11. 测试

```
pytest -o addopts=""        # 全量：839 passed, 2 skipped, 1 warning
```

本轮新增测试文件 2 个（`tests/test_semantic_check.py` 12 例、`tests/test_planning.py` 12 例），扩展 `tests/test_execution_pipeline.py` 6 例；另修了 3 处「端点数量绊线」与迁移表数断言（57 个 API 操作、29 张业务表）。

唯一长期存在的 warning 仍是 `agent_runs ↔ research_subtasks` 双向外键环（设计如此，见 M2.0 报告 §11）。

---

## 12. 下一步建议

1. **让 `contradicted` 有真实样本**：现在冲突判定只能靠单测；可以刻意挑一个「多来源数字打架」的题目（例如不同机构的光伏装机口径）来验。
2. **核查覆盖全部证据**：现在是「上限 30 条 + 结论自己挂的优先」，更好的做法是先按结论聚类、再逐簇核查（一组一调，成本也可控）。
3. **规划结果进任务结构**：把模型规划的子任务直接写成 `research_subtasks`（而不是只放在 plan 阶段输出里），前端就能显示「计划的 vs 实际跑的」。
4. **人工复核闭环**：`confirmed` 目前只改核查行；应该规定「结论没 `confirmed` 就不许进成稿」（口径要你定）。
5. **前端真点一遍**：页面已接真接口，建议在浏览器里走 `#/tasks/1` → `#/checks?report=1` → 点一次「人工确认」。

---

## 13. 追加能力：成稿闸门（confirmed 才能进成稿）

> 2026-11-13 追加。起因是你问了两个问题：「链接打不开」和「结论必须 confirmed 才能进入成稿吧」。
> 后者原本只是本报告 §12 的一条**建议**（"口径要你定"），现在按你的口径落成**硬闸门**。

### 13.1 规则

一条结论要进成稿，必须同时满足：

1. 报告里存在这条结论（按 `finding_index` 对齐）；
2. `finding_checks` 里有它的一行核查记录；
3. 其 `human_review_status = "confirmed"`（人在核查页点过，并写了理由）。

模型判定 `supported` **不算数**——`supported` 只是 `verdict`，与 `human_review_status` 是两条互不覆盖的状态线。

拦截点（三处，同一个判据，代码只有一份）：

| 位置 | 文件 | 行为 |
| --- | --- | --- |
| 建视频任务（API） | `backend/app/api/routes/research_reports.py` | 409 `ConflictError`，`details` 带完整 readiness |
| 建视频任务（交接层） | `backend/app/video/handoff.py` | `VideoHandoffError("draft_not_confirmed")`，**在创建任务目录之前**就拒绝 |
| 命令行出片 | `backend/scripts/produce_video.py` | 退出码 3，打印 `draft_readiness` 与 `next_step` |

判据的唯一实现是 `backend/app/research/draft_gate.py`（`readiness_of` / `confirmed_findings` / `assert_draft_ready`），**没有任何"忽略闸门"的开关**。

### 13.2 交接出去的稿子长什么样

`content/jobs/<job>/research/report.md` 现在分两节：

- 「可进成稿的结论（人工已确认）」——只放确认过的，每条标 `结论 #i（核查记录 #k｜模型判定 supported｜人工已确认）`；
- 「尚未人工确认（不得进成稿）」——其余结论照样列出，标「待人工复核」。

两类编号必须分开看：**结论序号来自报告**，**核查记录号来自 `finding_checks.id`**。第一版把两者都写成 `#N`，于是「核查 #4」和「未确认 #4」看起来像同一条，已改。

### 13.3 真机验证（演示库，未用真实开发库）

演示库 `%TEMP%\fmai_demo\demo.db`（从最近一次 e2e 临时库复制的副本），工作台跑在 `http://127.0.0.1:8000/app/`：

| 步骤 | 请求 | 结果 |
| --- | --- | --- |
| 1 | `GET /api/research-reports/1/draft-readiness` | `allowed=false`、`reason=not_confirmed`、`total_checks=6`、`confirmed=0` |
| 2 | `GET /api/research-reports/1/checks?limit=100` | 200，6 条，`notes=["usage_share:1/6"]` |
| 3 | `POST .../checks/4/review {"status":"confirmed","note":"对照 S16 原文…"}` | 200，`human_review_status=confirmed` |
| 4 | `GET .../draft-readiness` | `allowed=true`、`confirmed=1`、`by_human_review={"pending":5,"confirmed":1}` |
| 5 | `POST /api/research-reports/1/video-job {"job_id":"gate_demo_after_confirm"}` | **201**、`stage=storyboard_ready`，产出 `content/jobs/gate_demo_after_confirm/` |

### 13.4 顺带修掉的三个真缺陷

1. **`GET /checks` 500（两处列形态）**：`finding_checks.notes` 实际写的是数组 `["usage_share:1/6"]`，schema 却声明成对象；`supporting/contradicting/missing` 可能为 NULL，schema 却声明成字符串。任何一个形态对不上，整个列表就 500。现在四种形态都收，并有回归用例 `test_checks_list_survives_the_notes_and_quotes_columns`。
2. **失败不记账**：规划/分析/核查撞 `max_tokens` 时抛错，但**那几次调用是花了钱的**，任务成本却记 0。现在 `PlanningError`/`AnalysisError`/`SemanticError` 都带 `usage`，执行器用 `_usage_kwargs` 逐次入账；`research_tasks.cost_spent` 与 `SUM(model_calls.cost_estimate)` 一致（第九轮实测 4 次 ¥0.159294，任务行、调用明细、`/api/system/costs` 三方对齐）。
3. **探针误触真实库（未写入）**：演示期有一次忘了传 `DATABASE_URL`，落到了真实开发库并尝试 `INSERT research_reports`，被 `table research_reports has no column named research_task_id` 挡住，**未写入任何数据**；此后所有建库/建任务命令都显式带 `--database-url` 或 `DATABASE_URL`。

### 13.5 闸门覆盖第二条出口：脚本（2026-11-13 补）

脚本本身就是成稿，只拦「建视频任务」等于留了后门。现在 `backend/app/services/script_service.py` 的
`ScriptService.create` 在带 `research_report_id` 时先过同一个闸门：

- 报告不存在 → 422（原有校验不变）；跨选题 → 422（原有校验不变）；
- 闸门不放行 → **409 `ConflictError`**，`details` 就是 `DraftReadiness.as_dict()`（`reason` 为
  `no_checks` / `not_confirmed`），前端拿到的信息和视频任务那条出口完全一致。

判据仍然只有 `backend/app/research/draft_gate.py` 一份，脚本层只是调用 `readiness_of`。
回归用例 `tests/test_draft_gate.py::test_script_from_an_unconfirmed_report_is_refused`：
没有核查记录时 `POST /api/scripts` → 409 `no_checks`；插一条 `confirmed` 之后同一个请求 → 201 且回填 `research_report_id`。

### 13.6 Windows 一键启动脚本加固（同日）

`scripts/start.ps1` 是「重启后还能一条命令起来」的入口，这次补了三处真问题：

| 问题 | 原来的表现 | 现在 |
| --- | --- | --- |
| 文件无 BOM | Windows PowerShell 5.1 按 ANSI（GBK）解析，脚本里的中文注释与提示全变乱码 | 存成 **UTF-8 带 BOM**，并在文件头写明原因 |
| 端口被占用 | 直接把 uvicorn 的堆栈甩给人看 | 先用 `TcpClient` 探 400ms，占用则打印 `Get-NetTCPConnection` 查到的占用进程名与 pid，提示换 `-Port 8011`，**退出码 3** |
| 起没起来不知道 | 只打印地址，起失败要自己翻日志 | 起一个探测 job，每 800ms 打 `/health` 最多 10 次，通了打印绿色「健康检查通过」，8 秒没通也明说 |

另加 `-SkipPreflight`（跳过预检，CI 里省一次冷启动），并用 `try/finally` 清掉探测 job。

实测（Windows PowerShell 5.1，不是 pwsh 7）：

- 端口占用分支：`start.ps1 -Port 8000` → 正确打印「端口 8000 已被占用…占用进程：python (pid 11736)」，**退出码 3**，中文无乱码；
- 真启动分支：`start.ps1 -Port 8011 -Database %TEMP%\fmai_start_test.db -SkipPreflight -NoBrowser`
  → 自动建库（alembic 一路跑到 `b5_finding_checks`）→ 启动自检 **8/8 项通过** → 探测 job 打印「健康检查通过」→
  `/health` 200、`/app/` 200（1021 B）、`/api/system/status` 里 `table_count=30`、`revision=b5_finding_checks`、`journal_mode=delete`。

### 13.7 还没做的

- 前端页面已接 `draft-readiness`（报告页新增「成稿闸门」列、核查页顶部显示闸门状态），但**还没在浏览器里真人点过**；
- 闸门已覆盖「建视频任务」与「建脚本」两条出口；`/api/reviews` 的审核流水不拦（它的语义是"对已存在的稿子给意见"，不是产出稿子）；
- `confirmed` 的审计目前只有 `human_note` / `human_reviewed_at`，没有独立的操作人字段（本地单人使用时足够）。
