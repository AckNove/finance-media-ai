# KNOWN_ISSUES.md — 哪里还疼

> 每条要有**证据**和**是否阻断**。解决后把状态改成"已解决"并保留原记录（别删，否则下次会重踩）。
> 最近更新：2026-11-15（提交 `8f10d14`）

## 阻断（需要外部条件才能继续）

### K-01 `contradicted` 没有真实样本（TEST_ONLY）

- **现象**：语义核查的 `contradicted` 判定只有单元测试覆盖，三次专门挑"来源口径打架"的主题真跑，
  结果都是 `partially_supported` / `insufficient_evidence`。
- **证据**：储能装机主题 `checks=4` → `partially_supported 1 / insufficient_evidence 3`；
  模型对 73.76GW vs 78.3GW 这类差异判"证据不足以断定冲突"。
- **判断**：这**不是缺陷**——模型"宁可说不知道"是想要的性格。缺的是能真正证伪的语料。
- **下一步**：`TASK_BACKLOG.md` B-04（换同指标、同口径、直接打架的主题）。

### K-02 真实素材为零（BLOCKED）

- **现象**：目前所有成片的素材都是程序生成的画板/数据卡（B 类），**零 A 类真实素材**。
- **为什么**：真实素材需要用户提供文件 + 来源/链接/许可四项信息；授权不明的图片一律不进片。
- **已备工具**：`python -m backend.scripts.register_source ...`（登记即算哈希、写进任务根
  `source_register.csv`，`commercial_use != allowed` 预检直接拒）。
- **下一步**：`TASK_BACKLOG.md` B-03。

### K-03 云渲染与自动发布（用户已明确先不做）

- **现象**：渲染只在本机跑；发布永远人工。
- **证据**：User said (m28360 前后)：「云渲染与自动发布目前先不做」。
- **影响**：成片速度受本机 CPU 限制（一条 90 秒竖屏片约 6–10 分钟）。
- **现状**：不阻断，`TASK_BACKLOG.md` B-10 记录为推迟。

### K-04 多平台账号 API（NOT_IMPLEMENTED，改人工回填）

- **现象**：没有抖音/视频号/B 站/小红书的开放平台对接。
- **替代**：发布后人工回填 CSV 进 `content_calendar` + `platform_metrics`。
- **证据**：`DECISION_LOG.md` D-06（用户选择）。

## 非阻断（知道就好，别当 bug 修）

### K-05 5 个历史任务的 `job.json` 仍记着 C 盘绝对路径

- **证据**：`content/jobs/{gate_demo_after_confirm,v5_flow_acceptance_v2,v6_accept_chain_11131719,v6_accept_company_v2,v6_accept_industry}/job.json`
  第 11–12 行的 `storyboard` / `notes` 是 `C:\Users\...`。
- **为什么留着**：渲染链路不读这两个字段（`render.py:703`、`preflight.py:179`、`content_qc.py:248`
  都是 `root / "storyboard" / "storyboard.csv"`），旧任务照样能重渲；改写历史快照等于伪造记录。
- **新任务**：已改成相对路径（`DECISION_LOG.md` D-04）。

### K-06 全仓 439 处旧绝对路径（全在 `content/`）

- **证据**：`backend/`、`web/`、`config/`、`scripts/`、`tests/` **一处都没有**；439 处都在
  `content/` 下当时生成的验收 JSON/MD 里。
- **判断**：历史快照，不改写。

### K-07 外键环告警 `SAWarning: ... cycles between tables "agent_runs, research_subtasks"`

- **现象**：每次跑迁移/`create_all` 都有一条 SAWarning，全量测试共 6 条 warning。
- **为什么这样设计**：`agent_runs.subtask_id → research_subtasks.id`（祖先任务）与
  `research_subtasks.agent_run_id → agent_runs.id`（当前执行尝试）语义不同，不是冗余。
- **为什么不修**：改成 `use_alter=True` 需要 `ALTER TABLE ADD CONSTRAINT`，SQLite 不支持 →
  `create_all` 建表时该外键会**静默丢失**，比告警更糟。
- **证据**：`tests/test_fk_cycle_semantics.py` 5 例钉住语义（删任一侧后 `PRAGMA foreign_key_check` 为空）。

### K-08 `.env` 只在进程启动时读一次

- **现象**：改完 `.env` 不重启，页面仍说"缺密钥"。
- **这是设计**：`backend/app/core/config.py` 导入时把 `.env` 灌进 `os.environ`（`FM_SKIP_DOTENV=1` 可跳过）；
  运行中反复读环境变量会让"配好了没生效"更难排查。
- **提示**：服务商页已经明写「改完 `.env` 之后必须重启服务」。

### K-09 直连 GitHub 时通时不通

- **现象**：`git push` 偶发 `Failed to connect to github.com port 443` / `Connection was reset`；
  全局 git 配置里有三条指向 `127.0.0.1:7890` 的代理键，但本机没有代理在监听。
- **应对**：推送一律带 `-c http.proxy= -c https.proxy= -c http.https://github.com.proxy=`；
  失败就挂后台每 60 秒重试（实测第 5 次成功过）。

### K-10 `Scripts\*.exe` 内嵌旧路径（搬家后）

- **现象**：`.venv\Scripts\pip.exe` 这类 console launcher 里编译进了安装时的绝对路径。
- **应对**：一律用 `.venv\Scripts\python.exe -m <module>`，不直接跑 exe。项目脚本本来就是这个形态。
- **证据**：搬迁后 `sys.prefix` 正确解析成 `D:\finance-media-ai\.venv`，`import fastapi/sqlalchemy` 正常。

### K-11 系统 ffmpeg 是 4.2.2，项目自带 8.1.2

- **现象**：PATH 上的 `ffmpeg` 是 miniconda 的 4.2.2（不支持 `-fps_mode`）；项目用 `tools/ffmpeg` 的 8.1.2。
- **坑**：OpenKatana 用 `shutil.which('ffmpeg')` 找 ffmpeg，所以调用它时必须把 `tools/ffmpeg/bin`
  **前置到子进程 PATH**，否则它拿 4.2.2 渲视频素材会失败。
- **现状**：`backend/app/video/render.py` 已按此处理；`config/settings.yaml` 里 `video.ffmpeg_dir: tools/ffmpeg/bin`。

### K-12 启动自检里 `pricing` / `database` 的 `severity` 是 warning，但 `ok=True`

- **现象**：看到 `pricing True warning` 容易误读成"有问题"。
- **含义**：`severity` 是"这条检查失败时要多重"，不是"现在失败了"。两项当前都 `ok: True`
  （价目齐全且带核对日期；库可连接、19 张表）。
- **判断**：不要因为 `severity` 字段去改报告。

### K-13 工作库升过一次版本（2026-11-15 本轮实测导致，属预期）

- **现象**：`data/finance_media.work.db` 在本轮 B-02 实跑时从 `b6_v6_operations` 升到了
  `b8_calendar_tags`（32 表，文件从 991,232 B 涨到 995,328 B）。
- **为什么会发生**：这正是本轮修的东西——原来"库存在就直接用"，导致工作库落后两个迁移也没人知道；
  现在启动脚本会先比对版本、落后就自动升级并复检。
- **影响**：只影响本机工作库（gitignore 的 `*.db`），**受保护开发库 `data/finance_media.db`
  全程未被打开写入**（sha256 `2df764e5…8203` 未变）。
- **不需要做什么**：下次启动会读到 `current`，不会重复迁移。

## K-14 选题列表里有一条编码事故留下的空壳（非阻断）

- **现象**：`topics` 里 `id=1` 那条的标题/摘要是一串乱码（`ä¸ºä»ä¹å...`），是第一次用
  PowerShell `Invoke-RestMethod` 建任务时按 Latin-1 发送造成的；对应的 `research_tasks` #1 已
  `cancelled`（`failure_reason=human_cancel`），不占额度、不会自己跑。
- **为什么留着没清**：`TopicUpdate` 只接受 `description/priority/research_status/content_status/
  football_analogy_required/estimated_duration`，**没有能改 `topics.status` 或删除选题的接口**；
  直接写库又违反"真实数据只经接口改"的口径。
- **绕过办法**：在页面上忽略它即可；新建选题走 `#/topics` 正常。
- **将来怎么修**：给 `/api/topics/{id}` 加 `status` 字段允许置 `archived`（需要一次迁移评审），
  或在选题页加"归档"按钮走同一个 PATCH。
- **教训**：Windows PowerShell 5.1 发 JSON 必须自己 `[System.Text.Encoding]::UTF8.GetBytes($json)`，
  否则中文按 Latin-1 出去就回不来了；python 侧一律用 urllib/requests 发 UTF-8 字节。

---

## K-15 报告 #2 有 3 条结论的数字来自模型而不是证据（非阻断，但**绝不能进稿**）

- **现象**：电力选题（报告 #2）重判后 6 条结论里，`insufficient_evidence` 3 条，理由都是"给定证据里没有
  这个数字"——煤电固定成本回收比例 ≥50%、容量机制利润占比 123%/76%/63%、绿电直供投资超 34 亿元。
- **含义**：这些数字是模型凭记忆写出来的，**不是从抓到的网页里读出来的**。语义核查如实标了"证据不足"，
  这正是它该做的事。
- **处置**：成稿闸门只放行 `human_review_status=confirmed` 的结论，所以它们默认进不了脚本；若人工硬要确认，
  必须自己回原文核对过——页面上每条都列了来源编号。
- **要做到"这三个数字也能用"**：需要重新检索并抓到明确写出这些数字的页面（新的研究任务），不是改核查逻辑。
