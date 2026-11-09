# M1 交付记录与 Goal 能力自检

> 本文件是 M1（统一选题研究系统架构设计）的**执行记录**：能力自检、子任务、验收对照、实测证据、未完成事项与风险。
> 用途：让审查者不必翻遍全部文档，也能判断"M1 到底做完了没有、哪些是设计哪些是空话"。

---

## 1. Goal 模式能力自检（14 项，如实标注）

> 判定口径：**原生支持** = 当前会话里可直接调用并有工具支撑；**需额外配置** = 能力存在但要用户先授权/安装；**不支持** = 没有对应机制，须用替代流程；**尚未确认** = 我没有实测过。

| # | 能力 | 结论 | 依据 / 替代流程 |
|---|---|---|---|
| 1 | 长期目标保存 | **原生支持** | `create_goal` / `get_goal`；本 Goal id `goal-61ff4a4b-0572-4f34-b69e-1ee0eff052e3`，可用轮次 256 |
| 2 | 目标自动拆解为子任务 | **不支持（原生）** | 没有"把任务书自动变成任务对象"的机制。替代：把 M1.1–M1.7 显式拆成本文件 §3 的对照表 + `implementation_roadmap.md` 的 2.0–2.9 阶段表 |
| 3 | 任务依赖关系 | **不支持（原生调度）** | 无 DAG 引擎。替代：文档中画依赖图（`implementation_roadmap.md` §0），由我按序执行；如你要求，可用 `agent_teams` 的任务依赖（需你显式开启 AgentTeams） |
| 4 | 自主执行终端命令 | **原生支持** | `pwsh` 工具；本轮已实跑 pytest / git / alembic |
| 5 | 读取与修改项目文件 | **原生支持** | `read` / `write` / `edit` / `glob` / `grep` |
| 6 | 运行自动化测试 | **原生支持** | `.venv\Scripts\python.exe -m pytest` |
| 7 | 依测试错误自主修复 | **原生支持** | 已实证：本轮之前修过 6 处测试假设错误、迁移 CHECK 约束错误、枚举列错误 |
| 8 | 调用多个 Agent / 子任务 | **原生支持（本项目未使用）** | `subagent` / `subagent_fork` / `workflow` / `agent_teams`；M1 是单人设计任务，用子 Agent 反而增加不一致风险 |
| 9 | 跨多轮保留进度 | **原生支持** | 同一 session + Goal 轮次 + 工作区与 git 提交作为持久记忆 |
| 10 | 阻塞后暂停等用户 | **原生支持** | 结束回合并在回复中提问；`blocked` 状态需同一阻断原因持续 ≥3 轮才可标记 |
| 11 | 阶段完成后等待人工批准 | **机制支持，非系统级闸门** | 我能"停手并列出待批项"，但**没有**强制审批控件；批准 = 你的下一条消息。因此我把"进入 M2"写成显式停止点 |
| 12 | Git commit 与 push | **原生支持** | 本机凭据已配置；禁 force push（已遵守）；提交信息用 UTF-8 临时文件 + `git commit -F`（PowerShell 无 heredoc） |
| 13 | 自主调用外部 API / 网络搜索 | **需额外配置** | Harness 侧有 `web_search` 工具，但**项目代码内**没有任何模型/搜索客户端；且未配置任何密钥。M1 不需要，M2/M3 需你授权 |
| 14 | 记录每个子任务的执行结果 | **部分（需约定）** | 会话内有工具调用记录、git 提交有记录；**没有**结构化的 run ledger 文件。替代：本文件 §3 手工登记；如需严格台账，可加 `docs/research_hub/m1_run_log.md`（会增加噪声，默认不加） |

**结论**：Goal 模式在"保存目标 + 自主执行命令/改文件/跑测试/修错误/提交推送/跨轮续做"上是真实可用的；在"自动拆解 + 依赖调度 + 结构化 run ledger + 系统级审批闸门"上**不具备原生能力**，本报告全部用显式清单与文档替代，未假装拥有。

---

## 2. M1 里程碑执行卡（按任务书 §四.1 的十项纪律）

| 项 | 内容 |
|---|---|
| **目标** | 完成现状审计 + 七大模块设计 + 三项数据库决策落地 + 九 Agent 架构 + 工具架构 + 数据库设计 + API 设计 + 路线图；**只设计不开发** |
| **子任务** | M1.1 现状审计 / M1.2 七大模块 / M1.3 三项决策 / M1.4 Agent 架构 / M1.5 工具架构 / M1.6 数据库设计 / M1.7 API 设计（+ M1.8 路线图与验收标准，任务书未列但属必要交付） |
| **前置依赖** | M0 已完成的品牌与内容配置（`config/brand.yaml`、`content_rules.yaml`、`content_taxonomy.yaml`）、19 表 / 21 端点 / 96 测试的既有代码基线 |
| **预计修改文件** | `docs/research_hub/*`（新建 13 份）+ `README.md`（一行索引）。**明确不改** `backend/`、`config/`、`data/`、`alembic.ini` |
| **执行步骤** | ①实测审计（表/端点/Agent/测试/迁移）②写架构与流程 ③写数据库设计（38 表方案 + 迁移与回滚）④写 Agent 与工具契约 ⑤写 API 与状态机 ⑥写路线图与验收标准 ⑦补模块逐条对照与逐工具测试方法 ⑧跑测试验证零回归 ⑨提交推送 ⑩停 |
| **测试标准** | `pytest` 96 passed 不变；`alembic_version` 仍为 `de8148733a9b`；`git diff --name-only HEAD~1 HEAD -- backend config data` 为空 |
| **验收标准** | 见 §3 对照表逐条为 DONE；13 份文档可独立阅读；关键判断与仓库代码一致（抽查 3 处） |
| **完成状态** | **M1 设计交付完成**（见 §3、§4）；**实现全部未开始**（属 M2–M10） |
| **未完成事项** | ①工作台 UI 设计未做（任务书未要求，M8 再设计）②未做真实模型/搜索的任何接入（禁止）③`backend/app/main.py:27` 旧定位文案未改（待你批准 Q6）④向量检索明确不做（决策：SQLite + LIKE） |
| **风险** | 见 §5 |

---

## 3. M1 交付对照表（逐条可核）

| 任务书要求 | 交付位置 | 状态 |
|---|---|---|
| **M1.1** 审计 backend 模块 / 模型 / 19 表 / 外键索引约束 / 迁移历史 / 全部 API / 九 Agent / 调度与执行记录 / 来源证据报告模型 / 测试 / config-docs-README-roadmap | `current_state_audit.md`（19 表 + 21 端点 + 9 Agent + 96 测试逐项，含文件位置） | **DONE** |
| M1.1 输出五分类（已实现/部分/只有定义/未实现/需改造） | `current_state_audit.md` §2–§7（图例 `DONE`/`DATA-ONLY`/`PROMPT-ONLY`/`NOT IMPL`/`NEEDS ADJ`） | **DONE** |
| M1.1 特别要求：RecordingDispatcher ≠ 真实执行 | `current_state_audit.md` §4（明确写：只写 `agent_runs(status=pending)`，`output_payload` 为空） | **DONE** |
| **M1.2** A Topic Hub（人工/主动发现/新闻事件采集/评分/去重/多合集/多标签/审核/优先级/状态 + 六维评分） | `module_design.md` §A（含权重建议与"候选 ≠ 立项"约束） | **DONE** |
| M1.2 B Research Hub（创建/目标/范围/拆解/计划/调度/依赖/进度/重试/暂停恢复/人工介入/成本日志） | `module_design.md` §B | **DONE** |
| M1.2 C Source Hub（搜索/抓取/公告财报/PDF/Excel/上传/存储/来源信息/抓取时间/发布时间/内容版本） | `module_design.md` §C（含"AI 摘要不得当来源"） | **DONE** |
| M1.2 D Evidence Hub（事实与数字提取/片段/来源与结论关联/交叉验证/冲突/可信度/历史版本） | `module_design.md` §D | **DONE** |
| M1.2 E Analysis Hub（8 类研究 + 五档结论） | `module_design.md` §E（8 类框架表 + `FindingTier` 最低证据要求） | **DONE** |
| M1.2 F Knowledge Hub（公司/行业/产业链/概念/案例/历史研究/关联；不引向量库） | `module_design.md` §F | **DONE** |
| M1.2 G Content Hub（视频/图文/短/长脚本/会员/课程/版本/审核/发布状态/效果反馈；研究≠作品） | `module_design.md` §G（含"效果反馈回流评分"） | **DONE** |
| **M1.3** 决策一：多对多 `topic_collections`/`topic_tags`/`tags` | `database_design.md` §4.1–4.3；`api_design.md` §3.2 | **DONE** |
| M1.3 决策二：复用 `topic_columns`，不建重复合集表 | `database_design.md` D2 + §6.6（`column_id` 双写与退场计划） | **DONE** |
| M1.3 决策三：不加 `concepts` 枚举 | `database_design.md` D3；`module_design.md` §F | **DONE** |
| **M1.4** 九 Agent 的定位/输入/输出/工具/数据库权限/调度权限/边界/失败/质量/成本 | `agent_architecture.md` §3（9 张表，逐角色十项） | **DONE** |
| M1.4 动态调度，不让所有 Agent 无差别参与 | `agent_architecture.md` §2 能力矩阵 + §4 routing 白名单（9 种 task_type） | **DONE** |
| **M1.5** 9–10 类工具的实现/输入输出/权限/费用/错误处理/测试方法 | `tooling_architecture.md` §2.1–2.10（逐工具六项）+ §2.11 逐工具测试方法表 | **DONE** |
| **M1.6** 现有 ER / 目标 ER / 新增 / 修改 / 保留 / 字段 / 主外键 / 索引 / 唯一约束 / 迁移 / 回滚 | `database_design.md` §2–§6（§5 改列清单、§5 末"明确不改"、§6.5 回滚策略） | **DONE** |
| M1.6 每张表解释存在价值，不为复杂而加表 | `database_design.md` §4 开头"为什么存在"对照表（19 张逐条） | **DONE** |
| **M1.7** API 路由/方法/请求参数/响应结构/权限/错误码/示例 | `api_design.md` §2.1–2.2（清单）+ §2.3（权限与错误码总表）+ §3（6 个完整示例） | **DONE** |
| （隐含要求）端到端示例流程 | `business_workflow.md`（14 步主流程 + 17 步推演，明确标注为设计示例） | **DONE** |
| （隐含要求）状态机 | `state_machine.md`（七套） | **DONE** |
| （隐含要求）开发路线图与验收标准 | `implementation_roadmap.md`、`acceptance_criteria.md` | **DONE** |
| （隐含要求）Mermaid 图，便于直观理解 | 全部 13 份文档含至少一处 Mermaid 或对照表 | **DONE** |

---

## 4. 实测证据（本文件写入时实跑）

```
pytest                                  → 96 passed in 11.71s, 1 warning
git log -1 --oneline                    → decebf0 docs(research-hub): 第二阶段统一选题研究系统架构设计
git status --porcelain                  → 只有 docs/research_hub/（本次增补）
git diff --name-only decebf0 -- backend config data tests alembic.ini → 空（零代码改动）
alembic_version                         → de8148733a9b（无新迁移）
docs/research_hub/ 文档数                → 13
本地 HEAD == origin/main                → 一致
```

**抽查 3 处关键判断与真实代码是否一致**（脚本实跑，非人工肉眼）：

| 文档中的判断 | 验证方式 | 实测结果 |
|---|---|---|
| `RecordingDispatcher` 只写 `agent_runs(status=pending)`，不写 `output_payload` | `inspect.getsource(RecordingDispatcher.dispatch)` | `status_pending=True`、`no_output_write=True` ✅ |
| 现有 API 为 21 个端点 / 14 条路径 | `TestClient.get("/openapi.json")` 计数 | `path_count=14`、`operation_count=21` ✅ |
| 当前数据库 19 张表（含 `alembic_version`） | `sqlalchemy.inspect(engine).get_table_names()` | `table_count=19` ✅ |

> 说明：本节数字在每次文档增补后由我重新实跑核对；若与仓库不一致，以仓库为准。

---

## 5. M1 层面的风险

| # | 风险 | 缓解 |
|---|---|---|
| M1-R1 | 38 表方案偏大，落地时可能发现 3–5 张表冗余 | 迁移分五次执行；每张表在 `database_design.md` §4 都有"为什么存在"，删表时优先删"暂无写入方"的表 |
| M1-R2 | 设计文档与后续实现漂移 | 每阶段强制同步 `current_state_audit.md`；G8 质量门 |
| M1-R3 | 无鉴权的前提下路线图涉及"发现选题"等主动联网能力 | 2.7 的发现能力默认关闭，必须显式开启并人工立项 |
| M1-R4 | 模型/搜索供应商未定，成本模型是估算 | `pricing_unknown=true` 时不显示金额；2.1 接入前先做小样本实测 |
| M1-R5 | 本文档记录的能力自检会随 Harness 升级过期 | 每进入新里程碑重做一次自检（§1） |

---

## 6. M2 前置审批项（未获批准不启动）

1. 模型供应商与预算上限（默认日 ¥5 / 任务 ¥2）
2. 是否允许真实联网搜索与抓取（默认先用"手动粘贴 URL"）
3. 迁移一次性还是分五次（默认分五次）
4. 是否顺手修 `backend/app/main.py:27` 的旧定位文案

> 以上四项在 `acceptance_criteria.md` §6 有同样记录（Q1–Q6），两处保持一致。
