# M2.0 交付报告（工程实施：T01–T12）

> 项目：AK Research Hub / `finance-media-ai`
> 基线提交：`7ec12836cbb29dd88b9226454001247fa8b3b321`（M1.2-final）
> 本轮范围：`docs/research_hub/m2_0_implementation_plan.md` 的 T01–T12
> 本轮授权：允许按计划开发；**真实开发库迁移、外部模型调用、联网抓取、云部署、Git 提交/推送均未获授权**
> 交付时间：2026-11-10（初版）／ 2026-11-10（**专项整改后终版**，见 §11）
> 工作区状态：**未 commit、未 push**，全部改动留在工作区（详见 §8）

> **终版数字**：测试 **253 passed / 1 warning**（初版 228 passed / 2 warnings）；
> §5 的演练与 §6 的只读核验在整改后**重新跑过**，结论未变；§9 的 R1 已消除、R2 降级为已记录的非阻断风险。
> 正文保留初版描述以便对照，凡与整改相关之处均在原地标注，完整整改过程见 **§11 专项整改**。

---

## 0. 一页纸结论

1. **T01–T12 全部完成**，没有一项是靠"创建了文件"宣布的：每一步都有可复现的命令与输出（§1、§4、§5）。
2. **测试从 96 → 253 passed**（0 failed）：初版 228，专项整改后新增 25 个用例
   （WAL 初始化 13、FK 环语义 5、调用者权限端到端与自检反向 7），覆盖配置层、调用者身份、工具权限、
   成本闸门、task_type 注册表、迁移批次、启动自检、请求追踪、合集 seed、WAL 显式初始化。
3. **数据库：18 → 28 张业务表**（29 张物理表含 `alembic_version`），B1/B2/B3 三批迁移**各自可往返**
   （`upgrade → downgrade -1 → upgrade` 全绿），全回滚后只剩 `alembic_version`。
4. **真实开发库 `data/finance_media.db` 一个字节都没动**：仍是 19 张表、revision `de8148733a9b`、
   `journal_mode=delete`、无 `-wal`/`-shm` 文件、文件 mtime 仍是 2026-11-09 17:54:30（§6）。
5. **`model_calls` / `tool_calls` / `research_tasks` / `research_subtasks` 交付时都是 0 行**，
   代码里没有任何写入这些表的语句（唯一命中是 `models/metering.py` 的类定义，§7）。
6. **能力一个都没接**：10 个工具的 `implemented` 全为 `False`；`config/models.yaml` 两个模型价目为 `null`，
   任何真实模型调用都会被 `budget.evaluate_model_call()` 以 `pricing_missing` 拒绝（M1.2 决策 Q8=B）。
   本轮交付的是**闸门与账本**，不是能力本身。
7. **计划里的一条被撤销**：A6（对开发库执行迁移）被人工明确撤销，因此三批迁移**只在临时库演练**。
   计划文件清单里的 `capabilities/db_read.py` / `db_write.py` 未创建（属 2.2/2.5 的真实读写实现）。
8. 初版遗留的最严重风险 R1（**下次启动应用会把开发库切成 WAL**）已在**专项整改**中消除：
   `journal_mode=WAL` 不再出现在连接事件里，只能由 `backend/app/database/wal.py` 的显式入口
   （`python -m backend.scripts.enable_wal`）开启，且默认拒绝真实开发库。整改后开发库仍是
   `journal_mode=delete`、sha256 与整改前基线**完全一致**（§11）。

---

## 1. T01–T12 完成情况

| 任务 | 状态 | 交付物 | 关键证据 |
|---|---|---|---|
| T01 基线冻结与保护 | ✅ DONE | `.gitignore` 增 `*.db-wal`/`*.db-shm`/`data/backups/**`（保留 `.gitkeep`）；新建 `data/backups/.gitkeep` | `git diff .gitignore`；`Test-Path data/backups/.gitkeep` |
| T02 配置文件与校验 | ✅ DONE | 新增 `config/models.yaml`(59 行)、`config/agent_routing.yaml`(304)、`config/pipelines.yaml`(120)；`config/settings.yaml` 增 `execution`/`cost`/`features`；新增 `backend/app/core/config_schema.py`(295)；`backend/app/core/config.py` 增 6 个类型化读取器 | §4 的 `test_config_layers.py` 17 例；六个读取器实测输出（§3.2） |
| T03 SQLite 参数与状态 | ✅ DONE（**已整改**） | `backend/app/database/session.py` 每连接执行 3 条 PRAGMA（`foreign_keys`/`busy_timeout`/`synchronous`）+ `read_sqlite_pragmas()`；**`journal_mode=WAL` 移出连接事件**，改由新增的 `backend/app/database/wal.py` 显式初始化（CLI：`backend/scripts/enable_wal.py`）；`DatabaseStatus` 增 `journal_mode`/`busy_timeout`；`FeatureFlags` 增 `model`/`search`/`execution` | §5 冒烟输出：临时库 `journal_mode=wal`（由显式初始化产生）；`tests/test_system_status.py` 5 例；`tests/test_wal_initialization.py` 13 例（§11.1） |
| T04 调用者身份与工具权限 | ✅ DONE | 新增 `core/caller.py`(203)、`capabilities/{__init__(37),guard.py(81),tools.py(211),budget.py(228)}`、`parsers/__init__.py`(66)、`api/deps.py` 增 `HumanCaller`/`DevCaller`、`api/router.py` 增 `ROUTE_CALLERS`(21 条)、5 个路由模块声明 `require_caller` | §4 `test_caller_permissions.py` 20 例、`test_tool_registry.py` 15 例、`test_budget.py` 15 例；§5 第 7 项伪造头实测 |
| T05 task_type 注册表 | ✅ DONE | 新增 `execution/{__init__,task_types.py(87),routing.py(205)}`；9 份 `agents/*/PROMPT.md` 声明行与旧子任务名清理 | §4 `test_task_type_registry.py` 21 例；扫描脚本 `TOTAL_HITS=0` |
| T06 B1 迁移 | ✅ DONE | `20261110_1700_b1_topic_taxonomy.py`(373)：4 新表 + `topic_columns` 3 列 + `topics` 7 列 + 2 个部分唯一索引 + 3 段幂等回填 | §5 演练项 [1][2][3][8][10] |
| T07 B2 迁移 | ✅ DONE | `20261110_1700_b2_task_layer.py`(380)：4 新表 + `agent_runs` 3 列 + `research_reports` 3 列 | §5 演练项 [1][4][6][7] |
| T08 B3 迁移 | ✅ DONE | `20261110_1700_b3_metering.py`(211)：`model_calls`/`tool_calls` + `research_tasks` 4 列（含预留） | §5 演练项 [1][5]；§7 零行证明 |
| T09 启动自检 | ✅ DONE | 新增 `core/startup_checks.py`(243)：7 项检查，error 拒绝启动、warning 只记录；`main.py` lifespan 挂 `app.state.startup_report` | §5 第 2/3 项：7 项全绿 + 1 条 pricing warning；`test_startup_checks.py` 9 例 |
| T10 请求追踪 | ✅ DONE | 新增 `core/request_context.py`(61)、`api/middleware.py`(92)：纯 ASGI 中间件生成/透传 `X-Request-Id`，访问日志与异常日志同 id | §5 第 4/9/10 项；`test_request_tracing.py` 19 例 |
| T11 文档同步 | ✅ DONE | `system_architecture.md` §6 目录树改为**实际布局**；`database_design.md` §6.4 增 7 条实测注记；`implementation_roadmap.md` 2.0 表数 26→**28**；工具名 `db_read`→`db_query`（4 份文档 8 处）；`README.md` 与 `docs/research_hub/README.md` 同步 | `git diff --stat docs/`（6 份文档改动） |
| T12 全量测试与交付 | ✅ DONE | **253 passed**（初版 228，整改 +25）；临时库迁移演练 14 项；本报告 | §4、§5、§6、§7、§8、§11 |

**未做（本轮明确不授权或不属于 2.0）**：M2.1 模型适配层、执行层 `TaskRuntime`/`Orchestrator`、
真实搜索/抓取/解析/渲染、Skill & Tool Library 落地、内容风格 Style Profiles、Git 提交与推送。

---

## 2. 实际新增 / 修改 / 删除的文件

> 全部路径相对仓库根 `C:\Users\25803\finance-media-ai`。行数为实测。

### 2.1 新增（源码与配置）

| 文件 | 行数 | 作用 |
|---|---|---|
| `config/models.yaml` | 59 | provider/模型登记；价目全 `null`（= missing）；`pricing_policy: reject_unknown` |
| `config/agent_routing.yaml` | 304 | **task_type 唯一权威**（10 种）+ 9 个 Agent 的 tools/db_scopes |
| `config/pipelines.yaml` | 120 | `research_to_script`（11 阶段）、`script_to_video`（5 阶段），含 gate 与 human_gate |
| `backend/app/core/config_schema.py` | 295 | 全部 `extra="forbid"` 的配置 schema；跨字段校验 |
| `backend/app/core/caller.py` | 203 | `CallerKind` / `CallerContext` / `build_http_caller` / `require_caller` |
| `backend/app/core/startup_checks.py` | 243 | 7 项启动自检 + `StartupCheckError` |
| `backend/app/core/request_context.py` | 61 | `X-Request-Id` 生成、清洗、ContextVar |
| `backend/app/api/middleware.py` | 92 | 纯 ASGI 请求追踪中间件 |
| `backend/app/capabilities/__init__.py` | 37 | 统一导出 |
| `backend/app/capabilities/guard.py` | 81 | 工具/数据域权限闸门（四类拒绝原因） |
| `backend/app/capabilities/tools.py` | 211 | 10 个工具登记表 + `ToolRegistry.dispatch()`（全部未实现） |
| `backend/app/capabilities/budget.py` | 228 | 价目解析 + 单次/任务/当日三道闸门（含预留） |
| `backend/app/parsers/__init__.py` | 66 | `ParsedDocument` + `DocumentParser` Protocol（**零实现**） |
| `backend/app/execution/__init__.py` | 37 | 显式声明"本阶段没有执行器" |
| `backend/app/execution/task_types.py` | 87 | 10 种 `TaskType` + 人工闸门集合 + 未知类型错误 |
| `backend/app/execution/routing.py` | 205 | 路由表读取 + 三处一致性校验 |
| `backend/app/models/taxonomy.py` | 180 | B1 四表 ORM（`tags`/`topic_collections`/`topic_tags`/`topic_score_snapshots`） |
| `backend/app/models/execution.py` | 224 | B2 四表 ORM（`research_tasks`/`research_subtasks`/`research_task_dependencies` 等价物/`task_events`） |
| `backend/app/models/metering.py` | 137 | B3 两表 ORM（`model_calls`/`tool_calls`） |
| `backend/scripts/__init__.py` | 7 | 脚本包 |
| `backend/scripts/seed_collections.py` | 164 | 六大合集按 `code` 幂等 upsert；默认拒写开发库（退出码 2） |
| `backend/app/database/wal.py` | 175 | **整改新增**：WAL 显式初始化入口 + 目标库识别与防误伤 |
| `backend/scripts/enable_wal.py` | 81 | **整改新增**：`--database-url`/`--dry-run`/`--allow-development-db` CLI |
| `data/backups/.gitkeep` | 5 | 备份目录占位（`.db` 被忽略） |

### 2.2 新增（迁移）

| 文件 | 行数 | revision |
|---|---|---|
| `backend/migrations/versions/20261110_1700_b1_topic_taxonomy.py` | 373 | `b1_topic_taxonomy` ← `de8148733a9b` |
| `backend/migrations/versions/20261110_1700_b2_task_layer.py` | 380 | `b2_task_layer` ← `b1_topic_taxonomy` |
| `backend/migrations/versions/20261110_1700_b3_metering.py` | 211 | `b3_metering` ← `b2_task_layer` |

### 2.3 新增（测试）

| 文件 | 行数 | 用例数 |
|---|---|---|
| `tests/test_config_layers.py` | 230 | 17 |
| `tests/test_caller_permissions.py` | 333 | **27**（初版 20，整改 +7） |
| `tests/test_budget.py` | 278 | 15 |
| `tests/test_tool_registry.py` | 141 | 15 |
| `tests/test_task_type_registry.py` | 198 | 21 |
| `tests/test_startup_checks.py` | 135 | 9 |
| `tests/test_request_tracing.py` | 135 | 19 |
| `tests/test_seed_collections.py` | 117 | 6 |
| `tests/test_wal_initialization.py` | 228 | **13（整改新增）** |
| `tests/test_fk_cycle_semantics.py` | 146 | **5（整改新增）** |

### 2.4 修改（43 个已跟踪文件，`1032 insertions(+), 122 deletions(-)`；新增本报告后继续增长）

| 类别 | 文件 |
|---|---|
| 配置/约定 | `.gitignore`、`config/settings.yaml`（+`execution`/`cost`/`features`）、`requirements.txt`（`httpx`→`httpx2`） |
| 数据库层 | `backend/app/database/session.py`（连接级 PRAGMA 三条，WAL 移出）、`backend/app/database/__init__.py`（导出 WAL 入口）、`backend/migrations/env.py`（**修 `disable_existing_loggers=False`**） |
| 核心 | `backend/app/core/config.py`（6 个读取器 + `cost_daily_cap_cny`）、`backend/app/core/errors.py`（`PermissionDeniedError`）、`backend/app/core/caller.py`（`settings_for_request` + `allowed_kinds`）、`backend/app/core/startup_checks.py`（路由走查 + 四查） |
| ORM | `backend/app/models/types.py`（`enum_column(server_default=...)`）、`models/topic.py`、`models/agent.py`、`models/research.py`、`models/enums.py`（+9 个枚举）、`models/__init__.py` |
| API | `backend/app/api/deps.py`、`api/router.py`（`ROUTE_CALLERS` + `NON_API_ROUTES`）、`api/routes/{health,topics,knowledge,sources,research_reports}.py` |
| 服务/主程序 | `backend/app/services/system_service.py`、`backend/app/schemas/system.py`、`backend/app/main.py` |
| Agent 提示词 | `agents/{orchestrator,financial_researcher,football_researcher,industry_researcher,case_analyst,fact_checker,scriptwriter,visual_designer,video_producer}/PROMPT.md`（9 份，注册表声明 + 旧子任务名清理） |
| 测试 | `tests/test_migrations.py`（14 例）、`tests/test_system_status.py`（PRAGMA 断言 `wal`→`delete`）、`tests/test_caller_permissions.py`（差分在 2.3 表中已计入新增行数） |
| 文档 | `README.md`、`docs/research_hub/{system_architecture,database_design,implementation_roadmap,tooling_architecture,agent_architecture,acceptance_criteria,README}.md` |

### 2.5 删除

| 文件 | 原因 |
|---|---|
| `backend/migrations/versions/20261110_1623_bgen_draft.py` | 临时 autogenerate 草稿（443 行），内容已拆成 B1/B2/B3 三份正式迁移 |

**没有删除任何业务代码、配置、测试或文档。**

---

## 3. 核心模块调用关系

### 3.1 HTTP 请求路径（M2.0 新增的部分加粗）

```
请求
 └─ **RequestContextMiddleware**（纯 ASGI）
      ├─ 生成/清洗 X-Request-Id → ContextVar + 响应头
      └─ 记录 `METHOD /path -> status 12.3ms request_id=req-…`
 └─ FastAPI 路由
      ├─ **require_caller(CallerKind.HUMAN)** ← **build_http_caller(request, settings)**
      │     ├─ 路径前缀 /api/_dev/ 且 app_env != production → dev
      │     ├─ 否则 → human
      │     └─ X-Caller 头只作审计：非 human 值 → warning + forged_header=True（**不升级身份**）
      ├─ 参数校验（Pydantic）→ 422 request_validation_error
      └─ Service 层 → ORM → SQLite（每连接 4 条 PRAGMA）
```

### 3.2 启动路径

```
create_app()
 ├─ install_exception_handlers(app)
 ├─ **install_middleware(app)**                       # 幂等
 └─ lifespan 启动
      ├─ configure_logging() / ensure_runtime_dirs()
      ├─ **run_startup_checks(app, settings)**  → app.state.startup_report
      │    1 config_sections      六个配置读取器全部解析（error）
      │    2 task_type_registry   routing.validate_registry()（error）
      │    3 agent_roster         roster_keys_missing_prompts()（error）
      │    4 tool_specs           TOOL_NAMES == TOOL_SPECS（error）
      │    5 route_callers        app.openapi() ↔ ROUTE_CALLERS 双向比对（error）
      │    6 pricing              价目缺失 → **warning**（Q8=B 在调用时拒绝）
      │    7 database             只读 SELECT 1 + 表数（连不上只 warning）
      └─ **assert_startup_ok(report)** → 有 error 就拒绝启动
```

### 3.3 工具调用路径（骨架，尚无调用者）

```
（将来的 AgentExecutor）
 └─ ToolRegistry.dispatch(caller, tool_name, **args)
      ├─ 未知工具 → ToolResult(status="error", error_code="unknown_tool")
      ├─ guard.require(caller, spec, routing=…)
      │     ├─ allowed_kinds 不含该 kind → denied / permission_denied
      │     ├─ agent_only 且 caller 不是 agent → denied
      │     ├─ agent 白名单不含该工具 → denied / tool_not_in_whitelist
      │     └─ 缺少 required_scope → denied / scope_denied（fail-closed）
      └─ spec.implemented is False → ToolResult(status="not_implemented")
```

### 3.4 成本闸门路径（纯函数，无副作用）

```
evaluate_model_call(provider, model, est_calls, est_cost_cny, …)
 ├─ models_config.find_model() → 未登记 → model_not_registered / 未启用 → model_disabled
 ├─ resolve_price() → 价目缺失 → pricing_missing（Q8=B，**绝不按 0 元放行**）
 ├─ 单次闸门：est_cost_cny > upper_bound_per_call_cny → single_call_exceeded
 ├─ 任务闸门：spent + reserved + est > budget_max_* → task_calls_exceeded / task_cost_exceeded
 └─ 当日闸门：daily_spent + est > cost.daily_cap_cny → daily_cap_exceeded
```

### 3.5 配置读取路径

```
config/*.yaml ──load_yaml_config()──> pydantic schema（extra="forbid"）──lru_cache──> 读取器
   settings.yaml      → settings.yaml 段: execution / cost / features
   models.yaml        → get_models_config()      (ModelsConfig.find_model / pricing_status)
   agent_routing.yaml → get_agent_routing()      (allowed_agents_for / tools_for / db_scopes_for)
   pipelines.yaml     → get_pipelines_config()   (PipelineSpec / PipelineStage)
```
`clear_settings_cache()` 一次清 8 个缓存（含上述 6 个），测试用它在用例间隔离配置。

### 3.6 task_type 三处一致性

```
config/agent_routing.yaml 的 task_types 键集合
        ==  backend/app/execution/task_types.py 的 TASK_TYPES（启动自检 + 单元测试）
        ==  9 份 agents/*/PROMPT.md 的声明行并集（routing.validate_registry()）
        ⊇  docs/research_hub/{module_design,api_design,agent_architecture}.md 里的字面量（测试扫描）
```
任何一处不齐 → 启动拒绝（error 级），或 `pytest` 直接失败。

---

## 4. 自动化测试结果

**命令**：`.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts="" -q`
**结果（整改后终版）**：`253 passed, 1 warning in 40.50s`（0 failed）
**结果（初版，保留对照）**：`228 passed, 2 warnings in 33.87s`

> 注：`pyproject.toml` 的 `addopts` 已含 `-q`，命令行再加 `-q` 会变成 `-qq` 从而**抑制最后的统计行**
> （只看到 `-- Docs:` 就以为没输出）。核对总数时用 `-o addopts=""` 覆盖，或直接跑 `pytest`。

| 测试文件 | 用例 | 覆盖点 |
|---|---|---|
| `test_agent_contracts.py` | 10 | Agent 协议、名册、prompt 文件存在性（M1 遗留） |
| `test_budget.py` | 15 | 价目 known/missing/approved_exception；单次/任务/当日三闸门含预留；`evaluate_model_call` 六种拒绝原因 |
| `test_caller_permissions.py` | **27** | agent 上下文不可伪造；`X-Caller` 只作审计；dev 仅非生产、生产 404；`ROUTE_CALLERS` 与路由实际挂载**逐条**一致且 21 条；只有 `/health` 公开；四类自检反向用例（§11.2） |
| `test_config.py` | 11 | 原有 YAML 断言（品牌/内容规则/研究来源/视频模板/12 项质检） |
| `test_config_layers.py` | 17 | 6 个读取器 + 非法配置拒绝 + 价目 missing 不为 0 + 未知段抛 `ValueError` |
| `test_fk_cycle_semantics.py` | **5** | 外键环两侧语义与 `SET NULL` 实测、删后 `foreign_key_check` 空 + `integrity_check` ok、关系映射无歧义（§11.3） |
| `test_health.py` | 6 | 存活检查与必需路径 |
| `test_knowledge.py` | 7 | 知识条目 CRUD、检索、关联约束 |
| `test_migrations.py` | 14 | 逐批表数（18/22/26/28）、每批往返、部分索引真伪、回填映射与幂等、`alembic check` 无漂移、离线 SQL 的已知限制 |
| `test_models_constraints.py` | 12 | ORM 约束（CHECK/唯一/自引用） |
| `test_request_tracing.py` | 19 | ID 形状、清洗白名单、响应头、错误响应、伪造头不改身份、访问日志、ContextVar 复位 |
| `test_research_reports.py` | 4 | 报告创建与校验 |
| `test_seed_collections.py` | 6 | 6 合集 upsert 幂等、标志修正保留 id、`dry_run`、**拒写开发库返回 2** |
| `test_sources_evidence.py` | 6 | 来源/证据与二选一目标 |
| `test_startup_checks.py` | 9 | 7 项全绿；四类 error 会让启动拒绝；pricing 只 warning |
| `test_strategy_docs.py` | 20 | 品牌/内容战略文档与配置一致性（M0 遗留） |
| `test_system_status.py` | 5 | features 七键全 false；`journal_mode` 真读且**测试库必须是 `delete`**（读到 wal 即说明查状态改了库） |
| `test_task_type_registry.py` | 21 | 三处一致性、人工闸门口径、文档扫描、未知类型 422 |
| `test_tool_registry.py` | 15 | 工具表与 `TOOL_NAMES` 一一对应、**全部 `implemented=False` 的诚实断言**、四类拒绝原因、`unknown_tool` |
| `test_topics.py` | 11 | 选题 CRUD、分页过滤、非法载荷 |
| `test_wal_initialization.py` | **13** | WAL 只能由显式入口开启；普通连接/启动/状态查询都不改库文件；幂等、dry-run、拒绝开发库/缺失库/内存库/非 SQLite；开发库保持 `delete`（§11.1） |

**1 条 warning（整改后仅剩这一条，已定位并判定为非阻断）**：

`SAWarning: Cannot correctly sort tables; there are unresolvable cycles between tables
"agent_runs, research_subtasks"` —— 出现在 `backend/migrations/env.py:77`
（`context.run_migrations()` 期间的依赖图排序），根因在 SQLAlchemy `sql/ddl.py`：
只有 `use_alter=True` 的外键才会被移出依赖图。`research_subtasks.agent_run_id` 与
`agent_runs.subtask_id` 互指（都是 `SET NULL`），是 `database_design.md` §5 批准的双向语义
（"当前执行尝试"与"祖先任务"，不是冗余列）。

**判定：不修**。若改 `use_alter=True`，SQLite 不支持 `ALTER TABLE ADD CONSTRAINT`，
`create_all` 建表时会**静默丢失该外键**（比警告更糟）。已用 `tests/test_fk_cycle_semantics.py`
把两侧 `SET NULL` 行为、`PRAGMA foreign_key_check`、`integrity_check`、关系映射无歧义全部钉住，
并列入 §9 R2（已降级）。

**初版另一条 warning 已真修（不是屏蔽）**：
`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
根因是 `starlette/testclient.py` 首选 `import httpx2`、缺失时回退 `import httpx` 并告警；
本项目代码**从不直接 import httpx**（全仓 grep 为空）。处置：安装 `httpx2 2.16.0`（附
`httpcore2`、`truststore`），`requirements.txt` 的测试依赖由 `httpx` 换成 `httpx2`。
装完告警消失、测试全绿（§11.3）。

**测试分级（按任务书要求区分）**：

| 级别 | 数量 | 例子 |
|---|---|---|
| 单元测试 | ~125 | `test_budget`、`test_config_layers`、`test_caller_permissions`、`test_task_type_registry`、`test_tool_registry`、`test_wal_initialization` |
| 集成测试（进程内 HTTP + 真实 SQLite） | ~95 | `test_topics`、`test_knowledge`、`test_system_status`、`test_request_tracing`、`test_startup_checks`、`test_seed_collections`、`test_fk_cycle_semantics` |
| 数据库迁移演练（真实 Alembic 调用） | 14 | `test_migrations.py`（含逐批往返与回填幂等） |
| 端到端（真实 uvicorn 进程） | 0 | **本轮未启动连开发库的服务进程**（避免 WAL 持久化）；整改后即使启动也不会改库模式（§11.1），但端到端仍属 2.1 范围 |

---

## 5. 数据库临时迁移演练结果

**演练脚本**：`%TEMP%\fmai_rehearse.py`（只操作 `%TEMP%\fmai_rehearsal.db`，结束后删除）
**注入方式**：`cfg.cmd_opts = SimpleNamespace(x=[f"db_url=sqlite:///{tmp}"] )`，与 `alembic -x db_url=` 等价

| 序号 | 检查 | 实测输出 |
|---|---|---|
| [1] | 全量升级后表数/revision | `tables=29 business=28 revision=b3_metering` |
| [2] | B1–B3 十张新表是否齐全 | 全部存在：`tags`、`topic_collections`、`topic_tags`、`topic_score_snapshots`、`research_tasks`、`research_subtasks`、`research_subtask_dependencies`、`task_events`、`model_calls`、`tool_calls` |
| [3] | `topics` 新增 7 列 | 全部存在 |
| [4] | `agent_runs` 新增 3 列 | 全部存在 |
| [5] | `research_tasks` B3 四列 | 全部存在 |
| [6] | `topic_columns` 新增 3 列 | 全部存在 |
| [7] | `research_reports` 新增 3 列 | 全部存在 |
| [8] | 两个部分唯一索引是否真"部分" | `{'uq_topic_primary_collection': True, 'uq_topic_current_score': True} all_partial=True` |
| [9] | 完整性 | `foreign_key_check=[] integrity=ok index_count=170` |
| [10] | 回填幂等 | `backfill_idempotent=True rows=1`（重复执行仍只有 1 行） |
| [11] | 全回滚 | `after_downgrade_tables=['alembic_version']` |
| [12] | 再升级 | `reupgrade_tables=29 business=28` |
| [13] | 模型与迁移是否漂移 | `alembic_check=clean`（`No new upgrade operations detected.`） |
| [14] | 临时库清理 | `temp db cleaned: True` |

> **整改后复跑**：本表 14 项在专项整改完成后**原样重跑一次**，输出完全一致（§11.4）。
> 复跑时脚本退出码为 0；此前显示 `[exit code: 1]` 是 PowerShell 管道（`| Select-Object`）造成的假象。

**逐批往返**（由 `tests/test_migrations.py::test_batches_are_individually_reversible` 固化）：
`de8148733a9b(18) → b1(22) → b2(26) → b3(28)`，每批 `upgrade → downgrade -1 → upgrade` 全部成功。

**演练中发现并修掉的 3 个真问题**（都是"只在临时库才会暴露"的问题，证明了演练的必要性）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | B1 `downgrade` 崩溃：`sqlite3.OperationalError: no such column: status` | SQLite batch 重建会反射已有 CHECK，删列后 DDL 仍引用该列 | 先 `batch_op.drop_constraint(batch_op.f("ck_topics_topic_status"), type_="check")`；**必须用 `batch_op.f()`**，裸名字会被命名约定二次拼接成 `ck_topics_ck_topics_topic_status` |
| 2 | B1 `upgrade` 对**已有行**的库失败：`NOT NULL constraint failed: _alembic_tmp_topics.status` | `topics.status` NOT NULL 且无默认值，批量重建插入新列时无值 | 迁移加 `server_default="candidate"`；`enum_column()` 增 `server_default` 参数；ORM 侧同步声明 |
| 3 | ORM 映射歧义：`AmbiguousForeignKeysError ... ResearchSubtask.agent_run` | `research_subtasks` 与 `agent_runs` 之间有多条外键路径 | `relationship(foreign_keys=[agent_run_id])` |

**SQLite 批量模式的已知限制**（已写成测试，明确记录而非隐瞒）：
`alembic upgrade --sql` 一旦进入 B1 就抛 `CommandError: ... batch mode`，因此**无法离线审阅 B1–B3 的 SQL**；
替代复核手段是"临时库往返 + `alembic check`"，两者都已自动化。

---

## 6. 对真实开发数据库未做修改的证明

**证明对象**：`C:\Users\25803\finance-media-ai\data\finance_media.db`
**取证方式**：以 `mode=ro`（只读 URI）打开，不可能写入

| 指标 | 交付时实测值 | 说明 |
|---|---|---|
| 文件大小 | `540672` bytes | 与 M1.2 结束时一致 |
| 文件 mtime | `2026-11-09T17:54:30`（本地） | **早于本轮全部工作（2026-11-10）** |
| SHA-256 | `2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203` | 可作为下一轮的对照基线 |
| `-wal` / `-shm` 文件 | 都不存在 | 一旦被切成 WAL 就会生成 |
| `PRAGMA journal_mode` | `delete` | **仍是默认值**，未被切成 `wal` |
| 表数量 | `19` | 未出现任何 B1/B2/B3 新表 |
| B1–B3 十张表 | `[]`（一个都没有） | |
| `alembic_version` | `de8148733a9b` | 未升级 |
| 非空表 | 仅 `alembic_version`(1 行) | 业务表全空，**没有写入任何 seed/回填数据** |
| `model_calls` 表 | 不存在 | 见 §7 |

**做法上的保证**：

- 三批迁移**只在** `%TEMP%` 下的临时库执行；测试统一走 `tests/conftest.py` 的临时库 fixture。
- `backend/scripts/seed_collections.py` 内置 `PROTECTED_DB_SUFFIX = "data/finance_media.db"`：
  指向开发库时**默认拒绝写入并返回退出码 2**，必须显式加 `--allow-development-db`（本轮从未加过）。
  该行为有测试固化（`test_seed_collections.py`）。
- 本轮**没有启动过任何连接开发库的服务进程**。初版的理由是"启动会持久改写 `journal_mode`"（R1）；
  **整改后这条理由已不成立**（WAL 移出连接事件，§11.1），但端到端仍未做：它属于 2.1 的范围，
  而不是靠"能做就顺手做"。
- 整改轮新增的 `backend/app/database/wal.py` 同样内置 `PROTECTED_DB_SUFFIX = "data/finance_media.db"`，
  默认拒绝、`--dry-run` 连都不连（`tests/test_wal_initialization.py` 固化）。

---

## 7. `model_calls` 为 0 的证明

| 证据 | 实测 |
|---|---|
| 开发库 | `model_calls` 表**根本不存在**（19 张表里没有） |
| 临时库全量迁移后行数 | `model_calls: 0`、`tool_calls: 0`、`research_tasks: 0`、`research_subtasks: 0` |
| 代码中是否有写入语句 | 全仓搜索 `INSERT INTO model_calls` / `INSERT INTO tool_calls` / `ModelCall(` / `ToolCall(`：**只有 2 处命中，都是 `backend/app/models/metering.py` 的类定义**，无任何实例化或插入 |
| B3 迁移文件 | 只 `create_table` + `add_column`，注释明写"本迁移只建表不写行，交付时 `model_calls` 必须 0 行" |
| `ToolRegistry.dispatch()` | 成功路径也**不写** `tool_calls`（`get_tool_registry()` 单例不带数据库会话）；2.0 无真实工具，写空行等于伪造审计 |
| 外部调用 | 本轮零模型调用、零联网：`config/models.yaml` 两个模型 `enabled: false` 且价目 `null`；`integrations.*` 全 `false` |

---

## 8. Git 差异与工作区状态

| 项 | 值 |
|---|---|
| 分支 / HEAD | `main` / `7ec12836cbb29dd88b9226454001247fa8b3b321`（未产生新提交） |
| 已跟踪修改 | **43 个文件**（`git diff --stat`：`1032 insertions(+), 122 deletions(-)`；含整改新增的 `session.py`/`database/__init__.py`/`requirements.txt`/`startup_checks.py`/`caller.py` 等改动，报告自身行数继续增长） |
| 未跟踪路径 | **40 个**（`git ls-files --others --exclude-standard` 实测） |
| 未跟踪的**历史文件** | `docs/research_hub/m1_1_architecture_review.md`（M1.1 审查报告，一直未跟踪，本轮**未修改、未提交**） |
| 提交 / 推送 | **均未执行**（任务书明确禁止） |
| 远程状态 | 与 `origin/main` 一致（`git rev-list --left-right --count HEAD...origin/main` = `0 0`），本轮无任何远程交互 |
| 依赖变更 | **新增 `httpx2 2.16.0`（含 `httpcore2`、`truststore`）**，属测试工具链；`requirements.txt` 已同步。**未安装任何业务/付费依赖** |
| 未授权操作确认 | 未调外部 API、未联网抓取、未改 Secret、未连云服务、未迁移开发库 |

未跟踪文件（40 个，`git ls-files --others --exclude-standard`）：
`backend/app/api/middleware.py`、`backend/app/core/{caller,config_schema,request_context,startup_checks}.py`、
`backend/app/capabilities/{__init__,guard,tools,budget}.py`、`backend/app/execution/{__init__,task_types,routing}.py`、
`backend/app/parsers/__init__.py`、`backend/app/database/wal.py`、`backend/app/models/{taxonomy,execution,metering}.py`、
`backend/scripts/{__init__,seed_collections,enable_wal}.py`、三个迁移文件、三份新 YAML、
十个新测试文件（原八个 + `test_wal_initialization.py` + `test_fk_cycle_semantics.py`）、
`data/backups/.gitkeep`、`docs/research_hub/{m1_1_architecture_review,m2_0_implementation_plan,m2_0_delivery_report}.md`。

---

## 9. 剩余问题与风险

| # | 风险 | 等级 | 现状与建议 |
|---|---|---|---|
| R1 | ~~下次启动应用会把开发库切成 WAL~~ **已消除（§11.1）** | ~~高~~ **已解决** | 整改前 `build_engine()` 每连接执行 `PRAGMA journal_mode=WAL`（对库文件**持久**）。整改后连接事件只剩 `foreign_keys`/`busy_timeout`/`synchronous` 三条连接级参数；WAL 只能由 `backend/app/database/wal.py` 的 `enable_wal()` 显式开启（CLI `python -m backend.scripts.enable_wal`），默认拒绝 `data/finance_media.db`，且 `dry_run` 连都不连。回归测试证明普通连接/应用启动/`/api/system/status` 都不改库（`tests/test_wal_initialization.py`）。**残留须知**：显式初始化一旦执行仍是持久的，动手前先 `sqlite3 .backup` |
| R2 | `agent_runs ↔ research_subtasks` 外键环告警 | 低（**原「中」已降级**） | 已定位到 `backend/migrations/env.py:77` 与 SQLAlchemy `sql/ddl.py` 的拓扑排序；环是 `database_design.md` §5 批准的双向语义，**不修**（改 `use_alter=True` 会让 SQLite `create_all` 静默丢外键）。已用 `tests/test_fk_cycle_semantics.py` 钉住两侧 `SET NULL`、`foreign_key_check`、`integrity_check` 与关系映射 |
| R3 | B1–B3 无法离线生成 SQL | 中 | `alembic upgrade --sql` 到 B1 抛 batch mode 错误。已在测试中显式记录；人工审阅 SQL 只能靠"临时库 dump"。若要审阅，可用 `sqlite3 tmp.db .schema` 导出后 diff |
| R4 | 价目全 `null`，2.1 一接模型就会被拒 | 中 | 这是 Q8=B 的**预期行为**，不是 bug。开工 2.1 前必须：把真实价目填进 `config/models.yaml`（含 `currency`、单位 per 1K tokens）、人工确认 `daily_cap_cny`/`default_task_cost_cny`，否则 `pricing_missing` 会拦住全部调用 |
| R5 | 十个工具全部 `implemented=False` | 中 | 现在 `ToolRegistry.dispatch()` 对任何工具都返回 `not_implemented`。2.2/2.3 逐个实现时，必须同时补"真实 provider 才置 True + 对应测试"，禁止先置 `True` 再补实现 |
| R6 | 无鉴权，本地信任边界 | 中 | 只有 `human`/`dev` 两种 HTTP 调用者，且只监听 `127.0.0.1`。**任何暴露到公网的做法都必须先做真实鉴权 + HTTPS**；`X-Caller` 永远不能当凭据 |
| R7 | 多合集/多标签只有表，没有写入路径 | 中 | `topic_collections`/`topic_tags` 已建表但无 API、无服务；`topics.column_id` 处于双写期（M1.2 §6.6 计划 2.7 切读、2.10 评估删除） |
| R8 | 双状态并存：`topics.status` 与 `topics.content_status` | 低 | 回填只覆盖 `published→content_produced`、`archived→archived`；其余组合仍以 `content_status` 为准。写业务代码前需明确"谁是权威"，否则会出现两处不同步 |
| R9 | `pyproject.toml` 的 `packages` 只列 `backend`/`backend.app` | 低 | 不影响运行与测试（pytest 用 `pythonpath=["."]`），但 `pip install -e .` 会漏掉新子包；建议与 `[project.optional-dependencies] research` 一并处理（计划 A5，本轮未做） |
| R10 | 启动自检把 `pricing` 定为 warning | 低 | 这是刻意的"不阻塞启动"，但容易被误读为"已就绪"。§5 的冒烟输出里能直接看到这条 warning，交付评审时应把它当成**待办**而不是噪音 |
| R11 | 请求追踪只在应用层 | 低 | 无 `uvicorn` access log 联动，也不写库；跨进程（未来 worker）追踪需要把 request_id 传到 `task_events`，属 2.4 范围 |
| R12 | `data/backups/` 仍为空 | 低 | 目录与 `.gitkeep` 已就位，但**尚无任何真实备份**（因为本轮没迁移开发库）。首次迁移前必须先产生第一份备份 |

---

## 10. M2.0 之后的建设建议（只记录，本轮不开发）

### 10.1 Skill & Tool Library（未来技术方向）

人工明确的方向，本轮**未安装、未执行**任何第三方 GitHub 项目，也未引入大型 Agent 框架。建议按四层落地：

| 层 | 职责 | 落地位置（建议） |
|---|---|---|
| **Skill** | 专业工作方法：怎么写研究计划、怎么交叉验证、怎么组织结构化脚本 | `skills/<skill_id>/SKILL.md` + 可选样例；由 Agent 在执行时按 `task_type` 加载 |
| **Tool** | 实际执行能力：搜索、抓取、解析、模型、写库 | 已有 `backend/app/capabilities/` 的 `ToolRegistry` 作为唯一入口 |
| **MCP / API** | 工具与外部世界的连接方式（本地 MCP server 优先，HTTP API 次之） | `backend/app/capabilities/providers/`、`search/`、`fetch.py` |
| **Tool Registry** | 统一登记、权限检查、调用审计 | **已完成骨架**：`tools.py`（登记 + `dispatch`）、`guard.py`（鉴权）、`budget.py`（成本）、`tool_calls`（账本，待 2.2 起真实写入） |

关键约束建议（写进下一版工具文档）：
1. 第三方 Skill/Tool **只能只读引入并锁版本**（记录 commit hash + license），不得自动执行安装脚本；
2. 每个外来 Tool 必须先通过本地离线测试（`FakeProvider`）才能进白名单；
3. Skill 不得直接调用模型或网络，只能通过 `ToolRegistry`；
4. 多 Agent 共享同一套 Tool 定义，禁止每个 Agent 自带一份工具实现。

### 10.2 内容风格 Style Profiles（未来内容生产）

方向已定：参考"小Lin说"的通俗表达与可视化、参考"巫师财经"的深度研究与故事结构，合成 AK 自有风格
（观点鲜明、犀利、有趣，同时保证研究深度与事实准确）。建议在 Content Hub 建 `style_profiles` 概念
（可在 2.9 与 `scripts`/`content_items` 同批落地）：

| 字段（建议） | 内容 |
|---|---|
| `profile_id` / `name` | 如 `ak_default`、`ak_deep_dive`、`ak_short_news` |
| `narrative_structure` | 开场钩子 → 冲突/悬念 → 机制拆解 → 反方与失效条件 → 结论（JSON 数组） |
| `analogy_policy_ref` | 指向 `config/brand.yaml` 的 `analogy_policy`（不得放宽） |
| `visual_grammar` | 图表类型偏好、配色、字幕节奏、镜头时长上限 |
| `pacing` | 目标语速（3.5–4.5 字/秒）、每段句数上限、hook 秒数 |
| `compliance_hooks` | 免责声明位置、必须出现的"哪里不成立"提示 |
| `shared_by` | `scriptwriter` / `visual_designer` / `video_producer` 共享同一份 |

**边界**：Style Profile 只影响"怎么讲"，不得影响"讲什么"与"证据要求"；`fact_checker` 的判定标准
不受任何 Style Profile 影响。

### 10.3 2.1 开工前的三个前置（建议写成 M2.1 任务书的一部分）

1. **填价目 + 确认预算**（否则 `pricing_missing` 拦住一切）；
2. **WAL 何时开关**：R1 已消除（不再自动切换），但**是否**要让开发库进入 WAL 仍是人的决定——先 `sqlite3 .backup` 到 `data/backups/`，再用 `python -m backend.scripts.enable_wal --dry-run` 看清目标；
3. **决定 `topics.status` 与 `content_status` 的权威关系**（R8），否则 2.7 选题工作台会双写不一致。

---

## 11. 专项整改（M2.0 最终验收前）

> 触发原因：初版交付摘要显示"下一次连接可能把真实开发库从 `journal_mode=delete` 持久切换为 WAL"，
> 不符合受控初始化要求。本轮按要求整改 WAL、复核 CallerContext 安全边界、复核两条测试告警，
> 并做完整回归。**本轮不是 M2.1，没有开发任何新业务功能。**

### 11.1 WAL 设置方式整改

**整改前的问题**：`backend/app/database/session.py` 在 `event.listen(engine, "connect", ...)` 里
执行四条 PRAGMA，其中 `PRAGMA journal_mode=WAL` 对 SQLite 库文件是**持久**副作用——
任何一次连接（含启动服务、查系统状态、跑 alembic）都会把库改成 WAL 并产生 `-wal`/`-shm`。
"连接"和"改文件模式"是两件事，混在一条事件里等于**隐式副作用**。

**整改后的实现**（三层分工，只有第二层会改文件）：

| 层 | 位置 | 内容 | 是否会改库文件 |
|---|---|---|---|
| 每连接 | `backend/app/database/session.py` 的 `_SQLITE_PRAGMA_STATEMENTS` | 只执行 `PRAGMA foreign_keys=ON`、`PRAGMA busy_timeout=5000`、`PRAGMA synchronous=NORMAL`（均为**连接级**参数） | **否** |
| 显式初始化 | `backend/app/database/wal.py` 的 `enable_wal(database_url, *, allow_development_db=False, dry_run=False)` | 单独开一条裸连接，读 `before` → `PRAGMA journal_mode=WAL` → 读 `after`，不一致抛 `switch_failed` | **是**（唯一入口） |
| CLI | `backend/scripts/enable_wal.py` | `--database-url` / `--dry-run` / `--allow-development-db`，输出 JSON；受保护库退出码 2、其他错误 1 | 同上 |

**目标识别与防误伤**（对应任务书"必须有明确的数据库目标、防止误作用于生产或其他真实数据库"）：

- `is_protected_database_url(url)`：URL 归一（容忍反斜杠）后以 `data/finance_media.db` 结尾即判为受保护，`enable_wal` 直接抛 `WalInitializationError(reason="protected_database")`；`--dry-run` 也**不连接**。
- `resolve_sqlite_target(url) -> Path`：`make_url` 解析后返回**绝对路径**（相对路径按 `PROJECT_ROOT` 解析），调用方能在日志里看到"到底动的是哪个文件"；非 SQLite 抛 `not_sqlite`、`:memory:` 或空抛 `in_memory`、文件不存在抛 `missing_database`（**不自动新建**）、不是文件抛 `not_a_file`。
- 幂等：第二次调用返回 `already_enabled=True, changed=False`；库已在 WAL 时不做任何写操作。

**回归测试**：`tests/test_wal_initialization.py`（13 例）逐条证明任务书要求的五件事——
① 连接事件里**不得**出现 `journal_mode`（结构断言，防止有人把它加回去）；
② 普通连接不改 `journal_mode`、不产生 `-wal`/`-shm`；
③ 应用启动（`create_app()` + lifespan）不改库文件模式；
④ `/api/system/status` 查询不改库文件模式；
⑤ 显式初始化能把临时库切到 `wal` 且重复调用幂等；另含 dry-run、拒绝开发库、拒绝缺失/内存/非 SQLite 库。
`tests/test_system_status.py` 的断言同步由 `wal` 改为 `delete`——**测试库从未做过 WAL 初始化，
读到 wal 就说明"查一次状态把库改了"**，这是比原断言更强的回归防线（不是把预期改成想要的结果）。

**本轮对真实开发库零操作**：整改全程没有对 `data/finance_media.db` 执行过 WAL 初始化，
也没有任何进程连过它（§11.5 用 `mode=ro` 只读核验）。

### 11.2 CallerContext 安全复核

复核范围：`backend/app/core/caller.py`、`backend/app/capabilities/guard.py`、`backend/app/api/deps.py`、
`backend/app/api/router.py`、五个路由模块、`tests/test_caller_permissions.py`。

**逐条验证任务书要求的七点**：

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | 外部请求不能通过 `X-Caller: agent` 冒充 Agent | ✅ | `build_http_caller()` 只看**路径 + `app_env`**：非 `/api/_dev/` 一律 `human`，`X-Caller` 仅作审计字段；非 human 值记 warning 并置 `forged_header=True`。端到端探针：带 `X-Caller: agent` 打 agent-only 端点 → **422 `domain_validation_error` + `details.reason=permission_denied` + `details.caller=human`** |
| 2 | 外部请求不能通过 `X-Caller: dev` 获取开发权限 | ✅ | 同上；`dev` 只由"请求路径以 `/api/_dev/` 开头 **且** `app_env != production`"产生，头不参与判定 |
| 3 | Agent 身份由可信的服务端内部逻辑创建 | ✅ | `CallerContext.for_executor(...)` 是唯一构造途径，强制 `source="executor"` + `task_id` + `attempt_token`；`__post_init__` 拒绝任何缺字段或非 executor 来源的 agent 上下文；HTTP 层没有任何路径能产出 `kind=agent` |
| 4 | dev 权限只在开发环境有效 | ✅ | 依赖工厂先调 `settings_for_request(request)`；生产环境下 dev 端点直接 `NotFoundError` |
| 5 | 生产环境开发接口返回 404 | ✅ | `test_dev_only_endpoint_returns_404_in_production`（裸 FastAPI + `TestClient`，`app_env=production` → **404 `not_found`**，不暴露端点存在）；非生产同端点 200 |
| 6 | 21 个 API 操作都有明确的调用者权限声明 | ✅ | `ROUTE_CALLERS` 21 条；启动自检 `_check_route_callers` 实测输出：**「21 个 API 操作均已挂载并声明调用者（受保护 20 个，公开 1 个，另 1 个豁免端点）」** |
| 7 | 越权请求有正确的拒绝结果 | ✅ | 403 不用（语义不合）→ 统一 **422 + `domain_validation_error`**，`details` 带 `reason=permission_denied`、`required`、`caller`；`PermissionDeniedError` 仍是 `DomainValidationError` 子类，错误信封与其他校验错误一致 |

**复核中发现并修复的两个真问题**（都是改实现，没有改测试预期、没有跳过测试）：

1. **启动自检只核对清单、不核对挂载**（最危险的漏洞）：原 `_check_route_callers` 只把
   `app.openapi()` 的路径集合与 `ROUTE_CALLERS` 做集合比较。若某个端点**忘了**挂
   `Depends(require_caller(...))`，清单仍然"对得上"，自检照样全绿——即"清单说有门禁，墙上其实没有门"。
   修法：新增 `iter_api_routes(app)` 递归穿透 Starlette 1.10 的 `_IncludedRouter`
   （真路由在 `original_router.routes`，前缀需自己累加），从 `route.dependant` 递归收集
   `require_caller` 依赖上挂的 `allowed_kinds`，然后**四查**：
   ① 每个真实路由必须在清单里；② 挂载的允许集合与清单**完全一致**（漏挂/多挂/口径不一致都报错）；
   ③ 清单标公开却挂了 `require_caller` 也报错；④ 清单里有不存在的端点报错。
   并保留 `app.openapi()` 与走查结果的双向校验，防止绕过。新增 4 个反向用例专门喂这四类错误。
2. **鉴权读的配置可能是错的**：`require_caller` 原用 `Depends(get_settings)`（进程级 `lru_cache` 单例），
   而真正生效的是 `app.state.settings`（`create_app(settings)` 可注入）。两者不一致时，
   "生产环境隐藏 dev 端点"会按**错的环境**判断。修法：新增 `settings_for_request(request)`
   （优先 `request.app.state.settings`，缺失才回退 `get_settings()`），依赖签名改为 `dependency(request: Request)`。

另记一处口径修正：根路径 `GET /`（身份横幅，`include_in_schema=False`、不碰数据库）不属 21 个 API 操作，
用 `NON_API_ROUTES` **显式登记豁免**，而不是靠"`openapi()` 里看不见"混过去——豁免也必须是白纸黑字。

### 11.3 测试告警复核

| 告警 | 位置与根因 | 是否影响运行时 | 判定 |
|---|---|---|---|
| `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` | `starlette/testclient.py`：首选 `import httpx2`，缺失时回退 `import httpx` 并 `warnings.warn`；本项目**从不直接 import httpx**（全仓 grep 为空） | 不影响运行时（只是测试客户端依赖） | **已真修**：安装 `httpx2 2.16.0`（附 `httpcore2`、`truststore`），`requirements.txt` 测试依赖换成 `httpx2>=2.0,<3.0`；告警消失，测试全绿 |
| `SAWarning: Cannot correctly sort tables ... cycles between tables "agent_runs, research_subtasks"` | 出现在 `backend/migrations/env.py:77`（`context.run_migrations()` 期间的依赖图排序），根源在 `.venv/Lib/site-packages/sqlalchemy/sql/ddl.py`：只有 `use_alter=True` 的 FK 才会被移出依赖图，否则环无法拓扑排序 | 不影响建表/迁移/运行；`PRAGMA foreign_key_check` 与 `integrity_check` 均正常 | **明确记录为非阻断风险，不修**（§9 R2）。若改 `use_alter=True`，SQLite 不支持 `ALTER TABLE ADD CONSTRAINT`，`create_all` 建表会**静默丢失该外键**，比警告更糟 |

**为"不修"补的实测试（不是口头结论）**：`tests/test_fk_cycle_semantics.py`（5 例）——
两侧外键都存在；删子任务 → `agent_runs.subtask_id` 变 `NULL`；删 run → `research_subtasks.agent_run_id`
变 `NULL`；删后 `PRAGMA foreign_key_check` 为空、`integrity_check=ok`；`ResearchSubtask.agent_run`
关系映射无歧义。
**踩坑记录**：断言必须用**原始 SQL** 读取，因为会话 `expire_on_commit=False`，ORM 身份映射会返回
事务里的旧值，看起来像"SET NULL 没生效"（假阴性）；另外 `agent_runs.task_id` 有唯一约束，
测试数据要带 uuid 后缀避免串行冲突。

### 11.4 完整回归结果

| 项目 | 命令 / 脚本 | 结果 |
|---|---|---|
| 全量测试 | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts="" -q` | **253 passed, 1 warning in 40.50s**（0 failed） |
| B1/B2/B3 临时库演练 | `%TEMP%\fmai_rehearse.py` | 14 项全过：29 表/28 业务表/`revision=b3_metering`；新表与新列齐全；两个部分唯一索引均带 `WHERE`；`foreign_key_check=[]`；`integrity=ok`；`index_count=170`；回填幂等；downgrade 后只剩 `alembic_version`；重新 upgrade 回到 29/28；`alembic check` clean；临时库已清理。**退出码 0** |
| ORM ↔ 迁移 Schema Parity | `%TEMP%\fmai_parity_check.py` | **`PARITY: PASS`（28 张业务表逐表比对表集合/列名/可空性/索引名/唯一约束名）** + `alembic check: no drift`，退出码 0 |
| 真实开发库只读核验 | `%TEMP%\fmai_integration_verify.py`（`file:...?mode=ro`） | 见 §11.5 |

> 退出码提示：本轮两次遇到 `[exit code: 1]` 其实是 **PowerShell 管道**（`| Select-Object`）造成的假象；
> 把脚本输出单独重定向后 `$LASTEXITCODE=0`。判断成败要看脚本自己的 `VERDICT`/`PARITY` 行，不能只看外壳退出码。

### 11.5 真实开发数据库未发生变化的验证证据

```
path                 = C:\Users\25803\finance-media-ai\data\finance_media.db
size_bytes           = 540672
sha256               = 2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203
sha256_matches_baseline = true        # 与本轮开始前基线完全一致
mtime_utc            = 2026-11-09T09:54:30.757697+00:00     # 未被任何写操作触碰
sidecars             = {"-wal": false, "-shm": false}      # 没有产生 WAL/SHM 附属文件
journal_mode         = delete                              # 仍是 delete，未被切成 WAL
table_count          = 19                                  # 仍为 19 张表
revision             = de8148733a9b                        # 迁移版本未前进
new_tables_present   = []                                  # 没有任何 B1/B2/B3 新表
foreign_key_check    = []
integrity_check      = ok
non_empty_tables     = {"alembic_version": 1}               # 业务表全空
VERDICT: PASS
```

（读数说明：同一次只读连接里 `PRAGMA busy_timeout` 返回 `5000`，那是 Python `sqlite3.connect`
客户端默认值，**不是库文件属性**，不能当作"程序改过库"的证据；库文件层面的判据是
`journal_mode`、`sha256`、`-wal`/`-shm` 是否存在。）

### 11.6 本轮整改的文件清单

**新增（4 个）**：

| 文件 | 行数 | 作用 |
|---|---|---|
| `backend/app/database/wal.py` | 175 | WAL 显式初始化入口、目标库识别与防误伤 |
| `backend/scripts/enable_wal.py` | 81 | CLI：`--database-url` / `--dry-run` / `--allow-development-db` |
| `tests/test_wal_initialization.py` | 228 | 13 例：只有显式入口能切 WAL，其余路径一律不动库 |
| `tests/test_fk_cycle_semantics.py` | 146 | 5 例：外键环两侧 `SET NULL` 实测与完整性 |

**修改（关键 9 个；全部 43 个已跟踪改动见 §8）**：

| 文件 | 改了什么 |
|---|---|
| `backend/app/database/session.py` | 连接事件四条 PRAGMA → 三条（移出 `journal_mode`）；docstring 说明 WAL 改由 `wal.enable_wal` 显式调用 |
| `backend/app/database/__init__.py` | 追加导出 `WAL_JOURNAL_MODE`/`DEFAULT_JOURNAL_MODE`/`WalResult`/`WalInitializationError`/`enable_wal`/`is_protected_database_url`/`resolve_sqlite_target` |
| `backend/app/core/caller.py` | 新增 `settings_for_request()`、依赖签名改 `dependency(request)`、依赖上挂 `allowed_kinds`、新增 `allowed_kinds_of()` |
| `backend/app/core/startup_checks.py` | 新增 `RouteInfo` + `iter_api_routes()`；`_check_route_callers` 由"清单比对"改为"挂载比对 + 四查" |
| `backend/app/api/router.py` | 新增 `NON_API_ROUTES`（显式登记 `GET /` 豁免） |
| `tests/test_caller_permissions.py` | 20 → 27 例：端到端越权探针 3 个、自检反向 4 个 |
| `tests/test_system_status.py` | PRAGMA 断言 `wal` → `delete`（更强防线） |
| `requirements.txt` | 测试依赖 `httpx` → `httpx2` |
| `README.md` / `docs/research_hub/{system_architecture,acceptance_criteria}.md` | 同步 WAL 口径（显式初始化）、连接参数三条、权限声明与挂载逐条一致 |

### 11.7 整改后仍需人工确认的三件事

1. **是否**让开发库进入 WAL（能力已就绪、默认拒绝，但"开不开"是人的决定，且持久）。
2. 价目表仍为 `null`（Q8=B 预期行为），2.1 开工前必须填真价并确认预算上限。
3. `topics.status` 与 `topics.content_status` 的权威关系未定（R8），2.7 之前必须定。

---

## 附录 A. 复现命令

```powershell
cd C:\Users\25803\finance-media-ai

# 全量测试（253 passed, 1 warning）
# 注意：pyproject 的 addopts 已含 -q，再加 -q 会变 -qq 并抑制统计行，故用 -o addopts="" 覆盖
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts="" -q

# 迁移 head 与漂移检查（只读）
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check

# 临时库演练（不碰开发库）
$env:PYTHONPATH='.'; .\.venv\Scripts\python.exe $env:TEMP\fmai_rehearse.py

# ORM ↔ 迁移 Schema Parity（临时库，结束后自删）
$env:PYTHONPATH='.'; .\.venv\Scripts\python.exe $env:TEMP\fmai_parity_check.py

# 开发库只读取证（mode=ro，绝不写）
$env:PYTHONPATH='.'; .\.venv\Scripts\python.exe $env:TEMP\fmai_integration_verify.py

# WAL 显式初始化的正确用法（先看清目标，再决定是否执行）
.\.venv\Scripts\python.exe -m backend.scripts.enable_wal --database-url sqlite:///./data/finance_media.db --dry-run

# Git 现状
git status --porcelain; git diff --stat; git rev-parse HEAD
```

## 附录 B. 与实施计划的偏差（3 项，均已获人工授权或属计划内裁决）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| 1 | A6：对开发库执行 B1/B2/B3 | **未执行**，只在临时库演练 | 人工任务书撤销 A6（"禁止对 `data/finance_media.db` 执行任何迁移"） |
| 2 | 新增 `capabilities/db_read.py`、`db_write.py` | **未创建** | 这两个文件是"受控读写的真实实现"，属 2.2/2.5；2.0 只有登记与鉴权骨架。已在 `system_architecture.md` §6 与交付报告写明 |
| 3 | 工具名 `db_read` | 统一为 `db_query` | 与 `TOOL_NAMES`/`agent_routing.yaml` 对齐，文档 8 处已同步 |

## 附录 C. 本轮"没有做但容易被误以为做了"的清单

- 没有接入任何模型供应商（`models.yaml` 两个条目 `enabled: false`、价目 `null`）。
- 没有联网（`integrations.web_search.enabled=false`，无 HTTP 客户端依赖；注意 `httpx2` 只是测试客户端的依赖，业务代码不使用）。
- 没有执行层：`backend/app/execution/` 只有注册表与路由校验，**没有** `run_task()`、队列、worker。
- 没有解析器：`backend/app/parsers/__init__.py` 只有 Protocol。
- 没有真实工具：`TOOL_SPECS` 十项 `implemented=False`。
- 没有把六大合集写进开发库（只在临时库验证了 seed 幂等）。
- **没有把开发库切成 WAL**：WAL 只能由 `backend/app/database/wal.py` 显式开启，整改轮全程未对开发库调用过（§11.5 的只读核验可证）。
- 没有提交、没有推送。
