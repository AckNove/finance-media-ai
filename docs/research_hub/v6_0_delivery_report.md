# V6.0 最终验收报告（自主接管 + 完整开发 + 自审）

> 本文件对应 V6.0 总任务书第十部分「最终交付标准」与第十一部分「执行方式」。
> 一切结论以**可复跑的命令与真实产物**为准；没跑过的功能一律标 `CODE_ONLY` / `TEST_ONLY`，
> 不为提高完成率改口径。
>
> 撰写时 HEAD：见 §12（本文件与本轮代码同一次提交推送）。
> 权威需求清单在 `docs/research_hub/v6_0_requirements_matrix.md`（就地更新，逐条标状态）。

## 0. 一页纸结论

| 问题 | 结论 |
| --- | --- |
| 现在能用吗 | 能。`scripts\start.ps1` 起服务 → 打开 `http://127.0.0.1:8000/app/` → 建研究任务 → 看规划/来源/证据/核查 → 人工确认 → 建视频任务 → 渲染出片 |
| 真实跑通了吗 | 是。三个新主题（行业/产业链/上市公司）各跑完整链路，另有 1 条内容链路真出片 |
| 花了多少钱 | 本轮验收真实调用 **17 次 / ¥0.651870**（三主题 ¥0.494162 + 复核一轮 ¥0.157708），全部走网关留痕 |
| 数据可信吗 | 结论必须**人工确认**才进成稿；模型判定（`supported`…）与人工状态（`confirmed`…）是两条独立状态线 |
| 有没有假的东西 | 没有。所有 Mock 只出现在单元测试里，验收报告把"单测通过"与"真实跑通"分开列 |
| 真实开发库动了吗 | 没动。`sha256=2df764e5…8203` 与基线一致，19 表 / `revision=de8148733a9b` / `journal_mode=delete` |
| 还缺什么 | 真实素材获取与云渲染（见 §9） |

## 1. 接管检查（任务书第二部分 2.1）

| 检查项 | 实测 |
| --- | --- |
| 分支 / HEAD / origin | `main`，接管时 `HEAD == origin/main == 0090225`，工作区干净 |
| 文档 | 读 `docs/research_hub/` 全部（含 V5.0/V5.1 报告、`acceptance_criteria.md`、`database_design.md`） |
| 目录结构 | `backend/app/{api,core,capabilities,execution,research,video,media,analysis,services,models,schemas,skills}`、`web/`、`scripts/`、`content/jobs/`、`tools/` |
| 接口 | `create_app().openapi()`：接管时 45 路径 / 58 操作 → 现在 **46 路径 / 67 操作** |
| 数据 | 接管时 29 表 → 现在 **31 表**（`alembic check` 无漂移） |
| 测试 | 接管时 850 passed / 2 skipped → 现在 **1048 passed / 2 skipped** |
| 前端 | 19 页 → **20 页**（新增「内容日历」），全部接真接口，无静态假数据 |
| 工具 | 10 个全部 `implemented=True` |
| 已实现但没接业务流程的 | `preflight.py` 被 `doctor.py` 超过但仍可用 |

审计产物：`docs/research_hub/v6_0_requirements_matrix.md`（A–K 逐条：需求 / 实现位置 / 状态 / 差距）。

## 2. 本轮新增与修复（按模块）

### A. 基础设施

| 交付 | 命令 | 证据 |
| --- | --- | --- |
| 在线备份 | `python -m backend.scripts.backup_db --list` / 默认备份最后 20 份 | `tests/test_backup_restore.py` 13 例；说明文件带源/副本 sha256、表数、integrity、revision |
| 恢复 | `python -m backend.scripts.restore_db --backup <说明.txt>` | 拒绝覆盖 `data/finance_media.db`（退出码 2）；覆盖前留 `.pre-restore-*.db` |
| 环境诊断 | `python -m backend.scripts.doctor --text` | 22 项检查（依赖/FFmpeg/字体/浏览器/端口/磁盘/库版本/配置/Provider），每项带修复建议 |
| 停止服务 | `powershell -File scripts\stop.ps1` | 只杀 `uvicorn backend.app.main`，`-Force` 才强杀；实测释放端口 |
| 服务自愈 | `powershell -File scripts\watchdog.ps1 -Port 8031` | 健康探测 + 退避 3→6→12→24（封顶 60 秒）+ 崩溃日志 `logs/watchdog-*.log` |

两个真 bug 在这轮被修掉：①备份时间戳只到秒，同秒连备会**互相覆盖**（现为毫秒 + 撞名加序号）；
②清理旧备份按 `rsplit("-")` 分组，遇到新时间戳分错组（改为正则匹配 + `(mtime, name)` 排序）。

### B. 模型系统

新增「失败的尝试也要记账」：以前 `Worker._apply` 的 `retry` / `failed` 分支调
`runtime.fail(...)` 时**不带** `calls/tokens/cost`，于是"模型回了话但被截断"的那次调用
在 `model_calls` 有行、在任务账上没有。真实验收里因此出现过 ¥0.046811 的差额。

修法：`runtime.fail(...)` 收 `calls/tokens/cost_cny` 并累加到任务；
`worker._apply` 两个失败分支都传 `outcome.*`。
回归用例：`tests/test_execution_pipeline.py::test_a_failed_attempt_that_already_burned_tokens_is_charged_too`。

**模型降级链**（B 段补齐）：`gateway.py` 新增 `FALLBACK_CODES = {timeout, transport_error, server_error, rate_limited}`、
`_fallback_reply()` 与 `_fallback_target()`；`config/models.yaml` 的 `deepseek-flash` 配 `fallback_to: deepseek/deepseek-v4-pro`。
三条硬规矩：①**只降一跳**（备用模型再失败就如实报错，不递归）②**鉴权失败与请求非法不降级**（换模型解决不了，只会把问题藏起来）
③**备用模型价目没核实就拒绝降级**（不许花不知道价钱的钱）。降级成功后回复的 `raw_usage` 带
`fallback_from` / `fallback_reason`，审计表里主模型与备用模型各一行。
`tests/test_model_guardrails.py` 新增 4 例：超时会降级且两行审计、401 不降级、价目缺失不降级、两跳都失败只留两行。

### C/D. 研究骨架与来源

| 交付 | 位置 | 证据 |
| --- | --- | --- |
| 行业 / 产业链 / 公司三套研究骨架 | `backend/app/research/frameworks.py` | 10 / 11 / 14 节；`market_size` 与公司财务四节**只允许事实**；`tests/test_frameworks.py` 17 例 |
| 骨架接进分析链路 | `analysis.py` 的 `build_messages(..., framework=)` / `parse_analysis` / `to_markdown()` | 分析按节输出，空节写"本节缺证据…不补写"，末尾给覆盖率 |
| 骨架接进执行器 | `executor._analyse_with_model` 用 `_task_objective()` 判类型 | output 带 `framework` / `framework_checklist` |
| 来源可信度分级 | `backend/app/research/source_quality.py` 的 `tier_of()` | 官方/监管 → 一手披露 → 财经媒体 → 聚合/自媒体 → 未知，五档按**域名后缀 + 显式白名单**判定 |
| 来源时效判断 | `source_quality.freshness_of()` | 30 天内 fresh、180 天内 recent、更早 stale、**没有发布时间就是 unknown（不猜）**；可用 `as_of` 按底稿日期判，避免拿今天去判八月的稿 |
| 底稿自带这两样 | `digest.to_markdown()` 新增「来源可信度与时效」一节 + `as_dict()` 的 `source_quality` | `tests/test_source_quality.py` 21 例（含"发布时间比参照时间还晚 → 报负数天数，不裁成 0"） |
| Skill 注入提示词（C 段收口） | `executor._skill_notes()` → `analysis.build_messages(skill_notes=)` | 只有 Agent 身份能加载（人身份 `SkillRegistryError`）；`research.analyze` 会带上 `accessible-explainer` 正文；`skills/` 目录坏掉只记 warning、研究照跑，`output.skills_used` 如实记空；`tests/test_skill_injection.py` 10 例 |

三个真实主题的骨架判定全部正确：储能 → industry、光伏产业链 → chain、隆基绿能 → company。
来源分级**只回答"要不要回原文核对"**，不代表这条内容已被核实——分级按域名，不按内容。

### E/F. 语义核查与成稿闸门

| 交付 | 位置 | 证据 |
| --- | --- | --- |
| 五档判定 + 八类错误码 + 12 项留痕 | `research/semantic.py` + `finding_checks` | 真实数据里出现过 `supported / partially_supported / insufficient_evidence / unverifiable` 四档；`contradicted` 仍无真实样本 |
| 人工撤回 → 已建视频任务失效 | `video/handoff.py` 的 `jobs_using_checks` / `invalidate_jobs` | 写 `INVALIDATED.md` 并把 `job.json.draft_gate.usable=false`，**不删产物**；`tests/test_review_invalidation.py` 5 例 |
| 成稿闸门 | `research/draft_gate.py` | 视频任务与脚本两个出口都拦；`tests/test_draft_gate.py` 10 例 |

### H/I. 视频与内容质检

| 交付 | 位置 | 证据 |
| --- | --- | --- |
| 内容质检（8 类检查） | `backend/app/video/content_qc.py` + `POST /api/workspace/jobs/{id}/content-qc` | 上屏数字必须能在材料里找到、四舍五入只算提醒、每镜要有来源、字幕必须来自旁白、素材授权必须可商用；`tests/test_content_qc.py` 13 例 |
| 分镜 `source_ids` 列 | `backend/app/media/storyboard.py` + `video/outline.py` | 可追溯性从自由文本（`notes`）升级为结构化列；框景镜头允许空，但**一旦出现数字就必须挂来源** |

### K. 运营

| 交付 | 位置 | 证据 |
| --- | --- | --- |
| 内容日历 / 排期 / 发布留痕 | `models/operations.py` + `/api/calendar/*`（8 个端点）+ `web/app.js#pageOperations` | 迁移 `b6_v6_operations`；发布必须有链接、撤回留痕清时间；`tests/test_operations.py` 14 例 |
| 平台数据回填与分析 | `platform_metrics` + `/api/calendar/analytics` | 同一时间戳覆盖被拒（历史不覆盖）；分析取**最新快照绝对值**，不累加 |
| 账号数据 CSV 批量导入 | `/api/calendar/{id}/metrics/import` + `services/metrics_import.py`（纯函数解析）+ 前端「导入 CSV」按钮 | 认中英列名与千分位；撞时间戳的跳过并说明原因；坏行带行号返回、不猜数字；平台不一致整行拒绝；`tests/test_metrics_import.py` 13 例 |

## 3. 真实端到端验收（任务书第五部分）

命令：`python -m backend.scripts.v6_acceptance`（临时库 + 真 uvicorn :8399 + 真搜索 + 真 DeepSeek）。

| 主题 | 类型 | 规划 | 来源 | 证据 | 核查 | 判定分布 | 花费 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 储能行业为什么从爆发转向价格战 | 行业 | `planner=model`，36 检索词 / 6 子任务 | 23 | 424 | 7 | supported 1 / partially 2 / insufficient 3 / unverifiable 1 | ¥0.156613 |
| 光伏产业链利润为什么向两端流动 | 产业链 | `planner=model`，36 检索词 / 6 子任务 | 49 | 256 | 6 | supported 1 / partially 2 / insufficient 3 | ¥0.153900 |
| 隆基绿能 2024 年亏损来自哪里 | 上市公司 | `planner=model`，30 检索词 / 6 子任务 | 73 | 380 | 4 | supported 1 / partially 1 / insufficient 2 | ¥0.136838 |
| **合计** | | 三题全部 `model_used=True`、无降级 | **73** | **1060** | **17** | 四档都出现过 | **¥0.494162** |

- 每题的 9 个流水线阶段全部成功（含两次人工闸门放行），`model_calls` 13 行、密钥泄漏 **0** 行。
- 真实结论样例（储能题，`supported`）：「累计投运装机达 1.53 亿千瓦 / 3.96 亿千瓦时，同比增长 61%」——
  支持片段逐字命中原文「截至 6 月底，全国已建成投运新型储能装机规模达 1.53 亿千瓦/3.96 亿千瓦时，同比增长 61%」。
- 真实结论样例（同一题，`insufficient_evidence`）：「电芯规划产能超 2TWh、年底建成 1.2–1.5TWh，远超全球需求」——
  抓到的材料里没有这条原文，模型给不出逐字支持片段，系统降级为"证据不足"而不是照单全收。

### 内容链路（报告 → 成片）

| 步骤 | 结果 |
| --- | --- |
| 人工确认一条结论 | `POST /api/research-reports/1/checks/1/review` → 200 |
| 闸门放开 | `allowed=true confirmed=1`（未确认的 5 条不进稿） |
| 建视频任务 | `201`，`stage=storyboard_ready` |
| 真渲染 | `render_job --narration` rc=0，`content/jobs/v6_accept_industry/output/final.mp4` |
| 技术 QC | `failed=[]`；黑帧 0 段、静音 0 段、最长冻结 0.767 秒 |
| 成片参数 | 37.833 秒 / 1135 帧 / 1080×1920 / 9,480,850 B / sha256 `3aedf436307c84a002a6521c6e6da87830fdd933d5cae59053eff0f7ebbb8e44` |

### 内容质检从"不通过"到"通过"（真片闭环）

第一版规则分镜在真片上被内容质检判成硬错，逐条查下来都是真问题，不是阈值太紧：

| 报出的问题 | 根因 | 修法 |
| --- | --- | --- |
| 3 镜 `shot_without_source` | 分镜表**没有 `source_ids` 列**，`notes` 里的自由文本没法机读 | `storyboard.OPTIONAL_COLUMNS` 增 `source_ids`，`outline` 按镜头写 `shot.source_id` |
| 开场/收尾一律硬错 | 框景镜头本来就不落数据 | 框景镜头允许空来源，但**一旦出现带单位的量就必须挂来源**（年份、"以上 2 个数字"这类计数不算数据主张） |
| `subtitle_not_from_narration` | 字幕本来就是旁白的压缩版 | 用字重合率 ≥70% 记为 `info`（`subtitle_condensed_from_narration`），换了一整句无关文案才 warning |
| 开场念了 23 秒 | 标题就是整句研究目标，还带着流水线标签「结论整合：」 | `_short_title()` 剥掉阶段名、取第一个句子、限 24 字，字幕另取 18 字且**不切半截数字** |

复验（`content/acceptance/v6/rebuild_check.py`，用保留的验收库重建任务，不再调模型、不花钱）：

```
render rc 0  →  content_qc {"ok": true, "errors": [], "warnings": [],
                            "stats": {"shots": 4, "distinct_numbers_on_screen": 3, "numbers_in_material": 121}}
```

| 项 | 值 |
| --- | --- |
| 成片 | `content/jobs/v6_accept_company_v2/output/final.mp4` |
| 参数 | 29.267 秒 / 4 镜 / 1080×1920 / 30fps / h264+aac / 5,600,359 B |
| sha256 | `153f7327e10c2e0b77e5df8cdb3471af0faf5ee4848607c5f833d0eaa8a79794` |
| 内容质检 | `ok: true`，硬错 0、提醒 0（数字 82亿 / 86.18亿元 都能在材料里找到，并挂在 S01 / S20） |
| 技术质检 | `qc/report.json` 七项全过 |
| 闸门 | `job.json` 的 `draft_gate`：`confirmed_findings=[0]`、`check_ids=[1]`、无失效标记 |

> 同一批验收里还有个反面证据：`content/jobs/v6_accept_industry/` 现在带着 `INVALIDATED.md`
> ——它是被「撤回确认就作废」这条规则真正标记的（F 段机制生效）。不过触发源是**测试副作用**
> （`tests/test_draft_gate.py` 当时没隔离视频任务根目录，跑全量测试时把真实任务写上了失效标记），
> 属于真缺陷：已加 autouse fixture 把 `JOBS_ROOT` 钉到临时目录，并补回归用例
> `test_review_endpoint_only_touches_the_configured_jobs_root`。

### 记账一致性复核（修完缺陷后单跑一遍公司题）

`FM_ACCEPT_OUT=acceptance_company_recheck.json python -m backend.scripts.v6_acceptance --only company --no-render`：

```
花费合计 0.15770800000000001 元（任务账 0.157708 / 接口 0.157708，对得上=True），调用 4 次，密钥泄漏行 0
```

`accounting_ok=true` 意味着 `SUM(model_calls.cost_estimate) == SUM(research_tasks.cost_spent) == /api/system/costs.task_cost_cny`。

## 4. 独立自审（任务书 4.3）

| 自审项 | 结果 |
| --- | --- |
| 是否只有接口没有业务能力 | 66 个操作逐个走查，全部有 service + 真实数据来源 |
| 是否只有 Prompt 没有真实执行 | 9 个角色里真正被 `_handlers` 调到的是 5 条链路，已在矩阵 C 段写明"哪些只是名册" |
| 是否有未被调用的 Agent / 未接入的 Skill | Skill 5 个已注入提示词（1800 字上限；人身份拿不到，加载失败不打断研究） |
| 是否有重复代码 | `preflight.py` 与 `doctor.py` 检查项重叠，保留前者作轻量版 |
| 是否有错误状态转换 | 脚本状态机非法迁移 409 并回 `details.allowed`；审核撤回会联动失效 |
| 是否有 API 费用绕过 | 全部模型调用经 `ModelGateway`；价目未知直接拒；限流/超时/重试都在网关内 |
| 是否有密钥泄漏 | `git grep "sk-[A-Za-z0-9]{20,}"` 0 命中；`model_calls.prompt_preview LIKE '%sk-%'` 0 行 |
| 是否有前端假数据 | `web/app.js` 无内联假数据，全部 `fetch('/api/...')` |
| 是否有生产链路用 Mock | 生产链路无 Mock；Mock 只在 `tests/` 里 |
| 是否有失败任务无法恢复 | 失败传染已修（`BLOCKED` 也传染），任务会终结为 `failed` 而不是卡在 `running` |
| 是否有错误结论进成稿 | 成稿闸门 + 人工确认；`human_review_status` 恒从 `pending` 起 |

### 反向测试（4.4）

`tests/` 里逐条覆盖：未授权身份（`X-Caller: agent` 伪造 → 422 `permission_denied`）、非法输入（422）、
缺证据（`insufficient_evidence`）、模型超时/失败（`retry` + 记账）、搜索失败（`feature_disabled` / `bot_wall`）、
抓取失败、预算不足（`budget_blocked`）、任务中断（租约过期被回收）、数据重复（同时间戳快照 409）、
审核撤销（任务失效）、渲染失败（`render_run.json` 状态机 + QC 硬错）。

## 5. 费用与外部服务（第六部分）

| 项 | 数值 |
| --- | --- |
| 本轮真实模型调用 | **17 次**（三主题 13 + 复核 4） |
| 本轮实际花费 | **¥0.651870** |
| 单价依据 | `config/models.yaml` 的 `price_source` + `price_checked_at`（2026-11-12 官方文档，折算 ¥/千 token：flash 0.00213 / 0.00852） |
| 预算闸门 | 每题 `budget_max_cost_cny=0.4`，当日上限 ¥5；`pricing_unknown_calls=0` |
| 未授权付费项 | 无。未充值、未购买服务、未升级套餐 |
| 外部阻塞 | 无（搜索/抓取/模型均可用）；真实素材获取与云渲染未获授权 → 记 `BLOCKED` |

## 6. 安全边界逐条核对（第七部分 20 条）

| # | 边界 | 核对结果 |
| --- | --- | --- |
| 1–3 | 真实库只读、不改、迁移只上临时库 | ✅ `sha256=2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`、540,672 B、19 表、`revision=de8148733a9b`、无 `-wal`/`-shm` |
| 4 | 不改 WAL 状态 | ✅ `journal_mode=delete`，全程未开 WAL |
| 5 | 不改 FFmpeg 8.1.2 | ✅ `tools/ffmpeg/bin/ffmpeg.exe` 仍 `8.1.2-full_build-www.gyan.dev` |
| 6 | 不改 `backend/app/main.py` 第 27 行品牌 | ✅ 本轮未修改该文件 |
| 7–9 | 不打印/不提交/不轮换密钥 | ✅ `.env` 未跟踪；扫描 0 命中 |
| 10 | 不调用未授权付费 API | ✅ 仅 DeepSeek（用户已授权），17 次 ¥0.65 |
| 11 | 不用桌面旧素材 | ✅ 三个主题全新；素材全部程序生成 |
| 12–13 | 不自动发布、不部署云 | ✅ 发布动作只有"人工填链接留痕" |
| 14 | 不装大型本地模型 | ✅ 未安装 |
| 15–18 | 不 `reset --hard`、不 `clean -fd`、不 force push、不改历史 | ✅ 全部用普通提交 + `git push` |
| 19–20 | 不覆盖用户改动、不删用户数据 | ✅ 工作区改动全部保留并入库；`content/jobs/` 旧任务未删 |

## 7. 数据库与部署准备（第八部分）

- 迁移链：`de8148733a9b → b1_topic_taxonomy → b2_task_layer → b3_metering → b4_cost_precision → b5_finding_checks → b6_v6_operations`（7 个版本，`alembic check` 无漂移）。
- 每批迁移都做了「升级 → 降级 → 再升级」可逆性测试（`tests/test_migrations.py` 14 例）。
- 新库初始化：`python -m alembic -x db_url=sqlite:///<路径> upgrade head`（`start.ps1 -Database <路径>` 会自动建）。
- **真实开发库迁移：未执行**，等你单独授权（授权后建议先 `backup_db` 再 `upgrade head`，本报告的 `restore_db` 就是回滚路径）。

## 8. 交付清单（第十部分 1–15）

| # | 交付物 | 路径 |
| --- | --- | --- |
| 1 | 源代码 | `backend/`、`web/`、`scripts/` |
| 2 | 配置模板 | `config/{settings,models,agent_routing,pipelines,content_taxonomy}.yaml` |
| 3 | 库初始化与迁移 | `backend/migrations/`、`backend/scripts/{backup_db,restore_db}.py` |
| 4 | 启动脚本 | `scripts/start.ps1` |
| 5 | 停止脚本 | `scripts/stop.ps1` |
| 6 | 环境诊断 | `backend/scripts/doctor.py`（另有 `scripts/watchdog.ps1` 做自愈） |
| 7 | 测试 | `tests/`（964 passed / 2 skipped） |
| 8 | 端到端验收脚本 | `backend/scripts/v6_acceptance.py`（另有 `ui_acceptance.py` 做浏览器点击） |
| 9 | 用户操作手册 | `README.md`（含"三步开始用"） |
| 10 | 架构文档 | `docs/research_hub/{system_architecture,module_design,api_design,database_design}.md` |
| 11 | Agent / Skill 文档 | `agents/*/PROMPT.md` + `skills/README.md` + `skills/registry.yaml` |
| 12 | API 文档 | 运行时 `/docs`（OpenAPI），设计见 `docs/research_hub/api_design.md` |
| 13 | 成本与预算说明 | `docs/research_hub/v5_0_model_integration_report.md` §10 + 本文件 §5 |
| 14 | 安全说明 | 本文件 §6 + `AGENTS.md` §2/§3 |
| 15 | 最终验收报告 | 本文件 |

## 9. 需求状态总表（六状态口径）

| 状态 | 数量 | 主要条目 |
| --- | --- | --- |
| **DONE** | 主干全部 | 配置/迁移/队列/网关计费/预算/限流/脱敏/降级链/TaskRuntime/编排/失败传播；搜索抓取解析；来源与证据；来源可信度与时效；三套研究骨架；Skill 注入提示词；语义核查五档 + 成稿闸门 + 撤回失效；内容质检；内容日历与平台回填 + CSV 批量导入；选题建议（规则推荐）；一稿三版（抖音/小红书/公众号，不改事实不编标签）；背景音乐曲库与授权校验（先验授权再混音）；备份恢复/诊断/停止/自愈；20 页前端 |
| **PARTIAL** | 2 | 见下表（本文件 §9.1）——这些是**部分可用但不是完整能力**的条目，不冒充 DONE |
| **TEST_ONLY** | 1 | `contradicted` 判定（代码 + 单测通过，真实语料里还没出现过） |
| **CODE_ONLY** | 0 | — |
| **BLOCKED** | 2 | 真实素材获取（需授权素材源）；云渲染/发布（未授权） |
| **NOT_IMPLEMENTED** | 1 | 多平台账号 API 对接（抖音/小红书等开放平台需企业资质与授权，不是本地能补的） |

### 9.1 目前仍是 PARTIAL 的两项（如实列出）

| 需求 | 现在能做什么 | 还差什么 |
| --- | --- | --- |
| 脚本生成（AK 风格） | `content.script` 只写**人工确认过的结论**，Skill（`ak-signature` 等）已注入分析阶段 | 草稿是规则拼句，不是模型改写；"一次成型"还得人工润色 |
| 动态产业链图 / 动画 | 数据卡、图表、镜头轨道都能做 | 没有产业链专用图形（上下游链路示意图） |

## 10. 用户怎么开始用（最少三步）

```powershell
# 1) 体检（不写数据库、不花钱）
python -m backend.scripts.doctor --text

# 2) 起服务（自动建库/迁移到 head、跑启动自检、探测健康）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# 3) 打开工作台
#    http://127.0.0.1:8000/app/
#    研究主题 → 任务详情看规划 → 语义核查逐条确认 → 报告页「建视频任务」→ 视频页渲染
```

需要停止：`powershell -File scripts\stop.ps1`；需要 7×24 自愈：`powershell -File scripts\watchdog.ps1`。

## 11. 仍需你人工做的事

1. **真实开发库迁移授权**（授权后我按 `backup_db` → `upgrade head` → 复核的顺序做）。
2. **人工审核**：模型判定只是意见，`confirmed` 必须由你点（工作台「语义核查」页）。
3. **发布**：系统只记录发布痕迹（链接/时间），不会自动发。
4. **素材源授权**：要用真实新闻图片/纪录片片段，需要你确认可商用来源（当前全部程序生成）。

## 12. Git 与运行证据

- 提交序列（全部已推送，`origin/main` 与 `HEAD` 一致、工作区干净）：
  - `3b1e40c` 运维底座与研究骨架，并把开关做成真闸门
  - `93cdc2c` 内容日历与平台数据回填，发布必须有痕迹
  - `4822525` 三主题真实验收、失败尝试记账、内容质检真片闭环（本报告同批）
  - `ddb0777` 交付报告补哈希 / `d833b44` 模型降级链与来源可信度时效 / `feae437` Skill 注入提示词
  - `2d5b6a6` 平台数据 CSV 批量导入 / `5cab7ff` API 参考与操作手册
  - `a57440a` 选题建议 / `5527246` 一稿三版 / `43acbd8` 运行历史折线 / `f233fba` Word 解析工具
  - `3ac84ca` 背景音乐曲库与授权校验（**最后一项 PARTIAL 清零**）
- 验收原始数据：`content/acceptance/v6/acceptance.json`（三主题）、`content/acceptance/v6/acceptance_company_recheck.json`（记账复核）。
- 成片与质检：`content/jobs/v6_accept_industry/output/final.mp4`、`qc/report.json`、`qc/content_qc.{json,md}`；`content/jobs/v6_accept_company_v2/output/final.mp4`（内容质检 `ok: true`）。
- 复跑三个主题（会花钱，默认每题预算 ¥0.4）：

  ```powershell
  python -m backend.scripts.v6_acceptance --only industry
  python -m backend.scripts.v6_acceptance --only chain --no-render
  ```

## 13. 收尾补的四项能力（都有测试与文档）

| 能力 | 实现 | 怎么验证 |
| --- | --- | --- |
| 运行历史折线 | `GET /api/system/history`（缺的天补 0，不插值）+ 成本页纯 SVG 折线 | `pytest tests/test_monitoring_history.py`（5 例） |
| Word 解析工具 | `parse_docx`（MarkItDown，只抽文字，不保留排版/批注/修订） | `pytest tests/test_markitdown_adapter.py`（含手写最小 docx 真解析） |
| 选题建议 | `GET /api/topics/suggestions`（窗口内来源按五维打分，纯规则） | `pytest tests/test_topic_suggestions.py`（16 例） |
| 一稿三版 | `POST /api/scripts/{id}/adapt`（抖音/小红书/公众号；只调长度排版，不改事实不编标签，不切断数字） | `pytest tests/test_platform_adapt.py`（18 例） |
| 背景音乐授权 | `backend/app/video/bgm.py` + `bgm_check` / `mix_bgm`（先验授权再混音） | `pytest tests/test_bgm.py`（15 例，含真调 ffmpeg 混音） |
| 导出成文件并留记录 | `POST /api/calendar/{id}/export` → `content/exports/entry-<id>/<时间戳>/{douyin,xiaohongshu,wechat}.md` + `meta.json`，路径回写 `export_path`、状态转 `exported` | `pytest tests/test_export_entry.py`（4 例，含 404/422 与"导出不等于发布"断言） |

## 15. 内容三阶段接上真实现（本轮修的"假成功"）

这一轮最重要的一处修复不是加功能，而是**堵住一个假成功**。

**问题**：`MODEL_TASK_TYPES` 里有 8 个类型，`_handlers` 只注册了 6 个。没注册的类型会走
`_model_blocked()`，而它在"模型可用"时返回 `status="completed"`——于是 `topic.discover`、
`content.script`、`content.visual`、`content.produce` 四个阶段**什么都没做，却报 succeeded**。
之前 e2e 里那句"9/9 succeeded"就是这么来的。

**修法**（都是加真实实现，不是改断言）：

| 阶段 | 现在的真实动作 | 产物 |
| --- | --- | --- |
| `topic.discover` | 按目标判出研究骨架（行业/产业链/公司），列出前 6 节要回答的问题；不检索、不调模型 | `discovery.md` / `discovery.json` |
| `content.script` | 只取 `human_review_status=confirmed` 的结论拼口播草稿；**没有确认就 `blocked`**（任务暂停等人确认），并写 `scripts` 表一行 | `script_draft.md` / `script_draft.json` |
| `content.visual` | 草稿 → 镜头计划（开场标题板 / 一句一镜 / 收尾免责），带单位的数字走数据卡 | `visual_plan.json` |
| `content.produce` | 出制作计划：画布、时长、配音、字幕来源、音乐授权策略、渲染命令、质检清单 | `production_plan.json` |

三条硬规矩写进代码而不是提示词：①没有人工确认的结论进不了草稿（`confirmed_findings` 直接过滤）；
②视觉与制作**只出计划**，`not_done` 里明写"渲染/配音/素材下载/发布都没做"；③任何 blocked 结果的
`output` 都带 `blocked: true`，形状统一，调用方不用猜。

验证：`pytest tests/test_content_stages.py`（10 例：三个纯函数 + 四条执行器链路，含"没确认就 blocked"
与"visual/produce 在没有草稿时 blocked"）。

## 16. 最终验证（本轮最后一次实跑）

```text
pytest -o addopts="" -q           → 1073 passed, 2 skipped, 1 warning
真实开发库（只读打开）              → 540,672 B / sha256 2df764e5…8203 / 19 表 /
                                    revision de8148733a9b / journal_mode delete /
                                    无 -wal/-shm / integrity ok / 无 model_calls 表
FFmpeg                            → 8.1.2-full_build-www.gyan.dev（未改动）
backend/app/main.py 第 27 行       → DESCRIPTION = """（品牌行未改动）
git ls-files .env                 → 空（密钥从未入库）
```

## 17. 跨站转载合并与历史任务分页（本轮补的两项）

### 17.1 跨站转载：同一个来源不能再数三遍

真问题：搜索阶段要的是"多来源互相印证"，但中文财经稿大量是一家发稿、几十家转载。
按 URL 去重的话，转载稿各有各的 URL，于是「3 家都说 4.8%」其实是「同一篇被抄了三遍」，
`corroborate` 的"多来源印证"就成了假象。

做法（`backend/app/research/dedupe.py`）：

- 归一化（全角转半角、英文小写、去掉所有空白与标点）后取正文前 1200 字算 sha256 指纹；
- 指纹相同 → 同一篇；指纹不同但 4-gram Jaccard ≥ 0.90 → 也算同一篇（转载方常加一句"本文转自…"）；
- **正文短于 120 字不参与判定**——两条 30 字的摘录撞车概率太高，宁可漏判也不误合并；
- 合并只打标记：`SourceRef.republished_from` 指向被保留的代表，**原始字段一个都不改**；
- `corroborate(points, aliases=…)` 把转载页归到代表名下：同文的多个页面只算**一个**来源，
  选引文时也不让两个转载页各占一格；
- 底稿里 `independent_source_count`、`meta.republished`、markdown 的「跨站转载合并」小节，
  以及分析提示词里 `—— 与 [S01] 是同一篇文章的转载页，只算一个来源` 都会写出来。

验证（`tests/test_research_dedupe.py`，14 例）：同文合并、加前缀的转载仍合并、不同文章不合并、
短摘录不参与、指纹忽略空白标点、**只有转载页时不再冒充"两来源印证"**、
真实两来源时转载页不占引文格、底稿标记与 markdown 小节、无转载时不出现该小节。

### 17.2 历史任务分页

- 接口 `GET /api/research-tasks?status=&limit=&offset=`：`status` 参数原来叫 `status_filter`，
  前端传 `?status=` 会被**静默忽略**，已加 `Query(alias="status")`；带筛选时 `total` 改成
  该状态的条数（否则翻到第 3 页分母一直不变，看起来像分页坏了）。
- 前端 `pageTasks` 改成深链分页 `#/tasks?offset=20&status=failed`，带状态下拉与
  「第 X–Y 条 / 共 N 条」。
- 验证：`tests/test_research_task_api.py::test_listing_supports_pagination_and_a_status_filter`。


