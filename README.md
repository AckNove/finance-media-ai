# finance-media-ai

以个人 IP 为核心的**金融知识自媒体研究与内容生产平台**（第一阶段：基础设施）。

内容方向：基础金融产品（股/债/基金）、衍生品与结构性产品、金融机构与资本运作、
产业链与企业经营、足球商业与转会金融、金融事件与真实商业案例。
风格：专业金融讲解为主，足球作为教学隐喻而非内容边界，类比必须说明**哪里成立、哪里不成立**。

> 当前阶段状态：数据库、服务层、API、Agent 角色与协议、内容/视频流程骨架已就绪并测试通过；
> **未接入任何模型 API、搜索工具、渲染引擎**。详见 `docs/phase1_acceptance.md` 的功能状态表。

## 快速开始（Windows / PowerShell）

```powershell
# 1. 虚拟环境（Python 3.12）
py -3.12 -m venv .venv

# 2. 依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 环境变量（不放真实密钥）
Copy-Item .env.example .env

# 4. 建库（Alembic 迁移）
.\.venv\Scripts\python.exe -m alembic upgrade head

# 5. 启动
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
#    http://127.0.0.1:8000/docs

# 6. 测试
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

## 目录结构

```
backend/app/{api,core,models,schemas,services,database}   服务代码
backend/migrations/                 Alembic 迁移（初始 revision de8148733a9b）
agents/                            九角色提示词 + roster.yaml + PROTOCOL.md
knowledge/{8 个子域}/               知识条目 Markdown（规范见 knowledge/README.md）
content/{topics,scripts,storyboards,assets,published}
video_engine/{templates,renderers,subtitles,audio,quality_control}
prompts/{system,research,writing,review,production}
config/{settings,brand,content_rules,research_sources}.yaml
data/{raw,processed,exports}        本地数据（大文件不入库）
docs/                              架构、数据模型、Agent 手册、工作流、验收报告
tests/                             76 个 pytest 用例
```

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/architecture.md` | 技术选型、分层、配置优先级、错误模型、扩展点与限制 |
| `docs/database_schema.md` | 18 张表、ER 图、字段设计、金融六域取舍、PostgreSQL 迁移注意 |
| `docs/agent_manual.md` | 九角色、统一协议、调度接口、新增角色步骤 |
| `docs/workflow.md` | 十四步内容/视频工作流、状态机、四个模板、12 项质检 |
| `docs/phase1_acceptance.md` | 12 项验收实测结果 + 功能状态（DONE/PARTIAL/NOT IMPLEMENTED） |
| `agents/PROTOCOL.md` | Agent 统一输入输出协议（10 字段） |

## 安全约定

- 密钥只走环境变量；`.env` 已被 `.gitignore` 忽略，仓库只提交 `.env.example`（空占位）。
- `config/*.yaml` 不存放任何真实 API Key。
- 系统**不会自动发布**任何内容（`config/brand.yaml` 中 `auto_publish: false`），
  也不输出面向个人的具体投资买卖建议（`config/content_rules.yaml` 硬约束）。
- 第一阶段是本地单机服务：无鉴权、无限流，不要暴露到公网。

## 尚未实现（避免误解）

视频渲染与合成、配音与字幕生成、图表生成（matplotlib 未安装）、脚本/审核/发布接口、
知识库 Markdown 自动同步、语义检索与向量库、Agent 真实执行器（模型 API 与搜索工具）、
鉴权、CI 与云部署。逐项状态见 `docs/phase1_acceptance.md`。
