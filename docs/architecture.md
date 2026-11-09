# 架构说明（第一阶段）

> 适用范围：`finance-media-ai` 第一阶段（基础设施）。本文只描述**已经能跑的代码**，
> 以及明确标注为"未实现"的扩展点。任何未接线能力都不会在本文中被写成已完成。

## 1. 阶段目标与边界

第一阶段交付一个**可运行、可测试、可迁移**的单体骨架，解决四件事：

1. 知识资产与事实可追溯的数据底座（SQLAlchemy + Alembic + SQLite）。
2. 可启动的 FastAPI 服务与真实业务接口（非固定返回值）。
3. 九个 Agent 的角色定义、统一协议与可扩展调度接口（**不接任何模型**）。
4. 内容与视频生产的工作流文档、模板与质检清单（**不实现渲染引擎**）。

明确不做：云部署、付费 API、Kubernetes、微服务、独立向量数据库、完整视频生成。

## 2. 技术栈（实测版本）

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.12.10 | 项目虚拟环境 `.venv` |
| FastAPI | 0.162.2 | HTTP 服务与 OpenAPI 文档 |
| Uvicorn | 0.54.0 | ASGI 服务器 |
| SQLAlchemy | 2.0.57 | ORM（2.0 声明式 `Mapped` 风格） |
| Alembic | 1.20.1 | 数据库迁移 |
| Pydantic | 2.14.3 / pydantic-settings 2.16.2 | 请求响应模型与环境配置 |
| PyYAML | 6.0.3 | 读取 `config/*.yaml` 与模板 |
| pytest | 8.4.2 + httpx 0.28.1 | 单元/接口测试 |
| SQLite | 内置 | 第一阶段本地开发与测试数据库 |

PostgreSQL、matplotlib、FFmpeg **未安装**：`pyproject.toml` 里作为可选依赖组
（`postgres`、`charts`）登记，视频引擎依赖（FFmpeg）只写进文档，不假装已就绪。

## 3. 运行时形态

```
uvicorn backend.app.main:app
        │
        ├── create_app(settings)           # backend/app/main.py
        │     ├── lifespan: 日志 + 运行时目录 + 关闭时释放连接池
        │     ├── install_exception_handlers(app)
        │     ├── /health                   # 不访问数据库
        │     └── /api/*                    # topics / knowledge / sources / evidence / research-reports
        │
        └── SQLAlchemy Engine ──► SQLite 文件库（data/finance_media.db）
```

单进程、单进程内单引擎（`backend/app/database/session.py` 的模块级单例），
没有引入连接池中间件、任务队列或缓存层。

## 4. 目录职责

| 目录 | 职责 | 第一阶段状态 |
| --- | --- | --- |
| `backend/app/api/` | 路由与依赖（`deps.py`），只做参数解析与调用服务层 | 已实现 |
| `backend/app/schemas/` | Pydantic 请求/响应模型，是系统的边界契约 | 已实现 |
| `backend/app/services/` | 业务逻辑与事务边界（`BaseService`），数据库访问只在这里 | 已实现 |
| `backend/app/models/` | SQLAlchemy 模型（18 张表） | 已实现 |
| `backend/app/database/` | 引擎、会话、`Base`、命名约定 | 已实现 |
| `backend/app/core/` | 配置、错误、日志、工具函数 | 已实现 |
| `backend/migrations/` | Alembic 环境与版本脚本 | 已实现（初始迁移） |
| `agents/` | 名册、协议、九个角色提示词 | 提示词与协议已实现；执行器未实现 |
| `knowledge/` | 知识条目 Markdown 存放区（与数据库配合） | 目录与规范已建立，无同步脚本 |
| `content/` | 选题/脚本/分镜/素材/发布物 | 目录与规范已建立 |
| `video_engine/` | 模板、渲染器、字幕、音频、质检 | 模板与质检清单已定义；渲染未实现 |
| `prompts/` | 通用提示词模板 | 已建立，无代码调用 |
| `config/` | 应用与业务配置（YAML） | 已实现 |
| `tests/` | pytest 测试（76 个用例） | 已实现并通过 |
| `docs/` | 架构、数据模型、Agent、工作流、验收 | 已实现 |

## 5. 分层与依赖方向

```
api (路由/依赖) ──► services (业务规则/事务) ──► models (ORM) ──► database (引擎/会话)
     │                    │
     └── schemas ◄────────┘        core (config/errors/logging/utils) 被各层引用
```

硬规则（代码里已遵守，后续请继续遵守）：

1. **路由不写 SQL**：路由只调用服务层方法，事务由服务层用 `BaseService._commit()` 统一提交。
2. **模型不出边界**：接口响应一律经 Pydantic Schema（`ORMBase` 开启 `from_attributes`）。
3. **错误只有一种形状**：`{"error": {"code", "message", "details?"}}`。
4. **金额、时间、枚举有统一约定**（见 §7）。

## 6. 请求／配置／错误

### 6.1 配置优先级

`backend/app/core/config.py` 使用 `pydantic-settings`：

```
环境变量（含 .env） > Settings 默认值
```

`config/*.yaml` **不参与** `Settings` 推导，它承载业务型配置（品牌、内容规则、来源规范、
视频规格），通过 `get_app_config() / get_brand_config() / get_content_rules() /
get_research_sources() / load_yaml_config()` 以只读字典形式提供。

原则：**密钥只走环境变量**；YAML 里不出现任何真实 API Key（`.env.example` 中的
LLM/搜索项全部是空占位）。

### 6.2 统一错误模型

| 异常 | HTTP | code |
| --- | --- | --- |
| `NotFoundError` | 404 | `not_found` |
| `ConflictError` | 409 | `conflict` |
| `DomainValidationError` | 422 | `domain_validation_error` |
| `DependencyUnavailableError` | 503 | `dependency_unavailable` |
| 请求体校验失败（Pydantic） | 422 | `request_validation_error` |
| 数据库完整性冲突 | 409 | `database_integrity_error` |
| 其它 SQLAlchemy 错误 | 500 | `database_error` |
| 未捕获异常 | 500 | `internal_error` |

约定：**创建/更新时引用不存在的关联对象**（如 `topic_id` 不存在）视为请求体校验问题，
返回 422 `domain_validation_error` 并在 `details.field` 指出字段；**读取不存在的资源**
返回 404 `not_found`。

## 7. 全局约定

| 主题 | 约定 |
| --- | --- |
| 时间 | 一律 UTC（`DateTime(timezone=True)` + `utcnow()`），响应是 ISO 8601 |
| 金额 | `Numeric(20, 2)`（`backend/app/models/types.py: MoneyAmount`），不用 float |
| 枚举 | Python `str Enum` + `Enum(native_enum=False, create_constraint=True)`，库内存字符串、带 CHECK 约束，便于迁移到 PostgreSQL |
| 命名 | `Base.metadata` 带命名约定：`ix_*/uq_*/ck_*/fk_*/pk_`，约束名可被 Alembic 稳定引用 |
| slug | 由服务层 `make_unique_slug()` 生成，中文标题回退 `item-<hash8>` |
| 版本 | 知识条目与脚本用 `version` 递增，`is_current` 标记当前版本（知识条目） |
| 软约束 | 证据必须二选一挂在知识条目或研究报告上（CHECK `has_claim_target`） |

## 8. 数据库与会话策略

- 引擎：`build_engine(settings)`；SQLite 时 `check_same_thread=False`，并在每个连接上执行
  `PRAGMA foreign_keys=ON`（**外键真的生效**，测试里有对应用例）。
- 会话：`Session(autoflush=False, autocommit=False, expire_on_commit=False)`；
  `get_db()` 作为 FastAPI 依赖，`session_scope()` 供脚本使用。
- 迁移：`alembic upgrade head`；`backend/migrations/env.py` 的 `target_metadata` 指向
  `Base.metadata`，且 `import backend.app.models` 保证 18 张表全部注册。
  SQLite 下 `render_as_batch=True`，`compare_type=True`。
- 初始迁移 revision：`de8148733a9b`（`backend/migrations/versions/20261109_1754_initial_schema.py`）。
- 测试用 `Base.metadata.create_all` 建临时库；迁移本身由 `tests/test_migrations.py`
  用真实 Alembic 命令单独验证（两条路径都覆盖）。

## 9. 扩展点（未来接线位置，均未实现）

| 扩展 | 位置 | 现在是什么状态 |
| --- | --- | --- |
| 接入 LLM / 搜索 | `backend/app/services/agent_registry.py` 的 `TaskDispatcher` 子类 | 只有 `RecordingDispatcher`（落库不执行）与 `UnavailableDispatcher`（显式 503） |
| 语义检索 | `KnowledgeService.search()` | 当前是 SQL `LIKE` + 加权排序，明确不是向量检索（无向量库） |
| PostgreSQL | `DATABASE_URL` + `alembic upgrade head` | 模型只用通用类型；尚未在 PG 上实测 |
| 视频渲染 | `video_engine/renderers/` | 只有 README 与计划，无代码 |
| 图表生成 | `pyproject.toml` 的 `charts` 可选依赖 | matplotlib 未安装 |

## 10. 已知限制

1. `KnowledgeService.search()` 是全表 `LIKE` 扫描，数据量大后需要索引或全文检索方案。
2. 无鉴权、无多用户、无限流：第一阶段是本地单机服务，不要暴露到公网。
3. `agent_runs` 只登记任务，不会自动执行；`output_payload` 始终为空。
4. 视频模板与质检清单目前**没有代码读取**，是给人看的规范（测试只校验文件结构）。
5. `publications` 只建模发布记录与效果字段，没有任何平台对接代码。
6. 未接入结构化日志采集与指标系统，只有标准 `logging`。
