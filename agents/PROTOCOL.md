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
