# M1.2-final 收尾报告：人工决策落地、批次冲突修正与证据定位规范

> **本文件是什么**：M1.2 两份主交付文档（`m1_2_decision_register.md` / `m1_2_delivery_report.md`）经人工审查后，
> 按"M1.2 最终审查修订任务"逐条执行的结果记录。
> **本文件不是什么**：不是新一版架构设计，不回改 M1.1 审查报告，不写任何代码。
> **状态**：**已完成修订，等待人工确认提交与推送**（本轮**未提交、未推送**）。

| 项 | 值 |
|---|---|
| 执行日期 | 2026-11-10 |
| Git 基线 | 分支 `main`，HEAD = origin/main = **`0029c3e`**（ahead/behind `0 0`） |
| 改动范围 | 仅 `docs/research_hub/*.md`（12 个已跟踪文件被修改 + 本文件新增）；**业务代码零改动** |
| 测试 | `pytest -p no:warnings` → **96 passed in 11.37s** |
| 数据库 | 迁移 head = current = `de8148733a9b`，`backend/migrations/versions/` 仍只有 1 个文件，**未执行任何迁移** |
| 跨文档复检 | **26 项检查全部 OK，0 失败，文档总数 17** |

---

## 0. 一页纸结论

1. **Q5–Q9 五项全部按你的决定落地**：A / A / A / **B** / A。其中 **Q8 你选了 B（未知价拒绝调用），与我原来的建议 A 相反，已按你的决定改写 7 处文档**，并在决策台账里写明"任何人不得再按旧建议执行"。
2. **Q7 的最晚决策点冲突已消除**：原来"2.5 B5 批次建 `companies`"是错的（`companies` 实际在 **B6** 建），现统一为 **B6 `b6_content_layer` 开始之前**，并给出四处文档的校核依据。
3. **B4 证据定位从"一句话"升级为"四条规则 + E1–E8 验收清单"**（`database_design.md` §7.5）：extraction 版本、字符偏移基准、文本规范化、quote_hash 校验全部写死，**本轮只写规范不写代码**。
4. **文件清单与 Git 状态已实测核对**，并**发现并更正了交付报告里的统计错误**（附录 A 原写 `1003 insertions/210 deletions`，实测为 **1154/216**；逐文件 `+N` 为估算值，已在原文件标注"以实测表为准"）。
5. **`m1_1_architecture_review.md` 处于未跟踪状态是预期行为**，不是丢失也不是遗漏提交：M1.1 任务书明确禁止提交，该文件从未被 `git add`。**未覆盖、未删除**（`git log` 对该文件 0 条记录，文件内容 689 行 / 52,484 字节完好）。
6. **业务代码零改动、零迁移、零外部调用**：`git diff --name-only -- backend config data alembic.ini tests` 无输出；`pytest` 96 passed 与改动前一致。
7. **仍未解决的事项只剩 3 项外部授权**（模型供应商与预算、是否允许真实联网、是否修 `backend/app/main.py:27`），它们不是设计决策，等你批。

---

## 1. 人工决策 Q5–Q9 如何落实

### 1.1 决策与落地位置总表

| # | 你的决定 | 落实动作（改了什么） | 落地的文件与章节 | 什么算"落实完成"（可检查） |
|---|---|---|---|---|
| **Q5** | **A** 保留 `review_records` 弱多态 + 孤儿巡检 | ①决策台账 D11 状态由"待确认"改"**已确认（人工 Q5=A）**"，并加"不再评估改成一表一关联列"与"为什么保留弱多态"；②**把孤儿巡检 SQL 写进文档**（四类 target_type 逐一 `not exists` 校验）；③验收加"该查询返回 0 行" | `m1_2_decision_register.md` §2 D11；`database_design.md` §4.21；`acceptance_criteria.md` 2.5 | 2.5 阶段跑巡检 SQL 返回 **0 行**；`review_records` 的 `target_type` CHECK 含 `report_finding` |
| **Q6** | **A** 确认复用 `topic_columns` 作为六大合集唯一主数据 | ①D2 状态改"**已确认（人工 Q6=A）**：长期保留、不再评估迁到独立 `collections` 表"；②加"不可再变的边界"：新增合集只能走 `extension_policy`（YAML 加条目 + seed upsert + `taxonomy_version` +1），**不得新建表** | `m1_2_decision_register.md` §2 D2；`database_design.md` §4.20；`config/content_taxonomy.yaml` `extension_policy` | 启动校验第⑤项"合集数量 = YAML 数量"通过；DB 中不存在第二张"合集定义表" |
| **Q7** | **A** `institutions` 与 `companies` 按类型分工 | ①**新增决策 D13**（决策正文 §2 D13，含判定口径四问、互指规则、误判处理、最晚点）；②`database_design.md` §4.17 增"与 `institutions` 的实体边界"判定表；③§3 表清单第 17 行改"**2.7（B6 批次）**"；④`module_design.md` §F 公司档案行同步 | `m1_2_decision_register.md` **D13**（新增）；`database_design.md` §3 / §4.17；`module_design.md` §F | 建 `companies` 前，任何"某主体该进哪张表"的提问都能用 D13 的四问判定；两表无同一实体重复行（靠 `institution_id`/`parent_id` 互指） |
| **Q8** | **B** 未知价**默认拒绝调用**；仅人工登记的例外可调用 | **改写 7 处文档**（下表 1.2 详列）：价目缺失 → `budget_blocked` + `details.reason=pricing_unknown`，**不发请求、不落 `reserved` 行、不计费**；放行通道改为 `config/models.yaml: approved_exceptions` 六项登记（模型 + 单次上限 + 总预算 + 批准人 + 批准时间 + 有效期），过期即失效 | 决策台账 D6、`system_architecture.md` §4.1/§8.2、`tooling_architecture.md` §2.7、`database_design.md` §4.15.4、`implementation_roadmap.md` 2.1/§3、`acceptance_criteria.md` 2.1、`api_design.md` 错误码、`agent_architecture.md` §7、`m1_delivery_checklist.md` M1-R4 | 2.1 阶段：未登记例外的未知价模型**调不通**且 `model_calls` 查不到任何残留行；`tests/test_pricing_policy.py` 四用例全过 |
| **Q9** | **A** `research_reports.findings` 永久保留为派生快照、不作权威 | ①D9 状态改"**已确认（人工 Q9=A）**：3.x 也不删"；②加"重算与校验规则"（`rebuild-findings-snapshot` 属人工运维动作；2.5 加一致性测试）；③加"禁止用法"（判定一律读 `report_findings`，JSON 只用于展示与兼容） | `m1_2_decision_register.md` §2 D9；`database_design.md` §4.13；`acceptance_criteria.md` 2.5 | `report_findings` 与 JSON 快照按 `ordinal` 逐项一致；任何"是否可发布"的判断都不读 JSON |

### 1.2 Q8（唯一的"反向"决策）逐处改写清单

| # | 文件 | 位置 | 改前 | 改后 |
|---|---|---|---|---|
| 1 | `m1_2_decision_register.md` | §2 D6 | 代价③"未知价按上界估会高估成本"、状态"已定" | 新增"**未知价策略（Q8=B）**"整行 + "为什么是 B 不是 A" + 硬门含 `UnknownPricingError` |
| 2 | `m1_2_decision_register.md` | §3 | Q8 行"我的建议 **A**"、状态"待人工确认" | "**最终决定 B：默认拒绝**"，并注明"与本表原建议相反，以人工决定为准" |
| 3 | `system_architecture.md` | §4.1 第 6 条 | "价目缺失时 `pricing_unknown=true`，按上界预估值计费" | "价目缺失 → **默认拒绝**；例外须 `approved_exceptions` 六项登记" |
| 4 | `system_architecture.md` | §8.2 注 | `cost` 段键不含策略 | `cost` 段增 `pricing_policy`；写明"已定为 `reject_unknown`，与预留机制同批上线" |
| 5 | `tooling_architecture.md` | §2.7 | 执行顺序 ①②③④（无价目闸门） | 增 **⓪ 价目闸门**；错误处理增"价目缺失且无例外 → `budget_blocked`（不落 reserved 行、不发请求）"；`config/models.yaml` 增 `pricing_policy` 与空的 `approved_exceptions`（含注释示例） |
| 6 | `database_design.md` | §4.15.4 | "价目缺失 → 取上界估算" | "默认拒绝；`pricing_unknown=true` 的行**只可能由人工例外产生**" |
| 7 | `implementation_roadmap.md` | 2.1 目标/文件/测试/验收/费用 + §3 说明 | 无价目策略要求 | 目标与验收加"未登记例外的未知价调不通、库里 0 残留行"；测试加 `test_pricing_policy.py` 四用例；费用行改"未填价目 = 无法调用 = 0 费用" |
| 8 | `acceptance_criteria.md` | 2.1 | 只要求"预留按上界取" | 改为"**价目策略（Q8=B）**"门 + **人工例外四用例** |
| 9 | `api_design.md` | §2.2 错误码表 + `details.reason` 集合 | 无 `pricing_unknown` | `conflict`(409) 增该原因；`details.reason` 集合增 `pricing_unknown` 并给出 `{missing_models, policy}` 结构 |
| 10 | `agent_architecture.md` | §7 成本分工"全部"行 | "`pricing_unknown` 时不下结论性费用" | "价目缺失时调用会被拒绝；**不得把'没调通'写成'已分析'**" |
| 11 | `m1_delivery_checklist.md` | M1-R4 | "`pricing_unknown=true` 时不显示金额" | "已定 Q8=B：价目缺失即拒绝调用，未填价目不会花出真钱" |

> 这 11 处（对应 9 个文件）是 Q8 的**完整落地面**。只改决策台账而不改工具契约，等于"文档说拒绝、代码却照跑"，这是本次重点防范的假落实。

---

## 2. Q7 批次冲突如何修正

### 2.1 冲突是什么

| 文档 | 原表述 | 冲突点 |
|---|---|---|
| `m1_2_decision_register.md` Q7 行 | "最晚决策点：**2.5 B5 批次建 `companies` 前**" | 把 `companies` 的建表批次写成 B5 |
| `database_design.md` §6.2 B6 行 + 表创建顺序 | `B6: companies → content_items → script_evidence_links → ALTER(scripts)` | `companies` 在 **B6** |
| `implementation_roadmap.md` 2.5 行 | "迁库 ✅ **B5**（2 新表 + `review_records` 约束重建）" | 2.5 阶段**不含** `companies` |
| `implementation_roadmap.md` 2.9 行 | "迁库 ✅ **B6**（3 新表含 `companies` + `scripts` 加列 + 危险 FK 改写）" | `companies` 在 2.9 阶段的 B6 |

**同一张表的建表批次出现两个版本 → 施工时必然踩空**（在 B5 建 `companies` 会因为它引用的 `industries`/`institutions` 已在库而看似可行，但会破坏 B5"只做结论层"的批次边界，且 B6 的回滚演练会漏掉它）。

### 2.2 修正动作

1. **决策台账 Q7 行**改为"**B6 `b6_content_layer` 开始之前**"，并在该行直接写明旧口径是错的，附四处校核依据。
2. **新增 D13**，其中"最晚决策点（已统一）"一行同样写 **B6 之前**，并注明"此前任何文档出现'在 2.5/B5 建 `companies`'都是旧的错误口径"。
3. **`database_design.md` §3 第 17 行**：`2.7` → **`2.7（B6 批次）`**，并指向 D13。
4. **`database_design.md` §4.17**：加判定表 + "最晚决策点：B6 之前"。
5. **`module_design.md` §F**：阶段列 `2.7` → **`2.7（B6 批次）`**。

### 2.3 修正后的自检逻辑（写死的四条一致）

```text
① database_design.md §6.2 B6 行         → 建 companies            ✅
② database_design.md §6.2 表创建顺序     → B6: companies → …      ✅
③ implementation_roadmap.md 2.5 行      → 迁库 B5，2 新表（无 companies） ✅
④ implementation_roadmap.md 2.9 行      → 迁库 B6，3 新表（含 companies） ✅
⇒ Q7 最晚决策点只能是 B6 之前
```

复检脚本 `forbid` 项"2.5 B5 批次建 `companies`"在非历史文档中**已彻底消失**（仅 `m1_2_delivery_report.md` 作为历史记录保留原建议，且已加"人工最终决定"列）。

---

## 3. 证据定位规范如何明确（B4 验收前置）

规范全文见 `database_design.md` **§7.5**（本轮新增，5 个小节）。这里给"为什么这四条够用"。

### 3.1 四条规则的分工

| 规则 | 解决的具体问题 | 写死的口径 |
|---|---|---|
| **规则一：一条证据只属于一个 extraction** | "这段引文到底是从哪一份文本里切的" | `(source_snapshot_id, extraction_id)` 共同确定唯一文本域；**禁止跨 extraction 混用偏移**；无文本层来源只能进 `source_summaries`（摘要≠证据） |
| **规则二：extraction 版本与演进** | "parser 升级后老证据怎么办" | 版本三要素 `parser_name + parser_version + span_offset_base`（+`extraction_kind`）任一变化 = **新行**；证据**显式记录所用 `extraction_id`**，之后永远指向它；需迁移时**新建 evidence + `supersedes_evidence_id`**，旧行 `invalidated_at` 置位，**严禁原地改偏移** |
| **规则三：字符偏移基准** | "char_start 是按字节还是按字符" | **Unicode 码点**（不是 UTF-8 字节、不是 UTF-16 code unit）；`[char_start, char_end)` 左闭右开；叠加 `span_offset_base`（默认 0）；换行统一 `\n`；剔除 BOM；越界/反向区间 = 与 extraction 不匹配 → **blocked，不得写出证据** |
| **规则四：quote_hash 校验** | "引文被改过怎么发现" | 规范化算法沿用 §7.2（NFC → 折叠空白 → 全角转半角 → 去零宽 → **大小写不折叠** → sha256 小写十六进制，恒 64 字符）；落库时三方自洽（原文/区间/哈希）；复核时重切比对，四种结果各有动作（一致 / 不一致→`disputed`+降档+blocker / 切不出→blocked+`invalidated_at` / 规范化后 <8 字符→拒绝） |

### 3.2 B4 验收清单 E1–E8（对应 `acceptance_criteria.md` 2.2）

| # | 验收项 | 判定方式 |
|---|---|---|
| E1 | evidence 9 个定位/效力新列齐备且旧 `ck_evidence_has_claim_target` 消失 | `PRAGMA table_info(evidence)` + `sqlite_master` 的 CHECK 文本 |
| E2 | `ck_evidence_offset_pair` 存在（两列同空或同非空） | 同上 |
| E3 | 一条证据能被精确回切 | `text[char_start:char_end]` 规范化后 == `original_excerpt` 规范化后（逐字符） |
| E4 | `quote_hash` 可复算 | 两次规范化哈希相同、长度 64 |
| E5 | 跨 extraction 混用被拒 | 用 A 的偏移配 B 的 extraction → 422，证据表 0 行新增 |
| E6 | parser 升级不污染旧证据 | 新抽取成立后，旧证据 `extraction_id`/`char_*`/`quote_hash` 均不变且可复算通过 |
| E7 | 越界即 blocked | 构造越界偏移 → 父级 blocked，证据表 0 行新增 |
| E8 | 失败留痕不删除 | 制造 `quote_mismatch` → `invalidated_at` 非空、行仍存在、`review_records` 有 `needs_revision` |

### 3.3 为什么本轮**只写规范、不写代码**

- 实现位置（`backend/app/core/text_norm.py`、`span_utils.py`、`source_snapshot_service.py`）属于 **2.2 阶段**的交付物，本轮写代码就违反了"不修改业务代码"的硬约束。
- 规范的**验收口径**可以先定，因为它不依赖实现细节：E3/E4/E5/E7/E8 都能用"库里有没有多出一行、哈希能不能复算"来判定。
- **不写代码不会拖慢 2.2**：2.2 一开始就要按 §7.5 建 `text_norm.py`，测试名（`test_quote_locator.py` / `test_quote_hash.py`）与用例数已写进路线图与验收清单。

---

## 4. 文件清单与 Git 状态核对结果

### 4.1 实测 Git 状态（`git status --short`）

```
 M docs/research_hub/README.md
 M docs/research_hub/acceptance_criteria.md
 M docs/research_hub/agent_architecture.md
 M docs/research_hub/api_design.md
 M docs/research_hub/current_state_audit.md
 M docs/research_hub/database_design.md
 M docs/research_hub/implementation_roadmap.md
 M docs/research_hub/m1_delivery_checklist.md
 M docs/research_hub/module_design.md
 M docs/research_hub/state_machine.md
 M docs/research_hub/system_architecture.md
 M docs/research_hub/tooling_architecture.md
?? docs/research_hub/.m12final_check.py      ← 复检脚本，交付前删除
?? docs/research_hub/m1_1_architecture_review.md
?? docs/research_hub/m1_2_decision_register.md
?? docs/research_hub/m1_2_delivery_report.md
```

- 已跟踪文档共 **13** 份（`git ls-files docs/research_hub`），目录内实际 **17** 份（含 4 份未跟踪：M1.1 报告、决策登记表、M1.2 交付报告、本报告）。
- `git diff --shortstat`（已跟踪文件，截至本报告撰写前）：**12 files changed, 1154 insertions(+), 216 deletions(-)**。

### 4.2 逐文件实测行数变化（`git diff --numstat`）

| 文件 | + | − | M1.2-final 本轮又改了哪里 |
|---|---|---|---|
| `database_design.md` | 583 | 68 | **§7.5 证据定位规范（新增 5 小节）**、§4.15.4 价目策略、§4.17 实体边界判定表、§3 第 17 行 B6、`pricing_unknown` 注释 |
| `system_architecture.md` | 129 | 18 | §4.1 第 6 条、§8.2 `pricing_policy` 注 |
| `agent_architecture.md` | 109 | 13 | §7 成本分工"价目缺失即拒绝" |
| `tooling_architecture.md` | 83 | 13 | §2.7 **⓪ 价目闸门**、`config/models.yaml` 例外块 |
| `implementation_roadmap.md` | 82 | 45 | 2.1 目标/文件/测试/验收/费用、§3 估算说明 |
| `api_design.md` | 61 | 18 | `details.reason=pricing_unknown` 段、`conflict` 行 |
| `acceptance_criteria.md` | 45 | 16 | 价目策略门 + 人工例外四用例、**E1–E8 证据定位**、文档数 17 |
| `state_machine.md` | 23 | 4 | — |
| `module_design.md` | 14 | 5 | §F `companies` 行加 D13 口径与 B6 批次 |
| `README.md` | 10 | 6 | 文档数 16→17、文档地图加本报告 |
| `m1_delivery_checklist.md` | 9 | 6 | M1-R4 改 Q8=B、文档数 17 |
| `current_state_audit.md` | 6 | 4 | — |

### 4.3 与 M1.2 交付报告 §6 / 附录 A 的核对结果

| 核对项 | 交付报告原写 | 实测 | 结论 |
|---|---|---|---|
| 改动文件数量 | 12 改 + 3 新增（+1 保留） | 12 改 + 4 未跟踪 | ✅ 数量吻合（本轮多出本报告） |
| 是否有代码文件 | 无 | `git diff -- backend config data alembic.ini tests` 空 | ✅ 无 |
| 总行数变化（附录 A） | `1003 insertions(+), 210 deletions(-)` | **1154 / 216** | ❌ **原数字错**（含 M1.2-final 增量），已在附录 A 加"数值更正"表并声明以实测为准 |
| 逐文件 `+N 行`（§6 表） | 估算值（如 database_design +547） | 实测 583/68 | ⚠️ **估算而非实测**，已在附录 A 用实测表覆盖 |
| 文档总数 | 16 份 | 写作前 16 份、本报告加入后 **17 份** | ✅ 同步更新 README / checklist / acceptance |

> **为什么要专门列这一节**：交付报告里"12 个文件"是对的，但"1003 insertions"是笔误级错误。
> 这种数字不影响架构判断，却会让下一次核对耗时——所以本轮把估算值全部换成 `git diff --numstat` 实测值。

### 4.4 `m1_1_architecture_review.md` 为什么是未跟踪（任务第 3 项）

| 检查 | 命令 | 结果 |
|---|---|---|
| 是否被 Git 跟踪 | `git ls-files --error-unmatch docs/research_hub/m1_1_architecture_review.md` | `error: pathspec … did not match any file(s) known to git` → **未跟踪** |
| 是否有过提交记录 | `git log --oneline -1 -- docs/research_hub/m1_1_architecture_review.md` | **空**（从未提交） |
| 文件是否完好 | 行数/字节 | **689 行 / 52,484 字节**，内容完整（§0–§13 + 附录 A/B/C） |

**结论：这是预期状态，不是丢失、不是遗漏。** 原因：M1.1 任务书第六节明确要求"**不要提交代码或推送 GitHub**"，
因此该报告自 2026-11-10 00:xx 创建后一直只在工作区。它将在下一次你批准的提交中与其余文档一起入库。
**本轮未覆盖、未删除、未修改它的任何内容。**

---

## 5. 业务代码零改动的验证结果

```powershell
# 1. 业务代码/配置/数据/测试/迁移配置 均无改动
git diff --name-only -- backend config data alembic.ini tests
# → （无输出）

# 2. 迁移文件数量未变（仍只有 M1 的初始迁移）
(Get-ChildItem backend\migrations\versions\*.py).Count
# → 1

# 3. 迁移版本未变、未执行任何 upgrade/downgrade
python -m alembic heads     # → de8148733a9b (head)
python -m alembic current   # → de8148733a9b (head)

# 4. 未创建/修改数据库文件（本轮所有操作均为只读读文件 + 改 Markdown）
```

补充约束执行情况：

| 任务书禁止项 | 本轮是否触碰 | 证据 |
|---|---|---|
| 修改业务代码 | **否** | 上述 `git diff` 无输出 |
| 修改数据库模型 / Alembic 迁移 | **否** | `backend/migrations/versions/` 仍 1 个文件；`backend/app/models/` 无 diff |
| 执行数据库迁移 | **否** | 未运行 `alembic upgrade`；`alembic current` 仍为 head |
| 调用模型 API / 搜索 API / 付费服务 | **否** | 全程只有 `pytest`、`git`、文件读写；无网络客户端代码被调用 |
| 修改密钥 / 环境变量 | **否** | `.env` 不存在；`.env.example` 无 diff |
| 修改 `backend/app/main.py:27` | **否** | 该文件无 diff（旧定位文案仍在，仍是待批项） |
| 自动进入 M2 | **否** | 本轮只做文档 |
| 提交 / 推送 | **否** | HEAD 仍为 `0029c3e`，与 origin/main 一致，`0 0` |

---

## 6. 测试结果

```powershell
.venv\Scripts\python.exe -m pytest -p no:warnings
# → 96 passed in 11.37s
```

| 项 | 改动前（M1.2 交付） | 本轮 | 结论 |
|---|---|---|---|
| 用例总数 | 96 | **96** | 无增无减（本轮不写代码、不加测试） |
| 通过 | 96 passed | **96 passed** | ✅ |
| 耗时 | 7.96–8.11s | 11.37s | 波动属机器负载，非回归 |
| 告警 | 1 个已知无害（`httpx` + `starlette.testclient` 弃用提示） | 同上（本次 `-p no:warnings` 未显示） | 未新增 |
| 迁移测试 | `test_migrations.py` 5 用例通过 | 同上 | 迁移链未被触碰 |

**跨文档一致性复检**（脚本 `docs/research_hub/.m12final_check.py`，只读，**交付前已删除**）：

```text
forbid 类 12 项：旧 Q7 最晚点 / 旧 Q8 策略 / 旧迁移口径 / 旧评分量纲 / 旧文档数(10/11/13) /
                 旧 TopicStatus 简写 / 旧预算字段名 / Q8 允许态 / 待确认残留(Q8)  → 全部 OK
require 类 14 项：D13 / Q5–Q9 关闭表 / Q7=B6 / Q8 拒调用 / Q9 快照永久 /
                 Q5 弱多态 / pricing_policy×2 / reject_unknown / 价目策略验收门 /
                 §7.5 / E1–E8 / companies B6 / pricing_unknown 原因码 … → 全部 OK
RESULT failed=0 doc_count=16
```

> 说明：脚本运行时本报告尚未写入，故计数为 16；写入后目录实有 **17** 份（已用 `(Get-ChildItem docs\research_hub\*.md).Count` = 17 复核）。
> 该脚本是过程工具，**交付前已删除**，不在仓库中留存。

---

## 7. 仍未解决的问题（诚实清单）

### 7.1 需要你授权的（3 项，不是设计决策）

| # | 事项 | 现状 | 建议 |
|---|---|---|---|
| A1 | 模型供应商与预算上限 | 未定；`config/models.yaml` 未创建；默认按日 ¥5 / 任务 ¥2 写进文档 | 2.1 开工前给一个供应商 + 预算；**Q8=B 下不给价目就调不通**，所以这项必然要在 2.1 前定 |
| A2 | 是否允许真实联网抓取 | 默认"先手动粘贴 URL / 上传"，2.2 不接真实网络 | 建议 2.2 先只做"手动 URL + 上传"，联网在 2.3 之后单独批 |
| A3 | 是否修 `backend/app/main.py:27` 的旧定位文案 | 仍是"以个人 IP 为核心的金融知识自媒体研究与内容生产平台" | 这是 API 文档字符串的一行，属"改代码"，需你点头；不影响功能 |

### 7.2 设计与工程层面仍存在的已知风险（不阻塞 2.0）

| # | 风险 | 性质 | 已做的缓解 | 什么时候必须解决 |
|---|---|---|---|---|
| R1 | 弱多态 `review_records` 会产生孤儿记录 | 结构性 | Q5=A 接受此代价 + 2.5 加孤儿巡检（返回 0 行为验收） | 2.5 起每次发布前巡检 |
| R2 | 价目表靠人工维护，`approved_exceptions` 也可能被写错 | 运营性 | 六项必填 + `expires_at` 过期即失效 + `pricing_gaps.log` 留痕 | 2.1 起每周对账 |
| R3 | 部分唯一索引 Alembic 不识别（`uq_topic_primary_collection`、`uq_platform_item_present`、`uq_script_seg_*`） | 工具性 | 迁移里手写 `op.create_index(..., unique=True, sqlite_where=...)` + PRAGMA 断言 | B1 / B6 两次迁移时 |
| R4 | `evidence` 新列与旧 `page_number`/`quote_locator` 并存 | 认知性 | §7.4 指定"新列是权威、旧列是人类辅助" | 2.2 起写代码时按此实现 |
| R5 | 六批次迁移的**回滚演练**尚未真正跑过（本轮禁止迁移） | 未验证 | §6.5 写了可执行步骤 + 保留策略 | **B1 第一次迁移时就要演练并贴输出** |
| R6 | M1.1/M1.2 四份过程文档仍在工作区未提交 | 流程性 | 本报告 §4.4 已说明原因 | 你批准提交后一并入库 |

### 7.3 明确"不是问题"的三件事（避免误读为遗漏）

1. **`research_reports.findings` 双写**：这是 Q9=A 的**有意设计**（表为权威、JSON 为快照），不是冗余。
2. **`topic_columns` 承担"栏目 + 合集"双语义**：Q6=A 已确认，长期如此。
3. **21 个端点全部无鉴权**：单机 `127.0.0.1` 下的**已知且已被接受的边界**，改公网前必须先做鉴权（已写进 `api_design.md` §2.3 三条安全边界）。

---

## 8. 本轮改动文件清单（供提交时核对）

**修改（12 个，均已跟踪）**

| 文件 | 本轮改了什么 |
|---|---|
| `docs/research_hub/database_design.md` | **§7.5 证据定位规范（新）**；§4.15.4 价目拒绝策略；§4.17 实体边界判定表；§3 第 17 行 B6；`pricing_unknown` 列注释 |
| `docs/research_hub/system_architecture.md` | §4.1 第 6 条（价目缺失=拒绝）；§8.2 `pricing_policy` 注 |
| `docs/research_hub/tooling_architecture.md` | §2.7 执行顺序增 ⓪ 价目闸门；错误处理；`config/models.yaml` 增 `pricing_policy` 与 `approved_exceptions` |
| `docs/research_hub/implementation_roadmap.md` | 2.1 目标/文件范围/测试/验收/费用；§3 成本说明 |
| `docs/research_hub/acceptance_criteria.md` | 2.1 价目策略门 + 人工例外四用例；2.2 增加 E1–E8 与偏移基准项；§4 文档数 17 |
| `docs/research_hub/api_design.md` | `conflict` 行增 `pricing_unknown`；`details.reason` 集合与新增说明段 |
| `docs/research_hub/agent_architecture.md` | §7 成本分工"全部"行 |
| `docs/research_hub/module_design.md` | §F 公司档案行（D13 + B6） |
| `docs/research_hub/m1_delivery_checklist.md` | M1-R4 改 Q8=B；文档数 17 |
| `docs/research_hub/README.md` | 文档数 17；文档地图加本报告 |
| `docs/research_hub/m1_2_decision_register.md` | **D2/D6/D9/D11 按人工结论改写**、**新增 D13**、**§3 改为 Q5–Q9 已关闭表**、依赖图与变更记录 |
| `docs/research_hub/m1_2_delivery_report.md` | §7 加"已关闭"更新与最终决定列；**附录 A 加数值更正表** |

**新增（1 个）**

| 文件 | 内容 |
|---|---|
| `docs/research_hub/m1_2_final_review.md` | 本报告 |

**未改动（保留原样）**

- `docs/research_hub/m1_1_architecture_review.md`（M1.1 审查报告，**未覆盖未删除**）
- `docs/research_hub/business_workflow.md`
- `docs/research_hub/current_state_audit.md`（本轮无新增改动，M1.2 已修）
- `docs/research_hub/state_machine.md`（同上）

---

## 9. 建议的提交方式（**等你批准后**才执行）

```text
提交类型：docs(m1.2-final)
提交内容：12 个已跟踪文档 + 4 个新增文档（M1.1 报告、决策登记表、交付报告、本报告）
不包含：任何 backend/ config/ data/ tests/ 改动（本轮未产生）
提交后推送：git push origin main（普通 push，不使用 --force）
```

**我现在的状态：停在这里等你确认。** 你回一句"提交推送"我才执行；在此之前仓库保持
`HEAD = 0029c3e = origin/main`、工作区仅上述文档改动。

---

## 附录：本轮实测命令与输出

```powershell
# —— Git 基线 ——
git rev-parse --abbrev-ref HEAD          # main
git rev-parse --short HEAD               # 0029c3e
git rev-parse --short origin/main        # 0029c3e
git status --short                       # 12 M + 4 ??（含复检脚本，交付前删除）

# —— 零代码改动 ——
git diff --name-only -- backend config data alembic.ini tests   # （无输出）
(Get-ChildItem backend\migrations\versions\*.py).Count          # 1
.venv\Scripts\python.exe -m alembic heads                       # de8148733a9b (head)
.venv\Scripts\python.exe -m alembic current                     # de8148733a9b (head)

# —— 测试 ——
.venv\Scripts\python.exe -m pytest -p no:warnings               # 96 passed in 11.37s

# —— 跨文档复检（只读脚本，跑完删除）——
.venv\Scripts\python.exe docs\research_hub\.m12final_check.py   # RESULT failed=0 doc_count=16

# —— 规模统计 ——
git diff --shortstat                                            # 12 files changed, 1154 insertions(+), 216 deletions(-)
```

**证据分级**（沿用 M1.1/M1.2 约定）：`[V]` = 本轮命令实测；`[d]` = 文档推理；`[u]` = 需未来代码验证。
本报告中 §4、§5、§6 全部为 `[V]`；§1、§2、§3 的"应然"部分为 `[d]`，其实现验证归入 2.0–2.2 阶段 `[u]`。
