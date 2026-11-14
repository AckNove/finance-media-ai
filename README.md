# finance-media-ai

个人 IP **电力之王AK** 的金融与商业研究内容生产平台；对外内容品牌 **AK带你拆解世界**。

> **品牌主张**：用金融与商业视角，拆解世界运行的逻辑。

内容按**六大长期合集**组织（一个账号、六类内容，合集不是独立账号）：

| 合集 | 说明 |
| --- | --- |
| 金融知识 | 货币、利率、汇率、金融产品、金融机构、市场机制与风险 |
| 财经时事 | 把热点从"发生了什么"讲到"为什么、谁受益、往哪走" |
| 产业链研究 | 上下游结构、利润分配、成本与技术壁垒、竞争格局 |
| 公司商业解剖 | 商业模式、收入成本利润现金流、竞争优势与资本运作 |
| 足球金融与商业 | 转会定价、俱乐部财务、版权赞助；既是研究领域也是教学场景 |
| 金融与商业案例 | 危机、骗局、企业兴衰、资本运作、历史事件 |

风格：**观点鲜明、犀利、有趣**，但观点必须能指回证据；足球是六大合集之一与常用类比场景，
**不是内容边界**，类比必须说明**哪里成立、哪里不成立**。

品牌、内容分类、商业模式与合规红线的完整定义见
`docs/brand_strategy.md`、`docs/content_strategy.md`、`docs/business_model.md`。

> **当前状态：V5.0 统一平台已跑通「真实搜索 → 真实抓取 → 证据链入库 → 报告草稿」，
> 视频侧保留 V2.2 Phase 2 的分镜/时间轴/渲染/质检链路。真实模型调用是唯一的大阻塞：
> 仓库里没有任何 API Key，也没有本机模型服务，所以模型阶段会明确标 `BLOCKED`，不会伪造。**
>
> 一句话看懂能不能用：
>
> | 能力 | 状态 | 证据 |
> | --- | --- | --- |
> | 真实搜索 + 真实抓取（SSRF/robots/反爬拦截闸门） | ✅ 已实测 | 抓到今日头条/新浪财经等正文，解析出发布时间；验证命令见 `docs/research_hub/v5_0_delivery_report.md` |
> | 任务运行时（队列/租约/重试/暂停/人工闸门） | ✅ 已实测 | 9 阶段 DAG + 3 道闸门，重启后可恢复 |
> | 常驻 worker（`python -m backend.scripts.run_worker`） | ✅ 已实测 | 真跑：采集格完成、模型格 `blocked:model_not_configured`、空闲自动退出 |
> | 证据链（来源/证据/报告落库，逐字核对） | ✅ 已实测 | 一次真实跑：sources=3 / reports=1 / evidence=11 / verified=11 |
> | 语音合成（Edge TTS + SAPI 兜底，词级时间） | ✅ 已实测 | 中文拿到真实词级边界，可直接出 SRT/ASS |
> | 视频渲染与质检（FFmpeg 8.1.2 + OpenKatana） | ✅ 已实测 | 42.5 秒 1080×1920 成片，17 项 QC |
> | 分镜 → 时间轴（真实引擎，7 镜 1275 帧） | ✅ 已实测 | 只跑时间轴 79 秒；后台渲染进度写 `qc/render_run.json`，工作台可轮询 |
> | 表格数据 → 指标 + 可复现图表 | ✅ 已实测 | 同输入同字节的 PNG，图上强制印出处行；图表自动登记为素材清单里的 B 类行 |
> | 旁白音轨（分镜口播稿 → 真实 TTS → 时间轴音轨） | ✅ 已实测 | 7 镜 30.6 秒全部合成；超长只记 `overrun`，**不拉伸镜头**；TTS 不可用就明说"没有旁白" |
> | 报告 → 规则分镜 → 数据卡 → 真实成片 | ✅ 已实测 | `content/jobs/v5_research_film/output/final.mp4`：71.667 秒 / 1080×1920 / 30fps / 旁白 8 段 / QC 七项全过（SHA-256 见交付报告 §S） |
> | 研究闭环前半段（真实搜索→抓取→解析→证据入库→报告→审核） | ✅ 已实测 | `flow_check` 一次跑完：sources=6 / evidence=17 / report=1 / review=1；后半段（模型分析）BLOCKED |
> | 研究 → 成片全链（报告直接排分镜、出带旁白的 MP4） | ✅ 已实测 | `content/jobs/v5_flow_acceptance_v2/output/final.mp4`：89.3 秒 / 2679 帧 / 1080×1920 / 旁白 8 段 / QC 全过（SHA-256 见报告 §9） |
> | 统一模型网关（OpenAI 兼容、预算与成本闸门、限流与密钥脱敏） | ⚠️ 代码就绪、**无密钥** | 缺 key 时返回 `not_configured`；价目未知直接拒绝请求；`model_calls` 仍 0 行 |
> | 18 页工作台（真实接口，无假数据） | ✅ 可用 | `http://127.0.0.1:8000/app/` |
>
> 注意：`journal_mode=WAL` **不会**因为启动服务或查状态而自动打开，它是显式动作——
> `python -m backend.scripts.enable_wal --database-url <库> --dry-run` 先看清目标，再去掉 `--dry-run` 执行。

## 快速开始（Windows / PowerShell）

```powershell
# 一条命令：建/复用 .venv、准备独立工作库（自动 alembic upgrade head）、预检、启动
# （默认连后台执行器一起起，否则界面上建的研究任务会一直停在「已创建」）
.\scripts\start.ps1
#   工作台  http://127.0.0.1:8000/app/
#   接口文档 http://127.0.0.1:8000/docs
#   健康检查 http://127.0.0.1:8000/api/system/status
#   只看界面、不想让它自动跑任务：.\scripts\start.ps1 -NoWorker

# 只做体检（不启动服务）：ok / blocked / error 三档，blocked 会告诉你怎么解
.\.venv\Scripts\python.exe -m backend.scripts.preflight

# 重启后恢复：回收过期租约并列出卡住的任务（默认只看不改，加 --apply 才动）
.\.venv\Scripts\python.exe -m backend.scripts.recover_tasks

# 报告 → 分镜 → 成片（一条命令；按 report_id 走完交接与渲染）
.\.venv\Scripts\python.exe -m backend.scripts.produce_video --report-id 1 --narration
#   加 --no-render 只排分镜与时间轴；默认拒绝对 data/finance_media.db 动手

# 没有跑过联网研究？把仓库里已提交的真实底稿导进独立库，再走上一条命令
.\.venv\Scripts\python.exe -m backend.scripts.import_digest `
    --digest content/research/task-1/digest.json --database-url sqlite:///./data/demo.db
#   底稿里的来源、数据点、印证结果原样搬进 sources / research_reports / evidence，
#   不改写数字、不补来源；导入后打印 report_id，接着用 produce_video --report-id <id>
```

手工步骤（想自己控制每一步时）：

```powershell
# 1. 虚拟环境（Python 3.12）
py -3.12 -m venv .venv

# 2. 依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 环境变量（不放真实密钥；示例使用独立库，不触碰受保护的开发库）
Copy-Item .env.example .env
$env:DATABASE_URL = "sqlite:///./data/quickstart.db"

# 4. 在独立示例库执行迁移
.\.venv\Scripts\python.exe -m alembic upgrade head

# 5. 灌入六大合集（幂等；不使用 --allow-development-db）
.\.venv\Scripts\python.exe -m backend.scripts.seed_collections

# 6. 启动
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload

# 7. 测试（pyproject 的 addopts 已带 -q，想看清用例数就覆盖它）
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
```

> **数据安全**：`data/finance_media.db` 是只读的开发库，`start.ps1` 会拒绝在它上面启动；
> 默认工作库是另建的 `data/finance_media.work.db`。迁移、WAL、seed 一律不针对那个受保护的库。

## 已实现的接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 存活检查（不访问数据库） |
| GET | `/api/system/status` | 数据库连通性、表数量、迁移版本、功能开关、Agent 名册 |
| POST / GET | `/api/topics` | 创建 / 列表（支持域、研究状态、内容状态过滤与分页） |
| GET | `/api/topics/columns` | 栏目列表 |
| GET / PATCH | `/api/topics/{topic_id}` | 详情 / 部分更新 |
| POST / GET | `/api/knowledge` | 创建 / 列表知识条目 |
| GET | `/api/knowledge/search?q=` | 多字段关键词检索（LIKE + 加权排序，非语义检索） |
| POST | `/api/knowledge/relations` | 创建知识关联 |
| GET | `/api/knowledge/{item_id}/relations` | 查询关联 |
| GET / PATCH | `/api/knowledge/{item_id}` | 详情 / 更新（版本自增） |
| POST / GET | `/api/sources` | 登记来源（服务端算 `content_hash`）/ 列表 |
| POST | `/api/evidence` | 登记证据（必须挂在知识条目或研究报告上） |
| GET | `/api/evidence?knowledge_item_id=` | 按主张目标查询证据（附来源摘要） |
| POST / GET | `/api/research-reports` | 创建 / 列表研究报告 |
| GET | `/api/research-reports/{report_id}` | 报告详情 |
| POST / GET | `/api/research-tasks` | 按流水线拆解并建研究任务 / 任务列表 |
| GET | `/api/research-tasks/{task_id}` | 任务详情（阶段进度、计数、计划、失败原因） |
| GET | `/api/research-tasks/{task_id}/events` | 任务事件流（created/claimed/子任务/闸门/重试） |
| POST | `/api/research-tasks/{task_id}/run` | 同步推进最多 N 格（真搜索真抓取） |
| POST | `/api/research-tasks/{task_id}/pause\|resume\|cancel` | 暂停 / 恢复 / 取消 |
| GET | `/api/system/costs` | 模型与工具调用成本聚合（按模型/工具/天） |
| GET | `/api/system/providers` | 服务商状态（ready / missing_api_key / provider_disabled…，不回显密钥） |
| GET | `/api/system/agents` | 九角色名册与各自可做的 task_type |
| GET | `/api/workspace/jobs` | 视频任务目录、成片与产物清单 |
| GET | `/api/workspace/jobs/{job_id}` | 分镜 / 素材 / 来源三张表 + 脚本与质检文本 |
| GET | `/api/workspace/jobs/{job_id}/file?path=` | 产物文件（路径穿越一律拒绝） |
| GET | `/api/workspace/search?q=` | 真实搜索（受 `features.search` 开关约束） |
| GET | `/api/workspace/config` | 配置快照（features / cost / execution / models 价目状态） |

共 **41 个路径 / 54 个操作**，全部登记在 `backend/app/api/router.py` 的 `ROUTE_CALLERS`，
启动自检会逐条比对「清单 vs 真实挂载」，不一致就拒绝启动。

统一错误响应：`{"error": {"code": "...", "message": "...", "details": ...}}`。

每个响应都带 `X-Request-Id`（`req-<16 位十六进制>`，可用同名请求头透传，非法值会被替换），
访问日志与异常日志都带同一个 id。

## 调用者身份与工具权限

- 每个路由必须显式声明允许的调用者（`backend/app/api/router.py` 的 `ROUTE_CALLERS`）：
  `GET /health` 公开，其余 **36 个操作只允许 `human`**；启动自检会把 `app.openapi()`
  与「真实路由上挂着哪个依赖」双向比对，未声明、多余、或清单说 human 而端点没挂依赖，
  都会让进程拒绝启动。
- `agent` 身份**不能由外部请求声明**：`X-Caller` 只作审计标签，非 `human` 值会被忽略并记录
  `forged_header` 告警；agent 上下文只能由执行层在进程内构造（`CallerContext.for_executor`）。
- `dev` 端点仅限 `/api/_dev/*` 且 `APP_ENV != production`，生产环境返回 404。
- 越权一律 422 `domain_validation_error` + `details.reason="permission_denied"`。
- 工具登记表 `backend/app/capabilities/tools.py` 里 10 个工具**全部有真实实现**：
  `web_search`、`fetch_url`、`parse_pdf`、`parse_xlsx`、`llm_complete`、
  `db_query`、`db_write`、`task_update`、`handoff`、`fact_check`。
  `db_write` 有三层闸门（可写表白名单、Agent 的 `db_scopes`、禁止写金额与租约字段），
  读账本表一律拒绝——工具便捷不等于可以绕过审计。

## 一个研究任务会经过什么

```
POST /api/research-tasks
  → Orchestrator 读 config/pipelines.yaml，把目标拆成 9 个 task 阶段 + 3 道人工闸门
  → TaskRuntime 落库（research_tasks / research_subtasks / task_events）
POST /api/research-tasks/{id}/run
  → Worker 抢租约 → AgentExecutor 分派处理器
       research.collect  真搜索 + 真抓取（SSRF/robots/反爬拦截闸门）→ 页面写入 sources
       research.extract  结构化抽取（页码/行号/工作表定位）
       research.analyze  先落底稿与证据链 → 再查模型；无密钥 → BLOCKED + 下一步怎么做
       verify.claims     逐字核对，命中才 VERIFIED，不命中只说"需人工复核"
  → 命中人工闸门自动 pause（原因落在库里，可查）
```

真实模型调用就绪后，`research.analyze / synthesize / verify.claims / content.*` 会从
BLOCKED 变成真正产出；在此之前它们**不会**用假文本冒充结论。

## 目录结构

```
backend/app/{api,core,models,schemas,services,database}   服务代码
backend/app/execution/            执行层：task_types / routing / runtime（队列租约）/ orchestrator（DAG）/ executor / worker / tools（task_update·handoff）
backend/app/capabilities/         权限闸门 guard.py、工具登记 tools.py、成本闸门 budget.py
backend/app/capabilities/model/   统一模型网关：providers / tokens / gateway（预算·缓存·重试·成本复算）/ tool
backend/app/capabilities/search/  真实搜索与抓取：safety（SSRF/robots）/ fetcher / engine（三引擎 + 真实地址还原）
backend/app/capabilities/tts/     语音：provider（契约）/ edge（Edge TTS，词级时间）/ sapi（离线兜底）/ subtitle（SRT·ASS）
backend/app/capabilities/storage/ db_query / db_write（三层闸门：白名单·db_scopes·禁写金额与租约）
backend/app/capabilities/verification/ fact_check（只查已落盘快照，逐字核对，不联网）
backend/app/capabilities/adapters/ MarkItDown 解析、structured（带页码/行号定位的抽取）、search·model·tts 工具适配器
backend/app/capabilities/media/   OpenKatana 与 FFmpeg 本地媒体适配层
backend/app/research/             研究底稿 digest、逐字核查 verify、证据链落库 persistence
backend/app/analysis/             表格数字→指标（parse_number/yoy/cagr/share/HHI）+ 可复现图表（line/bar/share_bar）
backend/app/media/                分镜校验、素材清单与时间轴构建
backend/app/video/                handoff（报告→视频任务）、outline（底稿→规则分镜）、cards（数据卡画板）、render（分镜→时间轴→成片→质检）、narration（口播稿→旁白音轨）、generated（自绘图表登记为素材）
backend/app/skills/registry.py    Skill Registry（sha256 固定、按 task_type 装载、工具门禁）
backend/app/parsers/              解析产物与解析器 Protocol；实现位于 capabilities/adapters/
backend/app/database/             session.py（连接级 PRAGMA）、wal.py（WAL 显式初始化，默认拒绝开发库）
backend/migrations/               Alembic 迁移（初始 de8148733a9b + B1/B2/B3 + B4 成本精度）
backend/scripts/                  seed_collections / enable_wal / install_ffmpeg / check_media_toolchain
                                  / media_selftest / research_task / run_worker / render_job
                                  / produce_video / preflight / recover_tasks
                                  / model_smoke（真实模型冒烟）/ model_e2e_check（12 步真实闭环）/ v51_evidence（核查与成本取证）/ import_digest（底稿导入复现）
                                  / flow_check（前端全链路验收）/ daily_check（每日体检：任务、核查、花费、备份、磁盘；首页卡片是同一份判断）
scripts/start.ps1                 一键启动（建库、预检、端口占用检查、起服务、起后台执行器、健康探测、开浏览器）
web/                              18 页工作台（原生 HTML/CSS/JS，挂在 /app/）
agents/                           九角色提示词 + roster.yaml + PROTOCOL.md
skills/                           Skill 目录（ak/ 三个自研 + vendor/anthropic 的 pdf、xlsx）
knowledge/{8 个子域}/              知识条目 Markdown（规范见 knowledge/README.md）
content/jobs/<job_id>/            每条视频一个任务目录（分镜、素材清单、时间轴、成片、质检）
content/research/<task_id>/        研究底稿（digest.md / digest.json，规则汇编，未使用模型）
video_engine/{templates,quality_control}  视频模板与 QC 规则（YAML）
config/{settings,brand,content_rules,content_taxonomy,research_sources,models,agent_routing,pipelines}.yaml
docs/research_hub/                架构、交付报告、决策登记、遗留问题
tests/                            pytest 回归测试（1207 例）
```

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/brand_strategy.md` | 品牌架构（IP 名/内容品牌）、主张定位使命个性、受众、风格、出镜、与旧策略差异 |
| `docs/content_strategy.md` | 六大合集、多标签分类规范、8 种内容形式、报告→内容产品链路 |
| `docs/business_model.md` | 四条收入线、会员/课程边界、合作披露、金融合规红线 |
| `docs/roadmap.md` | P0–P5 阶段路线图、非目标、待决策事项 |
| `docs/architecture.md` | 技术选型、分层、配置优先级、错误模型、扩展点与限制 |
| `docs/database_schema.md` | 18 张表、ER 图、字段设计、金融六域取舍、PostgreSQL 迁移注意 |
| `docs/agent_manual.md` | 九角色、统一协议、调度接口、新增角色步骤 |
| `docs/workflow.md` | 十四步内容/视频工作流、状态机、四个模板、12 项质检 |
| `docs/phase1_acceptance.md` | 12 项验收实测结果 + 功能状态（DONE/PARTIAL/NOT IMPLEMENTED） |
| `docs/research_hub/` | 研究工作台架构与各阶段交付记录：M2.0、V2.2 Phase 2（视频）、V5.0（统一平台） |
| `docs/research_hub/v5_0_delivery_report.md` | **V5.0 交付报告**：20 个模块的落地状态、真实运行证据、BLOCKED 项与最少人工操作 |
| `docs/research_hub/v5_0_oss_evaluation.md` | 17 个开源项目的源码级评估（固定 SHA、许可证、采用方式） |
| `agents/PROTOCOL.md` | Agent 统一输入输出协议（10 字段）+ 品牌与内容规则 |

## 安全约定

- 密钥只走环境变量；`.env` 已被 `.gitignore` 忽略，仓库只提交 `.env.example`（空占位）。
- `config/*.yaml` 不存放任何真实 API Key。
- 系统**不会自动发布**任何内容（`config/brand.yaml` 中 `auto_publish: false`），
  也不输出面向个人的具体投资买卖建议（`config/content_rules.yaml` 硬约束）。
- 第一阶段是本地单机服务：无鉴权、无限流，不要暴露到公网。

## 尚未实现（避免误解）

**真实模型已接通并真跑过一遍闭环**（2026-11-12 深夜）。密钥放在项目根的 `.env`
（`.gitignore` 已忽略），`features.model: true`；`research.analyze` 与
`research.synthesize` 会真调 `deepseek-flash`，成本按真实 token 落 `model_calls`。
一份完整实测见 `docs/research_hub/v5_0_model_integration_report.md` §10：

| 项 | 实测 |
| --- | --- |
| 12 步闭环 | 9/9 子任务 succeeded，44 条事件 |
| 真实来源 / 证据 | 6 条来源、48 行 evidence（逐字命中才 verified） |
| 模型调用 | 2 次（analyze + synthesize），9846 tokens，**¥0.039752** |
| 报告结论 | 每条挂 `source_ids`，局限说明如实写"由模型生成、需回原文核对" |

复跑：`python -m backend.scripts.model_e2e_check`（临时库 + 真搜索 + 真模型，约 4 分钱）。
只要把 `config/settings.yaml` 的 `features.model` 改回 `false` 就**一键停服**——
网关在发请求之前就返回 `feature_disabled`，不花钱。

**V5.1 又补上了两块能力**（2026-11-13，报告见 `docs/research_hub/v5_1_semantic_and_planning_report.md`）：

| 项 | 实测（全新主题「光伏组件价格战为什么会吃掉龙头企业的现金流」） |
| --- | --- |
| 模型规划 | `planner=model`、6 个规划子任务、36 个检索词、5 条核心问题 / 5 条风险 |
| 语义核查 | 6 条结论逐条判定：`supported 1 + partially_supported 5`，人工态全部 `pending` |
| 动态补任务 | 规划里的高优先级子任务真被追加成采集任务（`direction_changed` 事件） |
| 全链数据 | 9/9 子任务、27 来源、416 证据、1 报告、54 事件 |
| 成本 | 4 次调用 47,132 tokens、**¥0.159294**；任务账 == `SUM(model_calls)` |

语义核查的边界写在代码里：**模型判定不等于人工确认**（`human_review_status` 恒从
`pending` 起步，只有人点才算 `confirmed`）；说"支持"但引不出逐字片段的一律降级成
证据不足；挂不上 `source_ids` 的结论直接丢弃。

仍然没做的（不掩饰）：`contradicted`（与证据冲突）**还没有真实样本**，只有单测覆盖；
语义核查一次最多看 30 条证据；规划出来的子任务还没写回任务表（只在 `research.plan`
阶段的输出里）。

**成稿闸门（2026-11-13 追加）**：一条结论要进成稿，必须有 `finding_checks` 行且
`human_review_status = "confirmed"`——模型判 `supported` 不算数。四个出口同一份判据
（`backend/app/research/draft_gate.py`）：建视频任务 API 返回 409、交接层
`VideoHandoffError("draft_not_confirmed")`、`produce_video` 退出码 3、**建脚本
`POST /api/scripts`（带 `research_report_id` 时）同样 409**；
`content/jobs/<job>/research/report.md` 会分成「可进成稿（人工已确认）」与
「尚未人工确认（不得进成稿）」两节。工作台报告页有「成稿闸门」列，核查页可直接点确认。

本地看页面：

```bash
# 用演示库起服务（不碰 data/finance_media.db，也不给密钥 → 浏览不花钱）
set DATABASE_URL=sqlite:///%TEMP%/fmai_demo/demo.db
set APP_ENV=development
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000/app/  → 「语义核查」页 → 点一次「人工已确认」
```

模型与价目现状（`config/models.yaml`，2026-11-12 按官方文档核对）：
`deepseek-flash`（输入 0.00213 / 输出 0.00852 元每千 token）、
`deepseek-v4-pro`（0.009372 / 0.028116），均按**高峰·缓存未命中**折算，
每条都带 `price_source` / `price_checked_at` / `price_basis`。
旧的 `deepseek-chat` / `deepseek-reasoner` 已不在官方模型表里，保留为禁用条目、价目留空。


以下几项确实还没做（不是没跑通，是没写）：

- 语义检索与向量库；知识库 Markdown 的自动同步（只能人工按 `knowledge/README.md` 落条目）。
- 真实鉴权/多用户、限流、CI 与云部署（当前是本地单机，`networkExposure: loopback`）。
- 概念类知识条目还只能落 `knowledge_domain=general`；标签只做了"打/摘/反查"，
  还没有标签共现、热度衰减这类聚合分析。
- 发布接口：合成与审核都在本地完成，**系统按设计不会自动发布**到任何平台。

已经做完、别再列成缺口的（本轮补齐）：

- 选题多合集：`GET/POST /api/topics/{id}/collections`、`DELETE .../{column_id}`，
  一个选题可有多个合集、只有一个主合集（部分唯一索引兜底）。
- 选题标签：`GET/POST /api/topics/{id}/tags`、`DELETE .../{tag_id}`；标签按
  `namespace+name` 复用（同名标签挂到第二个选题时复用同一行、`usage_count` 递增），
  摘干净后重新挂回来计数会回到 1；`create_missing=false` 时不会顺手造新标签。
  反查选题：`GET /api/topics?tag_id=1` 或 `?tag=净息差&tag_namespace=mechanism`；
  同名标签跨命名空间时报 422 并列出候选，不替你猜。
- 脚本与审核：`POST/GET /api/scripts`、`PATCH /api/scripts/{id}/status`（状态机
  `draft → in_review → approved|rejected`，批准时写 `approved_at/approved_by`）、
  `POST/GET /api/reviews`（结论回写到报告：pass→verified / needs_revision→disputed /
  reject→refuted；`needs_revision` 必须写清要改什么）。工作台的脚本页与审核页已经
  接上这些接口，按钮都打真请求。

数据层面的现状（`data/finance_media.db` 只读，所有真实跑都在临时库）：
真实开发库仍是 19 表 / `revision=de8148733a9b` / `journal_mode=delete`，没被迁移过；
`model_calls` 在**开发库**里仍是 0 行，真实模型调用只发生在临时库
（`python -m backend.scripts.model_e2e_check` 跑完就有 2 行、约 ¥0.04）。
`config/models.yaml` 的两条模型都填了核对过的价目，未填价目的模型仍会被
`budget.evaluate_model_call()` 以 `pricing_missing` 拒绝（M1.2 决策 Q8=B，宁可拒绝也不按 0 元记账）。

逐项状态见 `docs/research_hub/v5_0_delivery_report.md`（V5.0 二十个模块的落地状态、
真实运行证据与 BLOCKED 项）；视频侧的发布阻断项见
`docs/research_hub/phase2_handover_report.md` 与 `docs/research_hub/phase2_remaining_issues.md`。
