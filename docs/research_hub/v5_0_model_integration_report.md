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

---

## 7. 前端真实验收（§5）：用真 HTTP 服务把整条链点了一遍

脚本 `backend/scripts/flow_check.py`：起一个真 `uvicorn`（临时库 `%TEMP%\fmai_flow_*\flow.db`），
用 `urllib` 逐条打接口，**不打 mock、不改生产库**。可复跑：

```powershell
python -m backend.scripts.flow_check
```

### 7.1 实测返回码序列（一次跑完，2026-11-12 22:47）

| 步骤 | 接口 | 返回码 | 关键结果 |
| --- | --- | --- | --- |
| 建选题 | `POST /api/topics` | 201 | `topic_id=1` |
| 建研究任务 | `POST /api/research-tasks` | 201 | `task_id=1`，7 个子任务（跳过 3 个选题阶段） |
| 推进第 1 轮 | `POST /api/research-tasks/1/run` | 200 | `completed=[1,2,3]` |
| 推进第 2 轮 | 同上 | 200 | `blocked=[4]`（`research.analyze` 缺模型） |
| 任务终态 | `GET /api/research-tasks/1` | 200 | `paused`，`{succeeded:3, blocked:1, pending:3}` |
| 资料库 | `GET /api/sources` | 200 | **`total=6`**（真实抓取的公开网页） |
| 报告 | `GET /api/research-reports` | 200 | `total=1`，`report_id=1` |
| 证据链 | `GET /api/evidence?research_report_id=1` | 200 | **`total=17`** |
| 建脚本 | `POST /api/scripts` | 201 | `script_id=1`、`v1`、`draft` |
| 送审 / 通过 | `PATCH /api/scripts/1/status` | 200 / 200 | `in_review` → `approved` |
| 记审核 | `POST /api/reviews` | 201 | `review_id=1`、`needs_revision` |
| 从报告建视频任务 | `POST /api/research-reports/1/video-job` | 201 | `v5_flow_acceptance`、`stage=storyboard_ready`、`blocked=false` |
| 成本 | `GET /api/system/costs` | 200 | `model_calls=0`、`model_cost_cny=0` |
| 服务商 | `GET /api/system/providers` | 200 | `deepseek=missing_api_key` |

视频任务目录里落下的真实文件：`research/report.md`、`research/evidence.json`（50 KB）、
`research/sources.json`、`storyboard/storyboard.csv`（8 镜 / 36 秒，4 张数据卡）、
`storyboard/OUTLINE.md`、`assets/compositions/card_sh0*.json`、`assets_manifest.csv`、`HANDOFF.md`。
**这几份文件是从真报告 + 真证据排出来的，不是模板。**

### 7.2 验收中修掉的四个真缺陷（都不是改测试绕过）

| # | 现象 | 根因 | 修法 | 复现它的测试 |
| --- | --- | --- | --- | --- |
| 1 | 任务从 `research.plan` 开始就 `blocked`，后面的**真实检索/抓取**（不需要模型）全停在 `pending` | 规划阶段被归进 `MODEL_TASK_TYPES`，一律走"没模型就阻塞" | 新增 `AgentExecutor._plan`：用规则从问题拆检索词，产出标 `planner="keywords"` / `model_used=false`，下游直接复用 | `test_plan_derives_keyword_queries_without_a_model`、`test_collect_uses_the_queries_the_plan_stage_derived` |
| 2 | `GET /api/research-reports` **500** | `findings` 列存的是字符串数组，接口按对象数组解析 | `persistence._findings_of()` 改写结构化对象 `{text, source_id, numbers, verified}` | `tests/test_research_persistence.py` 全绿 + §7.1 实测 200 |
| 3 | `GET /api/sources` **500**（只要有带时分的发布时间） | 库里是 `DateTime`，schema 写成 `date` | `SourceOut.published_at` 改 `datetime` | 同上 |
| 4 | 同一份报告当天再点"建视频任务"只能撞 **409** | 路由不接 `job_id`，默认名 = 报告 id + 日期，不可重试 | 新增 `VideoJobIn`，允许调用方指定 `job_id`；同名目录仍拒绝覆盖 | `test_video_job_api_accepts_an_explicit_job_id` |

另外补了一道**数据保护**：产物按 `content/research/task-<id>/` 落盘，而 id 只在一个库里唯一。
验收时用临时库跑到 `task-1`，把仓库里那份真实底稿覆盖掉了（已用 `git checkout --` 恢复）。
现在 `_safe_digest_dir()` 会在"目录里已有同 id 但目标不同的底稿"时退到 `task-<id>-<指纹>`，
并加了 `test_digest_of_another_run_does_not_overwrite_an_existing_one`。

### 7.3 本轮测试与库状态

| 项 | 值 |
| --- | --- |
| 全量测试 | **786 passed, 2 skipped, 1 warning**（唯一 warning 仍是 `agent_runs↔research_subtasks` 外键环） |
| 新增测试 | `test_execution_pipeline.py` +3、`test_video_handoff.py` +1 |
| `model_calls` | **0 行**（全程没调用任何模型） |
| 真实开发库 | `sha256=2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`、540,672 B、**19 表**、`revision=de8148733a9b`、`journal_mode=delete`、无 `-wal`/`-shm` —— 与基线逐字节一致 |

---

## 8. 现在还剩什么（一句话一条）

1. **真实模型调用**：仍 `BLOCKED: model_not_configured`。你配好 `.env` + `features.model: true` 后，
   跑 `python -m backend.scripts.model_smoke` 就能验证；我不会替你花这笔钱。
2. **研究闭环的后半段**（模型分析 → 结构化 Finding → 结论段 → 语义核查）依赖模型，无密钥即无法真跑；
   前半段（真实搜索 → 抓取 → 解析 → 证据入库 → 报告草稿 → 审核）本轮已端到端跑通。
3. **视频链路的"内容策划/脚本生成"**同样要模型；分镜→数据卡→旁白→渲染→QC 已能真出片。
4. 未做：流式输出、多 provider 故障转移、语义检索、自动发布。

---

## 9. 验收后的两处内容修正（2026-11-12 晚）

抽帧看片时发现两个"技术指标全过、但上屏内容是错的"问题，都已修并重出片。

### 9.1 日历数字被当成主数字

`11月2日，多家电商平台的尾款支付活动…` 这句话，卡面曾被写成巨大的 **`11`**。
原因：从原文抠数字时只过滤了年份（`_YEAR`），没过滤"月/日/号/年/季度"里的数字。

修法（`backend/app/video/outline.py`）：

- 新增 `_CALENDAR_SUFFIXES` 与 `_is_calendar_number(text, number)`：只有当这个数字
  **每一处出现**后面都紧跟着日历词时，才判定为日历数字并排除（"11亿"这种仍算量）。
- `_number_set()` 在回退抠数字时同时过 `_YEAR` 与日历判定。

### 9.2 卡面只认"带单位"的数字

`21深度：信用卡分期收入属于利息…` 里的 `21` 是刊物名，同样被顶到卡面正中。

修法：`_number_with_unit()` **不再退回裸数字**——没有任何带单位的数字就返回空串，
这个镜头就退成底板镜头。理由：财务口径的量几乎都带单位（`%`、`个百分点`、`亿`、
`万亿`、`万元`、`倍`），没有单位的裸数字多半是序号、日期或刊物名；
**宁可少一张卡，也不让错的数字上屏**。

顺带把单位分支收紧到"量"的单位（去掉 `家`/`人`/`户`/`只`/`元`），否则
`42家A股上市银行共实现营业收入4.28万亿元` 会把 `42家` 顶上去，而那句话要讲的是 `4.28 万亿元`。

### 9.3 说明句从中间切进来要留省略号

`…度的净息差环比下降4个基点至1.69%` 这种半句没有省略号，读起来像打字打漏了。
`_caption_around()` 现在：窗口起点不在句/段边界时**必须加前导 `…`**，并顺带吃掉
紧跟的虚词（的/地/得/了/是/在/为/与/和），得到"…净息差环比下降4个基点至1.69%"。

同时新增一条硬约束：**数字与单位必须完整**（不能切出 `1362.` 这种半截数）——
窗口装不下时让数字收在行尾，而不是硬截。

### 9.4 修正后的成片

| 项 | 值 |
| --- | --- |
| 文件 | `content/jobs/v5_flow_acceptance_v2/output/final.mp4` |
| 大小 / SHA-256 | 19,181,193 字节 / `12261d5dd381fcbfac15b390f9a4af2a256a394144d908fdcb70741f58144de2` |
| 时长 / 帧 / 画布 | 89.3 秒、2679 帧、1080×1920、30 fps、h264 + aac |
| 数据卡 | 只剩 **2 张**（1.69%、1.53%），日历数字与刊物名不再上卡 |
| 旁白 | 8 段（edge `zh-CN-YunxiNeural`） |
| QC | `failed=[]`（`black_longest 0.3`、`freeze_longest 1.033`、`silence 0`） |
| 测试 | 全量 **788 passed, 2 skipped, 1 warning**（新增 2 例：日历数字不上卡、裸数字不排卡） |

旧目录 `content/jobs/v5_flow_acceptance/`（含那张写错的 `11`）已删除，仓库里只留修正后的这条片子。
