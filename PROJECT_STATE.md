# PROJECT_STATE.md — 项目现状（唯一台账）

> 本文件的每个数字都必须能由命令复算。**上一轮报告里的数字不算证据。**
>
> 更新命令：`python -m backend.scripts.state_measure --md`
> 生成时间：2026-11-15 21:48（Asia/Shanghai），对应提交 `1e68576` + 本轮工作区

## 1. 这个项目是什么

一个**财经内容生产工作台**：从真实公开信息里做研究（选题 → 拆题 → 搜索 → 抓取 → 解析 → 证据 →
模型分析 → 语义核查 → 人工确认 → 报告），再把报告做成视频（脚本 → 分镜 → 素材 → 配音 → 渲染 →
技术 QC + 内容 QC → 人工终审 → 排期 → 导出交付包），最后把发布结果回填成台账。

它不是一个"能生成内容的 Demo"：**没确认的结论进不了成稿**，**没链接的不算已发布**，
**没证据的数字不补写**。这些是硬闸门，写在代码里（见 `DECISION_LOG.md`）。

## 2. 实测规模（`python -m backend.scripts.state_measure --md`）

| 项 | 实测 |
| --- | --- |
| ORM 表（`Base.metadata.tables`） | 31 |
| API 路径 | 68 |
| API 操作 | 84 |
| 权限清单条目（`ROUTE_CALLERS`） | 84 |
| 工作台页面（`web/app.js`） | 20 |
| 已注册工具（`TOOL_NAMES`） | 11 |
| 测试文件 | 118 |
| `docs/*.md` | 10 |
| `docs/research_hub/*.md` | 51 |
| `backend/scripts/*.py` | 38 |
| `scripts/*.ps1` | 3 |
| 已出片的视频任务 | 9 |
| `backend/app` 行数 | 41,881 |
| `web/app.js` 行数 | 2,853 |

一致性不变量（改代码后必须仍成立，`tests/test_caller_permissions.py` 与启动自检会守）：

- **API 操作数 == `ROUTE_CALLERS` 条目数**（当前都是 84）；启动自检还会逐条比对"清单声明的调用者"
  与"端点上实际挂的依赖"，漏挂或多写都拒绝启动。
- 业务表 31 张（ORM），真实开发库仍停在迁移 `de8148733a9b`（19 张表）。

## 3. 受保护的真实开发库（每轮只读复核）

| 项 | 值 |
| --- | --- |
| 文件 | `data/finance_media.db` |
| 大小 | 540,672 B |
| sha256 | `2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203` |
| 表数 / 迁移版本 | 19 / `de8148733a9b` |
| journal_mode | `delete`（**没有开 WAL**） |
| 有 `model_calls` 表 | 否 |

| 真实开发库不动 | `data/finance_media.db` 永远 19 表 / `de8148733a9b`；启动脚本拒绝在它上面跑 |
| 工作库 | `data/finance_media.work.db`（本机实测已到 `b8_calendar_tags`、32 表，启动脚本会自动升到 head） |

**规矩**：这个库只读。任何迁移、seed、回填、开 WAL 的演练一律在临时库上做，
并在报告里给出"真实库未改动"的证据（大小 + sha256 + 表数 + 迁移版本）。

## 4. 测试与质量门

| 项 | 状态 |
| --- | --- |
| 最近一次全量 | **1542 passed, 2 skipped**（2026-11-15 21:47，耗时 6:01） |
| 上一级测试（受影响模块） | 本轮 `test_db_revision.py + test_migrations.py + test_scripts_encoding.py + test_startup_checks.py + test_system_status.py` → 61 passed |
| 已知告警 | 6 条 warning：`agent_runs ↔ research_subtasks` 外键环的 `SAWarning`（设计内，见 `KNOWN_ISSUES.md` K-07） |
| 需求矩阵 | `python -m backend.scripts.matrix_lint` → 109 行需求，DONE 107 / BLOCKED 1 / NOT_IMPLEMENTED 1，无问题 |
| 启动自检 | 8 项（`config_sections` / `task_type_registry` / `agent_roster` / `tool_specs` / `skill_registry` / `route_callers` / `pricing` / `database`），error 级不过就拒绝启动 |

## 5. 环境

| 项 | 值 |
| --- | --- |
| 仓库位置 | `D:\finance-media-ai`（2026-11-15 从 C 盘整体搬来，见 `DECISION_LOG.md` D-09） |
| Python | 3.12.10（`.venv`，搬迁后未重建仍可用） |
| 视频工具 | `tools/ffmpeg`（8.1.2，独立安装，不进 PATH）+ 本机 4.2.2 |
| 视频引擎 | `tools/openkatana`（0.1.3 固定 commit，独立 venv） |
| 模型 | DeepSeek `deepseek-flash` / `deepseek-v4-pro`，价目已核（2026-11-12） |
| 密钥 | 仓库根 `.env`（已 gitignore），**只在进程启动时读一次** |

## 6. 怎么在三分钟内确认"现在是好的"

```powershell
git status --porcelain                                   # 空 = 没有未提交的活
python -m backend.scripts.state_measure --md             # 规模数字
python -m backend.scripts.matrix_lint                    # 台账自查
python -m backend.scripts.daily_check                    # 今日体检（花钱、待确认、备份）
.venv\Scripts\python.exe -m pytest tests/ -k "chain or caller" -o addopts="" -q
```

## 7. 状态文件之间的关系

- `PROJECT_STATE.md`（本文件）：**现在是什么样**，数字可复算。
- `TASK_BACKLOG.md`：**还欠什么**，按 P0–P3 排，含阻塞原因。
- `DECISION_LOG.md`：**为什么这么做**，含被否决的方案与理由。
- `KNOWN_ISSUES.md`：**哪里还疼**，含证据与是否阻断。
- `docs/dev_protocol.md`：**怎么干活**（效率协议）。
