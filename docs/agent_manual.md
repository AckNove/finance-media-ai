# Agent 团队手册（第一阶段）

第一阶段交付的是**角色定义 + 统一协议 + 可扩展调度接口**。
**没有接入任何模型 API、没有网页搜索工具、没有自动执行**。任何"AI 已经能自己写稿"的说法
在当前代码里都是假的：`RecordingDispatcher` 只把任务登记进 `agent_runs` 表，
状态停在 `pending`，`output_payload` 永远为空。

## 1. 九个角色

名册的**唯一真相**是 `agents/roster.yaml`（纯 YAML，代码读取）；角色提示词在
`agents/<key>/PROMPT.md`；轮子与规则在 `agents/PROTOCOL.md`。

| key | 名称 | 定位 | 主要 task_type |
| --- | --- | --- | --- |
| `orchestrator` | Research Orchestrator | 总调度、任务拆解、结果整合、缺口标记 | `plan_research`、`merge_results`、`audit_coverage` |
| `financial_researcher` | Financial Researcher | 金融工具、产品、市场机制与风险研究 | `research_instrument`、`research_mechanism`、`research_risk` |
| `football_researcher` | Football Business Researcher | 足球商业、俱乐部财务、转会与足球金融 | `research_club_finance`、`research_transfer`、`research_league_economics` |
| `industry_researcher` | Industry Researcher | 产业链、上下游、企业、利润分配 | `map_value_chain`、`research_profit_pool`、`research_company` |
| `case_analyst` | Case Analyst | 金融事件、企业案例、资金流向 | `analyze_case`、`trace_fund_flow`、`compare_cases` |
| `fact_checker` | Fact Checker | 事实、数字、公式、来源、类比边界与合规审核 | `review_facts`、`verify_numbers`、`check_analogy_boundary`、`compliance_review` |
| `scriptwriter` | Scriptwriter | 把研究报告转成可拍摄脚本 | `write_script`、`rewrite_script` |
| `visual_designer` | Visual Designer | 知识图解、产业链图、资金流向图、分镜 | `design_storyboard`、`design_chart`、`design_diagram` |
| `video_producer` | Video Producer | 配音、字幕、合成与质检工作流 | `plan_production`、`check_subtitles`、`run_qc` |

`fact_checker` 是**唯一强制闸门**：研究报告与脚本在没有 `pass` 结论前不得进入下一阶段。

## 2. 统一输入／输出协议

十个字段在 `agents/PROTOCOL.md` 中定义，代码实现为
`backend/app/services/agent_registry.py: AgentTaskEnvelope`：

| 协议字段 | AgentRun 列 | 说明 |
| --- | --- | --- |
| `task_id` | `task_id`（唯一） | `task-<hex16>`，由 `new_task_id()` 生成 |
| `agent_name` | `agent_name` | 角色 key（与 `roster.yaml` 一致） |
| `task_type` | `task_type` | 该角色支持的 task_type |
| `input` | `input_payload` | 任务参数（JSON） |
| `context` | `context_payload` | 上下文（选题、知识条目、既有结论） |
| `sources` | `sources` | 来源清单（url/publisher/accessed_at…） |
| `evidence` | `evidence` | 证据清单（claim/support_type/nature…） |
| `output` | `output_payload` | 产出（未执行时为 `null`） |
| `status` | `status` | `pending/running/succeeded/failed/cancelled` |
| `errors` | `error_payload` | 错误与不确定项 |

`output` 的推荐结构（详见 `agents/PROTOCOL.md`）：
`summary / statements[]（每条带 nature=fact|inference|opinion 与 evidence_ids）/
limitations / open_questions / handoff`。

## 3. 代码侧接口

```python
from backend.app.services.agent_registry import (
    AgentTaskEnvelope, RecordingDispatcher, UnavailableDispatcher,
    get_dispatcher, load_roster, roster_keys_missing_prompts,
)

load_roster()                  # -> list[AgentRoleSpec]，按 roster.yaml 顺序
roster_keys_missing_prompts()  # -> [] 表示九个提示词文件都在

task = AgentTaskEnvelope(task_id="", agent_name="fact_checker", task_type="review_facts",
                         input={"claim": "..."}, context={"topic_id": 3})
get_dispatcher(db).dispatch(task)   # 落库 agent_runs(status=pending)，不执行、不编造输出
```

- `TaskDispatcher` 是抽象接口，未来接真实执行器时**新增子类**并改 `get_dispatcher()`
  的返回值，调用点与协议不变。
- `UnavailableDispatcher(reason)` 用于显式失败（503 `dependency_unavailable`），
  绝不用假数据兜底。

## 4. 七条共同规则（提示词里逐条重申）

1. 不编造来源、数据、案例、时间与金额。
2. 每条关键事实必须能指回 `sources` + `evidence`。
3. 事实、推断、观点必须分开标注。
4. 不确定就标 `ambiguous` 或放进 `open_questions`，不许用 confident 语气掩盖。
5. 足球类比必须写清"哪里成立、哪里不成立"（`holds` / `breaks_down` 均必填）。
6. 不输出面向个人的具体投资买卖建议；涉及合规红线时升级为 blocker。
7. 不自动发布任何内容：`auto_publish` 在 `config/brand.yaml` 中为 `false`，
   发布永远是人工动作。

## 5. 新增或修改一个角色

1. 在 `agents/roster.yaml` 的 `agents:` 列表里加一条（`key/display_name/role/prompt_file/
   responsibilities/task_types`）；`prompt_file` 用相对 `agents/` 的路径。
2. 新建 `agents/<key>/PROMPT.md`，必须包含：角色定位、接受的 task_type、输入示例、
   输出 JSON 结构、禁止事项、质量检查清单、交接说明。
3. 跑 `pytest tests/test_agent_contracts.py -q`：会校验名册数量、提示词存在、长度、
   协议字段与调度落库行为。
4. 若角色影响 `GET /api/system/status` 的 `agents.discovered`，同步更新
   `tests/test_system_status.py` 的期望值。

注意：`load_roster()` 只读 YAML，**不要在 roster.yaml 里写 Markdown 说明**——
缩进一旦混入散文就会 `yaml.scanner.ScannerError`。

## 6. 第二阶段接线清单（尚未实现）

1. 实现 `LLMTaskDispatcher(TaskDispatcher)`：构造提示词 → 调用模型 → 校验输出结构 →
   写 `output_payload` 与 `status`。
2. 把 `prompts/*.md` 里的 `{{占位符}}` 与角色的 `PROMPT.md` 组装成真实提示词。
3. 增加来源抓取器（受限白名单 + 记录 `accessed_at` 与 `content_hash`）。
4. 增加 `agent_runs` 的失败重试与耗时统计（`started_at/finished_at/duration_ms` 已建字段）。
5. 给 `fact_checker` 的结论接入 `review_records`，形成可审计的审核链。
