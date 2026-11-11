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

> 当前阶段状态：数据库、服务层、API、Agent 角色与协议、内容/视频流程骨架已就绪并测试通过；
> **M2.0 骨架已落地**（配置层、调用者身份与工具权限闸门、task_type 注册表、
> SQLite 连接参数与**显式** WAL 初始化、三批迁移 B1/B2/B3、启动自检、请求追踪），
> 但仍**未接入任何模型 API、搜索工具、渲染引擎**，
> 执行层（TaskRuntime/Orchestrator）尚未开始。详见 `docs/phase1_acceptance.md` 与
> `docs/research_hub/m2_0_delivery_report.md` 的功能状态表。
>
> 注意：`journal_mode=WAL` **不会**因为启动服务或查状态而自动打开，它是显式动作——
> `python -m backend.scripts.enable_wal --database-url <库> --dry-run` 先看清目标，再去掉 `--dry-run` 执行。

## 快速开始（Windows / PowerShell）

```powershell
# 1. 虚拟环境（Python 3.12）
py -3.12 -m venv .venv

# 2. 依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 环境变量（不放真实密钥）
Copy-Item .env.example .env

# 4. 建库（Alembic 迁移；动手前先备份现有库到 data/backups/）
New-Item -ItemType Directory -Force data\backups | Out-Null
if (Test-Path data\finance_media.db) {
    sqlite3 data\finance_media.db ".backup data\backups/before-$(Get-Date -f yyyyMMdd-HHmmss).db"
}
.\.venv\Scripts\python.exe -m alembic upgrade head

# 5. 灌入六大合集（幂等；默认拒绝对 data/finance_media.db 写入，需显式放行）
.\.venv\Scripts\python.exe -m backend.scripts.seed_collections --allow-development-db

# 6. 启动
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
#    http://127.0.0.1:8000/docs

# 7. 测试
.\.venv\Scripts\python.exe -m pytest -q
```

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

统一错误响应：`{"error": {"code": "...", "message": "...", "details": ...}}`。

每个响应都带 `X-Request-Id`（`req-<16 位十六进制>`，可用同名请求头透传，非法值会被替换），
访问日志与异常日志都带同一个 id。

## 调用者身份与工具权限（M2.0）

- 每个路由必须显式声明允许的调用者（`backend/app/api/router.py` 的 `ROUTE_CALLERS`）：
  `GET /health` 公开，其余 20 个操作当前**只允许 `human`**；启动自检用 `app.openapi()`
  与这张表双向比对，任何未声明/多余的端点都会让进程拒绝启动。
- `agent` 身份**不能由外部请求声明**：`X-Caller` 只作审计标签，非 `human` 值会被忽略并记录
  `forged_header` 告警；agent 上下文只能由执行层在进程内构造（`CallerContext.for_executor`）。
- `dev` 端点仅限 `/api/_dev/*` 且 `APP_ENV != production`，生产环境返回 404。
- 越权一律 422 `domain_validation_error` + `details.reason="permission_denied"`。
- 工具登记表 `backend/app/capabilities/tools.py` 里 10 个工具的 `implemented` **全部为
  `False`**：现在调用任何工具都返回 `not_implemented`，这是刻意为之，不是缺陷。

## 目录结构

```
backend/app/{api,core,models,schemas,services,database}   服务代码
backend/app/execution/             L2 执行层：task_types.py（十种 task_type）、routing.py（路由与一致性校验）
backend/app/capabilities/          L3 能力层骨架：guard.py（权限闸门）、tools.py（工具登记）、budget.py（成本闸门）
backend/app/parsers/               仅有 Protocol（ParsedDocument），无任何解析实现
backend/app/database/              session.py（连接级 PRAGMA）、wal.py（WAL 显式初始化，默认拒绝开发库）
backend/migrations/                Alembic 迁移（初始 de8148733a9b + B1/B2/B3 三批）
backend/scripts/seed_collections.py  按 code upsert 六大合集（幂等；默认拒绝写开发库）
backend/scripts/enable_wal.py     显式把库切到 WAL（默认拒绝开发库，支持 --dry-run）
agents/                            九角色提示词 + roster.yaml + PROTOCOL.md
knowledge/{8 个子域}/               知识条目 Markdown（规范见 knowledge/README.md）
content/{topics,scripts,storyboards,assets,published}
video_engine/{templates,renderers,subtitles,audio,quality_control}
prompts/{system,research,writing,review,production}
config/{settings,brand,content_rules,content_taxonomy,research_sources,models,agent_routing,pipelines}.yaml
data/{raw,processed,exports}        本地数据（大文件不入库）
data/backups/                     迁移前的 sqlite3 .backup 目标目录
docs/                              战略、架构、数据模型、Agent 手册、工作流、路线图、验收报告
tests/                             253 个 pytest 用例
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
| `docs/research_hub/` | **第二阶段架构设计**：13 份设计文档 + M2.0 实施计划与交付报告（现状审计、系统架构、业务流程、数据库设计、Agent 架构、工具能力、API、状态机、路线图、验收标准、决策登记、交付报告） |
| `agents/PROTOCOL.md` | Agent 统一输入输出协议（10 字段）+ 品牌与内容规则 |

## 安全约定

- 密钥只走环境变量；`.env` 已被 `.gitignore` 忽略，仓库只提交 `.env.example`（空占位）。
- `config/*.yaml` 不存放任何真实 API Key。
- 系统**不会自动发布**任何内容（`config/brand.yaml` 中 `auto_publish: false`），
  也不输出面向个人的具体投资买卖建议（`config/content_rules.yaml` 硬约束）。
- 第一阶段是本地单机服务：无鉴权、无限流，不要暴露到公网。

## 尚未实现（避免误解）

执行层（`TaskRuntime` / `Orchestrator` / `AgentExecutor`）、模型适配层（`ModelGateway` 与真实
provider）、搜索与抓取、文档解析（PDF/XLSX/HTML）、证据抽取、报表生成、视频渲染与合成、
配音与字幕生成、图表生成（matplotlib 未安装）、脚本/审核/发布接口、知识库 Markdown 自动同步、
语义检索与向量库、真实鉴权、CI 与云部署。逐项状态见 `docs/phase1_acceptance.md`
与 `docs/research_hub/m2_0_delivery_report.md`。

M2.0 之后仍然存在的结构性事实（不是 bug，是尚未到达的阶段）：
`model_calls` / `tool_calls` / `research_tasks` / `research_subtasks` 四张表建好了但**一行没写**；
`capabilities/tools.py` 里十个工具全部 `implemented=False`；`config/models.yaml` 里两个模型的
价目都是 `null`，因此任何真实模型调用都会被 `budget.evaluate_model_call()` 以
`pricing_missing` 拒绝（M1.2 决策 Q8=B）。

**另外两项已知缺口**（`docs/content_strategy.md` §3、§9 有详细说明）：

- 选题的**多合集分类**在库里已有 `topic_collections` 表（M2.0 B1 建好），但还没有写入路径，
  `topics.column_id` 仍在双写期；多标签 `topic_tags` 同样只有表没有接口。
- 概念类知识条目只能落在 `knowledge_domain=general`，枚举里没有 `concepts` 取值。
