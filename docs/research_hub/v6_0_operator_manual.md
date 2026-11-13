# V6.0 操作手册（本机自用版）

> 这份手册只讲**怎么把它跑起来、怎么用**。为什么这么设计、哪个模块怎么实现的，写在
> `v6_0_delivery_report.md` 与 `v6_0_requirements_matrix.md` 里。
> 所有命令都在仓库根目录执行（`C:\Users\25803\finance-media-ai`）。

---

## 0. 一句话说清这套系统能干什么

你给一个研究主题，它会：**规划要查什么 → 真去搜、真去抓 → 解析入库 → 用模型写出有来源的结论 →
逐条做语义核查 → 等你人工确认 → 确认过的结论才能进脚本/分镜/成片 → 最后给你一条带技术质检和内容质检的竖屏视频**，
全过程每一步都有记录、都能查、都能重跑。

它**不会**替你做价值判断（结论对不对、片子好不好看），也**不会**自动发布到任何平台。

---

## 1. 第一次跑起来（10 分钟）

### 1.1 前置检查

```powershell
cd C:\Users\25803\finance-media-ai

# 环境体检：python / 依赖 / ffmpeg / 中文字体 / 浏览器 / 端口 / 磁盘 / 数据库 / 配置 / 模型
.venv\Scripts\python.exe -m backend.scripts.doctor
```

看到 `bad` 的一律先修；`warn` 里最常见的是"端口被占用"（说明服务已经在跑）与"没有配置模型密钥"。

### 1.2 配模型密钥（只用环境变量）

仓库根的 `.env`（**已被 gitignore，永远不会进版本库**）：

```
DEEPSEEK_API_KEY=sk-...
```

- 密钥只在服务端读，不会出现在任何接口响应、日志、`model_calls` 表里（有测试盯着）。
- 想**一键停掉所有模型调用**（不花钱）：把 `config/settings.yaml` 的 `features.model` 改成 `false`。
  关掉后网关在发请求前就返回 `feature_disabled`，一分钱不花。

### 1.3 起服务

```powershell
# 方式一：脚本（推荐，会先查端口、再等健康检查通过）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# 方式二：手动
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/app/> 就是工作台（20 个页面，全部走真实接口）。

- 端口被占用：`scripts\start.ps1 -Port 8011`
- 想用别的数据库：`-Database sqlite:///C:/Users/25803/AppData/Local/Temp/demo.db`
- 停服务：`powershell -ExecutionPolicy Bypass -File scripts\stop.ps1`（只杀"确实是本项目 uvicorn"的进程）
- 崩了自动拉起 + 写日志：`scripts\watchdog.ps1`（默认最多重启 5 次，日志在 `logs\watchdog-<日期>.log`）

### 1.4 演示数据（不想花钱时先看界面）

用一条已经跑完的真实研究记录当演示库，**密钥留空**，随便点都不会产生费用：

```powershell
set DATABASE_URL=sqlite:///%TEMP%/fmai_demo/demo.db
set APP_ENV=development
set FM_SKIP_DOTENV=1
set DEEPSEEK_API_KEY=
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

---

## 2. 跑一条真实研究（会花钱，约 ¥0.04~¥0.16/条）

### 2.1 建任务

工作台「研究任务」页 → 新建，或者：

```powershell
curl -s -X POST http://127.0.0.1:8000/api/research-tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"title\":\"储能行业为什么打价格战\",\"objective\":\"说清供需、成本与价格的因果\",\"budget_max_calls\":8,\"budget_max_cost_cny\":0.5}"
```

预算两个口子都要设：`budget_max_calls`（调用次数）与 `budget_max_cost_cny`（金额）。
**价目表没核过的模型，网关直接拒绝请求**——不会"先花着"。

### 2.2 推进执行

研究是**一步步推进**的，不是点一下就全跑完：

```powershell
# 每个进程推进若干格；跑完自动退出
.venv\Scripts\python.exe -m backend.scripts.run_worker --task-id 1 --max-iterations 3

# 想一直跑（守着新任务）：不加 --task-id，加 --max-idle-minutes 10
```

任务会在**两处人工闸门**停下来（这是设计，不是卡住）：

| 闸门 | 停在哪 | 你要做什么 |
| --- | --- | --- |
| 立项闸门 | `topic.discover` 之后 | 看一眼选题值不值得查，然后放行 |
| 脚本闸门 / 事实闸门 | 出稿前 | 看一眼结论与核查结果 |

放行：`POST /api/research-tasks/1/resume`（工作台「研究任务」页也有按钮）。

### 2.3 看结果

| 想看什么 | 页面 | 接口 |
| --- | --- | --- |
| 规划了什么、用了哪些检索词、有没有降级成规则 | 研究任务 → 详情 | `GET /api/research-tasks/{id}`（`planning` 字段） |
| 抓到的来源 | 资料来源 | `GET /api/sources` |
| 证据（原句 + 出处） | 证据库 | `GET /api/evidence?research_report_id=1` |
| 报告正文 | 研究报告 | `GET /api/research-reports/{id}` |
| **逐条核查结论** | 语义核查 | `GET /api/research-reports/{id}/checks` |
| 花了多少钱 | 成本 | `GET /api/system/costs` |

---

## 3. 人工确认（这是成稿的唯一入口）

系统里**模型判定**与**人工确认**是两条独立状态：

- 模型给的是 `supported / partially_supported / contradicted / insufficient_evidence / unverifiable`；
- 人工给的是 `pending / confirmed / rejected / needs_more_evidence`。

**只有 `confirmed` 的结论才能进成稿。** 这不是建议，是硬闸门：三个出口都会被拦

| 出口 | 拦截点 | 不满足时 |
| --- | --- | --- |
| 建视频任务 | `POST /api/research-reports/{id}/video-job` | 409 + `reason=no_checks/not_confirmed` |
| 交接稿正文 | `video/handoff.py` 生成 `research/report.md` | 只有"可进成稿的结论"一节进分镜 |
| 建脚本 | `POST /api/scripts`（带 `research_report_id`） | 409 + 允许项清单 |

怎么确认：打开「语义核查」页 `#/checks?report=1`，逐条看支持/反对片段，点「确认」并写理由
（`confirmed` 必须写理由，否则 422）。**确认后再撤回，已经建好的视频任务会被标记失效**（`INVALIDATED.md`），
产物不删、但不许再用。

---

## 4. 出一条视频（竖屏 9:16）

```powershell
# 一条命令：报告 → 分镜 → 素材 → 时间轴 → 渲染 → 技术质检
.venv\Scripts\python.exe -m backend.scripts.produce_video --report-id 1 --job-id my_film

# 只重渲染（已有任务目录）
.venv\Scripts\python.exe -m backend.scripts.render_job --job my_film --narration --output output/final.mp4
```

产物在 `content/jobs/<job_id>/`：

```
research/        报告、证据、来源（交接过来的，只读）
storyboard/      storyboard.csv（人可改）+ OUTLINE.md
assets/          数据卡 JSON（每张卡上的数字都能追回证据）
audio/           旁白（edge-tts，中文）
output/final.mp4 成片
qc/              技术质检 + 内容质检
```

**两类质检分开，别混**：

- **技术质检**（`qc/render_run.json` 的 `qc`）：分辨率、音画等长、黑帧、冻结、静音、响度。
- **内容质检**（`python -m backend.scripts.content_qc --job my_film`）：上屏数字能不能在材料里找到、
  每镜有没有来源、字幕是不是从旁白来的、素材授权。**它不判断好不好看**——那必须人看片。

### 4.1 加背景音乐（先验授权，再混音）

默认配乐是本地合成的（`synthesize_score`，不含版权素材）。想换成人手选的曲子：

```powershell
# 1) 把音频放进 content/assets/bgm/，在 library.yaml 里登记（授权必须可商用）
# 2) 看曲库体检
.venv\Scripts\python.exe -m backend.scripts.bgm_check --list
# 3) 混音（授权不过会直接拒绝，不会生成半个文件）
.venv\Scripts\python.exe -m backend.scripts.mix_bgm --video content/jobs/my_film/output/final.mp4 `
    --track calm-01 --out content/jobs/my_film/output/final_bgm.mp4 --volume 0.3
```

授权白名单：`cc0 / public-domain / cc-by / cc-by-4.0 / cc-by-sa / royalty-free / purchased / own-work`。
`cc-by*` 必须写 `attribution`，否则拒绝——这条不是提醒，是硬拒绝。

---

## 5. 运营（内容日历）

「内容日历」页可以：排期（选题/脚本/报告都能挂上）、标已渲染、**标已发布（必须有链接，撤回留痕）**、
回填平台数据（手工一条，或整个 CSV 批量导入）。

CSV 批量导入的规矩：

```powershell
curl -s -X POST http://127.0.0.1:8000/api/calendar/1/metrics/import ^
  -H "Content-Type: application/json" ^
  -d "{\"csv_text\":\"captured_at,views,likes\n2026-11-01,12345,678\"}"
```

- 认中英列名（`captured_at/采集时间/date/日期`、`views/播放量`、`likes/点赞`…）、认千分位；
- **同一时间戳已有快照 → 跳过**（历史不覆盖），并在返回里说明；
- 坏行**带行号**返回，不猜数字；平台和排期不一致的行直接判错；
- 一次最多 500 行。

分析页的数是**最新一次快照的绝对值**，不做累加——累加会把"每天涨的粉"算成"总共的粉"。

---

## 6. 每天应该看的三个地方

1. **成本**（「成本」页 / `GET /api/system/costs`）：今天的钱、每个任务的账。任务账 = `model_calls` 之和，
   有测试盯着这个等号。
2. **失败与阻塞**（「研究任务」页的状态列）：`paused` 是等闸门，`failed` 是真出错（`failure_reason` 里写原因）。
3. **核查覆盖**（`/checks/summary`）：`pending` 越堆越多说明该做人工确认了。

---

## 7. 出问题怎么办

| 现象 | 先看 | 常见原因 |
| --- | --- | --- |
| 模型全报 `feature_disabled` | `config/settings.yaml` 的 `features.model` | 被一键停服了 |
| 报 `missing_api_key` | `.env` 里有没有 `DEEPSEEK_API_KEY` | 没配，或 `FM_SKIP_DOTENV=1` 把 .env 跳过了 |
| 报 `pricing_missing` | `config/models.yaml` 该模型价目 | 价目没核过（**这是故意的**，不许花不知道价钱的钱） |
| 任务一直 `pending` 不动 | 有没有在跑 worker | 研究不会自己跑，要有 `run_worker` 进程 |
| 页面空白 / 404 | 服务起没起、端口对不对 | `/app/` 是静态页，接口在 `/api/*` |
| `alembic check` 报有漂移 | 模型和迁移不一致 | 加迁移；**不许对真实库直接 upgrade** |
| 渲染失败 | `qc/render_run.json` 的 `error` | ffmpeg 路径、磁盘空间、素材授权 |

诊断命令：

```powershell
.venv\Scripts\python.exe -m backend.scripts.doctor --json     # 结构化体检
.venv\Scripts\python.exe -m backend.scripts.check_media_toolchain --text   # 26 项媒体能力
.venv\Scripts\python.exe -m pytest -o addopts="" -q           # 全套测试（约 4 分钟）
```

---

## 8. 备份与恢复

```powershell
# 备份（只读源库，在线安全；生成 .db + .json 说明，含两边 sha256）
.venv\Scripts\python.exe -m backend.scripts.backup_db --database-url sqlite:///data/finance_media.db --keep 20

# 看看有哪些备份
.venv\Scripts\python.exe -m backend.scripts.backup_db --list

# 先干跑看清楚会做什么，再真恢复
.venv\Scripts\python.exe -m backend.scripts.restore_db --backup backups\xxx.db --target sqlite:///... --dry-run
```

恢复是**破坏性操作**：覆盖前会先存一份 `.pre-restore-<时间戳>.db`；默认**拒绝**覆盖真实开发库
`data/finance_media.db`（要动它得显式加 `--allow-development-db`，而且这属于需要单独授权的操作）。

---

## 9. 五条不能破的规矩（系统自己也守着）

1. **真实开发库 `data/finance_media.db` 只读**：不迁移、不开 WAL、不 seed。迁移只在本机临时库演练。
2. **没核过的价目不许发请求**；预算不够直接拒；不自动充值、不自动买服务。
3. **密钥只从环境变量来**，不打印、不复制、不提交，任何响应里都不会出现。
4. **模型判定 ≠ 事实**：模型说 `supported` 也还要人去原文核；只有人工 `confirmed` 能进成稿。
5. **不自动发布**：所有平台动作都只是"记录人做过什么"。

---

## 10. 相关文档

| 文档 | 讲什么 |
| --- | --- |
| `docs/research_hub/v6_0_delivery_report.md` | 本轮交付了什么、真实验收结果、费用、安全边界核对 |
| `docs/research_hub/v6_0_requirements_matrix.md` | A–K 每条需求的实现位置与状态（DONE/PARTIAL/BLOCKED…） |
| `docs/research_hub/v6_0_api_reference.md` | 全部接口一览（**由代码生成**，别手改） |
| `docs/research_hub/v5_1_semantic_and_planning_report.md` | 语义核查与模型规划的细节 |
| `docs/research_hub/v5_0_model_integration_report.md` | 模型接入、价目核对、限流与脱敏 |
| `README.md` | 项目全景与目录结构 |
