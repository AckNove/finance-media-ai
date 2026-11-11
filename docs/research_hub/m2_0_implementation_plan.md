# M2.0 工程实施前审查与开发计划（AK Research Hub）

| 项 | 值 |
|---|---|
| 文档状态 | **待人工批准**（本文件不含任何已实施能力） |
| 编写依据 | M2.0 工程实施前审查任务书（2026-11-10） |
| 代码基线 | `main` @ `7ec12836cbb29dd88b9226454001247fa8b3b321`（= `origin/main`，ahead/behind `0 0`） |
| 设计基线 | `docs/research_hub/` 17 份文档（M1.2-final 已定稿） |
| 本轮实际执行的动作 | 只读命令 + 读文件 + 写这份计划；**未改代码、未建迁移、未跑迁移、未联网、未提交、未推送** |

**证据分级（全文通用）**

| 标记 | 含义 |
|---|---|
| `[V]` | 本机命令/测试**实测**得到（可复现，附录 A 有命令） |
| `[d]` | 从设计文档**读到**的约定（文档即当前设计真相，但未实现） |
| `[u]` | 本次**未验证**、需实施阶段实测确认的推断 |

---

## 0. 一页纸结论

1. **M2.0 的输入是健康的**：代码基线干净、单 head 迁移 `de8148733a9b`、19 张表除 `alembic_version`(1 行) 外全空、96 个测试全过 `[V]`。数据库**没有历史数据包袱**，B1/B2/B3 的回填逻辑可以写全并在测试库里造数据验证 `[V]`。
2. **M2.0 的产出不是新功能，而是让别人以后能安全写功能的地基**：三份配置、一套 CallerContext 权限、SQLite 并发参数、三批迁移、task_type 注册表、记账骨架、启动自检。**不新增任何业务端点** `[d]`（acceptance 2.0）。
3. **当前代码与设计文档的差距集中在 6 处**：配置体系（缺 3 份 YAML + 3 段 settings）、权限（零代码落点）、SQLite 参数（无 WAL/busy_timeout）、执行层与能力层（目录不存在）、19 张新表（一个模型都没有）、启动自检（不存在）。详见 §1.5 的 G-01…G-18。
4. **两处文档内部不一致**必须在实施时按「阶段级文档优先」裁决并回写：`capabilities/` 目录布局（roadmap 2.0 扁平文件 vs system_architecture §6 子包）、`test_system_status.py` 只断言 4 个 feature 键而 acceptance 2.0 要求 7 个。见 §1.5 的 G-16、G-17。
5. **配置段位置需要一个明确裁决**：设计文档写 `settings.cost.daily_cap_cny` `[d]`，但 `Settings` 是 pydantic-settings（读环境变量），而 `settings.yaml` 是独立 YAML 文件 `[V]`。本计划做法：**YAML 是权威，`Settings` 只增可选 env 覆盖字段**（§2.3.3），不把 YAML 塞进 pydantic-settings。
6. **迁移必须备份先行、三批独立往返、双路径结构一致**：新增 `tests/test_schema_parity.py` 比对「`create_all` 建的表」与「迁移建的表」（列/索引/约束集合）。现有 `tests/conftest.py` 用 `create_all` 建测试库 `[V]`，真实库由迁移建——两条路径不一致是只有上线才炸的隐患。
7. **WAL 会引入两个新的未忽略文件模式**：`*.db-wal`、`*.db-shm` 不在 `.gitignore` 里 `[V]`，2.0 必须补，否则每次跑测试/启动都会在 `git status` 里冒出噪音。
8. **权限必须真在代码里**：`CallerContext` + `guard.require()` + 「每个端点必须声明调用者类型（启动校验用 OpenAPI 路径集合对照声明清单）」。当前 21 个端点**零鉴权、零声明** `[V]`。
9. **成本控制在 2.0 只做到结构与纯函数**，不做任何真实调用：B3 建 `model_calls`/`tool_calls`，`budget.py` 提供可单测的 `evaluate()`。**2.1 之前 `model_calls` 表是空的，这不是缺陷，而是刻意的顺序**（先有账本再花钱，M1.1 P0-2）。
10. **本计划需要 8 项人工批准才能开工**（§8 的 A1–A8），其中 A1（改现有 `api/` 文件）、A2（改 `/api/system/status` 响应体）、A6（对开发库执行迁移）是硬阻塞项。

---

## 1. 项目工程现状审查（`[V]` 为主）

### 1.1 Git、Python 与依赖

| 项 | 实测值 |
|---|---|
| 分支 / HEAD / 远程 | `main` / `7ec12836cbb29dd88b9226454001247fa8b3b321` / `origin` = `github.com/AckNove/finance-media-ai.git` |
| 同步状态 | `git rev-list --left-right --count HEAD...origin/main` → `0 0` |
| 工作区 | `git status --short` 仅 `?? docs/research_hub/m1_1_architecture_review.md`（按 M1.1 约定保持未跟踪，**本计划不提交它**） |
| Python | `.venv` = **3.12.10** |
| 关键依赖 | fastapi 0.162.2、starlette 1.10.0、uvicorn 0.54.0、SQLAlchemy 2.0.57、alembic 1.20.1、pydantic 2.14.3、pydantic-settings 2.16.2、PyYAML 6.0.3、pytest 8.4.2、httpx 0.28.1、python-dotenv 1.2.4 |
| 测试基线 | `pytest -p no:warnings` → **96 passed** |

### 1.2 实际目录与文件规模

```
finance-media-ai/
├── agents/            9 个角色目录（各含 PROMPT.md）+ roster.yaml + PROTOCOL.md + README.md
├── backend/
│   ├── app/
│   │   ├── main.py(82)  api/{deps 24, router 19, routes/5 文件}  core/{config 112, errors 102, logging 15, utils 27}
│   │   ├── database/{base 30, session 103}   models/17 文件   schemas/7 文件   services/7 文件
│   └── migrations/{env.py, script.py.mako, versions/1 个 revision}
├── config/            brand / content_rules / content_taxonomy / research_sources / settings（5 份 YAML）
├── content/ knowledge/ data/ docs/ prompts/ tests/ video_engine/
```

- `backend/app/` **没有** `execution/`、`capabilities/`、`parsers/` 三个目录 `[V]`。
- `config/` **没有** `models.yaml`、`agent_routing.yaml`、`pipelines.yaml` `[V]`。
- `tests/` 12 个文件、96 个用例 `[V]`。
- `data/` 只有 `exports/ processed/ raw/` + `finance_media.db`(540,672 B)；**`data/backups/` 不存在** `[V]`。

### 1.3 数据库与迁移

| 项 | 实测值 |
|---|---|
| `alembic heads` / `current` | 均为 `de8148733a9b (head)`，**单 head**，只有 `20261109_1754_initial_schema.py` 一个 revision |
| 表数量 | **19 张**（18 张业务表 + `alembic_version`） |
| 数据量 | 除 `alembic_version` **1 行**外，全部表 **0 行**（含 `topic_columns`=0，六合集尚未灌入） |
| `journal_mode` | **`delete`** —— 未开 WAL |
| `busy_timeout` | 读到 5000，但这是 Python `sqlite3.connect(timeout=5.0)` 的**连接默认值**，**不是** `build_engine()` 设置的 |
| 部分索引 | 开发库 `partial_indexes = []`（无） |
| 外键 | 已开 `PRAGMA foreign_keys=ON`（`event.listen("connect", ...)`）；FK 列全部有前导索引 |

`backend/app/database/session.py` 实测：`build_engine()` 只设 `echo/future/pool_pre_ping` + sqlite `connect_args={"check_same_thread": False}` + 连接钩子执行 `PRAGMA foreign_keys=ON`；**没有 WAL / busy_timeout / synchronous** `[V]`。

### 1.4 现有能力盘点（不许把「结构存在」当「能力存在」）

| 层 | 实际有什么 | 证据 |
|---|---|---|
| API | 21 个操作 / 14 个路径（topics 5、knowledge 7、sources 2、evidence 2、research-reports 3、health/system 2） | `[V]` `/openapi.json` |
| 服务层 | 7 个文件：topic / knowledge / source(+evidence) / research / agent_registry / system / base | `[V]` |
| 权限 | **无**：全仓无 `Security(`/`HTTPBearer`/`OAuth2`/`get_current_user` | `[V]` |
| 模型调用 | **无**：无 `openai`/`anthropic`/`httpx.AsyncClient(` 于业务代码；`integrations.{llm,web_search,vector_store}.enabled` 全 `false` | `[V]` |
| Agent | 9 个角色文件 + 登记表 + `RecordingDispatcher`（只写 `agent_runs(status=pending)`、不写 output） | `[V]` |
| 视频 | 目录 + 4 模板 + 12 项质检 YAML，**无渲染** | `[V]` |

### 1.5 现有代码 ↔ 设计文档差距（M2.0 必须闭合的部分）

| 编号 | 差距 | 设计文档要求 | 现状 | 2.0 是否闭合 |
|---|---|---|---|---|
| G-01 | 三份配置缺失 | `config/models.yaml`、`agent_routing.yaml`、`pipelines.yaml` | 全不存在 `[V]` | 闭合（T02） |
| G-02 | settings 缺三段 | `features` / `execution` / `cost` | 无 `[V]` | 闭合（T02） |
| G-03 | `Settings` 缺字段 | execution/cost/features | 无 `[V]` | 闭合（T02） |
| G-04 | 无 CallerContext | tooling §1.3 的 frozen dataclass + 三层权限 | `api/deps.py` 只有 DbSession/AppSettings/分页 `[V]` | 闭合（T04） |
| G-05 | 无 guard | `capabilities/guard.py` 的 `require()` 由 `ToolRegistry.dispatch()` 统一调用 | 无 `[V]` | 闭合（T04，registry 骨架） |
| G-06 | 无 task_type 注册表 | YAML 权威 + `execution/task_types.py` + 启动校验 + 文档扫描测试 | 无 `[V]` | 闭合（T05） |
| G-07 | 无路由声明覆盖校验 | 每个端点必须显式声明 kind，未声明即配置错误 | 21 端点零声明 `[V]` | 闭合（T04+T09） |
| G-08 | 无 WAL / busy_timeout / synchronous | system_arch §3.4 参数与崩溃扫描 | 无 `[V]` | 参数部分闭合（T03）；崩溃扫描属 2.4 |
| G-09 | 19 张新表零模型 | database_design §4 的 19 张表 | 现有 18 张业务表模型 `[V]` | 建表（T06/T07/T08），**不建功能** |
| G-10 | 多合集/多标签无库支持 | `topic_collections`/`topic_tags`/`tags` | 只有 `topics.column_id` 单选 `[V]` | 建表+回填（T06） |
| G-11 | 无启动自检 | taxonomy↔DB 一致性、注册表一致性 | 无 `[V]` | 闭合（T09） |
| G-12 | `features` 只有 4 键 | acceptance 2.0 要求 `model`/`search`/`execution` 三键 + `database` 段 `journal_mode`/`busy_timeout` | `FeatureFlags{llm_providers, web_search, vector_store, video_rendering}` `[V]` | 闭合（T03） |
| G-13 | 无 `data/backups/` | G11 备份先于迁移 | 目录不存在 `[V]` | 闭合（T01） |
| G-14 | 无迁移批次测试 | 每批 upgrade→downgrade -1→upgrade 往返 | `test_migrations.py` 只测单 head 往返 `[V]` | 闭合（T06/T07/T08 各加用例） |
| G-15 | `.gitignore` 未忽略 WAL 附属文件 | — | 只有 `*.db`、`*.db-journal` `[V]` | 闭合（T03 补 `*.db-wal`/`*.db-shm`） |
| G-16 | **文档内部冲突：`capabilities/` 布局** | system_arch §6：`capabilities/tools/`（子包）+ `capabilities/parsers/{html,pdf,xlsx}.py`；roadmap 2.0：扁平 `capabilities/{guard,tools,db_query,db_write,budget}.py` | 两处写法不同 | 计划内**按 roadmap（阶段级）执行**，并把 system_arch §6 改成同一套（T11 文档修订，需 A7 批准） |
| G-17 | **冲突：`test_system_status.py` 断言 features 恰为 4 键** | acceptance 2.0 要求 7 键 | 现有测试 `assert features == {4 键}` `[V]` | 必然失败，T03 同步更新该测试 |
| G-18 | `pyproject.toml` 打包不覆盖子包 | roadmap 2.0 要加 `research` 可选依赖组 | `packages = ["backend", "backend.app"]` `[V]`，新子包不会被 setuptools 打包 | 建议改 `[tool.setuptools.packages.find]`（A5 批准） |
| G-19 | **文档数字冲突：2.0 结束时的业务表数** | `implementation_roadmap.md:64` 写「B1+B2+B3 完成后 **18 → 26** 张业务表」；而 `database_design.md` §6.4 的演进是 18→**22**(B1)→**26**(B2)→**28**(B3)，B3 再 +2 张（`model_calls`/`tool_calls`） | 两处差 2 张：roadmap 把 B2 的终点当成了 B3 的终点 `[V]` | **以 §6.4 明细为准 = 28**；T11 修 roadmap 该行；本文 §2.1/§6.2/§9.1 统一写 28 |

### 1.6 会阻碍 M2.0 的技术债

| 编号 | 债务 | 影响 | 处理 |
|---|---|---|---|
| T-01 | 测试库用 `Base.metadata.create_all` 建，真实库用迁移建 `[V]` | 两条结构路径可能漂移，只有上线才炸 | 新增 `test_schema_parity.py`（T06） |
| T-02 | `--strict-markers` 已开，但未注册任何 marker `[V]` | 2.1 起要写 `@pytest.mark.integration` 会直接报错 | T02 在 `pyproject.toml` 注册 `integration`/`slow`（A7） |
| T-03 | `filterwarnings = ["error::DeprecationWarning:backend.*"]` `[V]` | 新代码用到被弃用 API 会让测试红（这是好事，但要提前知道） | 计划内（无动作） |
| T-04 | 开发库未开 WAL、无 `-wal/-shm` 忽略规则 | 开了 WAL 后 `git status` 出噪音 | T03 补 `.gitignore` |
| T-05 | `busy_timeout` 现在是「巧合值」（驱动 timeout 的副作用） `[V]` | 换驱动/换参数就丢 | T03 显式 PRAGMA + 测试 |
| T-06 | `system_service` 直接读 YAML dict，无 schema 校验 `[V]` | 配置写错到运行期才炸 | T02 加 `config_schema.py` + T09 启动校验 |
| T-07 | 迁移 `env.py` 未对**部分索引**做特殊处理 `[d]` | Alembic `check` 看不见部分索引差异 | 每批手写 + `PRAGMA index_list` 断言 |
| T-08 | `m1_1_architecture_review.md` 永久未跟踪 `[V]` | `git status` 长期有噪音（M1.1 的约定） | 保持不动，交付时说明 |

---

## 2. M2.0 总体技术方案

### 2.1 目标 / 非目标

**目标（可验收）**

1. 三份新配置落地并被 schema 校验；`settings.yaml` 增 `features`/`execution`/`cost` 三段。
2. SQLite 在**真实库**上 `journal_mode=wal`、`busy_timeout=5000`、`synchronous=NORMAL`、`foreign_keys=ON`。
3. `CallerContext` + `guard.require()` 真实生效；21 个现有端点全部显式声明调用者类型，且启动校验能发现「漏声明」。
4. task_type 注册表三处一致（YAML / `task_types.py` / PROMPT.md），含 `topic.discover`。
5. B1/B2/B3 三批迁移各自可往返，业务表 **18 → 28**（B1 +4→22、B2 +4→26、B3 +2→28），六合集灌入且 `topics.column_id` 回填。
6. 启动自检 4 项，任一失败拒绝启动（可用 env 放宽）。
7. `pytest` 全绿（96 + 新增），`alembic check` 无新操作。
8. **不新增任何业务端点**。

**非目标（明确不做）**

- 不接真实模型、不联网抓取、不写 `web_search`/`fetch_url` 实现（2.2 起）。
- 不实现 `TaskRuntime`/`Orchestrator`/`AgentExecutor`（2.4）。
- 不实现预算的**真实**预留/结算（需要 `ModelGateway`，2.1）。
- 不做 B4/B5/B6（2.2/2.5/2.9）。
- 不改 `backend/app/main.py:27` 的旧品牌文案。
- 不改现有 21 个端点的**业务语义**（只加权限声明）。

### 2.2 分层与新目录（按 roadmap 2.0，扁平）

```
backend/app/
├── core/
│   ├── caller.py          新建  CallerKind / CallerContext / get_caller / require_caller
│   ├── config_schema.py   新建  ModelsConfig / AgentRoutingConfig / PipelinesConfig / CostConfig（extra="forbid"）
│   ├── config.py          修改  +4 个 getter、+env 覆盖字段
│   └── startup_checks.py  新建  四项自检
├── capabilities/
│   ├── __init__.py        新建  Protocol: Tool / ToolResult / SearchProvider / Fetcher / ModelGateway
│   ├── guard.py           新建  ToolSpec / PermissionDenied / require(caller, tool, spec)
│   ├── tools.py           新建  ToolRegistry（注册 + dispatch 骨架，2.0 内不注册真实工具）
│   └── budget.py          新建  纯函数 evaluate()（不写库、不调用）
├── parsers/__init__.py    新建  Parser Protocol（html/pdf/xlsx 实现留 2.3）
├── execution/
│   ├── __init__.py        新建
│   ├── task_types.py      新建  10 个 TaskType + ALL_TASK_TYPES
│   ├── routing.py         新建  load_routing / agents_for / assert_known / validate_registry
│   └── event_seq.py       新建  next_event_seq(db, task_id)
├── database/session.py    修改  WAL / busy_timeout / synchronous
├── api/deps.py            修改  +Caller 依赖
├── api/routes/*.py        修改  每个端点声明 caller kind（5 个文件）
├── services/system_service.py 修改  7 个 feature 键 + journal_mode/busy_timeout
└── main.py                修改  lifespan 调 startup_checks（**不动 :27**）
```

> **裁决**：`capabilities/tools.py` 用**扁平文件 + `ToolRegistry` 类**，不建 `tools/` 子包；`parsers/` 只放 `__init__.py` 的 Protocol。理由：2.0 验收只要求「目录 + Protocol」，过早拆子包会在 2.2/2.3 再返工。system_architecture §6 的写法在 T11 统一（A7）。

### 2.3 配置体系

#### 2.3.1 三份新 YAML 的最小必要内容（不多写）

| 文件 | 2.0 必须有的内容 | 2.0 刻意不写的 |
|---|---|---|
| `config/models.yaml` | `version`、`default_provider: null`、`providers: {}`、`cost: {currency: CNY, pricing_policy: reject_unknown, unknown_price_upper_bound_cny: 0.05, cache_ttl_hours: 24}`、`approved_exceptions: []`、`by_task_type: {}` | 任何真实模型名/单价（禁止虚构） |
| `config/agent_routing.yaml` | `version`、`task_types:`（10 种，每项 `description`/`allowed_agents`/`tools`/`human_gate`）、`agents:`（9 个 key，每项 `tools`/`db_scopes`） | Agent 的 prompt 内容（在 PROMPT.md） |
| `config/pipelines.yaml` | `version`、`execution: {enabled: false, max_concurrency: 1, lease_seconds: 300, heartbeat_seconds: 100, worker_poll_seconds: 2}` | 具体流水线编排（2.4） |

`config/settings.yaml` 新增三段（**预算数字只写在 `cost`，pipelines 不重复，避免双源**）：

```yaml
features:
  research_hub: false        # 2.0 起为 false；2.7 打开后才有业务端点
execution:
  enabled: false             # 2.4 才允许 true
  max_concurrency: 1
  lease_seconds: 300
  heartbeat_seconds: 100
cost:
  daily_cap_cny: 5.0
  default_task_calls: 20
  default_task_cost_cny: 2.0
  unknown_price_upper_bound_cny: 0.05
  pricing_policy: reject_unknown     # 人工 Q8=B
```

#### 2.3.2 校验点

- `config_schema.py` 用 pydantic `BaseModel` + `extra="forbid"`（拼错键 = 启动失败，而不是静默忽略）。
- `tests/test_config_models.py`：三份 YAML 可解析、`pricing_policy == "reject_unknown"`、`approved_exceptions == []`、task_types 恰 10 种且含 `topic.discover`、每个 `allowed_agents` 是 roster 子集、每个 agent 的 `tools` 是工具全集子集、`execution.enabled is False`。
- 现有 `tests/test_config.py` 的 11 个用例**必须继续通过**（它断言 settings/brand/content_rules/research_sources 的既有键，本计划不删任何既有键）。

#### 2.3.3 `settings.cost.daily_cap_cny` 与 `Settings` 的关系（裁决）

`Settings`（pydantic-settings）**只读环境变量与 `.env`**，不读 `settings.yaml` `[V]`。因此：

- YAML 的 `cost`/`execution`/`features` 段由 `get_cost_config()` / `get_execution_config()` / `get_features_config()`（`load_yaml_config` + schema 校验 + `lru_cache`）读取，**这是权威**。
- `Settings` 只增**可选覆盖**字段：`cost_daily_cap_cny: float | None = None`（env `COST_DAILY_CAP_CNY`），非空时覆盖 YAML 值；用途是本地临时限流与 CI。
- 文档口径：`system_architecture.md` §8.2 的 `settings.cost.daily_cap_cny` 改为 `cost config（config/settings.yaml → get_cost_config()，可被 COST_DAILY_CAP_CNY 覆盖）`（T11）。

### 2.4 权限：CallerContext + guard + 声明覆盖

**对象**（与 tooling §1.3 一致）

```python
class CallerKind(StrEnum):
    HUMAN = "human"; AGENT = "agent"; DEV = "dev"

@dataclass(frozen=True)
class CallerContext:
    kind: CallerKind
    name: str
    task_id: str | None = None
    subtask_id: str | None = None
    attempt_token: str | None = None
    db_scopes: frozenset[str] = frozenset()
    dry_run: bool = False
```

**识别规则（2.0 版，无鉴权）**

- `X-Caller: human|agent|dev` 头 → 对应 kind；**缺头 = human**。
- `kind=agent` 时**必须**带 `X-Task-Id` 与 `X-Attempt-Token`，否则 422 `permission_denied`（`details.reason="permission_denied"`、`details.required="attempt_token"`）。**这不等于「伪造不了」**：真执行层的 agent 走进程内构造，HTTP 侧带这两个头的请求在 2.0 只被审计标记，不当作可信 agent（文档已写 `X-Caller` 是审计标签不是凭据 `[d]`）。
- `kind=dev` 且 `app_env == "production"` → **404**（不是 403）。

**强制点**

```python
# api/deps.py
def get_caller(x_caller: str | None = Header(default=None), ...) -> CallerContext: ...
Caller = Annotated[CallerContext, Depends(get_caller)]

# 每个端点显式声明
@router.post("/topics", dependencies=[Depends(require_caller(CallerKind.HUMAN, CallerKind.AGENT))])
```

- `require_caller(*kinds)` 不匹配 → `DomainValidationError`，`details.reason="permission_denied"`、`details.required=[...]`（与 api_design §2.3 一致）。
- 工具层：`guard.require(caller, tool, spec)` 校验 `tool ∈ spec.allowed_tools` 且写表 ⊆ `caller.db_scopes`；违规抛 `PermissionDenied` → 转 422。2.0 内**没有真实工具**，但 `ToolRegistry.dispatch()` 的校验路径必须存在并有单测。

**声明覆盖校验（G-07）**：`api/router.py` 维护 `ROUTE_CALLERS: dict[tuple[str, str], frozenset[CallerKind]]`（path, method → kinds）；`startup_checks` 用 `app.openapi()` 的 paths 对照该清单，**集合不相等 → 拒绝启动**。不遍历 `app.routes`（Starlette 1.10 的 `_IncludedRouter` 没有 `.path`，这是已知教训）。

### 2.5 SQLite 并发参数（P1-6 的前置）

```python
# database/session.py（改动集中在 build_engine 与连接钩子）
connect_args = {"check_same_thread": False, "timeout": 30}
_SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)
```

- 只在 `sqlite` 方言下设置；连接钩子逐条 `execute`，任一条失败 → 记录并抛出（**不静默降级**）。
- `/api/system/status` 的 `database` 段新增 `journal_mode`、`busy_timeout`（只读探测，失败给 `None`，不让 status 500）。
- `.gitignore` 补 `*.db-wal`、`*.db-shm`（`*.db` 不匹配这两个）。
- 测试断言必须连**真实临时文件库**读 PRAGMA（内存库恒为 `memory`，测不出 WAL）。

### 2.6 记账骨架（B3）+ 预算纯函数

- `model_calls` / `tool_calls` 两表按 database_design §4.15/§4.16 建（含 `reserved_cost`/`pricing_unknown`/`billable`/`attempt_token`/`lease_until`/`settled_at`，`status` CHECK 含 `reserved,running,succeeded,failed,timeout,unknown,budget_blocked,released`）。
- `capabilities/budget.py` 只提供**纯函数**：

```python
@dataclass(frozen=True)
class BudgetRequest:
    est_tokens: int; est_cost: Decimal
    max_tokens: int | None; max_cost: Decimal | None

@dataclass(frozen=True)
class BudgetState:
    spent_cost: Decimal; reserved_cost: Decimal
    spent_calls: int; reserved_calls: int

def evaluate(req, state, *, cap_cost, cap_calls, pricing_status) -> BudgetDecision
```

  判定式写死为 `spent + reserved + est ≤ cap`（与 system_arch §8.2 一致）；`pricing_status == "missing"` 且无有效例外 → `blocked("pricing_unknown")`。**2.0 不写库**，所以没有「预留行」——预留必须与真实调用原子发生（2.1）。

### 2.7 迁移策略（B1/B2/B3）

**统一动作（每批都执行，缺一步该批不算完成）**

```powershell
# 0. 前置：备份目录
New-Item -ItemType Directory -Force -Path data\backups | Out-Null
# 1. 备份（用 sqlite3 的 .backup，不是复制文件——避免 WAL 未落盘导致的不一致）
& "D:\DeliveryOptimization\miniconda\Library\bin\sqlite3.exe" data\finance_media.db ".backup 'data/backups/pre-b1-<ts>.db'"
# 2. 升级
.venv\Scripts\python.exe -m alembic upgrade head
# 3. 结构校验
.venv\Scripts\python.exe -m pytest tests/test_schema_parity.py -q
# 4. 模型↔迁移一致
.venv\Scripts\python.exe -m alembic check
# 5. 往返（临时库，不碰开发库）
.venv\Scripts\python.exe -m pytest tests/test_migration_batches.py -q
# 6. 全量回归
.venv\Scripts\python.exe -m pytest -q
```

- **表数演进**：18 →（B1）22 →（B2）26 →（B3）**28**（B1 +4、B2 +4、B3 +2）。这是按 database_design §3/§4 统计的推算 `[u]`，**实施时必须用真实 `PRAGMA` 计数替换**；若文档数字与实测不符，**以实测为准改文档**（T11），不得反过来改表结构去凑数字。
- **回填**（B1 内、幂等）：

```sql
-- topics.column_id → topic_collections(is_primary=1)
INSERT INTO topic_collections (topic_id, column_id, is_primary, display_order, created_at, updated_at)
SELECT t.id, t.column_id, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM topics t
WHERE t.column_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM topic_collections c WHERE c.topic_id = t.id AND c.column_id = t.column_id);

-- topics.status 回填（开发库 0 行 → 空操作，但必须是可执行 SQL）
UPDATE topics SET status = CASE content_status
    WHEN 'published' THEN 'content_produced'
    WHEN 'archived'  THEN 'archived'
    WHEN 'approved'  THEN 'approved'
    ELSE 'candidate' END
WHERE status IS NULL;
```

  `TopicStatus` 枚举里**没有** `delivered`，只能用 `content_produced`/`archived`；`state_machine.md` §6.3 的旧写法在 T11 修正。
- **seed 六合集**：`backend/scripts/seed_collections.py` 读 `config/content_taxonomy.yaml` 的 6 个 id，按 `code` upsert 到 `topic_columns`（`is_active=1, is_system=1, taxonomy_version=1`），**幂等可重跑**；迁移只建表加列，**不 seed**（数据写入放迁移外，便于重跑与审计）。
- **独立测试库**：`tests/test_migration_batches.py` 每批用 `tmp_path` 建库，沿用现有 `test_migrations.py` 的注入写法（`Config(alembic.ini)` + `cfg.cmd_opts = SimpleNamespace(x=[f"db_url={url}"])` `[V]`），断言表集合、`PRAGMA index_list` 里 `uq_topic_primary_collection` 的 `partial=1`、`PRAGMA foreign_key_check` 为空、往返 exit 0。
- **回滚与失败恢复**（三层）：①`alembic downgrade -1`（必须与 upgrade 对称：删列/删表/删索引）②从 `data/backups/pre-bN-*.db` 恢复（停服务 → 覆盖 `data/finance_media.db` → 删 `-wal/-shm` → 重启）③`git revert` 迁移文件后重跑。**备份不存在 → 禁止 upgrade**（G11）。

### 2.8 测试策略

| 层 | 用例文件（新建） | 关键断言 |
|---|---|---|
| 配置 | `tests/test_config_models.py` | 三 YAML schema、pricing_policy、10 种 task_type、tools 白名单合法 |
| SQLite | `tests/test_sqlite_pragmas.py` | 真实临时文件库：`journal_mode=wal`、`busy_timeout=5000`、`synchronous=1`、`foreign_keys=1` |
| 权限 | `tests/test_caller_context.py` | 缺头=human；agent 缺 token→422 `permission_denied`；dev 在 production→404；`guard.require` 违规→`PermissionDenied` |
| 注册表 | `tests/test_task_type_registry.py` | 三处一致 + 文档字面量扫描（`module_design.md`/`api_design.md`） |
| 迁移 | `tests/test_migration_batches.py` | B1/B2/B3 各自往返 + 表数 + 部分索引 + FK 检查 + 回填 |
| 结构一致性 | `tests/test_schema_parity.py` | `create_all` 与迁移建库的 表/列/索引/约束 集合相等 |
| 启动 | `tests/test_startup_checks.py` | 四项检查各有一个失败用例（monkeypatch 造坏配置） |
| 预算 | `tests/test_budget.py` | 恰好等于上限、reserved 叠加、未知价拒绝、cap 为 None |
| 日志/审计 | `tests/test_logging_audit.py` | 请求带 `X-Request-Id` 时日志含之；无则生成 |
| 现有 96 个 | 全部保留 | 仅 `test_system_status.py` 的 features 断言按 7 键更新（G-17） |

`pyproject.toml` 注册 marker（A7）：`integration: 需要真实外部服务`、`slow: 慢用例`。

### 2.9 日志 / 异常 / 审计（2.0 的边界）

- **日志**：`core/logging.py` 增 `ContextVar` 的 `request_id`/`caller_kind`/`caller_name`，由中间件设置并写响应头 `X-Request-Id`（A3 批准）。**不打印密钥与请求体**。
- **异常**：全部走既有 `{"error":{code,message,details}}`（G6 已满足 `[V]`）；2.0 只新增三个 `details.reason` 取值：`permission_denied`、`unknown_task_type`、`pricing_unknown`，**不新增 HTTP 码**。
- **审计**：2.0 **不落库**。`tool_calls` 表虽已建，但 2.0 没有真实工具调用，写空行等于造假数据；审计只到结构化日志层，等 2.1/2.4 有真实调用再落 `model_calls`/`tool_calls`。这条边界要写进 `current_state_audit.md`。

### 2.10 与 M1.2 已定决策的对应（不许擅自改）

| 决策 | 2.0 如何遵守 |
|---|---|
| D2（Q6）`topic_columns` 是合集唯一主数据 | B1 只加 3 列，**不建 `collections` 表**；seed 走 upsert |
| D5 禁止重要数据物理级联删除 | 新建表 FK 默认 `RESTRICT`/`SET NULL`；CASCADE 只给纯连接表（`topic_collections`/`topic_tags`/`research_subtask_dependencies`） |
| D6/D7（Q8=B）先预留后记账、六批次迁移 | B3 建记账表在 2.0；`pricing_policy: reject_unknown` 进配置与 `budget.evaluate` |
| D8 task_type 单一注册表 | T05 三处同步 + 启动校验 + 文档扫描测试 |
| D10 `topic_columns` 停用机制 | B1 加 `is_active/is_system/taxonomy_version` + 启动校验 |
| D12 SQLite 参数是架构约束 | T03 显式 PRAGMA |
| Q5 保留 `review_records` 弱多态 | 2.0 不动该表 |
| Q9 `findings` 快照保留为派生 | 2.0 不动 |

---

## 3. 任务拆解（T01–T12）

每项按任务书要求列 10 个要素。行数是实现量级参考，不是承诺。

### T01 基线冻结与备份目录

| 项 | 内容 |
|---|---|
| 1. 目标 | 钉死开工前状态、建备份目录，确保任何失败都能回到 `7ec1283` |
| 2. 文件 | 新建 `data/backups/.gitkeep`；确认 `.gitignore` 的 `*.db` 覆盖 `data/backups/*.db` |
| 3. 输入/输出 | 输入：当前工作区；输出：`data/backups/` 存在 + 一份 `pre-b1-<ts>.db` |
| 4. 调用关系 | 无（纯运维） |
| 5. 技术/依赖 | `sqlite3.exe`（`D:\DeliveryOptimization\miniconda\Library\bin\sqlite3.exe`）、PowerShell |
| 6. 步骤 | ①`git status --short` 确认只有 `?? m1_1_architecture_review.md` ②记录 `git rev-parse HEAD` ③建目录 ④`.backup` 出 `pre-b1-<ts>.db` ⑤`.tables` 数表 = 19 |
| 7. 测试/验收 | `Test-Path data/backups/.gitkeep`；备份 > 0 字节；`.tables` 19 行 |
| 8. 失败处理 | 备份失败（磁盘/占用）→ **停止**，不进 T06；备份被 git 跟踪 → 查 `.gitignore` |
| 9. 库结构/数据 | 无（只读备份） |
| 10. 外部/费用 | 无 |

### T02 配置层扩展（三 YAML + settings 三段 + schema + Settings 字段）

| 项 | 内容 |
|---|---|
| 1. 目标 | 让「配置即真相」成立：缺段/拼错键在启动时炸，而不是运行期 |
| 2. 文件 | 新建 `config/models.yaml`、`config/agent_routing.yaml`、`config/pipelines.yaml`、`backend/app/core/config_schema.py`、`tests/test_config_models.py`；修改 `config/settings.yaml`、`backend/app/core/config.py`、`pyproject.toml` |
| 3. 输入/输出 | 输入：YAML 文本；输出：`get_models_config()/get_agent_routing()/get_pipelines_config()/get_cost_config()/get_execution_config()` 返回强类型对象 |
| 4. 调用关系 | `core/config.py` → `config_schema.py`（校验）；消费者：`execution/routing.py`(T05)、`capabilities/budget.py`(T04)、`startup_checks.py`(T09) |
| 5. 技术/依赖 | pydantic 2.x、PyYAML（均已在）；**不新增依赖** |
| 6. 步骤 | ①写三 YAML（内容见 §2.3.1）②写 `config_schema.py`（`extra="forbid"`）③`config.py` 加 getter（`lru_cache`）+ `clear_settings_cache()` 同时清 YAML 缓存 ④`settings.yaml` 加三段 ⑤注册 markers ⑥写测试 |
| 7. 测试/验收 | `pytest tests/test_config_models.py tests/test_config.py -q` 全过；`python -c "from backend.app.core.config import get_cost_config; print(get_cost_config().pricing_policy)"` → `reject_unknown` |
| 8. 失败处理 | schema 校验失败 → 改 YAML（**不改 schema 去迁就错的 YAML**）；`test_config.py` 既有 11 例红 → 说明删了既有键，回滚该处 |
| 9. 库结构/数据 | 无 |
| 10. 外部/费用 | 无（YAML 内禁止真实模型名/单价） |

### T03 SQLite 运行参数（WAL/busy_timeout/synchronous）+ status 扩展

| 项 | 内容 |
|---|---|
| 1. 目标 | 让「并发安全」从文档变成真实 PRAGMA，并可被 `/api/system/status` 观测 |
| 2. 文件 | 修改 `backend/app/database/session.py`、`backend/app/services/system_service.py`、`backend/app/schemas/system.py`、`tests/test_system_status.py`、`.gitignore`；新建 `tests/test_sqlite_pragmas.py` |
| 3. 输入/输出 | 输入：engine 构造参数；输出：每条连接 4 条 PRAGMA + status 的 `journal_mode`/`busy_timeout` |
| 4. 调用关系 | `build_engine` ← `get_engine` ← `get_db`/`session_scope`；`system_service.build_system_status` 读 PRAGMA |
| 5. 技术/依赖 | SQLAlchemy `event.listen("connect")`（已在用） |
| 6. 步骤 | ①`connect_args` 加 `timeout: 30` ②钩子扩为 4 条 PRAGMA（按方言分支）③`DatabaseStatus` 加两字段（默认 `None`，探测失败不 500）④`FeatureFlags` 加 `model`/`search`/`execution`（默认 false，来源 `integrations` + `execution.enabled`）⑤更新 `test_system_status.py` 断言 ⑥补 `.gitignore` |
| 7. 测试/验收 | `pytest tests/test_sqlite_pragmas.py tests/test_system_status.py -q`；手工 `curl 127.0.0.1:8000/api/system/status` 见 `journal_mode=wal`、`busy_timeout=5000` |
| 8. 失败处理 | WAL 无法开启（网络盘/权限）→ 启动失败并明确报错，**不静默退回 delete**；status 探测失败 → 字段 `None` + `warnings` |
| 9. 库结构/数据 | **会改变开发库持久属性**（`journal_mode=WAL` 是持久设置，并生成 `-wal/-shm` 文件）→ 属 A6 批准范围 |
| 10. 外部/费用 | 无 |

### T04 CallerContext + guard + 端点声明（含 `ToolRegistry` 骨架）

| 项 | 内容 |
|---|---|
| 1. 目标 | 权限在代码层真实生效；漏声明的端点无法启动 |
| 2. 文件 | 新建 `backend/app/core/caller.py`、`backend/app/capabilities/{__init__,guard,tools,budget}.py`、`backend/app/parsers/__init__.py`、`tests/test_caller_context.py`、`tests/test_budget.py`；修改 `backend/app/api/deps.py`、`backend/app/api/router.py`（`ROUTE_CALLERS`）、5 个 `api/routes/*.py` |
| 3. 输入/输出 | 输入：`X-Caller`/`X-Task-Id`/`X-Attempt-Token`；输出：`CallerContext` 或 422/404 |
| 4. 调用关系 | `get_caller` → `deps.Caller` → 所有路由；`guard.require` ← `ToolRegistry.dispatch`；`budget.evaluate` ← 2.1 的 `ModelGateway` |
| 5. 技术/依赖 | FastAPI `Header`/`Depends`；无新依赖 |
| 6. 步骤 | ①`caller.py`（枚举/dataclass/`get_caller`/`require_caller`）②`guard.py`（`ToolSpec`/`PermissionDenied`/`require`）③`tools.py`（注册表 + `dispatch` 先查 guard）④`budget.py` 纯函数 ⑤`deps.py` 暴露 `Caller` ⑥给 21 个操作加 `dependencies=[Depends(require_caller(...))]` ⑦`router.py` 写 `ROUTE_CALLERS` ⑧测试 |
| 7. 测试/验收 | `pytest tests/test_caller_context.py tests/test_budget.py -q`；`curl -H "X-Caller: agent" /api/topics` → 422 `permission_denied`；不带头 → 200 |
| 8. 失败处理 | 声明遗漏 → 启动校验报错并列出缺声明路径；`test_health` 等既有用例因缺头失败 → 在 `client` fixture 统一注入默认头（改 fixture，不放宽校验） |
| 9. 库结构/数据 | 无（2.0 不写 `tool_calls`） |
| 10. 外部/费用 | 无 |

### T05 task_type 注册表（YAML 在 T02，这里落代码与校验）

| 项 | 内容 |
|---|---|
| 1. 目标 | `topic.discover` 不再「文档里有、代码没有」；三处不一致则启动失败 |
| 2. 文件 | 新建 `backend/app/execution/{__init__,task_types,routing}.py`、`tests/test_task_type_registry.py`；修改 `agents/orchestrator/PROMPT.md`（补 `topic.discover`） |
| 3. 输入/输出 | 输入：YAML `task_types`；输出：`TaskType` 枚举、`agents_for()`、`assert_known()` |
| 4. 调用关系 | `routing.load_routing()` ← `startup_checks`(T09) / 未来编排层 |
| 5. 技术/依赖 | 无新依赖 |
| 6. 步骤 | ①`task_types.py` 写 10 个常量 + `ALL_TASK_TYPES` ②`routing.py` 加载校验 ③测试：键集合相等 / `allowed_agents ⊆ roster` / PROMPT.md 字面量 ⊆ 注册表 / `module_design.md`、`api_design.md` 正则扫描 ④orchestrator 的 PROMPT.md 补 `topic.discover` |
| 7. 测试/验收 | `pytest tests/test_task_type_registry.py -q`；故意从 YAML 删一个键 → 启动 `RuntimeError` 且消息列出差异 |
| 8. 失败处理 | 某 PROMPT.md 无 task_type 表 → 跳过该文件但**记录**（不静默通过）；文档扫描命中未知字面量 → 改文档或补注册表，**不放宽测试** |
| 9. 库结构/数据 | 无 |
| 10. 外部/费用 | 无 |

### T06 迁移 B1 `b1_topic_taxonomy`

| 项 | 内容 |
|---|---|
| 1. 目标 | 多合集/多标签/评分快照在库层成立，历史 `column_id` 兼容回填 |
| 2. 文件 | 新建 `backend/app/models/taxonomy.py`（`Tag`/`TopicCollection`/`TopicTag`/`TopicScoreSnapshot`）、迁移 `<rev>_b1_topic_taxonomy.py`、`backend/scripts/seed_collections.py`、`tests/test_migration_batches.py`、`tests/test_schema_parity.py`；修改 `models/topic.py`(+7 列)、`models/enums.py`(+4 枚举)、`models/__init__.py` |
| 3. 输入/输出 | 输入：`de8148733a9b` 库；输出：+4 表、+10 列、6 条合集、回填完成 |
| 4. 调用关系 | `models` → `Base.metadata` → 迁移；`seed_collections.py` → `topic_columns` |
| 5. 技术/依赖 | Alembic `batch_alter_table`（SQLite 必需）+ `op.create_index(..., sqlite_where=text("is_primary = 1"))` |
| 6. 步骤 | ①写模型（FK 默认 `RESTRICT`/`SET NULL`，连接表才 CASCADE）②手写 upgrade/downgrade（含部分索引 + CHECK + 回填 SQL）③写 seed（幂等 upsert）④写批次测试与结构一致性测试 ⑤临时库往返 ⑥备份后对开发库 `upgrade head` ⑦跑 seed ⑧校验 |
| 7. 测试/验收 | `pytest tests/test_migration_batches.py -k b1 -q`；`PRAGMA index_list(topic_collections)` 含 `uq_topic_primary_collection` 且 `partial=1`；`select count(*) from topic_columns where is_system=1` = 6；`alembic check` 无新操作 |
| 8. 失败处理 | 部分索引未被识别 → 手写 `op.execute` + 断言；`create_all` 与迁移不一致 → `test_schema_parity` 失败，**以迁移为准改模型**；回填 0 行正常（开发库空），但测试库必须造 3 条 topics 验证 |
| 9. 库结构/数据 | **是**：+4 表 +10 列 + 索引 + 6 行 seed（A6） |
| 10. 外部/费用 | 无 |

### T07 迁移 B2 `b2_task_layer`

| 项 | 内容 |
|---|---|
| 1. 目标 | 研究任务/子任务/DAG/事件流的结构就位（**不实现调度**） |
| 2. 文件 | 新建 `backend/app/models/execution.py`（`ResearchTask`/`ResearchSubtask`/`ResearchSubtaskDependency`/`TaskEvent`）、迁移；修改 `models/agent.py`(+3 列)、`models/research.py`(+3 列)、`models/enums.py`(+3 枚举)、`models/__init__.py`、`tests/test_migration_batches.py` |
| 3. 输入/输出 | 输出：22 → 26 张业务表 |
| 4. 调用关系 | 同 T06；`execution/event_seq.py` 供未来执行层取号 |
| 5. 技术/依赖 | 同 T06；`uq_task_event_seq(research_task_id, event_seq)` |
| 6. 步骤 | ①模型（`task_type` = `String(64)` + `CHECK (length BETWEEN 3 AND 64 AND task_type GLOB '*.*')`，**不做枚举 CHECK**）②迁移（含新列）③`next_event_seq()` + 单测 ④往返测试 |
| 7. 测试/验收 | B2 往返 exit 0；`PRAGMA table_info(agent_runs)` 含 3 新列；`uq_task_event_seq` 存在 |
| 8. 失败处理 | SQLite 加列必须 `batch_alter_table`；失败 → `downgrade -1` + 备份恢复 |
| 9. 库结构/数据 | **是**（A6） |
| 10. 外部/费用 | 无 |

### T08 迁移 B3 `b3_metering`

| 项 | 内容 |
|---|---|
| 1. 目标 | 记账结构先于任何真实模型调用存在（P0-2 硬要求） |
| 2. 文件 | 新建 `backend/app/models/metering.py`（`ModelCall`/`ToolCall`）、迁移；修改 `models/execution.py`(+4 列)、`models/enums.py`(+2 枚举)、`models/__init__.py`、`tests/test_migration_batches.py` |
| 3. 输入/输出 | 输出：`model_calls`/`tool_calls` 存在；`research_tasks` 有 `calls_reserved`/`cost_reserved`/`lease_heartbeat_at`/`attempt_token` |
| 4. 调用关系 | `budget.evaluate`（纯函数）只读 `BudgetState`，不依赖这两张表；2.1 的 `reserve_call()` 才是写入方 |
| 5. 技术/依赖 | 同 T06 |
| 6. 步骤 | ①模型（`status` CHECK 含 8 态）②迁移 ③往返 ④校验 `ix_model_calls_status` |
| 7. 测试/验收 | B3 往返 exit 0；8 态 CHECK 存在；`research_tasks` 4 新列存在 |
| 8. 失败处理 | 同 T07 |
| 9. 库结构/数据 | **是**（A6） |
| 10. 外部/费用 | 无（**2.0 内 `model_calls` 必须是空表**，任何非空行都说明有人偷偷调了模型） |

### T09 启动自检（`startup_checks`）

| 项 | 内容 |
|---|---|
| 1. 目标 | 把「配置/注册表/权限声明/合集一致性」从人工约定变成启动闸门 |
| 2. 文件 | 新建 `backend/app/core/startup_checks.py`、`tests/test_startup_checks.py`；修改 `backend/app/main.py`（lifespan 调用，**不动 :27**） |
| 3. 输入/输出 | 输入：`Settings` + `app`；输出：`list[CheckResult]`，任一 fail 抛 `StartupCheckError` 阻止启动 |
| 4. 调用关系 | `main.create_app().lifespan` → `startup_checks.run_all()`；依赖 T02（schema）、T05（注册表）、T04（`ROUTE_CALLERS`）、B1（`topic_columns` 存在） |
| 5. 技术/依赖 | 无新依赖；OpenAPI 用 `app.openapi()` 生成（避免 `app.routes` 的 `_IncludedRouter` 坑） |
| 6. 步骤 | ①四项检查：C1 task_type 三处一致 / C2 路由声明集合 == OpenAPI 路径集合 / C3 taxonomy YAML 的 6 个 code ⊆ `topic_columns`（表为空时按 env `STARTUP_STRICT_DB=false` 降级为 warning） / C4 三份 YAML 可被 schema 解析 ②`main.py` lifespan 调用 ③测试用 monkeypatch 各造一个失败 |
| 7. 测试/验收 | `pytest tests/test_startup_checks.py -q`；手工：删 YAML 一 key 后 `uvicorn` 启动 → 进程退出并打印缺哪个键 |
| 8. 失败处理 | C3 在「B1 未迁移」时必然失败 → 用 `STARTUP_STRICT_DB` env 明确降级，**但默认 strict**；检查本身抛异常 → 视为 fail（不吞异常） |
| 9. 库结构/数据 | 无（只读 `topic_columns`） |
| 10. 外部/费用 | 无 |

### T10 日志 / 异常 / 审计（2.0 边界）

| 项 | 内容 |
|---|---|
| 1. 目标 | 请求可追踪（`request_id`）+ 调用者身份进日志；**不落库** |
| 2. 文件 | 修改 `backend/app/core/logging.py`、`backend/app/main.py`（中间件）；新建 `tests/test_logging_audit.py` |
| 3. 输入/输出 | 输入：HTTP 请求；输出：`X-Request-Id` 响应头 + 日志含 `request_id`/`caller_kind` |
| 4. 调用关系 | 中间件 → `ContextVar` → `LogRecord` filter；`errors.py` 处理器已统一信封，不动 |
| 5. 技术/依赖 | `contextvars` + `logging.Filter`（标准库） |
| 6. 步骤 | ①`logging.py` 加 filter 与格式字段 ②`main.py` 加中间件（生成/透传 `X-Request-Id`）③测试：带头透传、不带头生成、日志含字段 |
| 7. 测试/验收 | `pytest tests/test_logging_audit.py -q`；`curl -H "X-Request-Id: abc" -i /health` 响应头回显 `abc` |
| 8. 失败处理 | 新增响应头属 API 行为变化 → A3 未批则不实施本任务（T10 可整体延后，不阻塞其他任务） |
| 9. 库结构/数据 | 无（**明确不写 `tool_calls`**） |
| 10. 外部/费用 | 无 |

### T11 文档与状态同步

| 项 | 内容 |
|---|---|
| 1. 目标 | 让文档与实测一致（M1.1 教训：文档先行代码没跟上=冲突） |
| 2. 文件 | 修改 `docs/research_hub/{current_state_audit,system_architecture,implementation_roadmap,acceptance_criteria,state_machine,database_design,README}.md`；新建 `docs/research_hub/m2_0_delivery_report.md` |
| 3. 输入/输出 | 输入：T01–T10 的实测结果；输出：文档数字与结构均与实测一致 |
| 4. 调用关系 | 无 |
| 5. 技术/依赖 | 无 |
| 6. 步骤 | ①用实测替换所有推算数字（表数、端点数、文件数、行数）②`system_arch` §6 目录布局统一为 roadmap 口径（G-16）③`state_machine` §6.3 的 `delivered` 改为 `content_produced` ④`current_state_audit` 标 B1/B2/B3 的真实状态（结构就绪/有生产者）⑤`README.md` 文档数 +1 |
| 7. 测试/验收 | 一致性脚本（`docs/research_hub/.m20_check.py`，交付前删除）跑 0 fail；`grep` 复核旧数字已无残留（历史文档白名单除外） |
| 8. 失败处理 | 文档与实测冲突 → **以实测为准改文档**，并在此报告里写明差异 |
| 9. 库结构/数据 | 无 |
| 10. 外部/费用 | 无 |

### T12 收口验证与提交（需 A8 批准）

| 项 | 内容 |
|---|---|
| 1. 目标 | 用一条命令链证明 M2.0 达成，再交付 |
| 2. 文件 | 无新增（除非修 bug） |
| 3. 输入/输出 | 输出：验收证据块 + commit |
| 4. 调用关系 | 无 |
| 5. 技术/依赖 | git、alembic、pytest、sqlite3 |
| 6. 步骤 | ①`pytest -q` ②`alembic check` ③三批往返 ④真实库 `PRAGMA` ⑤`git status`/`git diff --stat` ⑤写提交信息到 `$env:TEMP\*.txt` 后 `git commit -F`（**PowerShell 无 heredoc**）⑦普通 `git push`（不 force/不 amend） |
| 7. 测试/验收 | 见 §6 的 G1–G12 全过 |
| 8. 失败处理 | 任一门红 → 不提交；`push` 被拒 → `git fetch` 后普通 merge/rebase（**不 force**）；提交信息文件必须删除且不被提交 |
| 9. 库结构/数据 | 无（迁移已在 T06–T08 完成） |
| 10. 外部/费用 | 无 |

---

## 4. 执行顺序与依赖关系

```
T01 ──> T02 ──> T03 ────────────┐
          │                     │
          ├──> T05 ──> T04 ──┐  │
          │                  │  │
          └──────────────────┴──┴──> T06(B1) ──> T07(B2) ──> T08(B3) ──> T09 ──> T10 ──> T11 ──> T12
```

| 阶段 | 任务 | 依赖 | 可否并行 | 里程碑（可交付检查点） |
|---|---|---|---|---|
| 0 | T01 | — | — | 基线冻结 + 首份备份 |
| 1 | T02 | T01 | 否（其余任务都读配置） | 三份 YAML + schema 校验通过 |
| 2 | T03、T05 | T02 | **可并行**（不同文件） | SQLite PRAGMA 生效 / 注册表落地 |
| 3 | T04 | T02、T05（guard 要用工具白名单） | 否 | 21 端点全声明 + guard 有单测 |
| 4 | T06 → T07 → T08 | T03、T04（B1 的 `topics.status` 校验依赖 caller 无直接关系，但 B1 的结构一致性测试依赖模型注册）；三批**串行**（B2/B3 改 `research_tasks`，B1 后才能建） | 否 | 26 张业务表 + 6 合集 + 回填 |
| 5 | T09 | T02、T04、T05、T06 | 否 | 启动自检四项 |
| 6 | T10 | T03（日志中间件与 caller 依赖顺序不重要） | **可与 T09 并行** | 请求可追踪 |
| 7 | T11 | 全部 | 否 | 文档=实测 |
| 8 | T12 | 全部 + A8 | 否 | 提交+推送 |

**最小可交付切片（若只批准一部分）**：T01+T02+T05 可独立交付（纯新增，不碰现有行为）；T03 单独需 A2；T04 需 A1；T06–T08 需 A6；T10 需 A3；T12 需 A8。

---

## 5. 文件变更计划（完整清单）

### 5.1 新建（20 个文件）

| 文件 | 任务 | 预估行数 | 说明 |
|---|---|---|---|
| `config/models.yaml` | T02 | ~30 | 价目策略骨架，**无真实单价** |
| `config/agent_routing.yaml` | T02 | ~120 | 10 种 task_type + 9 Agent 的工具/数据域白名单 |
| `config/pipelines.yaml` | T02 | ~25 | 执行参数骨架 |
| `backend/app/core/config_schema.py` | T02 | ~150 | pydantic 校验模型 |
| `backend/app/core/caller.py` | T04 | ~110 | CallerContext + 依赖 |
| `backend/app/core/startup_checks.py` | T09 | ~140 | 四项自检 |
| `backend/app/capabilities/__init__.py` | T04 | ~90 | Tool/SearchProvider/Fetcher/ModelGateway Protocol + ToolResult |
| `backend/app/capabilities/guard.py` | T04 | ~90 | ToolSpec + require() |
| `backend/app/capabilities/tools.py` | T04 | ~80 | ToolRegistry 骨架 |
| `backend/app/capabilities/budget.py` | T04 | ~100 | 纯函数预算判定 |
| `backend/app/parsers/__init__.py` | T04 | ~40 | Parser Protocol |
| `backend/app/execution/__init__.py` | T05 | ~10 | — |
| `backend/app/execution/task_types.py` | T05 | ~70 | 10 个 TaskType |
| `backend/app/execution/routing.py` | T05 | ~90 | 加载/校验/查询 |
| `backend/app/execution/event_seq.py` | T07 | ~30 | `next_event_seq()` |
| `backend/app/models/taxonomy.py` | T06 | ~230 | 4 张表 |
| `backend/app/models/execution.py` | T07 | ~260 | 4 张表 |
| `backend/app/models/metering.py` | T08 | ~140 | 2 张表 |
| `backend/migrations/versions/*_b1/b2/b3_*.py` | T06–T08 | ~200/~240/~160 | 三段迁移 |
| `backend/scripts/seed_collections.py` | T06 | ~110 | 六合集 upsert |
| 测试 9 个文件 | T02–T10 | ~700 | 见 §2.8 |
| `data/backups/.gitkeep` | T01 | 1 | — |
| `docs/research_hub/m2_0_delivery_report.md` | T11 | ~200 | 交付报告 |

### 5.2 修改（12 个文件）

| 文件 | 任务 | 改动性质 |
|---|---|---|
| `config/settings.yaml` | T02 | +features/execution/cost 三段（**不动既有键**） |
| `backend/app/core/config.py` | T02 | +5 个 getter + env 覆盖字段 |
| `backend/app/core/logging.py` | T10 | +ContextVar filter |
| `backend/app/database/session.py` | T03 | WAL/busy_timeout/synchronous |
| `backend/app/schemas/system.py` | T03 | +3 feature 键 + database 两字段 |
| `backend/app/services/system_service.py` | T03 | 读新配置 + 探测 PRAGMA |
| `backend/app/api/deps.py` | T04 | +Caller 依赖 |
| `backend/app/api/router.py` | T04 | +ROUTE_CALLERS |
| `backend/app/api/routes/{health,topics,knowledge,sources,research_reports}.py` | T04 | 每端点加 `dependencies` |
| `backend/app/main.py` | T09/T10 | lifespan 调 startup_checks + 中间件（**不动 :27**） |
| `backend/app/models/{topic,agent,research,enums,__init__}.py` | T06–T08 | 加列/加枚举/登记新模型 |
| `tests/test_system_status.py` | T03 | features 断言按 7 键 |
| `pyproject.toml` | T02 | markers + `research` 可选依赖组（+A5 的 packages.find） |
| `.gitignore` | T03 | +`*.db-wal`、`*.db-shm` |
| `agents/orchestrator/PROMPT.md` | T05 | +`topic.discover` |
| `docs/research_hub/*.md`（7 份） | T11 | 数字与口径同步 |

**合计**：新建 ~21 个源文件 + 9 个测试文件 + 3 段迁移 + 1 份报告；修改 12 个现有文件（其中 6 个是 `backend/app/` 真实代码文件，**全部需要 A1/A2 批准**）。

---

## 6. 测试与验收方案

### 6.1 命令集（全部在 `C:\Users\25803\finance-media-ai` 下执行）

```powershell
# 1. 全量测试（G1/G2）
.venv\Scripts\python.exe -m pytest -q

# 2. 迁移与模型一致（G4）
.venv\Scripts\python.exe -m alembic check

# 3. 三批往返（G3/G11），临时库，不碰开发库
.venv\Scripts\python.exe -m pytest tests/test_migration_batches.py -q

# 4. 真实库 PRAGMA（G12 + P1-6）
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/finance_media.db');print(c.execute('PRAGMA journal_mode').fetchone(),c.execute('PRAGMA busy_timeout').fetchone())"

# 5. 表数与合集
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/finance_media.db');print(len(c.execute(\"select name from sqlite_master where type='table'\").fetchall()), c.execute('select count(*) from topic_columns where is_system=1').fetchone())"

# 6. 端点数量未增加（2.0 的硬约束）
.venv\Scripts\python.exe -c "from fastapi.testclient import TestClient;from backend.app.main import create_app;s=TestClient(create_app()).get('/openapi.json').json();print(len(s['paths']), sum(len(v) for v in s['paths'].values()))"
```

### 6.2 必须的期望值

| 项 | M2.0 完成后应为 |
|---|---|
| `pytest` | 全绿（96 + 新增 ≈ 140+），0 failed |
| `alembic check` | `No new upgrade operations detected.` |
| 业务表数（含 `alembic_version`） | **28 + 1 = 29 张物理表**（推算，实测替换 `[u]`） |
| `topic_columns where is_system=1` | **6** |
| `journal_mode` / `busy_timeout` | `('wal',)` / `(5000,)` |
| OpenAPI 路径 / 操作 | **仍为 14 / 21**（不增业务端点；若 T10 加中间件不影响） |
| `model_calls` 行数 | **0**（2.0 不许有真实调用） |

### 6.3 质量门映射（G1–G12）

| 门 | 本计划如何满足 |
|---|---|
| G1 全量测试 | T12 第 1 步 |
| G2 默认离线 | 无新增网络代码；`integration` marker 预留 |
| G3 迁移可逆 | T06–T08 各自往返用例 |
| G4 模型=迁移 | `alembic check` + `test_schema_parity` |
| G5 无假功能 | 本文 §2.1 非目标 + T08「`model_calls` 必须为 0 行」 |
| G6 错误信封 | 复用 `core/errors.py`，只加 `details.reason` |
| G7 成本可见 | 2.0 只建表，不产生费用（`model_calls` 空） |
| G8 文档同步 | T11 |
| G9 无敏感信息 | 只写 `.env.example` 占位；提交前 `git diff` 人工复核 |
| G10 提交可回溯 | T12 普通 commit+push |
| G11 备份先于迁移 | T01 + §2.7 统一动作 |
| G12 安全机制先于能力 | 顺序：T04/T03 在 T06 前，T06–T08 在 T09 前 |

---

## 7. 风险清单

| 编号 | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | WAL 在 Windows 上因权限/网络盘失败 | 低 | T03 阻塞 | 显式报错不静默降级；失败先确认 `data/` 本地盘 |
| R2 | Alembic 不识别**部分唯一索引**（`sqlite_where`） | 中 | `alembic check` 误报/漏报 | 迁移里手写 `op.create_index(..., sqlite_where=...)` + `PRAGMA index_list` 断言 + `test_schema_parity` |
| R3 | `create_all` 与迁移建出的结构漂移 | 中 | 测试绿但线上炸 | `test_schema_parity.py` 逐表比对列/索引/约束 |
| R4 | 更新 `test_system_status.py` 时顺手放宽了断言语义 | 中 | 掩盖真实回归 | 只把「4 键相等」改成「7 键相等」，不做子集断言 |
| R5 | 给 21 个端点加 `dependencies` 时漏改某个 | 中 | 破坏现有测试 | 启动校验（C2）在测试里就拦住：OpenAPI 集合 vs `ROUTE_CALLERS` |
| R6 | 回填逻辑因开发库为空而"看起来成功" | 高 | 上线才发现回填错 | 测试库先造 3 条 topics（1 条带 `column_id`、1 条 published、1 条 archived）再跑迁移 |
| R7 | 新增 pydantic 字段触发 `filterwarnings` 的 DeprecationWarning 变红 | 低 | 测试红 | 用 pydantic v2 新 API（`model_config`/`field_validator`），不用 v1 风格 |
| R8 | `--strict-markers` 下新增 marker 未注册 | 中 | 2.1 一写 marker 就红 | T02 一次注册 `integration`/`slow` |
| R9 | 迁移与 seed 顺序错（先 seed 后迁移） | 中 | seed 失败/重复行 | seed 脚本按 `code` upsert（幂等）+ 文档写死「先迁移后 seed」 |
| R10 | 8 项批准未齐却开工，导致返工 | 中 | 时间浪费 | 按 §4 的「最小切片」交付，硬阻塞项（A1/A2/A6）未批则只做 T01/T02/T05 |

---

## 8. 尚需人工批准的事项（A1–A8）

| 编号 | 事项 | 为什么必须批准 | 不批的后果 |
|---|---|---|---|
| **A1** | 允许修改现有 `backend/app/api/**`（`deps.py`、`router.py`、5 个 `routes/*.py`）为每个端点声明调用者类型 | 触及「现有代码」与「API 行为」（虽然只是加依赖，不改语义） | T04/T09 不完整 → 权限形同文档，M2.0 核心目标之一落空 |
| **A2** | 允许修改 `/api/system/status` 响应体（`features` +3 键、`database` +2 字段） | 属对外响应结构变化（向后兼容的新增字段），且 acceptance 2.0 明确要求 | G-12 无法闭合 |
| **A3** | 允许新增响应头 `X-Request-Id` | 属 API 行为变化（可观测性） | T10 延后（不阻塞其他任务） |
| **A4** | seed 六合集用**独立脚本**而非迁移内 seed；执行人＝我（按 A6 批准后） | 数据写入策略选择 | 合集缺失 → 立项守卫无法工作 |
| **A5** | 允许修正 `pyproject.toml`：`packages.find` + 新增 `research` 可选依赖组 | 构建配置变更 | 新子包不被打包（本地不影响，`pip install .` 会缺包） |
| **A6** | 允许对 `data/finance_media.db` 执行 B1/B2/B3（含 WAL 持久设置、6 行 seed、回填） | 任务书明令本轮不得执行迁移；实施阶段需新授权 | T06–T08 只能在临时库做，开发库停留在 19 表 |
| **A7** | 允许按 G-16 把 `system_architecture.md` §6 的目录布局改为 roadmap 口径，并修正 `state_machine.md` §6.3 的 `delivered` | 属"改已批准的设计文档措辞"，不是改决策 | 文档内部持续冲突（M1.1 同类问题会再现） |
| **A8** | 允许提交并推送 M2.0（`feat(m2.0): …`，普通 push） | 任务书本轮禁止提交/推送 | 成果只在本地 |

> **⚠️ 硬阻塞**：**A1 / A2 / A6** 任一不批，M2.0 无法按本文完成。A3/A4/A5/A7/A8 不批只影响对应子项。

---

## 9. M2.0 完成的最终验收标准

### 9.1 必须全部为真（逐条可命令验证）

- [ ] `pytest -q` → 0 failed（含现有 96 个）
- [ ] `alembic heads` 显示 **3 个新 revision 之上**、单 head；`alembic check` 无新操作
- [ ] 业务表 18 → **28**，物理表 19 → **29**（含 `alembic_version`）；与 `database_design.md` §6.4 的 22/26/28 演进一致（roadmap 旧写 26 已在 T11 修正）
- [ ] `topic_columns` 有 6 条 `is_system=1`；`topics.column_id` 全部回填到 `topic_collections`
- [ ] 真实库 `journal_mode=wal`、`busy_timeout=5000`
- [ ] `CallerContext` 生效：`X-Caller: agent` 无 token → 422 `permission_denied`；`_dev` 在 production → 404
- [ ] 21 个操作**全部**声明了 caller kind，且启动校验能发现人为删除某处声明的错误
- [ ] task_type 三处一致，`topic.discover` 在册；文档里没有未注册的 task_type 字面量
- [ ] `model_calls` / `tool_calls` 存在且 **0 行**
- [ ] 三批迁移各自 `upgrade → downgrade -1 → upgrade` exit 0，且每批前有 `data/backups/` 备份
- [ ] `data/backups/`、`.gitignore` 的 `-wal/-shm` 规则都到位
- [ ] **OpenAPI 路径数仍为 14、操作数仍为 21**（不新增业务端点）
- [ ] 文档（`current_state_audit`/`roadmap`/`acceptance_criteria`/`database_design` §6.4）数字与实测一致

### 9.2 反面清单（出现任一条即为未完成，无论测试颜色）

1. 把「建了表」说成「实现了任务调度」。B2 只建结构，调度是 2.4。
2. 把「`budget.evaluate` 能算」说成「预算已生效」。真实闸门在 2.1 的 `ModelGateway`。
3. 把「WAL 打开了」说成「并发安全了」。租约/心跳/崩溃扫描是 2.4。
4. 把「`ToolRegistry` 存在」说成「工具可用」。10 个工具全是 `Null`/未实现。
5. 把「`CallerContext` 有类」说成「权限生效」。必须真实拦下越权请求，且有测试。
6. 把 `seed_collections.py` 跑过一次说成「合集已可维护」。改合集仍需改 YAML + 重跑 + `taxonomy_version` +1。
7. 为了让测试变绿而放宽断言（尤其 R4 的 features 等价性）。
8. 给 `docs/research_hub/m1_1_architecture_review.md` 补提交（M1.1 约定它保持未跟踪）。

### 9.3 本轮（M2.0 审查）自身的验收

| 交付物 | 状态 |
|---|---|
| `docs/research_hub/m2_0_implementation_plan.md` | 本文 |
| 13 份设计文档已读 | 已读：本轮重读/复核了 `system_architecture`(§3.4/§4.1/§6/§8/§9/§11)、`database_design`(§1/§3/§4/§5/§6/§7)、`tooling`(§0/§1.3/§2.7)、`api_design`(§2.2/§2.3/§3/§5)、`agent_architecture`(§4.0/§4.1)、`implementation_roadmap`(§0/§0.1/2.0)、`acceptance_criteria`(§2/§3-2.0)、`state_machine`(§2)、`README`、`module_design`、`m1_delivery_checklist`、`m1_2_decision_register`、`m1_2_delivery_report` 的对应章节；§1.5 的差距均附实测证据。**说明**：其中 5 份（`current_state_audit` 之外的 `m1_2_*`、`module_design` 等）是同一会话内本人撰写并已复核的，本轮未逐行重读全文，凡本文引用的断言均标注了 `[V]`/`[d]` 来源 |
| 代码/迁移/配置零改动 | `git status --short` 仅本文 + 既有 `m1_1` 未跟踪文件 |
| 未提交未推送 | 是 |
| 未调用任何外部服务、未产生费用 | 是 |

---

## 附录 A：本文引用的实测命令

```powershell
cd C:\Users\25803\finance-media-ai
git rev-parse --abbrev-ref HEAD; git rev-parse HEAD; git status --short
git rev-list --left-right --count HEAD...origin/main
.venv\Scripts\python.exe -m pytest -q -p no:warnings
.venv\Scripts\python.exe -m alembic heads; .venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/finance_media.db');print(c.execute('PRAGMA journal_mode').fetchone(),len(c.execute(\"select name from sqlite_master where type='table'\").fetchall()))"
Get-ChildItem backend\app -Directory; Get-ChildItem config -File
.venv\Scripts\python.exe -c "from backend.app.main import create_app;from fastapi.testclient import TestClient;s=TestClient(create_app()).get('/openapi.json').json();print(len(s['paths']),sum(len(v) for v in s['paths'].values()))"
```

## 附录 B：本文与其他文档的关系

| 文档 | 本文如何使用它 |
|---|---|
| `implementation_roadmap.md` §0.1 + 2.0 | 任务拆解、文件范围、安全机制顺序（**唯一权威**） |
| `database_design.md` §3/§4/§5/§6 | B1/B2/B3 的表、列、约束、迁移批次与校验 |
| `system_architecture.md` §3.4/§4.1/§6/§8 | SQLite 参数、ModelGateway 硬门、目录映射、成本闸门 |
| `tooling_architecture.md` §1.3/§2.7 | CallerContext 定义与 llm_complete 预留顺序 |
| `agent_architecture.md` §4.0 | 10 种 task_type 注册表内容 |
| `api_design.md` §2.3 | 权限表、错误码、`details.reason` 取值 |
| `acceptance_criteria.md` §2/§3(2.0) | 质量门 G1–G12 与 2.0 勾选清单 |
| `state_machine.md` §2 | `TopicStatus` 回填与主合集约束 |
| `m1_2_decision_register.md` D1–D13 | 不许擅自更改的既定决策 |

## 附录 C：量级估算（仅供参考，不是承诺）

| 任务 | 代码行（含测试） | 预计节拍 |
|---|---|---|
| T01 | 0（运维） | 0.5 小时 |
| T02 | ~350 | 0.5–1 天 |
| T03 | ~180 | 0.5 天 |
| T04 | ~500 | 1–1.5 天 |
| T05 | ~250 | 0.5 天 |
| T06 | ~700（含 230 模型 + 200 迁移 + 110 seed + 测试） | 1.5–2 天 |
| T07 | ~650 | 1.5 天 |
| T08 | ~450 | 1 天 |
| T09 | ~200 | 0.5 天 |
| T10 | ~120 | 0.5 天 |
| T11 | 文档为主 | 0.5 天 |
| T12 | 0（验证+提交） | 0.5 天 |
| **合计** | **~3,400 行（含测试约 900）** | **约 8–11 个工作日**（单人，含验证与返工余量） |

> 2.0 完成时，平台的真实能力是：**有地基、有账本、有权限、没有工人**。
> 第一个真实能力出现在 2.1（模型适配层），第一个真实产出出现在 2.6（端到端报告）。



