# Agent 统一输入输出协议（v1）

所有 Agent（人类编排的角色文件 + 未来接入的模型执行器）必须使用同一份协议。
协议与 `agent_runs` 表字段一一对应，因此"一次任务"在数据库里就是一行可追溯记录。

## 1. 协议字段

| 字段 | 类型 | 必填 | 说明 | 对应 `agent_runs` 列 |
| --- | --- | --- | --- | --- |
| `task_id` | string | 是 | 形如 `task-3f2a9c…`，由调度器生成，全局唯一 | `task_id` |
| `agent_name` | string | 是 | 角色 key，取自 `agents/roster.yaml` | `agent_name` |
| `task_type` | string | 是 | 该角色接受的任务类型，见 roster 的 `task_types` | `task_type` |
| `input` | object | 是 | 任务参数（选题、研究问题、范围、约束） | `input_payload` |
| `context` | object | 否 | 上游产物引用：`topic_id`、`research_report_id`、`script_id`、术语表 | `context_payload` |
| `sources` | array | 否 | 已采集来源列表（`source_id` / `title` / `url` / `publisher` / `accessed_at`） | `sources` |
| `evidence` | array | 否 | 证据列表（`evidence_id` / `claim` / `support_type` / `page_number`） | `evidence` |
| `output` | object \| null | 否 | 产物；未执行时必须为 `null`，不得填占位文本 | `output_payload` |
| `status` | enum | 是 | `pending` / `running` / `succeeded` / `failed` / `cancelled` | `status` |
| `errors` | array | 否 | 错误对象数组：`{code, message, retryable}` | `error_payload` |

辅助字段（由调度器填写）：`requested_at`、`started_at`、`finished_at`、`duration_ms`。

### 1.1 最小示例

```json
{
  "task_id": "task-9f1c0d7b6a5e4f32",
  "agent_name": "financial_researcher",
  "task_type": "research_instrument",
  "input": {
    "question": "可转债的转股价值与溢价率如何驱动定价？",
    "domain": "financial_instruments",
    "audience_level": "intermediate",
    "constraints": {"jurisdiction": "CN", "as_of_date": "2026-06-30"}
  },
  "context": {"topic_id": 3, "glossary": ["转股价", "纯债价值", "溢价率"]},
  "sources": [],
  "evidence": [],
  "output": null,
  "status": "pending",
  "errors": []
}
```

## 2. 输出约定

`output` 的结构由 `task_type` 决定，但所有任务都必须包含：

```json
{
  "summary": "一段话结论",
  "statements": [
    {"statement": "...", "nature": "fact|inference|opinion", "evidence_refs": [1, 2], "confidence": "high|medium|low"}
  ],
  "limitations": "本次回答没有解决的问题与适用边界（必填，不许留空）",
  "open_questions": ["..."],
  "handoff": {"next_agent": "fact_checker", "reason": "..."}
}
```

- `nature` 必须是 `fact` / `inference` / `opinion` 三者之一。
- `fact` 且 `confidence` 为 `high` 的结论必须有 `evidence_refs`，否则降级为 `inference`。
- `limitations` 必填：它是"这份研究哪里不能用"的声明，不是客套话。

## 3. 硬性规则（所有角色共同遵守）

1. **不编造**：不虚构来源、数字、日期、公司、案例或引文。找不到就说找不到。
2. **三分法**：每条结论必须标注是事实、推断还是观点，禁止把推断写成事实。
3. **证据链**：重要事实必须关联 `evidence`；没有证据的重要事实一律不得进入脚本。
4. **不确定性显式化**：数据缺口、口径冲突、时间滞后必须写进 `limitations`，而不是含糊带过。
5. **类比边界**：足球类比必须同时说明"成立的部分"和"不成立的部分"；只讲成立部分视为违规。
6. **不自动发布**：任何 Agent 都不得触发发布动作；发布只能由人工执行。
7. **不做投资建议**：不输出面向个人的具体买卖、点位、仓位、标的选择建议。
8. **数据口径**：涉及金额、比例、时间的数据，必须注明币种、单位、时点与适用辖区。
9. **不越权**：超出角色职责的结论只标记为待办（写进 `open_questions`），不代替其他角色下结论。
10. **可追溯**：`output` 中出现的每个数字都应能通过 `evidence_refs` 回到 `sources`。

## 4. 状态机

```text
pending ──dispatch──> running ──成功──> succeeded
                         │
                         ├──业务失败（缺数据/无来源）──> failed（errors 里写 code，可重试）
                         └──人工取消────────────────> cancelled
```

- `failed` 必须带 `errors`，`message` 要写清"缺什么"，便于下一轮补齐。
- 任何角色都不得把 `failed` 直接改成 `succeeded`；修复后必须重新走一次任务。

## 5. 与调度器的关系（第一阶段实现）

- 调度器接口：`backend/app/services/agent_registry.py::TaskDispatcher`。
- 当前实现：`RecordingDispatcher` —— 把任务写入 `agent_runs`（`status=pending`、`output=NULL`），
  **不执行、不编造**。这是本阶段唯一允许的行为。
- 未接入执行器时若调用 `UnavailableDispatcher`，会返回 503 `dependency_unavailable`，
  明确告知"执行器不可用"，而不是返回假结果。
- 未来接入模型/搜索：新增一个 `TaskDispatcher` 子类（例如 `LLMDispatcher`），
  在 `get_dispatcher()` 中按配置返回；协议、数据库表与调用点保持不变。

## 6. 品牌与内容规则（所有角色必须遵守）

协议解决"怎么交接"，本节解决"产出必须符合什么标准"。配置真相见
`config/brand.yaml`、`config/content_taxonomy.yaml`、`config/content_rules.yaml`；
人类可读版见 `docs/brand_strategy.md` 与 `docs/content_strategy.md`。

### 6.1 身份与署名

- 对外内容品牌：**AK带你拆解世界**；个人 IP：**电力之王AK**（IP 名只上账号主页）。
- 产出面向读者，不面向"投资人"；不写"我建议你买"这类第一人称决策语句。

### 6.2 每条产出必须声明分类

在 `output` 中补充（除协议必填字段外）：

```json
{
  "collections": ["football_finance", "business_cases"],
  "primary_collection": "football_finance",
  "audience_level": "mass|learner|researcher",
  "tags": {"instrument": [], "industry": [], "geography": [], "period": [], "mechanism": []}
}
```

- `collections` 取自六大合集 id（`financial_knowledge` / `market_briefing` /
  `industry_research` / `company_teardown` / `football_finance` / `business_cases`），
  不得自造；新合集必须先在 taxonomy 中登记。
- 无法判断归属时，写进 `open_questions` 交给 `orchestrator`，**不要猜一个合集凑数**。

### 6.3 表达风格（硬要求）

1. 观点鲜明：结论必须是一个清晰判断，不能只罗列事实。
2. 犀利但守边界：可以指出利益方与代价，不做人身攻击、不嘲讽具体个人。
3. 有趣但不低俗：允许调侃与反差，禁止段子替代解释。
4. **不做新闻搬运**：任何时事产出必须包含"为什么发生 + 谁受益 + 机制是什么"三层增量。
5. 禁止标题党与情绪表述（"震惊""崩了""全都完了"），禁止用术语堆砌制造专业感。
6. 观点类结论（`nature=opinion` 或 `inference`）必须同时列出**支撑证据与反方证据**，
   并写出结论的失效条件。

### 6.4 足球的定位

- 足球是六大合集之一（`football_finance`），也是讲金融机制时的类比场景；
  **它不是账号的内容边界**，也不得被默认塞进其它合集的每一篇产出。
- 一旦使用足球类比：`holds`（成立的部分）与 `breaks_down`（不成立的部分）**均必填**，
  且类比文字占比不得超过脚本的 35%（`brand.yaml#analogy_policy`）。

### 6.5 合规（任何角色发现即阻断）

- 不输出买卖建议、点位、仓位、目标价、收益承诺、内幕消息。
- 命中 `content_rules.yaml#compliance.human_review_triggers` 任一关键词 → 以
  `blocker` 升级，整份产出判 `reject`，不得自行改写规避。
- 涉及商业合作的内容必须标注披露（`collaboration.disclosure_text_template`）；
  品牌方要求修改结论时，直接写进 `errors` 并终止该产出的交接。
- 数据必须带币种、单位、时点、辖区；跨境案例必须写明适用司法辖区。

### 6.6 越权与交接

- 合集的 `agents.primary` 决定谁下结论；协作角色只提供材料与 `open_questions`。
- 跨合集选题由 `orchestrator` 指定 primary 角色，其余角色以 support 身份交付。
- 任何情况下都不允许"为了交付而编造"，`failed` + 说清缺什么，比一份漂亮的假产出更合格。
