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
| **PARTIAL** | 0 | 已无（原本两项：产业链示意图在 §18 关掉，脚本改写见 §19） |
| **TEST_ONLY** | 1 | `contradicted` 判定：代码 + 单测通过，**两次真实语料尝试都没出现**（最新一次专门挑了「储能新增装机口径冲突」主题：21 来源 / 380 证据 / 4 条核查，结果是 partially_supported 1 + insufficient_evidence 3 —— 模型宁可判「证据不够」，也不轻易判「与证据冲突」，这本身是保守但诚实的表现） |
| **CODE_ONLY** | 0 | — |
| **BLOCKED** | 2 | 真实素材获取（需授权素材源）；云渲染/发布（未授权） |
| **NOT_IMPLEMENTED** | 1 | 多平台账号 API 对接（抖音/小红书等开放平台需企业资质与授权，不是本地能补的） |

### 9.1 现在没有 PARTIAL 了（这一段留给"什么时候该改回去"）

原本挂在这里的两项都已落地：

- 「动态产业链图 / 动画」→ §18；
- 「脚本生成（AK 风格）」→ §19（模型改写 + 逐句校验）。

判断标准不变：**程序能证明的才算 DONE**。比如"脚本一次成型"仍然建议人工润色一次，
但那是编辑判断，不是能力缺口——代码已经能把确认过的结论写成可用的口播稿。

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
pytest -o addopts="" -q           → 1101 passed, 2 skipped, 1 warning
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

## 18. 产业链示意图（本轮关掉最后一项 PARTIAL）

数据卡只能讲一个数字，讲产业链要的是"东西怎么流动、利润卡在哪一环"。做法
（`backend/app/video/chain.py` + `backend/scripts/chain_diagram.py`）：

- **图上只写有依据的东西**：节点取自研究结论里 `section` 命中 `upstream / midstream /
  downstream` 的句子，每行最多 3 个、每个最多 12 字；挂不上骨架的结论不上图；
- 某一环一条都没查到就写「（本环缺证据）」——**空着比编一句好看**；
- 三行按"整段时长三等分"依次进入（透明度 + 横向位移），行间箭头一路下沉、整块内容
  缓慢推近到最后一帧（否则纯色底会被 `freezedetect` 判成冻结画面）；
- 画板写成 `assets/compositions/<id>.json`，同时登记进 `assets_manifest.csv`
  （B 类原创、授权 `allowed`），分镜里用 `asset_type=composition` 引用它。

真机验证：

```text
python -m backend.scripts.chain_diagram --job <job> --seconds 4
→ 已生成产业链图：assets/compositions/chain_01.json（120 帧）
  upstream: ['硅料价格下跌三成'] / midstream: ['组件价格跌破现金成本线']
  downstream: ['电站装机增速放缓']
```

引擎侧真渲染 24 帧（工程目录内需先有 `fonts/NotoSansSC-VF.ttf`，与数据卡同一套字体
同步逻辑；缺字体时引擎报 `ContractError: Asset missing or unsupported: fonts/…`），
抽帧目视核验：三行标签与节点齐整、箭头在行间、最后一帧三行齐全。

验证：`tests/test_video_chain.py`（11 例：三行顺序与去重、无 section 不上图、每行上限、
空环节文案、逐行进入、画板契约、最后一帧仍在变化、登记 B 类且幂等、`register=False`
不建清单）。

## 19. 脚本改写接上模型（最后一项 PARTIAL）

规则版能把确认结论拼成句子，但拼出来是「资料腔」，不是口播。做法
（`backend/app/content/stages.py` 的 `build_script_messages` / `parse_script_draft`，
执行器 `_script` 里调用）：

- **提示词里只有人工确认过的结论**，带 1..N 编号，外加「结论里出现过的数字白名单」；
- 模型返回 `{"title","hook","lines":[{"text","source_index","role"}]}`，每句必须标注来自哪条结论；
- 逐句校验（对不上整句丢弃，进 `dropped`，不静默保留）：
  1. `source_index` 必须指向一条给定结论；
  2. **带单位的量**要原样出现在它引的那条结论里（「1.52%」不能变「1.5%」，
     「70-75 亿元」不能变「70 亿元」）；
  3. 其余裸数字也要原样出现（年份、计数）；
  4. 单句超过 45 字在标点处截断，角色不在词表里回落到 `evidence`；
- 模型不可用 / 返回的不是 JSON / 一句都没对上 → **退回规则版**，原因写进产物 `notes`
  （`generated_by` 仍是 `rules`，不冒充模型）；
- 成本按模型返回的真实 usage 回填 `calls / tokens / cost_cny`，走同一套预算闸门。

真机验证（一次调用，2026-11-14 00:21 +08:00）：

```text
主题：商业银行怎么赚钱（用 %TEMP% 下演示库的副本，先确认一条核查记录）
模型：deepseek-flash  prompt 341 + completion 918 = 1,259 tokens  ¥0.008548
产出：主链毛利率为负，这条链谁在硬扛
  一条主链，四个环节，全在亏钱。                   （hook，挂 S16/S19）
  2024年三季度起，主链毛利率持续为负。             （evidence）
  到2026年一季度，硅料、硅片、电池、组件普遍亏损。 （evidence）
  通威2024年预亏70-75亿元。                        （evidence）
  亏的不是某一个环节，是整条产业链。               （mechanism）
  全产业链亏损，这就是主链当下的账。               （closing）
丢弃：0 句
```

**如实说明两点**：①这次探针直接调网关、没接数据库 session，审计行没落库
（网关打的是默认库，那里没有 `model_calls` 表，写入被拒——所以**真实开发库也没被写**）；
②真跑链路（执行器 `content.script`）会带 `session_factory`，审计行正常落库，单测
`test_executor_uses_the_model_and_charges_the_call` 已钉住「调用 1 次、token 1000、
成本 0.003412 元」的记账。

验证：`tests/test_script_model.py`（8 例：提示词只含确认结论、正常改写、无来源丢弃、
数字不在结论里丢弃、区间不许压成单值、角色回落与非 JSON、执行器记账、失败退回规则版）。

---

## 20. 产业链图接进自动链路 + 配音重排不再留静音

上一节把产业链示意图做出来了，但它当时只能手工跑 CLI，而且**镜头时长是写死的 5 秒**——
配音一变长，画板后半段就是静止的最后一帧；配音比 5 秒短，镜头尾部又会留一段纯静音。
本节把这两件事一起修掉，并且是真机渲染验证过的。

### 20.1 自动插进分镜（`backend/app/video/chain.py`、`backend/app/video/handoff.py`）

- `insert_chain_shot(job_root, *, title, stages, asset_id="CHAIN_01", seconds=5.0, ...)`：
  写画板 → 读回分镜 → **插在开场之后**（先看结构再看数字）→ **重编镜头号**（避免两个 `sh02`）→
  `retime_rows` 重排时间 → 写盘 → 再 `load_storyboard` 校验一遍。返回插入位置、镜头号、帧数、总时长。
- `handoff._try_chain_shot(report, directory)`：结论里挂到上游/中游/下游的节点**至少覆盖两段**才插；
  只讲一环时不画（画了三段有一段全是「（本环缺证据）」，反而误导）。插不进去只记
  `{"ok": False, "reason": "chain_failed"}`，**不让交接整体失败**。
- `_try_outline` 的返回里多了 `chain` 字段，`job.json`/`OUTLINE.md` 都能看到这次有没有插、插在哪。

### 20.2 配音多长，镜头就多长（正反两个方向）

`backend/app/video/outline.py` 的 `retime_rows` 原来只放大不缩小：
`length_frames = max(原时长, 配音+0.35)`。于是配音只有 2 秒、镜头 5 秒时，尾部 3 秒是**纯静音**，
`silencedetect` 直接判失败。现在改成：**配到音的镜头以配音为准**（长了拉长、短了缩短，
下限 `min_seconds=2.0`）；没配到音的镜头保持原时长，绝不压成 2 秒。

### 20.3 画板跟着新时长重写（`resize_chains`）

- 新增 `chain.resize_chains(job_root, storyboard)`：按每个产业链镜头的**真实时长**重写帧数，
  三行节点从已有画板里读回来（不换内容，只换时长）。
- `cards.resize_cards` 加了一道保护：画板里**没有 `number` 节点**就不当数据卡处理——
  否则它会把产业链图按卡片形状重写，整张图变成空白。这是接通时才暴露的真缺陷。
- `render.py` 在重排之后同时调 `resize_cards` 与 `resize_chains`，任一有改动就重新导入素材。

### 20.4 真机验证（本地渲染，零费用）

临时任务 `%TEMP%\fmai_chain_render\chain_micro`（2 个来源 + 2 个数据点 + 三行产业链节点）：

| 项目 | 数值 |
| --- | --- |
| 规则分镜 | 4 镜 / 18.0 秒 |
| 插入示意图后 | 5 镜 / 23.0 秒（`sh02` = `CHAIN_01`，150 帧） |
| 配音重排后 | **26.133 秒 / 784 帧 / 4,569,979 字节** |
| 技术 QC | `black_segments=0`、`freeze_longest=0.533s`、`silence=0` → **`failed=[]`** |
| 重排说明 | 「5 个镜头改了时长，长了拉长、短了缩短」+「按新时长重写了 1 张产业链画板」 |

修之前的两版对照（同一任务）：第一版 `freeze` 与 `silence` 两项失败（画板 150 帧撑 210 帧的镜头，
尾部静止；镜头尾部留 2.2 秒静音）；补了 `resize_chains` 后只剩 `silence` 失败；
把「只放大」改成「按配音双向调整」后全绿。

抽帧目视：`sh02` 里上游/中游/下游三行依次出现、箭头下沉、画板整块缓推，底部字幕
「产业链：上游 → 中游 → 下游」。

### 20.5 测试

新建 `tests/test_chain_integration.py`（9 例）：插在开场之后且镜头号连续、时间轴连续不重叠、
总时长等于各段之和、素材进了清单且路径与分镜一致、缺分镜抛 `FileNotFoundError`、
只覆盖一段时不插、覆盖两段时插、`_try_outline` 的返回带 `chain` 结果、示意图坏了不影响交接、
「至少 2 个来源」这条底线没被示意图绕过。

```
pytest -o addopts="" -q   → 1101 passed, 2 skipped, 1 warning
```

## §21 又两个真缺陷：重试的钱没记账、worker 里脚本改写没接上

### 21.1 撞上限重试的第一次没进任务账

跑「产业链」这一题的真实链路时，验收脚本自己报出对不上：

```
花费合计 0.180756 元（任务账 0.133214 / 接口 0.133214，对得上=False）
```

差额 **0.047542** 正好是第一条 `research.verify`（`9520 / 3200 / 0.047542`，
`completion_tokens` 撞满 3200 = 被截断的那一次）。根因在三条"撞上限就减少输入重试"的阶梯路径上：
`analyze()`、`plan_research()`、`check_findings()` 成功返回时**只用最后一次 `record`** 记
tokens 与费用，前面撞上限那几次真花了的钱没进任务账，于是任务账小于模型账单。

改法（都改实现）：三条路径内部就有 `spent` 累加器（每次尝试都累加 prompt/completion/cost/calls），
成功分支改用 `spent` 而不是最后一次 `record`；`SemanticReport` 新增 `notes`，重试时写
`retried:N 次（撞上限后减少条数重试，费用按实际累加）`；`model_call_id` 仍指最后成功那条审计行。

新增三条回归（`tests/test_research_analysis.py`、`tests/test_semantic_check.py`、
`tests/test_planning.py` 各一条）：第一次撞上限、第二次成功，断言 `prompt_tokens`/`completion_tokens`/
`cost_cny` 都是两次之和，而不是最后一次。**注意**：把第二次的 `completion_tokens` 设成等于
`max_tokens` 也会被判成截断（代码里 `completion_tokens >= max_tokens` 同样算撞上限），
所以样本里第二次的用量必须明显小于上限。

### 21.2 `content.script` 在生产链路里从没调过模型

`AgentExecutor` 的 `_plan` / `_analyze` / `_verify_claims` 在 `model_gateway=None` 时会让
`plan_research` / `analyze` / `check_findings` 内部回落到全局 `get_model_gateway()`，
但 `_script` 写的是 `if self._model_gateway is not None:`——而 `backend/scripts/run_worker.py`
构造的正是 `AgentExecutor()`（不注入网关）。结果：**worker 跑出来的脚本永远是规则版**，
模型改写这条能力只在测试和手工探针里生效。

改法：`_script` 先 `self._model_gateway or get_model_gateway()`，再看
`ready_providers()`（对只实现 `complete()` 的测试替身按"就是可用"处理）。
新增回归 `test_a_worker_style_executor_still_reaches_the_global_gateway`
（`AgentExecutor(session_factory=...)` 不传网关 + monkeypatch 全局 gateway）。

### 21.2b 内容阶段的产物目录也会串味

`_stage_dir` 原来固定是 `content/research/task-<id>/content`，而 `task-<id>` 只在一个库里唯一。
实测：临时库里的 `task-1`（研究目标完全不同的另一份研究）跑完之后，剧本草稿被写进了仓库里
真实研究 `task-1`（商业银行那一份）的目录下——和之前 `digest.json` 被覆盖是同一类问题。
改法：`_stage_dir` 也按任务目标算 8 位指纹，发现同目录 `digest.json` 的目标不一致就落到
`task-<id>-<指纹>/content`，与底稿同层；目标一致时仍用原目录（不会因为指纹到处建新文件夹）。
新增回归 `test_stage_dir_does_not_overwrite_another_run_with_the_same_task_id`
（顺带把这次已经串味的四份脚本产物挪回 `content/research/task-1-a6a01af5/content/`）。

### 21.3 验收脚本：不再对着"等人确认"空转

上一轮 `content.script` 因为"没有人工确认过的结论"一直 `blocked`，而验收脚本只会
`POST /resume`，于是同一状态 `resume` 了 **22 次**（每次计数都是 `{'succeeded':10,'blocked':1}`）。
改法：`drive_task` 发现 paused 且阻塞原因是 `no_confirmed_findings` 时，先
`confirm_one_check(task_id)`（找这条任务最新报告里一条待确认核查，优先 `supported` /
`partially_supported`，`POST .../review` 确认），再放行；确认不了就停止空转并记录原因。
返回里多了 `confirmations` 字段。同时把视频任务 id 加上时间戳后缀（同名目录会 409
`job_exists`，历史任务不许覆盖）。

### 21.4 修完后的真实链路（2026-11-14 01:13 +08:00）

`FM_ACCEPT_OUT=acceptance_chain_final.json python -m backend.scripts.v6_acceptance --only chain --budget 0.5`
（临时库 + 真 uvicorn + 真搜索 + 真 DeepSeek）：

| 项目 | 结果 |
| --- | --- |
| 子任务 | 11 个，**11/11 succeeded**（含 `content.script`） |
| 驱动轮次 | 6 轮、放行 3 次闸门、**人工确认 1 次**（脚本扮演人） |
| 规划 | `planner=model`、36 个检索词、6 个规划子任务 |
| 数据 | sources **25**、evidence **440**、report 1、checks 6（`partially_supported 3 / insufficient_evidence 3`） |
| 模型调用 | **5 次**（plan 500/2170 ¥0.019553、analyze 14492/1838 ¥0.046528、synthesize 14524/1638 ¥0.044892、verify 9640/2219 ¥0.039439、**content.script 2550/1446 ¥0.017751**） |
| 记账 | 任务账 **0.168163** == 模型账单 **0.168163**（`对得上=True`） |
| 内容链路 | 确认核查 → `draft-readiness allowed=True confirmed=2` → `video-job 201`（`v6_accept_chain_11131719`）→ `render rc=0` 出片 |
| 密钥 | `prompt_preview` 命中 `sk-` 的行数 **0** |

```
pytest -o addopts="" -q   → 1108 passed, 2 skipped, 1 warning
```

## §22 产业链图为啥没画出来：骨架节点在落库时被丢了

真实链路复跑时 `job.json` 写着 `outline.chain = {"ok": false, "reason": "not_a_chain_story",
"message": "结论覆盖的产业链环节只有 0 段（至少 2 段才画示意图）"}`。一路查下来是两个问题叠在一起：

| # | 真因 | 修法 |
| --- | --- | --- |
| 1 | `executor._persist_report` 组 `findings_override` 时只带 `text/kind/source_ids/numbers`，**把模型给的 `section` 丢了**——库里 6 条结论的 `section` 全是 `null`，任何按节点归行的下游都拿不到东西 | 补上 `"section": item.section or ""`，并加断言"报告行里的结论必须带骨架节点" |
| 2 | `chain.nodes_from_findings` 只认 `upstream/midstream/downstream` 三个词，而产业链骨架有 11 节（`raw_materials`/`equipment`/`value_split`/`cost_structure`/`tech_routes`/`supply_relations`/`opportunities`），真实结论落在这些节上时等于全被丢掉 | 新增 `SECTION_STAGE` 归行表：`raw_materials`/`equipment` → 上游，`tech_routes`/`cost_structure`/`value_split`/`supply_relations` → 中游，`opportunities` → 下游；`bottlenecks`/`policy`/`risks` **不归行**（瓶颈可能出在任何一环，硬塞就是编） |

顺带修掉节点标签的两个读感问题：长标签按「等/的/中/年/元/吨/倍/%」这类停顿字断开，不再留下
「…等原材」这种半个词；数字不切断（`2022年多晶硅售价24万元每吨` → `2022年多晶硅售价…`，
而不是 `售价2…`）。

用真实那次的分析产物验证：修前 1 段、修后 **2 段**（上游 3 条、中游 1 条、下游写「（本环缺证据）」），
所以同样的数据现在会插进示意图。

## §23 再跑一次"故意找冲突"的真实主题（结果：仍没有 contradicted）

为了把最后一条 `TEST_ONLY` 关掉，专门挑了一个**来源口径天然打架**的主题再跑一遍真实链路：

| 项目 | 结果 |
| --- | --- |
| 主题 | 2024 年中国储能新增装机到底装了多少（官方口径 vs 协会/第三方口径） |
| 规划 | `planner=model`、30 个检索词、6 个规划子任务；6 条核心问题全部指向"口径是否一致" |
| 数据 | sources **21**、evidence **380**、report 1 |
| 语义核查 | **4 条**：`partially_supported 1` + `insufficient_evidence 3`（**没有 contradicted**） |
| 模型调用 | 5 次：plan 480/2424 ¥0.021675、analyze 13352/1690 ¥0.042839、synthesize 13384/1938 ¥0.045020、**verify 9184/3200 ¥0.046826（撞上限）→ verify 4876/1818 ¥0.025875（重试成功）** |
| 记账 | 任务账 **0.182235** == 模型账单 **0.182235**（撞上限那次也记进去了，§21 的修复在真实链路里生效） |
| 密钥 | `prompt_preview` 命中 `sk-` 的行数 **0** |
| 花费 | **¥0.182235** |

两点值得记下来：

1. **模型不愿意轻易说"冲突"**：这一轮证据里确实存在"官方累计 73.76GW vs 协会累计 78.3GW"这种
   数字不一致，但模型把它判成 `insufficient_evidence`（理由是给定证据不足以断定两者矛盾），
   而不是 `contradicted`。这符合"宁可说不知道"的设计，也说明 `contradicted` 需要**同一指标的
   直接反证**才触发，不是数字不同就触发。
2. **`content.script` 卡在等人确认**：这一轮 18 次 `/resume` 里有 14 次是同一个原因——
   脚本阶段等人工确认结论。这是设计如此（确认必须由人做），但也提醒操作者：
   **跑长任务时先确认核查，再让它自己往下走**，否则 worker 会反复空转（不影响正确性，只浪费时间）。

本轮累计真实花费：链式复跑 0.151573 + 0.168163、脚本改写 0.008548、本轮冲突主题 0.182235 ≈ **¥0.51**。

## §24 每日体检：一条命令看完全部该看的东西

前面几轮都是"出事之后才发现"（重试的钱没记账、脚本没走模型、产业链图画不出来）。这类问题在
**没人天天盯**的时候会一直躺着。所以这一节补的不是功能，是**每天两秒的自检**：

```
.venv\Scripts\python.exe -m backend.scripts.daily_check          # 人看的
.venv\Scripts\python.exe -m backend.scripts.daily_check --json   # 机器读的
```

它只读、不联网、不调模型，一次汇总七件事：

| # | 看什么 | 数据来源 | 为什么会卡住 |
| --- | --- | --- | --- |
| 1 | 任务各状态计数、暂停的任务、窗口内失败的任务 | `research_tasks` | 暂停多半在等人工闸门 |
| 2 | 窗口内失败的子任务（含原因与尝试次数） | `research_subtasks` | `failure_reason` 直接说是哪一步 |
| 3 | 待人工确认的语义核查条数（及所属报告） | `finding_checks` | **`content.script` 就是等这个**，不确认永远不动 |
| 4 | 被撤回确认而失效的视频任务 | `content/jobs/*/job.json` 的 `draft_gate` | 结论变了，旧片子不能再用 |
| 5 | 今天 / 近 N 天真实花费（调用数、tokens、金额） | `model_calls` 审计行 | 是实际账单，不是估算 |
| 6 | 最近一份备份有多旧 | `data/backups/*.db` | 超过 7 天就提醒 |
| 7 | 项目所在盘剩余空间 | `shutil.disk_usage` | 渲染产物以 GB 计 |

退出码语义：`0` 干净、`1` 有需要处理的事项、`3` 连不上库（路径写错时不假装"一切正常"）。

**对着老库也不会崩**：真实开发库仍停在 `de8148733a9b`（19 张表，没有 `research_tasks`/`model_calls`），
脚本会把这几块标成"查不了"，并提示"这个库还没迁移到当前版本"——**只读诊断不替任何人改库**。
实测输出：

```
数据库：真实开发库（只读）
任务：（没有任何任务）
今天花费：查不了（这个库没有 model_calls 表）
需要处理：
  ! 这几块查不了：任务, 子任务, 语义核查, 花费（这个库还没迁移到当前版本…）
  ! 1 个视频任务的确认被撤回过（已标记失效）
```

`tests/test_daily_check.py` 10 例覆盖：空库、任务/子任务/核查计数、窗口外失败不误报、花费只算当天 UTC、
失效任务扫描（含坏 `job.json` 不炸）、备份过期提醒与"新备份顶掉旧提醒"、受保护库标注、文本渲染八节齐全、
需要处理时退出码 1、库文件不存在时退出码 3。
