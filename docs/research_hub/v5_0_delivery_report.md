# V5.0 交付报告：统一自媒体生产平台

> 本轮目标（用户 V5.0 总指令）：接管已有成果、不推倒重来，把 finance-media-ai 推到
> **可正式投入日常自媒体生产**的状态；持续开发，遇安全/授权/凭据/费用阻塞才停。
>
> 本报告只写**真实跑过的**东西。没跑通的一律标 BLOCKED 或 PARTIAL，不写"已完成"。

- 起始基线：`e8bdf0b0192d9bbc9f674d72ce2e8f18485a43a8`（Codex 最后一笔），376 passed
- 本轮终态：见 §I（Git）与 §E（测试 607 passed）
- 报告日期：2026-11-12

---

## A. 接管审计

### A.1 仓库与基线实测

| 项目 | 实测值 |
| --- | --- |
| 分支 / HEAD | `main` / `e8bdf0b`（== `origin/main`，`rev-list --left-right --count` = `0 0`） |
| 工作区 | `git status --porcelain` 0 条 |
| 测试基线 | `pytest -o addopts="" -q` → **376 passed, 1 skipped, 1 warning** |
| 后端体量 | `backend/app` 约 6,600 行（本轮后 ≈ 14,700 行，见 §C） |
| 迁移 | 初始 `de8148733a9b` + B1/B2/B3 三批（28 张业务表 / 物理 29 张） |
| 依赖 | fastapi 0.162.2、uvicorn 0.54.0、httpx 0.28.1、SQLAlchemy 2.0.57、alembic 1.20.1、pydantic 2.14.3、markitdown 0.1.9、pdfplumber 0.11.10、openpyxl 3.1.5、beautifulsoup4 4.15.0、matplotlib 3.11.2；**无 openai SDK**（网关直接用 httpx 走 OpenAI 兼容协议） |
| 密钥 | 仓库**无 `.env`**；`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` 环境变量均不存在 |
| 本机模型服务 | 127.0.0.1 的 11434 / 1234 / 8080 / 8000 / 5000 全部不可用 |

### A.2 Codex 成果分类（不改写、不丢弃）

| 分类 | 内容 |
| --- | --- |
| **真实功能（已验证）** | 配置分层与 `extra="forbid"` schema、CallerContext 权限闸门与 37 条权限清单、B1/B2/B3 迁移与回填、启动自检（含路由挂载四查）、请求追踪中间件、MarkItDown 解析适配器、分镜/时间轴/素材清单校验、OpenKatana+FFmpeg 媒体适配、42.5 秒视频与 17 项 QC、Skill Registry（sha256 固定 + 工具门禁） |
| **只有接口/骨架（本轮补全）** | `capabilities/tools.py` 十个工具全部 `implemented=False` → 本轮实现 5 个；`execution/` 只有 task_types 与 routing → 本轮补 runtime/orchestrator/executor/worker；模型、搜索、TTS 三条能力线此前只有契约 → 本轮全部落地 |
| **只有文档** | `video_engine/` 只有 YAML 模板与 QC 规则，无渲染代码（本轮保持原状，渲染仍走 `content/jobs/<id>/script/build_*.py` + OpenKatana 适配层） |
| **有缺陷（本轮修）** | ①`model_calls.cost_estimate` 等 7 个成本列用 `NUMERIC(20,2)`，单次调用 0.0002 元被舍成 0.00，导致"当日已花"恒为 0、日上限闸门形同不存在；②搜索把引擎跳转链接当真实地址（搜狗 `/link?url=`）导致全部抓取失败；③反爬拦截页 HTTP 200 被当成"抓到正文"；④`runtime.complete` 把输出统一包成 `{"value": ...}` 造成下游隐性耦合；⑤`extract_article` 先删 `<script>` 再读 JSON-LD，作者/发布时间永远取不到 |

---

## B. 20 个模块的落地状态

| # | 模块 | 状态 | 代码位置 / 证据 |
| --- | --- | --- | --- |
| 1 | 数据采集（搜索+抓取） | ✅ 真实运行 | `capabilities/search/`（safety/fetcher/engine）；真抓到新浪财经等正文，见 §D.1 |
| 2 | 模型网关 | ⚠️ 代码完成、**无密钥 BLOCKED** | `capabilities/model/`（providers/tokens/gateway/tool）；缺 key 返回 `not_configured`，价目未知直接拒绝 |
| 3 | Skill 管理 | ✅ 复用并增强 | `skills/registry.py` + `skills/registry.yaml`（6 个技能，sha256 固定，按 task_type 装载） |
| 4 | Agent 执行 | ✅ 真实运行 | `execution/executor.py`（collect/extract/analyze/synthesize/verify 五类处理器 + agent_runs 落库） |
| 5 | 任务调度 | ✅ 真实运行 | `execution/runtime.py`（队列/租约/心跳/重试/暂停/恢复/取消/人工闸门）+ `worker.py` |
| 6 | 研究工作台 | ✅ 可用 | `/api/research-tasks` 8 个操作 + `web/app.js` 的任务页与事件流 |
| 7 | 行业与公司研究引擎 | ⚠️ PARTIAL | 底稿（digest）与证据链已真实产出；行业/公司专属分析需要模型，当前 BLOCKED |
| 8 | 数据分析 | ⚠️ PARTIAL | 结构化抽取有页码/行号/工作表定位；财务比率等衍生分析依赖模型 |
| 9 | 证据与事实核查 | ✅ 真实运行 | `research/persistence.py`（来源/证据/报告落库）+ `research/verify.py`（逐字核对） |
| 10 | 内容策划 | ⚠️ BLOCKED | 页面已就绪并明写"需要模型才算策划"，不产出模板化假策划 |
| 11 | 视频脚本 | ⚠️ PARTIAL | AK 三技能 + 提示词已注册；生成需要模型 |
| 12 | 分镜与镜头 | ✅ 复用 V2.2 | `media/storyboard.py`（16 列契约、连续性/整帧/词表校验） |
| 13 | 素材管理 | ✅ 复用 V2.2 | `media/manifest.py` + `core/job_paths.py`（授权不明拒绝渲染） |
| 14 | 语音字幕 | ✅ 真实运行 | `capabilities/tts/`（Edge TTS 词级时间 + SAPI 兜底 + SRT/ASS 生成） |
| 15 | 动态图表动画 | ✅ 复用 V2.2 | OpenKatana 组合文档渲染（柱状图生长、流程链、镜头推入） |
| 16 | 视频渲染 | ✅ 真实运行 | FFmpeg 8.1.2 + OpenKatana 0.1.3；42.5 秒 1080×1920 成片 |
| 17 | 视频 QC | ✅ 真实运行 | 17 项技术检查 + 关键帧联系表 + 分窗音量比对 |
| 18 | 前端工作台 | ✅ 18 页真实接口 | `web/`（原生 JS，挂在 `/app/`） |
| 19 | 运行监控与成本 | ✅ 真实运行 | `/api/system/costs|providers|agents`，全部来自 `model_calls`/`tool_calls` 聚合 |
| 20 | Windows 启动恢复 | ✅ 真实运行 | `scripts/start.ps1` + `backend/scripts/preflight.py` + `recover_tasks.py` |

---

## C. 本轮提交（按可验证单元）

| 提交 | 内容 | 文件 |
| --- | --- | --- |
| `7f4c640` | `feat(model)` 统一模型网关接真实 OpenAI 兼容协议 | 13 文件（gateway/providers/tokens/tool + B4 迁移 + 20 例测试） |
| `48a9c36` | `feat(search)` 真实搜索与抓取，带 SSRF 与 robots 闸门 | 10 文件（safety/fetcher/engine/tool + 68 例测试） |
| `38e1916` | `docs(research)` V5.0 开源项目源码级评估 | 1 文件（17 个项目，固定 SHA） |
| `8121cdd` | `feat(media)` 带定位的结构化抽取与真实词级时间语音合成 | 10 文件（structured/tts 五个 + 20 例测试） |
| `d5303b4` | `feat(search)` 还原搜索结果真实地址 | 2 文件（含脚本跳转解析 + 6 例测试） |
| `4145cf5` | `feat(execution)` 任务运行时、编排器、执行器与 worker | 8 文件 / 3,010 行 |
| `314c836` | `feat(research)` 研究底稿与逐字核查，模型缺席时如实阻塞 | 8 文件 / 875 行 |
| `b8593de` | `feat(research)` 抓到的资料落进证据链 | 7 文件 / 779 行（persistence + 7 例测试） |
| `8537a68` | `feat(api)` 研究任务与运行监控接口 | 6 文件（权限清单 21→32 操作） |
| `015dd0a` | `feat(web)` 工作台 18 页接真实接口 | 7 文件（workspace.py + web/ 三个文件，权限清单 →37） |
| `f5ad53a` | `feat(ops)` 一键启动、启动预检与重启恢复 | 5 文件（start.ps1 / preflight / recover_tasks / 9 例测试） |

本轮新增后端代码约 8,100 行（不含测试），新增测试 231 例（376 → 607）。

---

## D. 真实运行证据（不是单测替身）

### D.1 真实搜索 + 真实抓取（联网）

端到端脚本 `%TEMP%\fmai_runtime_smoke.py`（临时库，真网络）：

```
task_status=completed   fetched_pages=3
- 今日头条  1,984 字
- financialnews 2,724 字
- 新浪财经 4,650 字   发布时间解析为 2024-11-25T07:54
快照落盘 data/snapshots/<host>/<sha256>.html
失败项如实记录：fetch:http_error / fetch:robots_disallowed（微信） / fetch:network_error（凤凰）
```

过程中修掉的两个真问题：

- **搜狗把 href 换成 `/link?url=<token>`**，且该地址返回 200 + 248~288 字节 HTML，
  真实地址写在 `<script>window.location.replace("…")</script>` 里、**没有 Location 头**。
  修法：`data-mdurl`/`mu`/`data-url` 优先取值 + 脚本跳转解析 + 解析后仍过 `assert_public_url`
  （内网/回环一律保留原链接并告警，不让搜索引擎成为 SSRF 旁路）。
- **反爬拦截页也是 HTTP 200**：`hangyan.co` 返回 28 字"正在验证浏览器环境，请稍候…"，
  却被记成"抓到 1 篇资料"。修法：`page_quality()` 判 `thin_content` / `bot_wall`，
  `_collect` 逐条尝试直到攒够合格正文（`MIN_PAGE_CHARS=200`、9 个反爬标记）。

### D.2 证据链落库（同一跑，查库核对）

```
planned task=1  stages=6
worker: iterations=3  completed=[1,2]  blocked=[3]
task status=paused
db rows: sources=3  reports=1  evidence=11  verified=11
sub#3 research.analyze  blocked  reason=human_review: model_not_configured
底稿：artifacts/task-1/digest.json
证据行示例：42家A股上市银行共实现营业收入4.28万亿元，同比下降1.05%
```

即：**搜到的网页 → sources 表；汇编成底稿 → research_reports；每个带数字的主张 → evidence，
逐字核对命中才标 VERIFIED**；分析/结论停在这里等模型密钥。

### D.3 语音（Edge TTS 真实合成）

```
providers {'edge': True, 'sapi': True}
provider=edge  duration=4.584s  bytes=27,504  words=13  estimated=False  measured_by=ffprobe
captions: 2 句（0.10–3.23 / 3.24–3.75）
```

关键点：`edge_tts.Communicate` 默认 `boundary="SentenceBoundary"`，中文拿不到词级时间
（0 个事件）；显式传 `boundary="WordBoundary"` 才有 13 个词级边界。这条已写进实现与测试。

### D.4 视频链路（沿用 V2.2 Phase 2 成果）

`content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4`：
20,097,925 B / 42.500 s / 1080×1920 / 30 fps / h264+aac，17 项 QC 全过，
sha256 `0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9`。

### D.5 工作台真机冒烟（真服务进程）

`%TEMP%\fmai_web_smoke.py`（临时库 + `uvicorn --port 8377`）：

```
/api/system/status 200 | costs 全 0 带说明 | providers 三家（deepseek=provider_disabled）
agents count=9 | config | jobs | POST /api/research-tasks → 201（plan 落库、5 行事件）
task-detail / task-events / task-list 200 | unknown-task → 404 unknown_task
/app/ → 200 HTML
```

---

## E. 测试结果

| 层次 | 命令 | 结果 |
| --- | --- | --- |
| 全量单测（含集成） | `pytest -o addopts="" -q` | **607 passed, 2 skipped, 1 warning in 77.24s** |
| 其中跳过 | — | Windows 无符号链接权限 1 例；真实网络 TTS 1 例（需 `FM_EDGE_TTS_NETWORK=1`） |
| 唯一 warning | — | `agent_runs ↔ research_subtasks` 互相外键的 SAWarning（既有设计，非本轮引入） |
| 真实联网（人工跑） | `%TEMP%\fmai_runtime_smoke.py` | 搜索/抓取/证据链全链路通过 |
| 真实服务（人工跑） | `%TEMP%\fmai_web_smoke.py` | 13 条接口全部符合预期 |
| 真实 TTS | `FM_EDGE_TTS_NETWORK=1 pytest tests/test_tts.py` | 通过（mp3 + 13 个词级边界） |
| 真实模型 | — | **BLOCKED**：无密钥、无本机模型 |

本轮修掉的测试相关真问题：`fetcher.extract_article` 先删 `<script>` 再读 JSON-LD（作者恒为空）；
`_FakeProvider` 同时控制 available 与合成导致"降级顺序"用例假通过；`AgentStatus` 无 `BLOCKED`
取值（`enum_column` 生成 CHECK），因此 blocked 记录写 `FAILED` + `error_message="blocked:…"`，
**不新建迁移**。

---

## F. BLOCKED 项与解除条件（需要人做的动作）

| 阻塞 | 现象 | 解除条件（最少操作） |
| --- | --- | --- |
| **真实模型调用** | `research.analyze/synthesize/verify.claims`、`content.*`、`topic.discover` 全部 `blocked`，`reason=model_not_configured` | 在仓库根目录建 `.env`，写 `DEEPSEEK_API_KEY=sk-…`（或 `OPENAI_API_KEY`），然后把 `config/models.yaml` 里对应 provider 与模型的 `enabled` 设为 `true` 并填 `input_price_per_1k_cny` / `output_price_per_1k_cny`（**价目未知会被拒绝，这是刻意的**） |
| 本机模型（零成本方案） | `local` provider 默认关闭 | 启动一个 OpenAI 兼容的本机服务（如 127.0.0.1:11434），把 `config/models.yaml` 的 `local` 段 `enabled: true` 并填价目（可填 0） |
| 研究结论/行业分析深度 | 底稿只是规则汇编（"未使用模型"写死在摘要里） | 同上，接上模型后自动切换为真实分析 |
| 第二个独立真实视频源 | V2.2 遗留：只有一个 NASA 视频文件的两个入点 | 提供新的、授权明确的视频素材（放 `input/approved_sources/` 并登记授权） |

**这些阻塞不会自动降级成假数据**：`FakeModel`/`FakeSearch`/`FakeTTS` 只出现在单测里，
生产路径没有开关可以打开它们。

---

## G. 安全边界遵守情况（逐条）

| 约束 | 本轮状态 |
| --- | --- |
| 不改 `data/finance_media.db` | ✅ 只读校验通过，见 §H |
| 迁移只在独立临时库测试 | ✅ B4 迁移在 `%TEMP%` 库往返验证（upgrade→downgrade→再 upgrade，CHECK 约束全部保留） |
| 不改 SQLite WAL 状态 | ✅ 开发库仍 `journal_mode=delete`、无 `-wal`/`-shm` |
| 不改 FFmpeg 8.1.2 | ✅ 仍是 `tools/ffmpeg` 的 8.1.2-full_build |
| 不改 `backend/app/main.py` 第 27 行品牌文字 | ✅ 原样保留 `"finance-media-ai 第一阶段后端服务。"` |
| 不删/轮换/打印 Secret | ✅ 仓库无 `.env`；`providers` 接口只回 `key_env` 名字与状态，不回显值；预检输出不含密钥（有测试断言） |
| 不擅自消耗付费额度 | ✅ 无密钥、无付费调用；测试全部 MockTransport |
| 不允许未知价格请求 | ✅ 价目 missing → `budget_blocked/pricing_missing`，**一次 HTTP 都不发**（有测试） |
| 不部署云服务器 | ✅ 未部署 |
| 不装大型本地模型 | ✅ 未安装 |
| 不用桌面旧文件/历史下载/旧报告 | ✅ 旧任务目录 `20261110_jgb_gold_repricing` 未读、未引用 |
| 不发布公开内容 | ✅ 未发布 |
| 不覆盖用户本地修改 | ✅ 未做 reset/clean/restore，历史未改写 |
| 不 Force Push | ✅ 全部普通 `git push` |
| 不做无关大规模重构 | ✅ 改动集中在新增模块，既有 M2.0/V2.2 代码只做必要修补 |

---

## H. 数据库状态证明（受保护的开发库）

只读方式（`mode=ro`）打开 `data/finance_media.db`：

```
sha256      2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203
bytes       540,672
sidecars    -wal False / -shm False
tables      19
journal_mode delete
revision    de8148733a9b
non_empty   {alembic_version: 1}
```

与本轮开始前的基线逐字节一致。`model_calls` 表在开发库里**还不存在**（表数 19），
本轮任何代码都没有向它写入过行。

---

## I. Git 状态

- 本轮 11 个提交，全部 `git push origin main`（无 force push），逐条见 §C。
- 终态：`HEAD == origin/main`，`git status --porcelain` 为空（本报告所在提交除外）。
- 禁入项自检（`.env`/`*.db`/`data/snapshots`/`logs`/`.openkatana`/`tools/*`/旧任务目录）：
  提交前用 `git diff --cached --name-only` 逐条核对，无命中。

---

## J. 前端工作台 18 页

| 路由 | 页面 | 数据来源 |
| --- | --- | --- |
| `/` | 首页 | `/api/system/status`、`/costs`、`/api/research-tasks`、`/api/workspace/jobs` |
| `/topics` | 选题 | `/api/topics`、`/api/topics/columns` |
| `/tasks` | 研究任务（建 + 列） | `POST/GET /api/research-tasks` |
| `/tasks/:id` | 任务详情 | `/api/research-tasks/{id}`、`/events`、`/run`、pause/resume/cancel |
| `/search` | 真实搜索 | `/api/workspace/search` |
| `/sources` | 资料库 | `GET /api/sources` |
| `/evidence` | 证据链 | `GET /api/evidence?research_report_id=` |
| `/reports` | 研究报告 | `GET /api/research-reports`、`/{id}` |
| `/planning` | 内容策划 | 报告 + 模型状态（BLOCKED 时明说） |
| `/scripts` | 脚本 | job 的 `script/script.md` |
| `/storyboard` | 分镜 | job 的 `storyboard/storyboard.csv` |
| `/assets` | 素材 | `assets_manifest.csv` + `source_register.csv` |
| `/video` | 视频预览 | `/api/workspace/jobs/{id}/file?path=output/*.mp4` + QC |
| `/review` | 人工审核 | `?status_filter=paused` + 人工判断清单 |
| `/models` | 模型与价目 | `/api/workspace/config`（缺价目标 warn） |
| `/providers` | 服务商 | `/api/system/providers` |
| `/costs` | 成本 | `/api/system/costs` |
| `/settings` | 系统设置 | `/api/system/status`、`/agents`、`/api/workspace/config` |

关键约束：所有数字来自真实接口；空目录显示空、缺文件报缺失；失败时把
`{"error":{code,message,details}}` 原样显示，不吞掉原因。

---

## K. 未完成 / 部分完成（如实）

1. **真实模型驱动的分析、结论、脚本、策划**：BLOCKED（无密钥）。代码路径已就绪，
   接上密钥即可跑通，但**我不能替你确认它的输出质量**。
2. **行业/公司研究引擎**：PARTIAL。底稿与证据链是真实的，但"商业模式拆解""财报三表联动"
   这类分析需要模型；没有模型时它只给事实与定位，不给结论。
3. **动态图表动画**：沿用 OpenKatana 的组合渲染（V2.2 已验证柱状图/流程图/镜头运动），
   尚未引入 ECharts 数据层（评估结论是 DEPENDENCY，但未安装依赖，属于"未接入"而非"已完成"）。
4. **HyperFrames / Remotion / MoneyPrinterTurbo 等开源视频框架**：只做源码级评估（见
   `v5_0_oss_evaluation.md`），未接入运行时；理由写在评估里（AGPL、双档商业许可、无 Windows 支持等）。
5. **视频生产链与研究链尚未自动串接**：现在可以从报告人工挑主题去做视频（工作台里能看到），
   但"报告 → 视频任务"的自动交接还需要模型产出脚本，因此停在人工环节。
6. **`video_engine/` 仍是 YAML 模板**：渲染实际走 `content/jobs/<id>/script/build_*.py` + 适配层。
7. **5 个工具仍未实现**：`db_query`、`db_write`、`task_update`、`handoff`、`fact_check`。

---

## L. 已知风险

- **搜索依赖中文引擎的 DOM 结构**：搜狗/360 改版会让解析器失效。缓解：三引擎逐个降级、
  失败如实记录、`page_quality()` 兜住反爬页；但**长期稳定性无法保证**，属外部依赖风险。
- **`bing` 目前基本无效**（`li.b_algo` 命中 0，疑似 JS 拦截），实际可用的是 sogou + so360。
- **`edge-tts` 走微软未公开接口**：已固定 `7.3.1`，但不能保证长期可用；SAPI 作为离线兜底
  （无词级时间，标 `estimated=True`）。
- **`agent_runs ↔ research_subtasks` 外键环**导致 SQLAlchemy 拓扑排序告警（既有问题）。
- **研究任务运行是同步的**：`POST /run` 会在请求里跑完最多 N 格（真搜索可能几十秒）。
  生产环境应改为后台 worker 进程（`backend/scripts/research_task.py run` 已支持）。
- **模型输出质量未经验证**：没有密钥，我无法给出任何"分析质量"的判断。

---

## M. 下一步（接上密钥后按顺序就能跑）

1. 配 `.env` 的 `DEEPSEEK_API_KEY` + `config/models.yaml`（enabled + 价目）。
2. `python -m backend.scripts.preflight` 应变成 `providers: ok`。
3. 工作台 `/tasks` 建《商业银行是如何赚钱的》，`/tasks/:id` 点"推进"，观察阶段与事件流。
4. 报告落到 `/reports`，证据在 `/evidence`（带来源 URL、快照、SHA256、定位）。
5. 从报告建视频任务：补齐 `content.*` 阶段后，脚本/分镜落到 `content/jobs/<id>/`，
   跑渲染与 QC，成片在 `/video` 预览。

---

## N. 复现命令

```powershell
# 全量测试
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q

# 预检（ok / blocked / error 三档）
.\.venv\Scripts\python.exe -m backend.scripts.preflight

# 一键启动（独立工作库，不碰受保护库）
.\scripts\start.ps1

# 真实联网冒烟（会真的发请求，临时库）
$env:PYTHONPATH="C:\Users\25803\finance-media-ai"
.\.venv\Scripts\python.exe C:\Users\25803\AppData\Local\Temp\fmai_runtime_smoke.py

# 工作台接口冒烟（起临时服务）
.\.venv\Scripts\python.exe C:\Users\25803\AppData\Local\Temp\fmai_web_smoke.py

# 开发库只读核验（证明没被动过）
.\.venv\Scripts\python.exe C:\Users\25803\AppData\Local\Temp\fmai_v5_dbcheck.py

# 重启后恢复任务
.\.venv\Scripts\python.exe -m backend.scripts.recover_tasks --apply
```

---

## O. 本轮做的关键决策

| 决策 | 理由 |
| --- | --- |
| 成本列精度 2 → 6 位（B4 迁移） | 单次调用成本是 1e-4 量级，2 位小数会被舍成 0，**日上限闸门等于不存在** |
| `agent_runs` 不新增 `BLOCKED` 状态 | `AgentStatus` 是带 CHECK 约束的枚举列，加值要迁移；而且 agent_runs 记的是"单次尝试"，没产出就是 failed，"为什么"写 `error_message` 足够 |
| blocked 阶段先落底稿再报 blocked | 证据链不该等模型才存在；人被要求"配密钥"时，已有的资料与核对结果要能直接看 |
| 研究任务默认跳过 topic/content 阶段 | 现阶段这些阶段必然 BLOCKED，让它们挡在前面会让整条链第一步就停 |
| `extract` 无文件时 completed+skipped | 真实研究常常只有网页资料；原来判 failed 会让下游收到 `upstream_failed` 整条断掉 |
| 搜索走三引擎逐个降级 + 真实失败记录 | 单引擎不稳定；全失败时返回 `SearchUnavailable` 并列出每个引擎的原因，不返回空结果糊过去 |
| `features.*` 开关默认关闭并真生效 | 配置写了 false 就必须真的不可用，否则"配置"是装饰；搜索接口关着会告诉你怎么打开 |
| 工作台用原生 JS 不用框架 | 没有构建链依赖，`/app/` 直接由后端静态托管，避免为一个内部工具引入前端工程链 |

---

## P. 结论

| 维度 | 结论 |
| --- | --- |
| 系统可启动、可操作 | ✅ `scripts/start.ps1` 一条命令；18 页工作台接真实接口 |
| 真实搜索与抓取 | ✅ 已实测抓到真实正文、解析发布时间、落快照与哈希 |
| 证据链可追溯 | ✅ sources / research_reports / evidence 真实落库，逐字核对标 VERIFIED |
| 真实模型调用 | ❌ **BLOCKED**（无密钥、无本机模型），代码就绪但未验证输出质量 |
| 真实视频成片 | ✅ V2.2 已交付 42.5 秒成片 + 17 项 QC |
| 前端发起完整生产链路 | ⚠️ PARTIAL：研究链能建能跑能看；视频链仍从既有 job 读取，自动交接等模型 |
| 测试 | ✅ 607 passed / 2 skipped / 1 已知 warning |
| 安全边界 | ✅ 15 条逐条核对（§G） |
| 工程可用性 | **可用**：研究与证据链能真实跑，视频与语音已验证 |
| 完整内容生产（选题→发布级成片） | **未达成**：卡在模型密钥这一个外部条件上 |

**一句话**：除了"真实模型"这一环，其余链路都是真跑过的；接上密钥后 `analyze → synthesize →
content.*` 会从 BLOCKED 变成真实产出，不需要改架构。在那之前，系统会如实告诉你它卡在哪、
要你做什么，而不是给你一份看起来完整的假报告。

---

## Q. 收尾增补（本报告首版之后继续做的部分）

首版报告停在"研究链跑通、视频链沿用 V2.2"。之后又推进了六批，全部**真跑验证并已推送**：

| 增补 | 状态 | 证据 |
| --- | --- | --- |
| 最后 5 个工具（`db_query`/`db_write`/`task_update`/`handoff`/`fact_check`） | ✅ 十个工具有真实实现 | `tests/test_local_tools.py` 24 例；`db_write` 要过白名单 + `db_scopes` + 禁写金额三关 |
| 报告 → 视频任务交接（`POST /api/research-reports/{id}/video-job`） | ✅ | 落 `research/report.md`、`evidence.json`、`sources.json`、`job.json`、`HANDOFF.md`；脚本与分镜目录**故意留空**，不伪造 |
| 表格数字 → 指标 + 可复现图表（`backend/app/analysis/`） | ✅ | `tests/test_analysis.py` 29 例；同输入同字节 PNG，图上强制印出处行 |
| 分镜 → 时间轴 → 成片 → 质检（`backend/app/video/render.py`） | ✅ 真机 | 7 镜 1275 帧 1080×1920，只跑时间轴 79 秒；组合素材走引擎合成、同文件多入点、不覆盖已冻结的 `project/timeline.json` |
| 常驻 worker（`python -m backend.scripts.run_worker`） | ✅ 真跑 | 采集格 completed、模型格 `blocked:model_not_configured`、空闲 3 秒后自动退出 |
| 后台渲染（`python -m backend.scripts.render_job` + 工作台两个按钮） | ✅ 真跑 | 进度写 `qc/render_run.json`（原子替换，`running/done/failed` 三态）；实测 08:40:59 → 08:42:22 变 `done/timeline_ready` |

增补后的终态数字（与前文 §E/§P 的旧数字不同，以本节为准）：

- 测试：`pytest -o addopts="" -q` → **684 passed, 2 skipped, 1 warning in 83.53s**（唯一 warning 仍是 `agent_runs↔research_subtasks` FK 环）；
- API：**41 个操作**（22 个基础 + 8 研究任务 + 3 监控 + 8 工作台含后台渲染）——权限清单 `ROUTE_CALLERS`、启动自检与 `tests/test_caller_permissions.py` 的绊线三处一致；
- 工具：10 个全部真实实现（`README.md` 的"已实现 5 个"已改）。

**一条必须记住的副作用**：真机渲染会让引擎在自己的工程目录里记账——`project/<pid>/project.json`
会追加本次导入的素材，`assets/`、`media/`、`plates/` 会被写出（后三者已 gitignore）。
对**已冻结**的任务跑完真机渲染后要 `git checkout -- project/<pid>/project.json` 恢复，
否则冻结记录会被累计的素材列表污染。这条已写进 `backend/app/video/render.py` 的模块 docstring。

本轮新增的真实研究产物：`content/research/task-1/{digest.md,digest.json}`（3 条真实来源、
18 个候选数据点，正文里明写"规则汇编（未使用模型）"），作为"真实搜索→抓取→底稿"的入库证据。
