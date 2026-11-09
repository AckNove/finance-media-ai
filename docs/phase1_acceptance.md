# 第一阶段验收报告

- 验收时间：2026-11-09
- 环境：Windows / Python 3.12.10（`.venv`）/ SQLite
- 结论：**12 项验收全部通过**，其中"接口层已可用"与"执行/渲染层未实现"分别标注，
  未把占位写成已完成。

## 1. 逐项验收结果

| # | 验收项 | 结果 | 实测证据 |
| --- | --- | --- | --- |
| 1 | Python 虚拟环境可用 | ✅ DONE | `.\.venv\Scripts\python.exe -V` → `Python 3.12.10` |
| 2 | 依赖安装成功 | ✅ DONE | `pip install -r requirements.txt` 退出码 0；fastapi 0.162.2 / SQLAlchemy 2.0.57 / alembic 1.20.1 / pydantic 2.14.3 / pytest 8.4.2 |
| 3 | FastAPI 可启动 | ✅ DONE | `python -m uvicorn backend.app.main:app --port 8010` 真实起服务，非仅 TestClient |
| 4 | `/health` 返回正确状态 | ✅ DONE | `200 {"status":"ok","app":"finance-media-ai","version":"0.1.0"}` |
| 5 | 数据库可初始化 | ✅ DONE | `alembic upgrade head` 后 SQLite 中 19 张表（18 业务表 + `alembic_version`）、103 个索引 |
| 6 | Alembic 迁移可执行 | ✅ DONE | `upgrade head` / `downgrade base` 往返退出码 0；`alembic check` → `No new upgrade operations detected.` |
| 7 | 选题可写入并读取 | ✅ DONE | `tests/test_topics.py` 11 项通过（含唯一 slug、过滤、分页、404、422） |
| 8 | 知识条目可写入并检索 | ✅ DONE | `tests/test_knowledge.py` 7 项通过；检索为多字段 LIKE + 加权排序（非语义检索） |
| 9 | 来源与证据可建立关联 | ✅ DONE | `tests/test_sources_evidence.py` 6 项通过；`GET /api/evidence?knowledge_item_id=` 返回带来源摘要的证据 |
| 10 | Agent 提示词文件完整 | ✅ DONE | 9 个 `agents/<key>/PROMPT.md` 全部存在；`roster_keys_missing_prompts() == []` |
| 11 | 所有基础测试通过 | ✅ DONE | `pytest` → **76 passed, 1 warning in 8.07s** |
| 12 | `.env` 与密钥未进 Git | ✅ DONE | `.gitignore` 忽略 `.env*`（保留 `.env.example`）；提交前 `git status` 未见任何 `.env` 或凭据文件 |

## 2. 测试明细（pytest，76 项）

| 测试文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/test_topics.py` | 11 | 创建/读回、唯一 slug、分页与域过滤、更新、404、4 组非法载荷、栏目关联与缺失栏目 |
| `tests/test_knowledge.py` | 7 | 写入回读、类比与边界成对校验、多字段检索与标题优先、缺 q 的 422、更新自增版本并写 `last_verified_at`、关系创建/查询/自环拒绝/重复冲突 |
| `tests/test_sources_evidence.py` | 6 | `content_hash` 服务端计算、来源类型过滤、证据关联与来源摘要、目标二选一校验、缺失来源 422、查询缺目标 422 |
| `tests/test_research_reports.py` | 4 | findings/limitations 落库、`limitations` 空白拒绝、缺失选题 422、404 |
| `tests/test_models_constraints.py` | 12 | 18 张表登记、命名约定、priority CHECK、slug 唯一、自环 CHECK、证据目标 CHECK、**外键真实生效**、limitations NOT NULL、约束与索引可被 inspector 看到、时间戳自动填充、字段回读 |
| `tests/test_migrations.py` | 5 | 真实 Alembic `upgrade head` 建 18 表、revision 与脚本 head 一致、`downgrade base` 清表、重复 upgrade 幂等、离线 SQL 生成 |
| `tests/test_agent_contracts.py` | 10 | 九角色名册、提示词存在与结构、协议文档字段、信封 10 字段序列化、必填校验、调度落库（`status=pending`、`output_payload` 为空）、自动生成 task_id、`UnavailableDispatcher` 503 |
| `tests/test_config.py` | 11 | 四份 YAML 可解析、视频规格 1080×1920/30fps/9:16、集成开关默认关闭、品牌禁自动发布与类比边界策略、禁止投资建议、来源可信度与独立性规则、路径配置、`.env.example` 无真实密钥、`.gitignore` 规则、四模板结构（占比和为 1）、12 项质检清单 |
| `tests/test_health.py` | 6 | 9 条必需路径 + `/docs` + `/openapi.json` |
| `tests/test_system_status.py` | 4 | features 全 false、`table_count >= 18`、URL 脱敏、`agents.discovered == 9` |

运行命令与输出：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# 76 passed, 1 warning in 8.07s
```

唯一告警：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated;
install httpx2 instead`（FastAPI 0.162 + httpx 0.28.1 的已知弃用提示，不影响结果）。

## 3. 功能状态总表

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 目录结构（config/backend/agents/knowledge/content/video_engine/data/prompts/docs/tests） | **DONE** | 全部建立，空目录放 `.gitkeep` |
| SQLAlchemy 模型（18 表） | **DONE** | 含 CHECK/UNIQUE/FK，实测约束生效 |
| Alembic 初始迁移 | **DONE** | revision `de8148733a9b`，往返可执行 |
| 服务层（Topic/Knowledge/Source/Evidence/Research/Agent/System） | **DONE** | 业务校验与事务边界在服务层 |
| 统一错误处理 | **DONE** | 7 类错误码 + 统一响应体 |
| `GET /health`、`GET /api/system/status` | **DONE** | status 真探测数据库并读 `alembic_version` |
| 选题接口（创建/列表/详情/更新/栏目） | **DONE** | 5 条路径 |
| 知识接口（创建/列表/详情/更新/检索/关系） | **DONE** | 检索是 LIKE 加权，**不是**向量检索 |
| 来源与证据接口 | **DONE** | `content_hash` 服务端计算，证据二选一目标 |
| 研究报告接口 | **DONE** | findings JSON + limitations 硬校验 |
| 九个 Agent 提示词与统一协议 | **DONE** | `agents/PROTOCOL.md` + 9 个 `PROMPT.md` |
| Agent 调度接口 | **PARTIAL** | 只有 `RecordingDispatcher`（落库不执行）；真实执行器、模型 API、搜索工具**未接入** |
| 内容工作流文档与目录 | **PARTIAL** | 目录/命名/状态机/模板齐备；脚本写入、审批、分镜、素材接口**未实现** |
| 视频生产 | **PARTIAL** | 四模板 + 12 项质检清单已定义；渲染、配音、字幕、合成**未实现**（FFmpeg/Matplotlib 未安装） |
| 审核记录 | **PARTIAL** | `review_records` 表已建并有约束；无写入接口 |
| 发布记录 | **PARTIAL** | `publications` 表已建（含平台唯一约束）；无发布接口，无平台对接 |
| 知识库 Markdown 同步 | **NOT IMPLEMENTED** | `knowledge/` 只有规范与空目录，无导入/同步脚本 |
| 语义检索 / 向量库 | **NOT IMPLEMENTED** | 明确不做（约束：不引入独立向量库） |
| 鉴权 / 多用户 / 限流 | **NOT IMPLEMENTED** | 本地单机服务，禁止直接暴露公网 |
| 云部署 / CI | **NOT IMPLEMENTED** | 第一阶段不购买云资源；无 CI 配置 |

## 4. 已知问题与风险

1. **本机主目录是 Git 仓库**（`C:\Users\25803\.git`，origin 指向另一项目）。项目内已
   `git init` 隔离，`git rev-parse --show-toplevel` 为 `C:/Users/25803/finance-media-ai`；
   但任何 `cd` 到父目录执行 git 命令都会命中那一个仓库——操作前必须确认 toplevel。
2. **远程历史与本项目无关，已用 merge 整合**：远程 `https://github.com/AckNove/finance-media-ai.git`
   在接入前已有一次 `Initial commit`（`8632e0c`，内容仅 `README.md` 一行标题）。本地历史与之
   无共同祖先，因此采用 `git merge --allow-unrelated-histories` 合并（`README.md` 冲突采用本地
   完整版本，远程内容仅为同名 H1，无信息丢失），**全程未使用 `--force`**。推送结果：
   `8632e0c..c8c3caa main -> main`（快进推送）。
3. SQLite 下 `unique=True, index=True` 落成**唯一索引**而非表级 UNIQUE 约束，
   inspector 报告的形态与 PostgreSQL 不同（已在测试中按方言实际情况断言）。
4. `KnowledgeService.search()` 为全表 `LIKE`，数据量大时性能会退化。
5. 时间字段用 `DateTime(timezone=True)`，SQLite 实际存字符串；迁移到 PostgreSQL
   后时区语义会变化，需回归测试。
6. 视频模板与质检清单**没有代码消费**，属于规范文件；不要误认为渲染已就绪。
7. 无鉴权：任何能访问端口的人都能写数据，仅限本机使用。

## 5. 下一步建议（第二阶段）

1. **脚本与审批链路**：`scripts` 写入/更新/审批接口 + `review_records` 写入接口，
   把工作流 #7/#8 从"表已建"推进到可用。
2. **真实 Agent 执行器**：实现 `LLMTaskDispatcher`（DeepSeek/OpenAI 兼容），
   带输出结构校验与失败重试，先只做 `financial_researcher` + `fact_checker` 两个角色。
3. **来源抓取**：受限白名单抓取 + `content_hash` 去重，把 `sources` 从手工录入变成半自动。
4. **知识库导入**：`knowledge/**/*.md` → `knowledge_items` 的同步脚本（带 front matter 校验）。
5. **检索增强**：先做 SQLite FTS5 全文索引（不引入向量库），再评估是否需要 embedding。
6. **PostgreSQL 预演**：在本地 Docker 或临时实例上验证 `alembic upgrade head`。
7. **CI**：GitHub Actions 跑 `pytest`（Windows + Ubuntu 各一次）。
