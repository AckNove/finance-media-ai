# V5.0 真实模型接入报告（2026-11-12）

> 本轮任务：让系统真正具备 AI 研究能力。结论先放这里：
> **协议层已经打通并实测通过，但真实模型调用仍然 BLOCKED——机器上没有任何模型密钥。**
> 我没有用 Fake 顶替，也没有替你花钱。下面每一节都给出可复跑的验证方式。

---

## 0. 一页纸

| 问题 | 结论 | 证据 |
| --- | --- | --- |
| 网关能不能发真实请求 | **能**，实测打到 `api.deepseek.com` 并拿到服务端 401 | §2.2 两次真实 HTTP，含 `request_id` |
| 模型名对不对 | **已修正**：官方现行名是 `deepseek-flash` / `deepseek-v4-pro`，旧的 `deepseek-chat` / `deepseek-reasoner` 已不在模型表里 | §2.3 |
| 价目核过没有 | **核过**，附官方链接与核对日期；按高峰·缓存未命中折算成人民币/千 token | §2.4 |
| 能不能真的调用 | **不能**：`BLOCKED: model_not_configured`（缺 `DEEPSEEK_API_KEY`，且 `features.model=false`） | §3 冒烟输出 |
| 有没有偷偷计费 | 没有。`model_calls` 仍是 **0 行**；无密钥时网关在 provider 阶段就拒绝，不发请求 | §3.2 |
| 研究闭环 | **未完成**——它依赖模型分析/结论/语义核查，无密钥即无法真跑 | §5 |

---

## 1. 接管检查（改动前基线）

| 项 | 实测值 |
| --- | --- |
| HEAD | `28216e8b6128b24f8165a32c110637b154687d83` |
| `origin/main...HEAD` | `0 0`（与远端一致） |
| `git status --porcelain` | 空（工作区干净） |
| 最近六笔提交 | `28216e8` docs(v5.0) · `370c3e9` feat(topics) 标签反查 · `dd616ef` feat(topics) 标签写入 · `391fa55` feat(web) 脚本/审核页 · `aae5169` feat(content) 脚本与审核接口 · `fcbc3a4` feat(topics) 多合集 |
| 测试基线 | `769 passed, 2 skipped, 1 warning` |
| 模型配置 | `config/models.yaml`：三个 provider 全 `enabled: false`，两条模型价目全 `null` |
| 环境变量 | `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` 等**均不存在**；仓库无 `.env` |
| 本地模型服务 | `127.0.0.1` 的 11434 / 1234 / 8080 / 8000 / 5000 全部不可达 |

已有实现（未重写）：`backend/app/capabilities/model/{providers,tokens,gateway,tool}.py`、
`backend/app/execution/{runtime,orchestrator,executor,worker}.py`、
`backend/app/api/routes/{research_tasks,monitoring,workspace,research_reports}.py`。

---

## 2. 真实模型接入

### 2.1 密钥怎么进来

- 只从环境变量读：`DEEPSEEK_API_KEY`（`providers.deepseek.api_key_env`）。
- 仓库里**没有**密钥：`.gitignore` 覆盖 `.env*`；`config/models.yaml` 只写环境变量**名字**。
- 展示一律脱敏：`mask_secret()` 只保留至多 3 位前缀与长度，**不保留尾部**。
  理由写在代码注释里：前缀+尾部合起来足以让人对号入座，而展示任何一段对排查都没有帮助。
- 服务端回显也抹：DeepSeek 的鉴权失败响应会带 `Your api key: ****0000`（尾 4 位），
  网关新增 `_redact()`，命中完整密钥或尾 4 位就整段替换（§2.6 有测试）。

### 2.2 端点连通性（真打了一次，零费用）

用**明显无效**的密钥打真实 `POST https://api.deepseek.com/v1/chat/completions`：

```json
[{"model": "deepseek-flash", "status_code": 401,
  "body": "{\"error\":{\"message\":\"Authentication Fails, Your api key: ****0000 is invalid (request_id: ef39082d-191a-4e77-8110-450b56748b13)\",\"type\":\"authentication_error\",...}}"},
 {"model": "definitely-not-a-model", "status_code": 401,
  "body": "... request_id: 082b888a-59b4-41a8-900f-e6dd00d7395d ..."}]
```

这说明三件事：①这台机器能连上官方端点；②路径 `/v1/chat/completions` 与请求体形状被接受；
③鉴权先于模型名校验，所以**模型名是否有效只能等真密钥到位才能确认**（不猜、不假装验证过）。
401 不产生任何费用。

### 2.3 模型名（改了两个真错）

官方模型表（2026-11-12）只有 `deepseek-flash`（DeepSeek-V4.1-Flash-1017）与
`deepseek-v4-pro`（DeepSeek-V4-Pro-0813）。仓库里原来的 `deepseek-chat` /
`deepseek-reasoner` **已不在表中**（官方说明旧名 `deepseek-v4-flash` 等由 Flash 承接）。

处理方式：新增两条现行条目并启用，旧的保留为**历史条目**（`enabled: false` + 价目留空 →
`pricing_missing`），让旧日志仍能解释得通。同时补了 `context_window: 1000000`、
`max_output_tokens: 393216`（官方 1M 上下文 / 最大 384K 输出）。

### 2.4 价目（含出处，输入输出分别计价）

来源：<https://api-docs.deepseek.com/quick_start/pricing>，核对日期 **2026-11-12**。

| 模型 | 官方价（每 1M token，高峰·缓存未命中） | 折算后（元/千 token） |
| --- | --- | --- |
| `deepseek-flash` | 输入 $0.30 · 输出 $1.20 | 输入 **0.00213** · 输出 **0.00852** |
| `deepseek-v4-pro` | 输入 $1.32 · 输出 $3.96 | 输入 **0.009372** · 输出 **0.028116** |

折算口径写进 `price_basis`：`1 USD = 7.1 CNY`，每百万 ÷1000 = 每千；**一律取高峰价**，
宁可高估成本。官方高峰时段为 UTC 周一至周五 01:00–04:00、06:00–10:00，其余时间与节假日
半价——所以真实花费通常**低于**这里的估算。

`config/models.yaml` 每条都带 `price_source` / `price_checked_at` / `price_basis`；
`test_config_layers.py` 现在强制：**核过价的条目必须留出处，没核的必须留空且不得启用**。

### 2.5 超时 / 重试 / 限流 / 错误记录

| 能力 | 实现 | 位置 |
| --- | --- | --- |
| 超时 | `httpx.Client(timeout=provider.timeout_seconds)`，默认 120 s；`TimeoutException` → `status="timeout"` | `gateway.py` `_post()` |
| 重试 | 只对 `{408,429,500,502,503,504}` 与传输错误重试，默认 2 次、线性退避；**401/403 与 4xx 不重试** | `RETRYABLE_STATUS`、`_post()` |
| 限流（新增） | 进程内节流：`requests_per_minute` + `max_concurrency`；被自己拦下时**不发请求**、返回 `rate_limited`、仍写审计行 | `_LocalLimiter`、`LocalRateLimitError` |
| 错误记录 | 每次尝试都写 `model_calls`（含 `status`/`error_message`/`duration_ms`），失败不掩盖业务结果 | `gateway._log()` |
| 预算闸门 | 单次、任务、当日三道闸；价目 `missing` 直接 `pricing_missing` 拒绝 | `capabilities/budget.py` |

配速值：`deepseek-flash` 60 次/分、并发 2；`deepseek-v4-pro` 20 次/分、并发 1。
官方并发上限是 2500/500（账号级天花板），这里配的是"我该多快"，不是"我能多快"。

### 2.6 本轮实测数字

| 项 | 值 |
| --- | --- |
| 新增/改动代码 | `gateway.py`（限流 + 脱敏）、`providers.py`（`mask_secret` 收紧）、`config_schema.py`（价目出处/限流字段）、`startup_checks.py`（只对启用模型告警）、`config/models.yaml`、`backend/scripts/model_smoke.py` |
| 新增测试 | `tests/test_model_guardrails.py` **11 例**（限流两规则、被限流不发请求、缓存不吃额度、密钥不入库/不入错误/不入日志、`mask_secret`、`_redact`） |
| 全量测试 | **782 passed, 2 skipped, 1 warning**（唯一 warning 仍是 `agent_runs↔research_subtasks` 外键环） |
| `model_calls` 行数 | **0**（无密钥、无调用、无费用） |

一次性冒烟估算成本（若真调用，`max_tokens=200`）：flash ≈ **0.00175 元**，v4-pro ≈ **0.00583 元**。

---

## 3. 真实模型冒烟：BLOCKED

新增 `backend/scripts/model_smoke.py`（`python -m backend.scripts.model_smoke`），实测输出：

```json
{"ok": false, "blocked": true, "reason": "model_not_configured",
 "message": "模型还不能调用：provider=missing_api_key（provider deepseek 缺少密钥：请设置环境变量 DEEPSEEK_API_KEY）→ 在 .env 或系统环境变量里设置 DEEPSEEK_API_KEY；features.model=false（config/settings.yaml 的人工开关，脚本不改它）"}
```

退出码 **3**（未配置）。脚本刻意把**两个闸门一起报**，省得你改完一个再试第二个。

它做成这样是为了不撒谎：没有密钥就报 blocked；价目未知就报 `pricing_missing`；
预算不够就报 `budget_blocked`；这三条都不会退化成"假装成功"。冒烟强制
`cacheable=False`——命中缓存等于没测真实请求。

### 3.2 为什么 `model_calls` 还是 0 行

无密钥时网关在**第 2 步 provider 解析**就返回 `not_configured`，请求根本没发出去，
因此不写审计行、不计费。这是设计如此，不是漏写。

---

## 4. 研究闭环（未完成，原因见上）

需要模型的四个阶段现在是诚实的 `blocked`：

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| `research.collect` | **已真跑通** | 真实搜索 + 真实抓取 + 反爬页识别 |
| `research.extract` | **已真跑通** | 带页码/行号的结构化抽取 |
| `research.analyze` | `blocked: model_not_configured` | 但已先把底稿 `digest.md/json` 落盘 |
| `research.synthesize` | `blocked: model_not_configured` | 输出里 `report_ready=false` |
| `verify.claims`（语义核查） | `blocked: model_not_configured` | 逐字核查已能跑，语义核查不能 |

已有真实产出（可直接复核）：`content/research/task-1/digest.{md,json}`——
3 条真实来源（新浪财经 / 中国经济网 / 网易民生银行）、18 个候选数据点、
跨来源比对 12 处一致 / 1 处冲突 / 8 条单一来源，每条都带来源编号与快照哈希。

---

## 5. 需要你完成的最少操作（三步）

```powershell
# 1) 配置密钥（只写本机，不进 Git）
#    在项目根目录建 .env，写入：
#    DEEPSEEK_API_KEY=sk-你的真实密钥

# 2) 打开人工开关（config/settings.yaml）
#    features:
#      model: true

# 3) 复查并冒烟
python -m backend.scripts.preflight
python -m backend.scripts.model_smoke
```

第 3 步成功时退出码 0，输出里会有真实 `text`、`usage.prompt_tokens/completion_tokens`、
`audit.cost_estimate_cny` 与 `audit.id`——那就是"真实请求 + 真实计费 + 可追溯审计"三件齐了。
**在你做完这三步之前，我不会替你调用，也不会产生任何费用。**

预算上限（`config/models.yaml` 的 `cost` 段 + `config/settings.yaml` 的 `execution`）依然生效：
单次、单任务、当日三道闸都不会因为你配了密钥就消失。

---

## 6. 本轮没做的事（不掩饰）

1. **没有真实模型调用**——无密钥。所有"模型能写分析/结论/脚本"的说法目前都还是 BLOCKED。
2. **模型名有效性未验证**：401 先于模型名校验，`deepseek-flash` 是否被接受要等真密钥。
3. **汇率 7.1 是估算口径**，不是实时汇率；真实账单以官方美元扣费为准。
4. **没有做流式输出**与多 provider 故障转移：现在是单 provider、非流式。
5. **§6 视频链路**：报告→视频任务的交接已通（规则分镜 + 数据卡 + 旁白 + 渲染 + QC 全跑过），
   但"内容策划/脚本生成"这两步要模型，仍 BLOCKED。
