# Agent 团队手册（V6.0 现状）

> 这份文件描述**当前真正在跑的东西**，不是设想。
> 上一版（M1 阶段）写的是"角色定义 + 协议骨架、没有接入模型"，那个阶段已经过去了；
> 现在九个角色由 `TaskRuntime` 真派发、真调模型、真记账。
>
> 唯一真相分散在四处，本文件只是把它们说成人话：
> `agents/roster.yaml`（角色名册）、`agents/<key>/PROMPT.md`（提示词与接受的 task_type）、
> `config/agent_routing.yaml`（每个 task_type 允许谁、能用哪些工具、能写哪些表）、
> `skills/registry.yaml`（Skill 绑定到哪些角色与 task_type）。

## 1. 九个角色

| key | 名称 | 接受的 task_type | 能用的工具 | 能写的表（`db_scopes`） |
| --- | --- | --- | --- | --- |
| `orchestrator` | Research Orchestrator | `topic.discover`、`research.plan`、`research.synthesize` | `llm_complete`、`web_search`、`db_query`、`db_write`、`task_update`、`handoff` | topics、research_tasks、research_subtasks、report_findings、task_events |
| `financial_researcher` | Financial Researcher | `topic.discover`、`research.collect`、`research.extract`、`research.analyze`、`research.synthesize` | `web_search`、`fetch_url`、`parse_pdf`、`parse_docx`、`parse_xlsx`、`llm_complete`、`db_query`、`db_write`、`task_update`、`handoff` | sources、source_snapshots、source_extractions、evidence、report_findings |
| `football_researcher` | Football Business Researcher | 同上（同一组采集/提取/分析/整合） | 同上 | 同上 |
| `industry_researcher` | Industry Researcher | 同上 | 同上 | 同上 |
| `case_analyst` | Case Analyst | 同上 | 同上 | 同上 |
| `fact_checker` | Fact Checker | `verify.claims` | `fact_check`、`llm_complete`、`db_query`、`handoff` | evidence、report_findings、review_records |
| `scriptwriter` | Scriptwriter | `content.script` | `llm_complete`、`db_query`、`db_write`、`handoff` | scripts、script_evidence_links、report_findings |
| `visual_designer` | Visual Designer | `content.visual` | `llm_complete`、`db_query`、`db_write` | media_assets、scripts |
| `video_producer` | Video Producer | `content.produce` | `db_query`、`db_write`、`task_update` | media_assets、publications |

每个角色的 `PROMPT.md` 里有一行**声明式**的「本角色在注册表中接受的 task_type」；
`backend/app/execution/routing.py` 启动自检会把这一行、`config/agent_routing.yaml`、
`backend/app/execution/task_types.py` 三处逐项比对，任何不一致都拒绝启动。

## 2. 十个 task_type 与三处人工闸门

| task_type | 中文 | 谁做 | 是否人工闸门 |
| --- | --- | --- | --- |
| `topic.discover` | 选题发现 | orchestrator + 四个研究角色 | 否 |
| `research.plan` | 研究计划拆解 | orchestrator | 否 |
| `research.collect` | 资料采集 | 四个研究角色 | 否 |
| `research.extract` | 证据提取 | 四个研究角色 | 否 |
| `research.analyze` | 分析 | 四个研究角色 | 否 |
| `research.synthesize` | 结论整合 | orchestrator + 四个研究角色 | 否 |
| `verify.claims` | 事实核查（含语义核查） | fact_checker | **是** |
| `content.script` | 脚本撰写 | scriptwriter | **是** |
| `content.visual` | 视觉设计 | visual_designer | 否 |
| `content.produce` | 视频制作计划 | video_producer | **是** |

闸门的实现方式：`config/pipelines.yaml` 把 `require_human_review` 标在**前一个** task 阶段上，
`Worker._pause_for_gate` 在那一格跑完后把任务置 `paused`，等人的 `/resume`。
**阻塞不是失败**：`blocked` 会让任务停在 `paused` 并写事件，不会把子任务标成 failed。

## 3. 运行时是怎么跑起来的

```
POST /api/research-tasks → Orchestrator.plan_task() 建 9 个阶段（写 task.plan）
run_worker --task-id N → TaskRuntime.claim_next() 拿租约（lease_until + attempt_token）
                        → AgentExecutor.execute(claim) 按 task_type 分派 handler
                        → TaskRuntime.complete/fail + TaskEvent（事件序号唯一）
                        → _refresh_readiness() 解锁下游；失败/阻塞向依赖链传染
```

- 每个子任务的行在 `research_subtasks`，产出在 `output_payload`，事件在 `task_events`。
- 失败重试上限 2 次（`max_attempts`），重试也**照常记账**（见成本说明）。
- 研究计划阶段可以用模型规划，失败时自动退回规则拆词，并在产出里如实标
  `planner=keywords` + `degraded_reason`；模型规划成功时会动态追加 1–2 条补充检索任务
  （`direction_changed` 事件）。
- 每个 handler 只做一件事，且**只写自己那道工序该写的东西**：
  `_discover`（选题候选，不检索）、`_plan`（DAG）、`_collect`（搜索+抓取+快照）、
  `_extract`（解析+证据行）、`_analyze`（模型分析 + 骨架归节）、`_synthesize`（报告）、
  `_verify_claims`（逐字核对 + 模型语义核查）、`_script`/`_visual`/`_produce`（内容三阶段）。

## 4. 五个 Skill 与注入规则

| Skill | 给谁 | 用在哪些 task_type | 许可证 |
| --- | --- | --- | --- |
| `accessible-explainer` | financial_researcher、scriptwriter | `research.analyze`、`content.script` | Proprietary（自有） |
| `investigative-storytelling` | orchestrator、scriptwriter | `research.synthesize`、`content.script` | Proprietary（自有） |
| `ak-signature` | scriptwriter | `content.script` | Proprietary（自有） |
| `pdf` | 四个研究角色 | `research.extract` | Apache-2.0（Anthropic，commit `2c5958e`） |
| `xlsx` | 四个研究角色 | `research.extract` | Apache-2.0（同上） |

注入规则（`backend/app/research/analysis.py` 的 `_skill_block`）：

1. 只有**Agent 身份**能加载 Skill（人身份会被拒），加载失败只记 warning、**绝不打断研究链**；
2. 正文最多 1800 字，超出截断并加「…（Skill 正文过长，已截断）」；
3. 位置在系统提示词之后、明确标注「**只约束怎么写，不改变只能引用证据这条硬规则**」；
4. 用了哪些 Skill 会写进产出（`skills_used`），便于回溯这条稿子受了哪套方法影响。

## 5. 十一个工具与三道闸门

`web_search`、`fetch_url`、`parse_pdf`、`parse_docx`、`parse_xlsx`、`llm_complete`、
`db_query`、`db_write`、`fact_check`、`task_update`、`handoff`。

每次派发都要过 `backend/app/capabilities/guard.py` 的三道闸门：

1. 调用者类型是否在该工具的 `allowed_kinds` 里（`agent_only` 的工具人调不了）；
2. 这个角色在 `config/agent_routing.yaml` 里有没有被授权用这个工具；
3. `db_write` 额外要过白名单表 + `db_scopes` + 金额列禁令（三层）。

工具自己也会拒绝：搜索类在 `features.search=false` 时返回 `feature_disabled` 且**一个请求都不发**。

## 6. 加一个角色 / 工具 / Skill

| 想加 | 改哪里 | 别忘 |
| --- | --- | --- |
| 角色 | `agents/roster.yaml` + `agents/<key>/PROMPT.md`（含"接受的 task_type"那一行）+ `config/agent_routing.yaml` 的 `agents:` 段（**工具授权读这里，不读 `task_types:` 段**） | 启动自检会核对；`tools_for()` 缺一项就派发失败 |
| 工具 | `backend/app/capabilities/tools.py` 的 `TOOL_SPECS` + 适配器注册 + 各角色授权 | 只有真实现的工具才能标 `implemented=True` |
| Skill | 目录 + `SKILL.md`（YAML frontmatter 的 `name`/`description`）+ `skills/registry.yaml` 登记（含 sha256、agents、task_types） | 第三方必须写 `source`/`upstream_commit`/许可证，并进 `PROVENANCE.md` |

## 7. 已知边界

- **不同 Prompt ≠ 多个 Agent**：九个角色的差别在提示词、工具授权与可写表，**不在模型**；
  它们共用同一个模型网关（当前 `deepseek-flash`，可降级到 v4-pro）。
- **模型只负责"怎么写"**：结论必须来自证据；模型给的每条结论都要挂上已知来源，
  挂不上的进 `dropped`，不进报告。
- **只有人工确认的结论能进成稿**：脚本、视频任务、`produce_video` 三个出口都拦。
- 角色之间的 `handoff` 是**记账不是催办**：只有 `handoff=need_human` 会改任务状态。
