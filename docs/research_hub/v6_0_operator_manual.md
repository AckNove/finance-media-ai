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

`start.ps1` **默认连后台执行器一起起**（`python -m backend.scripts.run_worker --forever`）。

为什么默认带：没有执行器时，你在界面里建的研究任务会一直停在「已创建」——不是系统坏了，
是没人去认领子任务。真实故障就发生过一次，所以现在一键启动把两件事都做了。

- 只想看看页面、不想让它自动跑任务：`scripts\start.ps1 -NoWorker`
- 执行器日志：`logs\worker.out.log`（错误 `logs\worker.err.log`）；进程号与启动时间：`logs\worker.pid`
- `stop.ps1` 会连执行器一起停，并且**只停它自己启动的那个**（pid 会复用，所以 pid 文件里记了启动时间，
  对不上就跳过，要强停加 `-Force`）
- 执行器花钱仍受预算闸门约束（单次/任务/当日三道），不会因为"自动跑"就绕过成本控制

打开 <http://127.0.0.1:8000/app/> 就是工作台（20 个页面，全部走真实接口）。

- 端口被占用：`scripts\start.ps1 -Port 8011`
- 想用别的数据库：`-Database sqlite:///C:/Users/25803/AppData/Local/Temp/demo.db`
  （`-Database` 两种写法都收：写 `sqlite:///...` 这种 URL，或者直接写 `data/other.db` 这种路径；
  库文件不存在时脚本会先 `alembic upgrade head` 把表建好。**指向 `data/finance_media.db` 会被拒绝**，
  那是只读的开发库）
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

这三处不用一个个点开——一条命令替你看完：

```powershell
.venv\Scripts\python.exe -m backend.scripts.daily_check          # 人看的版本
.venv\Scripts\python.exe -m backend.scripts.daily_check --json   # 机器读的版本
```

它会汇总：任务各状态与暂停/失败、窗口内失败的子任务、待人工确认的核查（`content.script` 就是等这个）、
被撤回确认而失效的视频任务、今天与近 N 天的真实花费（来自 `model_calls`，不是估算）、最近备份有多旧、
**已经做好但还没标已发布的排期**（发出去之后要回来填链接，系统不代发也不会替你标）、磁盘余量。
**只读**，不改任何东西；退出码 `0` 干净 / `1` 有需要处理的事项 / `3` 连不上库。
对着还没迁移到当前版本的老库它会如实说"这几块查不了"，而不是抛异常。

不想开命令行时，**工作台首页第一张卡就是同一份体检**（`GET /api/system/daily`）：
标题写着"今天的体检 N 件要处理"，下面逐条列待办，右边三个按钮直接跳到花费明细 / 任务 / 语义核查。
卡片和命令行**共用同一份判断逻辑**（`backend/app/services/daily_service.py`），数字不会打架。

### 6.1 每周一次：账本自查

需求追踪矩阵（`docs/research_hub/v6_0_requirements_matrix.md`）是这个项目"做完了什么"的唯一台账，
它也可能写飘（引用的文件被改名、状态写成六种以外的词、表头数字忘了跟着代码改）。一条命令查一遍：

```powershell
.venv\Scripts\python.exe -m backend.scripts.matrix_lint          # 人看的版本
.venv\Scripts\python.exe -m backend.scripts.matrix_lint --json   # 机器读的版本
```

它查四件事：状态只许用六种取值、标 `DONE` 的行引用的文件/函数/接口**必须真的在仓库里存在**、
表头的规模数字（张表 / API 操作 / 页面 / 工具）与代码实测一致、仓库路径都还在。
退出码 `0` 没有硬问题 / `1` 有硬问题 / `2` 读不到矩阵。

**别把它的绿灯当验收通过**：它只验证"这东西确实在仓库里"，证明不了功能真能用——那是测试与真机验收的事。

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
# 备份写完会**当场验收一遍**，结论写到同一个目录的 <备份名>.verify.json
.venv\Scripts\python.exe -m backend.scripts.backup_db --database-url sqlite:///data/finance_media.db --keep 20

# 看看有哪些备份
.venv\Scripts\python.exe -m backend.scripts.backup_db --list

# 单独验一份备份能不能恢复（只读打开、完整性/外键检查、和它自己的源库比表与行数）
# 退出码：0 能恢复 / 1 有问题 / 2 目录里没有备份
.venv\Scripts\python.exe -m backend.scripts.backup_verify --latest
.venv\Scripts\python.exe -m backend.scripts.backup_verify --backup backups\xxx.db --json

# 先干跑看清楚会做什么，再真恢复
.venv\Scripts\python.exe -m backend.scripts.restore_db --backup backups\xxx.db --target sqlite:///... --dry-run
```

**"有备份文件"不等于"能恢复"**：文件被半截复制、同步盘改坏、结构停在旧版本，只有真打开验一遍才知道。
`backup_verify` 默认拿备份说明里记的**源库路径**做比对对象，所以验演示库的备份不会去跟真实库比、
不会报"少了 12 张表"这种假问题。每日体检会把"有备份但没验过 / 验过但不通过"列进待办。

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

### 升级数据库之前，先演练一遍

```powershell
# 拿当前库（或指定库）只读复制一份来演练：源库一个字节都不会被写
python -m backend.scripts.upgrade_drill --source-db data/finance_media.work.db
```

演练会自己复制、自己塞老数据、自己升、自己降、再升一次，最后逐步打印 OK/FAIL。
退出码 0=能升，1=有失败步骤（别升），2=参数或源库有问题。
真实开发库 `data/finance_media.db` 是受保护对象，演练只会**只读复制**它，不会就地升级。

### 装完之后，先跑这一条

```powershell
python -m backend.scripts.bootstrap_check
```

它会自己建一个空库，从头走到"能建选题、能建任务、worker 能推一格"，
共九步，每步打 `[OK  ]`/`[FAIL]`。一路绿灯说明这台机器上这套系统从零能长出来。
它**不会花钱**（运行期间把模型密钥从进程里摘掉），也不联网
（最后一步会故意把检索开关关掉，验"关了会不会如实停下"）。

### 走路时卡在"人工闸门"怎么办

任务详情页会多出一张「卡在人工闸门」的卡，写着是哪个门（人工立项 / 事实核查 / 脚本审批）、
挡在第几步、下一步做什么；下面紧接着是「这一步产出了什么」，可以展开看产物正文。
看完觉得可以，就点「看完，放行并继续跑」（先恢复、再推三格）。

看不清产物时，命令行也能看：

```powershell
curl.exe -s "http://127.0.0.1:8000/api/research-tasks/1/artifacts"
```

它只读列举 `content/research/task-<id>*` 里最新那份目录，文本类最多显示前面一段。

### 点完「推进执行」之后看什么

任务详情页的按钮下方会出现一段总结，三句话：这一轮做完了哪几格、停在什么地方、
下一步干什么。如果又停在另一道人工闸门，下面的「卡在人工闸门」卡会同步换掉，
产物卡也跟着刷新。

命令行同一份口径：

```powershell
python -m backend.scripts.research_task run --task-id 1 --max-iterations 3
```

输出里的 `summary` 与页面上是同一段文字（`stopped_at` 为 `max_iterations` 表示
到本轮上限、不是出错）。

### 只想要一份研究报告？选 `research_only`

「研究任务」页建任务时有一栏「产线」，下拉来自 `config/pipelines.yaml`：

| 产线 | 跑到哪里停 | 适合谁 |
|---|---|---|
| `research_only` | 事实核查（9 步） | 只要报告，不打算马上做视频 |
| `research_to_script` | 人工脚本审批（11 步） | 报告之后直接接着写稿 |
| `script_to_video` | 人工发布（5 步） | 已有脚本，只做片子 |

清单接口：`GET /api/pipelines`（每条的阶段数、闸门位置、最后一格）。

> 注意「跳过的阶段」默认填了 `topic_candidate,topic_scoring,research_plan`——那是给
> 没配模型密钥的人用的。想连选题拆解一起跑，把这一栏清空。

### 已经批准过脚本，只想做片子？

不用再从报告重走一遍研究：打开「脚本编辑」，在脚本那一行点**「按这份脚本做片子」**。
它会用这份脚本排分镜（一句一镜、带单位的量做数据卡），开出的任务目录在 `content/jobs/` 下，
随后按提示跑 `python -m backend.scripts.render_job --job <任务号> --narration` 出片。

三道闸门照旧：脚本必须**已批准**；脚本挂着的报告必须有**仍生效**的人工确认；分镜排不出来
只会告出原因，不会拿规则模板顶替。命令行走同一条路：

```powershell
.venv\Scripts\python.exe -m backend.scripts.research_task --help   # 研究侧参考
# 按脚本开任务（接口形式，需已启动服务）
curl.exe -X POST http://127.0.0.1:8000/api/scripts/3/video-job -H "Content-Type: application/json" -d "{}"
```

### 导出之后，包里有什么？

导出目录（`content/exports/entry-<id>/<时间戳>/`）里应该能"一眼拿全"：

| 文件 | 干什么用 |
|---|---|
| `README.md` | 一页纸：发什么、复制哪几份文案、标签几个、发完填什么 |
| `douyin.md` / `xiaohongshu.md` / `wechat.md` | 各平台成稿（标题、正文、标签） |
| `tags.txt` | 话题标签速查，每平台一行，复制即用 |
| `film/final.mp4` | 片子本身（同盘是硬链接，不额外占空间；跨盘会复制） |
| `publish_checklist.md` | 片子规格、技术质检结论、发完必须回填的三件事 |
| `meta.json` | 这次导出的机器可读记录 |

标签要**单独填**（内容日历的「标签」列，或表单里的输入框）：以前是从备注里按空格切的，
中文备注没有空格，整段会变成一个标签。标签为空时导出不会替你编，只会在清单里提醒你补。
## 分镜页和素材页怎么互相跳

核对「这一镜用的图能不能商用」时，不用再记号码切页面：

- 素材页「用于镜头」列：点镜头号 → 分镜页，那一行会高亮；
- 分镜页「asset_id」列：点素材号 → 素材页，那一条会高亮；
- 跳过去如果**没找到**，页面会直说「这份分镜里没有镜头 X」或「素材清单里没有 X」——
  多半是分镜引用了没登记的素材，先跑一遍渲染前预检。
## 任务详情里的「这条任务的预算」怎么看

建任务时填了 `budget_max_calls` 或 `budget_max_cost_cny`，详情页就会多一张预算卡：

- **已用 / 上限**：调用次数、token、金额三项，没设上限的写「不限」；
- **剩余**：还能花多少（最多显示到 0，不会出现负数）；
- **撞线之后**：标签变红、给出原因码（`task_calls_exceeded` / `task_cost_exceeded`），
  并写明下一次模型调用会被预算闸门拒绝——**不发请求、不记账**。

想继续就新建一条任务（预算重算），或者先确认这件事还值不值得再花钱。
## 成本页怎么看「钱花在哪」

- **按模型 / 按工具**：谁在收钱（模型贵还是工具贵）。
- **按阶段**：`research.analyze`（分析）、`research.synthesize`（综述）、`content.script`（写稿）
  这些阶段各花了多少——**想省钱先看这张表**，通常是分析与综述占大头。
- **按任务**：哪条研究烧掉了多少。`未绑定任务（脚本 / 单次调用）` 那条是你手动跑的探针、
  单次冒烟留下的，不是系统漏账。
- **按天 / 运行历史**：趋势与失败情况。

两张明细表都按花费降序；「按任务」最多列 10 条。
## 模型设置页：这个模型现在能不能用

页面顶部那张卡把「调不动」的原因直接写出来，从上到下的判断顺序是：

1. `feature_disabled` —— 总开关 `features.model` 关了（所有模型都停）；
2. `provider_disabled` —— 服务商没启用；
3. `missing_api_key` / `missing_base_url` —— 缺密钥或接口地址；
4. `model_disabled` —— 这个模型没启用；
5. `pricing_missing` —— 价目没配（未核价不许调用，也不按 0 元记账）；
6. `ready` —— 能用。

每行还带「上次调用」的状态。**这张卡不发任何外部请求**，所以它证明不了服务商此刻
是否可用——只证明配置齐不齐。改完配置刷新本页即可。

### 系统设置页：「这台机器够不够用」

打开【系统设置】，最上面那张卡就是体检结果（和 `python -m backend.scripts.doctor` 是同一份）。

1. 结论标签：`正常` / `注意` / `有问题`。**只有「有问题」会拦住功能**；「注意」通常意味着某项能力会降级。
2. 每个「注意 / 有问题」的检查项下面都有一行 `→` 补救命令，照抄执行即可。
3. 换机器部署时先看这张卡：依赖缺不缺、FFmpeg 在不在、字体在不在、库结构对不对、磁盘够不够。
4. 卡片底部写着这次体检读的是**哪个库、哪个目录**——别在错误的库上看结论。

### 服务商设置页：缺的是哪个变量、写在哪儿、写完怎么验

打开【服务商设置】，第一张卡「怎么配」按服务商一行写着该做什么：

1. **密钥放哪了**：`进程里有` / `.env 里有，但没生效` / `还没配` 三态。
   看到「`.env` 里有但没生效」就是**忘了重启服务**——`.env` 只在启动时读一次。
2. **按顺序做**：先改 `config/models.yaml` 的 `enabled`（页面不改配置）→ 在 `.env` 里加一行
   `KEY=你的密钥` → 重启 → 跑验证命令。
3. **验证**：卡片给出 `python -m backend.scripts.model_smoke --provider … --model …`。
   它会**真的发一次最小请求**、花几分钱，并把这次调用记进 `model_calls`——这是唯一能证明
   「密钥真的有效」的办法。
4. 这一页只显示变量在不在，**不显示变量值**，也不替你发请求。

### 采纳一条选题建议时，来源会跟着走

「选题」页下方的建议卡片上有「采纳为选题」。现在点它会把三样东西一起带进新选题：

1. **来源清单**：写进选题描述（`S1《…》 发布方 日期 链接`），研究时能直接回原文核对；
2. **合集**：标题命中合集就自动挂成主合集。如果提示「标题命中合集 X，但库里还没有这个栏目」，
   说明栏目还没建——跑一次 `python -m backend.scripts.seed_collections`，或在这一页手动建；
3. **分数与理由**：写进描述并注明「采纳时页面显示」「不是事实判断」，方便几天后回看当初的依据。

两个常见拒绝：

- 「已经有一个很像的选题了：#N《…》」——标题相似度过高（0.82 以上）就不重复建，
  去「选题列表」找那条，别让同一个故事分叉成三条研究任务；
- 「这些来源在当前库里查不到」——多半是换了数据库，点「重新计算」再挑一条。


## 从选题直接建研究任务（不用到「研究任务」页重敲）

1. 「选题」页 → 选题列表 → 点那行的「建研究任务」。
2. 默认产线是 `research_only`（先拿报告，要不要写稿再决定）；任务号、格数、带过去几条来源都会写在提示里。
3. 同一个选题**只能有一条没结束的任务**：再点会提示"已经有一个没结束的任务 #N"，并给链接去那条；
   想换角度就新建一条选题。
4. 建完不会自动往前跑：到任务详情点「推进执行」。
5. 选题状态不会自动跟着变（失败或停下时没人改回来，会留下假状态）——看任务状态为准。


## 整条链路：这份报告现在走到哪一步

「研究报告」页每行点「整条链路」，会列出七段：研究任务 → 报告 → 语义核查与人工确认 → 脚本 →
成片 → 排期 → 导出交付包。

- **做完了**：这一段齐了。
- **轮到这一步**：第一段没做完的就是它，点右边「去这一步」。
- **卡住**：有人在等（人工确认/人工闸门），或者有硬问题（任务失败、片子作废）——明细里写着原因。
- **还没有**：这一段根本没开始。

两条要注意的：
1. 「视频成片」只认**标题对得上**的任务目录：同号任务是别的库的（`report_id` 不跨库唯一）时，
   明细里会写「另有 N 个同号任务是别的库的」，**别把别人的片子当自己的**。
2. 这条链只回答"走到哪一步"，不判断内容好坏；片子好不好看仍然得人看。

### 任务详情里的「时间都花在哪」怎么看

- **干活**：每一格实际执行的时间加起来；重试过的格子会把几次加起来并标「跑了几次」。
- **等人**：停在人工闸门上等你确认的时间；还停着就一路记到「现在」。
- **模型调用**：所有模型调用的耗时合计。**它发生在「干活」里面**，所以不参与加减——
  拿「干活 + 等人」去和总时长对账时，别把这块再加一遍。
- **没算进任何一块**：任务创建到第一格开始之间的空档；有这段时卡片会写明秒数。
- 只有开始没有结束的格子标「还在跑」，耗时按到现在算，**可能偏大**——常见原因是上次进程被杀。

一条命令看同样的数字：`GET /api/research-tasks/{task_id}/timing`。
