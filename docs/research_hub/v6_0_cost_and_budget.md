# V6.0 成本与预算说明

> 这份文件回答三个问题：**花的是谁的钱、每次调用怎么记账、怎么保证不会失控**。
> 所有数字都可以用仓库里的脚本复核，不靠记忆。

## 1. 三个闸门 + 一个总开关

| 闸门 | 位置 | 默认值 | 触发时发生什么 |
| --- | --- | --- | --- |
| 单次调用上限 | `BudgetContext.max_cost_per_call_cny`（`capabilities/model/gateway.py` 传给 `budget.evaluate`） | 由审批例外决定，未登记则不限 | 估算超上限 → `budget_blocked`，**不发请求** |
| 任务累计预算 | `research_tasks.budget_max_calls` / `budget_max_cost` | 建任务时写死（验收脚本用 8 次 / 0.5 元） | 超出 → `budget_blocked`，任务留痕不静默 |
| 当日总额 | `config/settings.yaml` → `cost.daily_cap_cny` | **5.0 元** | 当天累计到顶 → 拒绝一切新调用（`budget.evaluate` 返回 `daily_cap_exceeded`） |
| 总开关 | `features.model` | `true` | 关掉后网关在**发请求之前**返回 `feature_disabled`，一分钱不花 |

另外两道与钱有关的硬规矩：

1. **价目未知 = 拒绝**。`cost.pricing_policy: reject_unknown`，模型没有登记输入/输出单价时直接
   `budget_blocked / pricing_missing`，不发请求、不写 reserved 行。
2. **降级不花糊涂钱**。`fallback_to` 指向的备用模型必须 `enabled` 且**输入输出价目都非空**，
   否则网关只记一条 warning 就放弃降级（`gateway._fallback_target`）。

## 2. 当前价目（2026-11-12 核对官方文档）

来源：<https://api-docs.deepseek.com/quick_start/pricing>，按 **1 USD = 7.1 CNY**、取**高峰价 / 缓存未命中**折算。

| 模型 | 输入（元/千 token） | 输出（元/千 token） | 上下文 | 限流 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `deepseek-flash` | 0.00213 | 0.00852 | 1M | 60 次/分、并发 2 | 日常研究与改写；超时/5xx/限流时降级到 v4-pro |
| `deepseek-v4-pro` | 0.009372 | 0.028116 | 1M | — | 备用与难例 |
| `deepseek-chat` / `deepseek-reasoner` | — | — | — | — | **已下架，仅作历史记录保留且禁用、价目留空** |

官方价按「百万 token、美元」挂牌，这里换算成「千 token、人民币」，并把换算口径写进
`config/models.yaml` 每条的 `price_basis` 与核对日期 `price_checked_at`。
**没核过的价格宁可留空**——留空会被闸门拒绝，写错会被当成真账。

## 3. 一次调用是怎么记账的

```
预算预检（估算） → 发请求 → 用真实 usage 复算成本 → 写 model_calls → 累加 research_tasks.cost_spent
```

- `model_calls` 每行都带 `provider/model/purpose/prompt_tokens/completion_tokens/cost_estimate/
  pricing_unknown/cache_hit/status/error_message/duration_ms`，`prompt_preview` **不含密钥**。
- **重试也要记**：规划/分析/核查撞 `max_tokens` 会减少条数重试，每次尝试都真花钱。
  三个模块（`planning.py` / `analysis.py` / `semantic.py`）都按 `spent` 累加，
  失败的尝试由 `runtime.fail(calls=…, tokens=…, cost_cny=…)` 记进任务账。
- **账要对得上**：任意时刻 `SUM(model_calls.cost_estimate) == research_tasks.cost_spent`。
  验收脚本会直接断言这一条（`accounting_ok`），V6.0 最后一轮实测 `0.168163 == 0.168163`。

自查命令：

```powershell
python -m backend.scripts.v51_evidence            # 最近一次 e2e 的模型调用明细与合计
python -m backend.scripts.model_smoke             # 不带密钥就退出码 3，不花钱
curl http://127.0.0.1:8000/api/system/costs       # 当日/累计成本与调用数
```

## 4. 真实花过多少钱（可复核）

| 批次 | 调用次数 | 花费（元） | 备注 |
| --- | --- | --- | --- |
| V5.0 单主题闭环（8 次 e2e 迭代） | 17 | ≈1.48 | 含 4 次调参重跑 |
| V5.1 语义核查 + 模型规划 | 4 | 0.159294 | 跑通后定稿那一轮 |
| V6.0 三主题验收（行业/产业链/上市公司） | 13 | 0.494162 | 73 来源 / 1060 证据 |
| V6.0 链式复跑（修记账前） | 5 | 0.151573 | 暴露"重试的钱没记账" |
| V6.0 链式复跑（修完后定稿） | 5 | 0.168163 | 11/11 子任务、含脚本改写 |
| 脚本改写单点验证 | 1 | 0.008548 | 341+918 tokens |

单条研究的量级：**¥0.15–0.20**（25 条来源 / 440 条证据 / 6 条核查 / 5 次模型调用）。
按 5 元日上限算，一天最多跑二十多条完整研究——这个上限是刻意压低的，不是能力上限。

## 5. 不想花钱时怎么做

| 目的 | 做法 | 结果 |
| --- | --- | --- |
| 完全停用模型 | `config/settings.yaml` 里 `features.model: false` | 网关直接返回 `feature_disabled`，不联网 |
| 只停联网检索 | `features.search: false` | `web_search`/`fetch_url` 返回 `feature_disabled`，**一个请求都不发** |
| 只停任务执行 | `execution.enabled: false` 或 `run_worker` 不加 `--force` | worker 打印 `execution_disabled` 并退出码 4 |
| 只看历史账 | `/api/system/costs`、`/api/system/history` | 只读，不触发调用 |
| 复现验收但不花钱 | `python -m pytest tests/ -o addopts=""` | 全部用例离线（`conftest.py` 会清掉真实密钥） |

## 6. 现在仍花钱的地方（要看清楚）

- `research.plan` / `research.analyze` / `research.synthesize` / `verify.claims` / `content.script`
  五个阶段会调模型；`research.collect` / `research.extract` 只走搜索与解析，**不调模型**。
- 成本与内容长度强相关：证据越多、核查条数越多，prompt 越大。上限靠
  `MAX_POINTS` / `MAX_FINDINGS` / `MAX_EVIDENCE` 与阶梯重试兜着，但**没有做 token 级预估**——
  这是已知缺口，写在需求矩阵的 PARTIAL 之外（本轮已无 PARTIAL，故记在这里）。
- 语音合成走 edge-tts（免费，需联网）；本地 `sapi` 兜底离线但只作技术验证。
