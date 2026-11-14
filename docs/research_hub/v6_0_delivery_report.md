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

| # | 交付物 | 路径（2026-11-14 复核） |
| --- | --- | --- |
| 1 | 源代码 | `backend/`（19 个模块目录）、`web/`（20 页工作台）、`scripts/`（4 个 PowerShell） |
| 2 | 配置模板 | `config/{settings,models,agent_routing,pipelines,content_taxonomy,content_rules,brand,research_sources}.yaml`（8 份，全部 `extra="forbid"` 强类型校验） |
| 3 | 库初始化与迁移 | `backend/migrations/versions/`（初始 + B1–B6 共 7 个 revision），配套 `backend/scripts/{backup_db,restore_db}.py` |
| 4 | 启动脚本 | `scripts/start.ps1`（建库、预检、端口占用检查、起服务、健康探测、开浏览器） |
| 5 | 停止脚本 | `scripts/stop.ps1`（只杀 uvicorn + `backend.app.main` 的进程，`-Force` 才强杀） |
| 6 | 环境诊断 | `backend/scripts/doctor.py`（22 项）+ `backend/scripts/daily_check.py`（每日业务体检）+ `scripts/watchdog.ps1`（自愈） |
| 7 | 测试 | `tests/`（**1119 passed / 2 skipped / 1 known warning**） |
| 8 | 端到端验收脚本 | `backend/scripts/v6_acceptance.py`（三主题 + 内容链路）、`ui_acceptance.py`（Playwright 真点击）、`flow_check.py`、`model_e2e_check.py` |
| 9 | 用户操作手册 | `docs/research_hub/v6_0_operator_manual.md`（10 节）+ `README.md`（三步开始用） |
| 10 | 架构文档 | `docs/research_hub/system_architecture.md`（**§12 是 V5–V6 现状快照**）+ `module_design.md` / `api_design.md` / `database_design.md` |
| 11 | Agent / Skill 文档 | `docs/agent_manual.md`（已按现状重写）+ `agents/*/PROMPT.md` + `skills/README.md` + `skills/registry.yaml` |
| 12 | API 文档 | `docs/research_hub/v6_0_api_reference.md`（由 `backend/scripts/dump_api_doc.py` 从 OpenAPI 生成，71 个操作带允许调用者）+ 运行时 `/docs` |
| 13 | 成本与预算说明 | `docs/research_hub/v6_0_cost_and_budget.md` |
| 14 | 安全说明 | `docs/research_hub/v6_0_security_notes.md`（含"没做到的六件事"）+ `AGENTS.md` §2/§3 |
| 15 | 最终验收报告 | 本文件（§1–§24） |

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
pytest -o addopts="" -q   → 1119 passed, 2 skipped, 1 warning
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

`tests/test_daily_check.py` 11 例覆盖：空库、任务/子任务/核查计数、窗口外失败不误报、花费只算当天 UTC、
失效任务扫描（含坏 `job.json` 不炸）、备份过期提醒与"新备份顶掉旧提醒"、受保护库标注、文本渲染八节齐全、
需要处理时退出码 1、库文件不存在时退出码 3、**一切干净时退出码 0**。

### §24.1 同一条体检也做成了首页卡片

命令行没人愿意每天开一次，页面是天天会看的。所以体检从脚本里**抽成服务层**
`backend/app/services/daily_service.py`，CLI 与接口共用同一份判断：

| 入口 | 位置 | 谁用 |
| --- | --- | --- |
| 命令行 | `backend/scripts/daily_check.py`（122 行薄层） | 运维、脚本、定时任务 |
| 接口 | `GET /api/system/daily?days=N`（`HUMAN_ONLY`，`Days` 上限 90） | 工作台首页卡片 |
| 页面 | `web/app.js` 的 `dailyCard()`，首页第一张卡 | 人 |

**为什么必须共用一份**：两个入口各写一遍，迟早出现"命令行说 3 件待办、页面说 5 件"这种没人敢信的东西。
现在首页卡片底下直接写着一行小字：「这条卡片和 python -m backend.scripts.daily_check 是同一套判断逻辑（生成时间），
所以命令行和页面不会打架」。

真机实测（演示库 `%TEMP%mai_demo\demo.db` 副本，服务跑在 8013，密钥留空故本轮 0 元）：

```
GET /api/system/daily?days=7 -> 200
ok=False  attention=[5 条语义核查还没人工确认（报告 [1]）——content.script 会一直等着,
                     1 个视频任务的确认被撤回过（已标记失效）,
                     data/backups 里还没有任何备份]
cost.today={calls:0}  cost.window={calls:4, tokens:47132, cost_cny:0.159294}
checks={by_status:{confirmed:1,pending:5}, pending:5, pending_reports:[1]}
```

无头浏览器（Playwright + 本机 chromium）打开 `#/` 实拍：卡片标题「今天的体检 3 件要处理」、
六枚标签（今天调用 0 次 / 近 7 天 4 次 ¥0.159294 / 待人工确认 5 条 / 暂停任务 0 个 / 最近备份 没有 / 磁盘 6.3 GB 可用）、
三条待办逐条列出、三个跳转按钮（看花费明细 / 看任务 / 去确认核查），
`console_errors=[]`、`page_errors=[]`。

`tests/test_daily_api.py` 5 例守住形状：必需键齐全、`ok` 与 `attention` 一致、
`days` 越界（0 与 91）返回 422、窗口天数原样带回、**空库（一张表都没有）不抛异常而是把四块标成查不了**。

### §24.2 顺手修掉的两处显示问题

1. 待办文本里原来带 Markdown 反引号（`——`content.script` 会一直等着`），在页面上原样显示成乱码一样的反引号；
   现在统一去掉反引号，命令行和页面读着都正常。
2. `sections_unavailable` 的键是给人看的中文标签（任务 / 子任务 / 花费 / 语义核查），值是数据库给的原因
   （`no such table: research_tasks`）——一开始测试按英文键断言，按真实形状改了测试而不是改实现。

## §25 确认完就能继续跑：把"下一步"答出来

前面几轮把闸门做严了（结论必须人工确认才进成稿），但留了一个体验缺口：
人在「语义核查」页逐条确认完，页面上没有告诉他"然后呢"。他得自己跳到「研究任务」页、
翻出是哪一条任务、看清卡在哪个阶段、再点恢复 + 推进。三步都在不同页面，实际上没人会这么干。

### §25.1 新增 `GET /api/research-reports/{id}/pipeline`

一份报告现在能直接回答四个问题：它属于哪条研究任务、任务现在什么状态、卡在哪个阶段（含稳定原因码）、
现在能不能点「继续跑」。

```json
{
  "report_id": 1, "research_task_id": 1,
  "task_status": "paused", "paused": true,
  "blocked": {"subtask_id": 12, "sequence": 12, "task_type": "content.script",
              "attempts": 1, "failure_reason": ""},
  "pending_checks": 5, "confirmed_checks": 1,
  "can_resume": true,
  "next_step": "任务停在人工闸门上，点「继续跑」放行"
}
```

判定口径（写在实现里，不靠前端猜）：

| 情况 | `can_resume` | `next_step` |
| --- | --- | --- |
| 卡在 `no_confirmed_findings` 且一条都没确认 | `false` | 先确认至少一条结论 |
| 卡在 `no_confirmed_findings` 但已确认 ≥1 条 | `true` | 可以点「继续跑」接着生成脚本 |
| 卡在 `no_script_draft` | `true` | 继续跑把 `content.script` 跑完 |
| 停在其它人工闸门 | `true` | 点「继续跑」放行 |
| 任务在跑 / 已完成 / 失败 / 已取消 | `false` | 如实说明该干什么，`note` 带失败原因 |
| 报告没有关联任务 | `false` | 说明可直接走脚本/视频链路 |

**确认本身不会触发花钱**：这里只回答"能不能继续"，要不要跑由人按键决定。
真正执行还是原来的两条接口（`POST /api/research-tasks/{id}/resume` 与 `/run`），没有新增自动执行路径。

### §25.2 页面：确认按钮旁边就是"下一步"

「语义核查」页在核查汇总卡下面加了一张「下一步」卡：任务号与状态、「卡在 `content.script`（等人放行）」、
已确认/待确认条数、一句话建议、以及（条件满足时才出现的）「继续跑（先恢复，再推进 3 格）」按钮，
底部一行小字写清"确认本身不会让系统自己花钱"。

无头浏览器实测（演示库副本，`can_resume=true` 的那份）：卡片渲染正确、
按钮存在、`console_errors=[]`、`page_errors=[]`；
`can_resume=false` 的那份（任务已完成）按钮不出现、文案变成「任务已经跑完，剩下的都是人工确认与出片」。

### §25.3 顺手修掉的表格错位

核查明细表原来写的是 `<table class="grid">`——而 `styles.css` 里 `.grid` 是给卡片用的
`display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))`，
挂在 table 上会把表头和数据行拆成互不对齐的两块：表头挤在左边，数据从中间开始，
结论那一列被挤到一行一个字。

修法两句：去掉 `class="grid"`、把表包进已有的 `.scroll`（`overflow-x: auto`）并给长文本列加 `min-width`。
现在 8 列横向滚动，结论、理由、支持/反对片段都读得开。

### §25.4 测试

`tests/test_report_pipeline.py` 6 例：卡在未确认时不许继续、确认一条后允许继续、
运行中的任务不需要人推、失败任务把原因带出来而不是假装能恢复、没有关联任务的报告如实说明、
未知报告 404。全量 `pytest -o addopts="" -q` → **1144 passed, 2 skipped, 1 warning**。
接口数 71 → **73 个操作**（`/api/system/daily` 与 `/api/research-reports/{{id}}/pipeline`），
启动自检双向核对仍然 8/8 通过。

---

## §26 模型写的那份稿子终于进得了片子（脚本 → 分镜）

### §26.1 问题：两条互不相干的喉舌

到这一轮为止，系统里有两套话：

* `content.script` 阶段让模型把**人工确认过的结论**改写成 AK 风格口播稿，存进 `scripts` 表，
  逐句核对来源与数字（不在所引结论里的数字整句丢弃）；
* 建视频任务（`POST /api/research-reports/{id}/video-job`）却只按报告里的数据点排**规则分镜**
  （`video/outline.py`：一句话一个数据点、镜头模板固定）。

两条线各自都跑得通，但搭在一起就出问题：模型写出来的稿子**从来没有进过片子**，
片子里念的永远是规则摘引的句子。任务书 G/H 两段要的是"脚本 → 分镜 → 成片"，这里断了一环。

### §26.2 做法：一句一镜，带单位的句子做数据卡

新增 `backend/app/video/script_board.py`：

| 函数 | 干什么 |
|---|---|
| `draft_from_script(script)` | 把 `scripts` 表的一行还原成 `ScriptDraft`（`segments` 里存着每句的文字、角色、来源、数字） |
| `storyboard_from_draft(draft, job_root=…)` | `plan_shots` → 逐镜算时间轴 → 带单位的句子生成数据卡 → 写 `storyboard/storyboard.csv` → **读回来校验** → 登记素材 → 写 `storyboard/FROM_SCRIPT.md` |
| `script_storyboard(script, job_root=…)` | 上面两步合一（接口与脚本直接用这个） |

四条硬规矩：

1. **句子一个字都不改**：脚本是模型逐句核对过来源与数字的产物，排分镜只负责"怎么上屏"。
2. **只认带单位的量上卡面**：`1.52%`、`4.28 万亿元` 这类能做数据卡；"2024 年第四季度"里的
   `2024` 不做卡（日历数字当主数字会误导，这是前几轮踩过的坑）。单位表与规则分镜
   **共用一套**（`video/outline.py` 的 `_UNITS`），这一轮补上了 `GW/MW/GWh/MWh/kWh/
   千瓦时/千瓦/兆瓦/吨`——实测拿一份真实光伏报告跑，补之前 4 个镜头**零数据卡**，
   补之后同一份稿子出 1 张卡（`920GW`）。
3. **来源跟着句子走**：每镜的 `source_ids` 落到分镜表的可追溯列，内容质检靠它判"这一镜能不能回溯"。
4. **时间轴按整帧累加**：`plan_shots` 给的是 `字数/4.2` 这类浮点秒（如 11.43 秒），直接写进分镜表
   会被 `load_storyboard` 判成 `bad_time`（时间必须落在整数帧上）。所以先换算成帧数累加，
   再写 `%.9f` 的秒——渲染器本来就是逐帧取图的，这一步不做，渲染阶段才报错就晚了。

顺手把 `video/outline.py` 里私有的 `_caption_around` 包成公开的 `caption_around()`：
"把句子截到 18 字还不切断数字"这套口径只能有一份，两处各写一遍迟早不一致。

### §26.3 接线：有脚本按脚本，没脚本按规则

* `video/handoff.py` 的 `create_video_job(..., script=None, script_id=None)`：
  给了脚本就走 `_try_script_storyboard`，没给才走 `_try_outline`。
* **脚本排不出来时不悄悄退回规则分镜**：规则分镜念的是原文摘引、脚本站的是另一回事，
  悄悄换掉等于让人以为"片子就是脚本那条"。所以只如实记 `outline.reason`（如 `empty_script`、
  `too_few_lines`）并保持 `storyboard/` 为空。
* 用哪种来源写进 `job.json.outline.source`（`script` / `rules`）与 `outline.script_id`，
  `next_step` 也跟着换文案（脚本那条指向 `FROM_SCRIPT.md`）。
* 接口侧 `POST /api/research-reports/{id}/video-job` 通过 `ScriptService.latest_for_report()`
  取该报告**最新一版**脚本（按 `version` 倒序），显式传入。

### §26.4 测试

`tests/test_script_board.py` 9 例（全离线）：脚本往返还原、空脚本拒绝、一句排不出片、
逐句保留原句与来源、带单位那句生成数据卡并登记素材、无数字保持纯色底且字幕等于原句、
时间轴从 0 开始且相邻相接、与 `plan_shots` 镜数一致、`FROM_SCRIPT.md` 写明"不许在这里改字"。

`tests/test_video_handoff.py` 新增 3 例：脚本在手时按脚本排（并断言规则分镜的 `OUTLINE.md`
不出现）、脚本坏掉时如实报 `empty_script` 而不是悄悄换规则分镜、接口层真的取了库里最新的脚本。

全量 `pytest -o addopts="" -q` → **1144 passed, 2 skipped, 1 warning**（唯一 warning 仍是
`agent_runs↔research_subtasks` 双向外键环，已记录为设计内风险）。

### §26.4b 拿真实研究产物实测一次

演示库（真实跑过的那份光伏主题的副本，报告 1 + 6 条核查）上：

1. 库里只有 1 条确认结论 → 规则版草稿 1 句 → 分镜**如实拒绝**（`too_few_lines`），
   `storyboard/` 保持为空，没有被偷偷换成规则分镜；
2. 在副本上再确认一条（模拟人在核查页点第二下）→ 草稿 2 句 → 分镜排出来：
   **4 个镜头 / 31.5 秒 / 1 张数据卡**，第一句脚本原样出现在 `storyboard.csv` 里，
   `FROM_SCRIPT.md` 已写出，`job.json.outline.source == "script"`、`blocked == false`。

整个验证过程只读演示库副本、不渲染、不调模型、不联网。

### §26.4c 真渲染之后又发现两处"看起来没问题"的问题

第一版真出片（39.9 秒 / 1197 帧 / QC 全过）抽帧目视时发现，**质检九项全绿，
画面其实有两处不合格**——这类问题程序测不出来，只能人眼/多模态看：

1. **字幕顶到左右边缘**：脚本分镜自己写了个 `MAX_CAPTION_CHARS = 24`，而规则分镜是
   `18`。46px 中文字幕在 1080 宽上大约每字占 1.35 个字号，18 字正好铺满，24 字必然溢出。
   修法两步：把上限**统一成规则分镜那一套**（`script_board.MAX_CAPTION_CHARS` 直接取
   `outline.MAX_CAPTION_CHARS`），再给渲染加**字号自适应**——`render.caption_font_size()`
   按这一份分镜里最长的一句估宽度，超了就只降不升地把字号压到放得下（下限 32）。
   实测同一份分镜：46 → 41，最长字幕 970px / 1080（占 90%），左右都有边距。
2. **片尾字幕像没写完**：`"本片为合规资料整理，不构成投资建议；所有数字…"` 被硬截成
   18 字加省略号。修法是**按标点收口**：句末标点（`。；！？`）命中就从那里断且不加
   省略号；逗号类要留够 13 字才允许断并补省略号；都没有才硬截。
   现在片尾是完整的"本片为合规资料整理，不构成投资建议"。

3. **卡面说明也可能顶出卡片**：数据卡的说明是 50px，同样按"18 个字铺满"的账算，
   一句 19 字的说明就会溢出。修法是把宽度估算抽成**共用模块**
   `backend/app/video/textfit.py`（`display_width()` / `fit_text_size()`），字幕与
   数据卡都调它——两处各算一套正是单位表那次翻车的同一个病根。卡面说明按 0.86 画幅
   预算缩放，下限 30px；短说明保持 50px 不动。

三条都补了回归（`tests/test_caption_layout.py` 6 例 + `tests/test_script_board.py`
1 例），全离线；改完重渲染同一份分镜：39.9 秒 / 1197 帧 / QC `failed=[]`，
画面与上一版一致（这条分镜的卡面说明只有 14 个字，字号不变属预期）。

### §26.5 这一步之后仍然没做的

* 分镜的"镜头时长"还是按字数估的（4.2 字/秒）——真正对齐要靠配音实测时长，
  那一步在渲染阶段（`render_job --narration`）才会按实测重排时间轴。
* 脚本里的"钩子句"只作为开场镜的口播，没有单独做标题卡；要更强开场需要人工在分镜表里改。
* 多镜头复用同一句话时会各排一镜（不去重），因为重复多半是脚本自身的问题，交给审核环节发现。

---

## §27 一键启动终于把执行器也带上了

### §27.1 问题：界面建的任务永远停在「已创建」

`scripts/start.ps1` 原来只起 `uvicorn`，`backend/scripts/run_worker.py` 要另外手工开一个窗口。
第一次真机验收时踩到的就是这个：界面里建了研究任务，刷新十几次都是「已创建」，
翻日志才发现没有任何进程去认领子任务。这不是"系统坏了"，是入场第一步就绊住。

### §27.2 改法

* `scripts/start.ps1` 新增「5. 后台执行器」段：起服务后用 `Start-Process` 拉起
  `python -m backend.scripts.run_worker --forever`（隐藏窗口，日志写 `logs\worker.out.log`
  与 `logs\worker.err.log`），并把 **pid 与启动时间** 写进 `logs\worker.pid`：
  `pid=26608` / `started=2026-11-14T04:29:12.6145906Z`。
* 参数 `-NoWorker` 给"只想看看页面"的场景；`finally` 里服务退出时会顺手停掉执行器。
* `scripts/stop.ps1` 收尾时解析那个 pid 文件：**先比对启动时间**（Windows 会复用进程号，
  只认 pid 早晚会杀错人），差超过 120 秒就判"不是这次启动的"并跳过，要强停加 `-Force`；
  命令行读得到时才退回命令行匹配。

### §27.3 为什么 pid 文件要记两行

第一版只写了 pid，真机第一轮 `stop.ps1` 就报「pid 648 不像是本项目的执行器，跳过」——
实测 `Get-CimInstance` 拿到的 `CommandLine` 偶尔是空串（进程刚被拉起或正在退出时），
命令行判断就失效了。加一行启动时间之后，判断不依赖命令行也能做对。

### §27.4 真机验证（两轮，端口 8099）

| 步骤 | 实测 |
| --- | --- |
| `start.ps1 -Port 8099 -NoBrowser -SkipPreflight` | 「启动自检：8/8 项通过」、`GET /health -> 200`、worker 日志首行 `{"event": "start", "database_url": "…finance_media.work.db", "worker": "worker-daemon"}` |
| `logs\worker.pid` | `pid=26608` / `started=2026-11-14T04:29:12.6145906Z` |
| `stop.ps1 -Port 8099` | 「已停止 1 个服务进程；跳过 0 个」、「后台执行器已停止（pid 26608）」、「端口 8099 已释放」 |
| 收尾核对 | 端口监听数 0、pid 文件已删、无残留 `run_worker` 进程 |

用的是 `data/finance_media.work.db` 工作库，**受保护的真实开发库自始至终没碰**。

### §27.5 顺手修掉的两个小坑

* **BOM**：`edit`/`write` 工具保存 `.ps1` 不带 BOM，PowerShell 5.1 在中文机器上会按 GBK 解析 →
  `tests/test_scripts_encoding.py` 立刻红。这次统一把三个脚本改回 **UTF-8 带 BOM + CRLF**。
* **护栏太粗**：原来的断言是"stop.ps1 里不许出现 `Remove-Item`"，可删自己的 `logs\worker.pid`
  是合理操作。改成**逐行检查**：含 `Remove-Item` 的行不得出现 `.db`、`data`、`-Recurse`。

### §27.6 测试

`tests/test_scripts_encoding.py` 新增 2 例：`test_start_script_runs_the_worker_by_default`
（必须有 `-NoWorker`、`run_worker`、`worker.pid`、`started=`）与
`test_stop_script_only_kills_the_worker_it_started`（必须读回启动时间、比对 `StartTime`、
留 `-Force` 口子）。文件 15 例全过。

---

## §28 排期里那条片子，到底能不能发

### §28.1 问题：日历只知道"打算发什么"，不知道"片子成不成"

`content_calendar` 记的是计划：哪天、哪个平台、哪条标题。可到要发的那一刻，
真正要回答的是三个更硬的问题——片子渲染出来了吗？它依据的结论现在还作数吗？导出过没有？
这三件事的答案都不在数据库里，只在 `content/jobs/<job_id>/` 这个目录里。

真实数据里就躺着一条反例：`content/jobs/v6_accept_industry/` 的 `output/final.mp4` 还在
（一条 37.8 秒的成片），但 `job.json` 的 `draft_gate.usable` 已经是 `false`——
它的结论后来被撤回了确认。**文件还在，结论已经不作数**，拿去发就是拿旧结论冒充当前结论。

### §28.2 做法：读目录事实，按"先闸门后文件"的顺序判

新模块 `backend/app/services/calendar_readiness.py`，`job_readiness(video_job_id, *, jobs_root=None)`
返回五种状态之一（每种都带人话标签与下一步建议，只有 `ready` 的 `usable` 为真）：

| 状态 | 什么时候 | 下一步 |
| --- | --- | --- |
| `no_job` | 排期还没填任务号 | 先建视频任务，渲染完回来填号 |
| `job_missing` | 目录或 `job.json` 不在 | 确认是不是换了机器或删过目录 |
| `film_missing` | 目录在、`output/*.mp4` 没有 | 到视频任务里跑一次渲染 |
| `draft_invalid` | `draft_gate.usable` 为 false（**哪怕片子还在**） | 重新确认结论后重跑渲染 |
| `ready` | 片子存在且闸门没判失效 | 可以导出/发布；发完回来填链接 |

判定顺序刻意是"先闸门、后文件"：如果反过来，`draft_invalid` 那条永远显示"就绪"。

接口侧：`GET/POST/PATCH /api/calendar*` 的返回里多了 `readiness` 字段（`CalendarReadinessOut`），
列表、详情、新建、改排期四处都有。前端「内容日历」页多了「底片就绪度」一列（就绪绿、
失效/缺片红），并给了「绑任务号」按钮——绑完立刻能看到这个号到底成不成。

### §28.3 实测

对仓库里现成的 11 个任务目录逐个判：

| job_id | 判定 |
| --- | --- |
| `v6_accept_industry` | `draft_invalid`（片子 37.8 秒还在，但结论被撤回） |
| `v6_accept_company_v2`、`v5_flow_acceptance_v2`、`v6_accept_chain_11131719` 等 | `ready` |
| 不存在的目录 / 空任务号 / 带 `../` 的输入 | `job_missing` / `no_job` / `job_missing` |

### §28.4 测试

`tests/test_calendar_readiness.py` 9 例：五种状态的构造与断言、穿越输入被拒、
影片存在但闸门失效时**仍要如实报出片子路径**（不能因为失效就说"没有片子"）、
渲染回执会带出来、接口层列表/详情/新建/改排期四处都带 `readiness`。
与既有的 `tests/test_operations.py` 合跑 23 例全过。

## §29 素材清单审计：清单说的和片子里有的，是不是一回事

`assets_manifest.csv` 上写着"这份片子的每一个画面和声音都登记在案"。这句话以前没人验过。

### §29.1 先发现清单本身是缺的

把 `content/jobs/` 下三个成片任务的清单、分镜表、`project/timeline.json` 摆在一起看：

| 任务 | 清单 | 时间轴里的 clip | 缺的那条 |
| --- | --- | --- | --- |
| `v6_accept_company_v2` | `CARD_sh02`、`CARD_sh03` | `solid_1d2a44`、`CARD_sh02`、`CARD_sh03` | `solid_1d2a44` |
| `v6_accept_industry` | `CARD_sh02` | `solid_1d2a44`、`CARD_sh02` | `solid_1d2a44` |
| `v5_flow_acceptance_v2` | `CARD_sh02`、`CARD_sh03` | `solid_1d2a44`、`CARD_sh02`、`CARD_sh03` | `solid_1d2a44` |

`solid_1d2a44` 是开场/收尾那种纯色渐变底板。`backend/app/video/render.py` 的 `prepare_clips()`
会把它渲染出来、写进渲染回执的 `asset_rows`，**但没有写进 `assets_manifest.csv`**——
于是"登记在案"这句话对底板是不成立的。

修法：新增 `_register_plate()`，底板也走 `register_generated_asset(kind="plate",
purpose="纯色/渐变底板 #xxxx", credit="本仓库程序生成的纯色底板（无外部素材）")`。
登记失败（`GeneratedAssetError`/`OSError`/`ValueError`）**不让渲染失败**——底板没有版权问题，
渲染没必要陪葬——但要在回执里写 `registered: False` 与 `register_error`，交给审计去发现。

### §29.2 审计模块：四份来源两两对账

`backend/app/video/asset_audit.py` 的 `audit_job(job_dir)` 只读本地文件，把四份来源拉齐：

1. **清单** `assets_manifest.csv`——我们说用了什么、授权是什么；
2. **分镜表** `storyboard/storyboard.csv`——打算用哪个素材；
3. **时间轴** `project/timeline.json`（或 `timeline.render.json`）——真正进成片的 clip；
4. **渲染回执** `qc/render_run.json` 的 `report.assets`——这一次实际导入过什么。

检查项与级别：

| 检查 | 级别 | 什么时候报 |
| --- | --- | --- |
| `manifest_missing` | error / info | 用了素材却没有清单（error）；没素材又没清单（info） |
| `timeline_missing` | warning | 时间轴和回执都拿不到——只能核对清单与分镜 |
| `asset_not_registered` | error | 成片里用了清单上没有的东西（来路不明） |
| `asset_unused` | warning | 清单登记了但这一版没用（可能白登记） |
| `asset_file_blank` / `asset_file_missing` | error | 清单那一条没写路径 / 文件已经不在了 |
| `asset_hash_mismatch` | error | 文件哈希与登记不符（登记之后被改过） |
| `license_not_allowed` | error | 商用授权不是 `allowed` 却进了成片 |
| `storyboard_asset_not_registered` | error | 分镜写了清单上没有的素材 |
| `all_registered` | info | 全部对得上，写出来是为了让人看到"确实查过" |

`ok` 只看有没有 error；`to_markdown()` 按 硬问题 / 提醒 / 说明 三段输出。
命令行：`python -m backend.scripts.asset_audit --job <任务号> [--root content/jobs] [--json]`，
退出码 `0` 无硬问题、`1` 有硬问题、`2` 任务目录不存在。

### §29.3 接进渲染闭环

`render_job()` 渲染并质检完成后调用 `_write_asset_audit()`，把结果写进
`qc/asset_audit.json` 与 `qc/asset_audit.md`，同时让渲染回执多一个 `asset_audit` 字段。
审计自身出错（读文件失败等）只记 warning，不让已经渲染好的片子报失败。

### §29.4 顺带修掉一个真 bug：没有音轨的片子会让质检崩掉

验证底板登记时先渲了一版**不带旁白**的片子（`narration=False`），`scan_media()` 里
`-af silencedetect` 直接让 ffmpeg 以 `返回码 4294967274`（即 -22，EINVAL）退出，
整个质检崩掉——真正的问题（这片子根本没有音轨）反而没被报出来。

修法：`scan_media(path, *, run_ffmpeg=None, has_audio=None)`——先探有没有音轨，
没有就**跳过静音检测**并在结果里留一句 `silence_note`；有音轨但检测本身失败，
也只把 `silence_note` 写上、不让质检整体失败。"没有声音"这件事由 `has_audio_stream` 那一项
如实判红，不靠静音检测代劳。

### §29.5 实测与测试

- 重渲 `v6_accept_company_v2`（临时副本）后，清单从 2 条变 3 条，
  多出来的正是 `solid_1d2a44,B,plate,assets/plates/solid_1d2a44.mp4`，
  审计输出「素材审计通过：清单 3 条，分镜用到 2 个，时间轴 3 个 clip；0 条提醒」。
- 对**改之前**渲好的三个任务跑审计，三个都如实报 `asset_not_registered: solid_1d2a44`——
  这正是补登记之前的状态，说明审计确实查得出这个问题。
- `tests/test_asset_audit.py` 12 例：干净任务通过、成片用了清单没有的东西、清单整个缺失、
  空目录只给说明、清单多登记、文件丢失、文件被改（哈希不符）、授权不允许、分镜引用未登记素材、
  没有时间轴只给提醒、只有渲染回执也能查出未登记、markdown 分段、命令行三个退出码。

### §29.6 审计接进接口与素材页

`GET /api/workspace/jobs/{job_id}` 现在多返回一个 `asset_audit` 字段，由 `_asset_audit_of()`
**在请求时现算**（不是只读渲染那一刻写下的 `qc/asset_audit.json`）：事后有人手动换过素材文件，
现算才查得出来，读旧快照会当作没问题。审计自身出错只回一条 `{"ok": false, "error": …}`，
不让"看素材页"变成 500。

素材页（`#/assets`）顶部加了一张审计卡：

- 标题后面挂一枚状态标签：`N 个硬问题`（红）/ `N 条提醒`（黄）/ `对得上`（绿）；
- 一行统计：清单 N 条 · 分镜打算用 N 个 · 时间轴实际用了 N 个 clip；
- 逐条列出 error / warning（info 只在不报错时露一条，说明"确实查过"）；
- 脚注固定写一句「只核对清单说了什么与片子里实际有什么是否一致，不判断素材好不好看」。

真机（Playwright，`#/assets?job=…`）：`v6_accept_company_v2` 与 `v6_accept_industry`
都显示「素材审计 1 个硬问题」+ `error solid_1d2a44：成片里用了 solid_1d2a44，但素材清单里没有这一条`，
`console_errors` 为空。

`tests/test_asset_audit_api.py` 3 例：接口带出审计、补登记后转为通过、
**事后改动素材文件时现算能查出哈希不符**（这条用例正是"不能读旧快照"的证据）。

### §30 从成片直接排进日历（并修掉两个真问题）

**为什么做这件事**：一条片子渲完之后，下一件事就是"哪天发"。原来的路径是——记住任务号 →
切到内容日历 → 手填标题与时间 → 再把任务号敲进输入框 → 点绑定。中间任何一步记错，
排期就绑到了错的片子或压根没绑，而排期表看不出来。

现在视频预览页多了一张「排进内容日历」卡：

- 还没排过：给一个表单（标题按来源报告的标题预填、平台、时间默认明天 20:00），
  点「排进日历」就用本任务的成片与来源报告建一条排期，`status=rendered`；
- 已经排过：直接显示 `已排期 #N · 平台 · 时间 · 就绪度`，附一个回内容日历的链接，
  不再给表单（避免一个人对同一条片子建出两条排期）。

排期仍然**只记"打算哪天发什么"**，系统不会自己发布；发布要人填链接。

**顺带修掉的两个真问题**（都是拿真任务跑的时候才露出来的）：

1. **报告 id 属于"跑这条片子时那个库"**。`job.json` 里写着 `report_id=1`，但换一个库
   （演示库、工作库）之后 1 号报告根本不存在，接口回 `422 research_report_id=1 不存在`，
   整个排期跟着失败。改法：遇到这种 422 就**退回"不绑报告"再建一次**，并在提示里如实写
   「报告 #1 在当前库里查不到，这条排期没绑报告」——不能让人以为已经绑上了。
2. **"片子在、但没有 job.json"以前被报成"任务目录不在了"**。真事：
   `content/jobs/v5_research_film/` 里 `output/final.mp4` 好端端躺着，但没有 `job.json`
   （它是在成稿闸门机制之前产的）。旧代码把"目录在但缺 `job.json`"和"目录真没了"
   混成一个 `job_missing`，还提示"确认是不是换了机器或删过目录"——把人引到完全错误的方向。
   现在多一档 `no_gate`：「片子在，但查不到成稿闸门记录」，`usable=False`，
   下一步写「翻记录人工核对一遍，或者重新交接、重新渲染一次」。

   实测三个真实任务目录：`v6_accept_company_v2` → `ready`、`v6_accept_industry` →
   `draft_invalid`、`v5_research_film` / `media_toolchain_check` → `no_gate`。

3. 另外修了**排期时间默认值算成 UTC**的老 bug：两处都写
   `new Date(...).toISOString().slice(0, 16)`，在北京时间下午打开页面会变成"第二天凌晨 5 点"。
   改用 `localInputValue()` / `tomorrowEvening()` 两个本地时间助手。

`GET /api/workspace/jobs/{id}` 的 `summary` 新增 `report` 字段（`report_id` /
`research_task_id` / `title` / `verification_status`），视频页因此能显示「来源报告 #1（研究任务 #1）：…」。

**测试**：`tests/test_video_to_calendar.py` 6 例（来源报告带出来、没有 job.json 时不编造、
排期显示就绪 / 失效 / 还没渲染三种状态、仓库里真实任务的 report 字段）；
`tests/test_calendar_readiness.py` 新增 `no_gate` 那一例。

### §31 页面上的三句"可能是假话"的话，改掉了

这一节记录的不是新功能，是三处**文案与事实对不上**的地方——它们比缺功能更糟，因为人照着页面的说法去判断，会被引到错误的结论上。

**1. 分镜说明写死了"纯规则"**。报告页建视频任务后有一段说明：「分镜是**纯规则**排的草稿……正式脚本仍需模型或人工写」。
这句话在脚本驱动分镜上线之后就不成立了：`content/jobs/<id>/job.json` 的 `outline.source` 可能是 `script`，
分镜里的句子来自模型写、人工确认过的那份脚本。现在页面按 `outline.source` 分叉：

- `script` → 「按脚本排（脚本 #N）」+ 「每句都是脚本原句、逐镜标着来源」；
- 其它 → 「按规则排」+ 「口播用原文摘引、想要像人写的稿子就先让 content.script 生成脚本」。

顺带给规则分镜的 `outline` 补上 `"source": "rules"`（原来只有脚本那边有 `source`，前端得靠"没有 script_id"去猜）。

**2. 页面文案里漏出了 Markdown 记号**。同一段里写着 `**纯规则**`，而页面用 `innerHTML` 直出字符串，
两个星号就原样显示在屏幕上。新增 `tests/test_ui_copy.py` 守这条：注释以外的字符串里不许出现 `**`。

**3. 同一个文件两种单位**。素材就绪度显示「9259 KB」，视频预览页显示「9.04 MB」——同一条片子，
两个页面两个数。现在只有一个 `bytes()` 助手（B / KB / MB 三档），日历、视频页、渲染回执都走它。

**没有做的事（免得夸大）**：这几个改动都是文案与格式层面，没有改判定逻辑；分镜来源的分叉靠的是
`job.json` 里已有的 `outline.source`，所以我给规则分镜补了 `source` 字段，让前端不用猜。
这些页面文字改动**没有做真机点击验证**（点"建视频任务"会在仓库里建真实任务目录），
用 `node --check` + `tests/test_ui_copy.py` + 交接层断言兜的底。

### §32 打开工作台就知道今天欠哪几条（顺手修掉 8 小时时差）

**动机**：内容日历记着"哪天发什么"，但要人主动去看。真实使用里最先被忘掉的就是它——
排期排好了、片子渲好了，然后**没人提醒你今天是发布日**。

首页新增一张「今天该发什么」卡（只读，不自动做任何事）：

- 只列**未发布**且时间落在**未来 24 小时内（或已经过点还没发）**的排期；
- 每条显示：排期时间、标题、平台、**底片就绪度**（能不能发）；
- 顶部两个计数：「未来 24 小时 N 条」与「其中 N 条过点还没发」；
- 有片子还不能发时，明写"我不替你判断能不能发"，并指到内容日历看具体原因；
- 脚注再强调一次：**系统不会自己发布，标「已发布」必须填链接**。

**真机验证**（临时库 `%TEMP%\fmai_home_queue\queue.db`，真 uvicorn :8057，Playwright）：
造了三条排期——今天下午那条（绑 `v6_accept_company_v2`）、昨天过点那条（绑
`v6_accept_industry`，实为 `draft_invalid`）、已发布那条。首页显示
「今天该发什么 · 未来 24 小时 1 条」+ 该条 `2026-11-14 15:55:29 · 今天下午要发的那条 · douyin · 片子就绪`，
已发布那条被正确排除，`page errors: []`。

**顺手修掉的真 bug：所有时间都差 8 小时。** 第一次跑这张卡就露馅了——那条"今天下午 2 小时之后"
的排期被算成"过点还没发"。查库才发现：`content_calendar.scheduled_for` 存的是 **naive UTC**
（`2026-11-14 07:54:37`），而这种字符串喂给浏览器 `new Date("2026-11-14T07:54:37")` 会被按
**本地时间**解释，北京时间的人看到的一切时间都整整差 8 小时。

修法是加一个 `parseTs()`：没有 `Z`/`+08:00` 偏移的一律补 `Z` 当 UTC 解析，`when()` 改成用
本地时区格式化输出。这样"我排的下周三 20:00"显示出来就是 20:00，而不是 12:00。
排期相关的比较（今天到期、过点、排序）全部改走 `parseTs`，不再直接拿字符串构造 `Date`。

`tests/test_ui_copy.py` 加了两条护栏：`when()` 必须走 `parseTs` 且不许再 `slice(0, 19)` 截原始串；
源码里禁止出现 `new Date(row.scheduled_for)`。

### §33 发布之后的闭环：账要自己看得出对不上

**动机**：运营层有两条独立的状态线——`status`（进度：计划中 → 有脚本 → 已渲染 → 已导出）
和 `publish_state`（发布：未发布 → 已发布）。谁也不会自动跟着谁走，于是必然出现两类错账：

1. **数据都回填了，却忘了标已发布**。这时候平台汇总里查不到这条的任何数字，
   人会以为"系统没记上"，其实是自己没标发布；
2. **标了已发布，进度还停在"计划中"**。回头对账时看不出这条到底走到哪一步。

更糟的是旧的汇总口径：`latest_*` 取的是**整个平台最新一份快照**，不看它属于哪条排期。
一条还没发布的稿子先录了数据，它的数字就会冒充"这个平台现在的表现"。

**改了什么**：

- `ops._latest_snapshot()` 与 `snapshots` 计数都改成**只算已发布排期**；
  汇总的 `note` 明写"取已发布排期里最新一次回填的绝对值"。
- `CalendarOut` 新增 `consistency`：`metric_count`（这条有几份快照）+ `warnings`
  （只报矛盾，不替人改）：
  - 有快照但没标已发布 → 「有 N 份平台数据快照，但这条还没标已发布：平台汇总只算已发布的排期，这些数字不会计入」；
  - 已标已发布但进度停在 `planned`/`scripted` → 「两个字段不会自己同步，建议把进度也标到已渲染或已导出」。
- 列表接口用一次分组查询把整页的快照数一次取回（`metric_counts()`），不做 N+1。
- 内容日历页在「底片就绪度」旁边多一个「账目对不上」标签，鼠标悬停看具体哪条不对。

**一条被改动的旧测试（这次的语义变更，写清楚）**：`test_analytics_uses_the_latest_snapshot_not_a_sum`
原来在**未发布**的排期上录三份快照就断言汇总是 900。按新口径这不该算，所以那条用例改成先标已发布
再录数据；同时新增 `test_analytics_ignores_data_from_entries_that_are_not_published`
把"未发布的数据不进汇总、但这一条自己会被提醒"钉住。这是**有意的口径变更**，不是为了让测试变绿。

**测试**：`tests/test_calendar_consistency.py` 4 例（干净条目不报、先录后发被提醒、
发布但进度没跟上被提醒、列表按行计数不串）；`tests/test_operations.py` 15 passed。

### §34 备份不只是写一个文件，还得验它能不能恢复

**为什么做这件事**：`backup_db.py` 原来只验"备份那一刻"——写完立刻 `PRAGMA integrity_check`，OK 就收工。
但真正会坏的是之后：文件被半截复制、被同步盘改写、变成 0 字节、库结构停在旧版本。这些情况原来的脚本
一个都查不出来；每日体检也只会说"还没有备份"或"备份太旧了"，不会说"这份备份打不开"。

**做了什么**

| 位置 | 内容 |
|---|---|
| `backend/app/services/backup_service.py`（新） | `verify_backup()` 只读打开备份，查：文件存在/非 0 字节、说明里记的 sha256 是否对得上、`PRAGMA integrity_check`、`PRAGMA foreign_key_check`、表数、`alembic_version`；给了当前库就**逐表比行数**。结论写 `<备份名>.verify.json` |
| `backend/scripts/backup_verify.py`（新） | CLI：`--backup` / `--latest` / `--out` / `--compare-with` / `--no-compare` / `--list` / `--json`；退出码 0 能恢复 / 1 有问题 / 2 目录里没有备份 |
| `backend/scripts/backup_db.py` | 写完备份**当场验收**，`verified` 与完整 `verification` 一起返回并记进说明文件 |
| `backend/app/services/daily_service.py` | 备份段多一个 `verified`；待办里加两条：「最近一份备份还没验证过能不能恢复」「最近一份备份验证不通过：<具体原因>」 |

**两个判断口径（写在这里免得以后被改坏）**

1. **比对对象默认取说明里记的 `source_path`**，不是 `data/finance_media.db`。验一份演示库的备份却拿真实库去比，
   会报"备份里没有当前库有的表：content_calendar…（拿它恢复会丢表）"这种纯属自己吓自己的假问题——
   假警报多了，真警报就没人看了。
2. **行数不同不算失败**。备份是某一刻的快照，之后库里继续长数据是正常的；只有"备份缺了现在有的**表**"
   才会被判 `ok=False`，因为那才是拿它恢复会真的丢东西。

**真机结果**（临时目录里拿工作库副本做的，没碰真实库）

```
[1] backup_db exit: 0
    备份文件: ['demo-20261114-061914-363.db']
    验收记录: ['demo-20261114-061914-363.verify.json']
    ok: True | integrity: ok | 表数: 32 | 行数差异: 0 条 | problems: []
[2] backup_verify --latest exit: 0
[3] 把备份截成一半后 exit: 1
    ok: False | problems: ['这个文件打不开成 SQLite 库：database disk image is malformed', '读不出 integrity_check 结果']
[4] daily_check exit: 1
    attention: ['1 个视频任务的确认被撤回过（已标记失效）',
                '最近一份备份验证不通过：这个文件打不开成 SQLite 库：database disk image is malformed；读不出 integrity_check 结果']
```

**顺手修掉的一个真 bug（Windows 特有）**：`with sqlite3.Connection` 只管事务、**不关连接**。验完一遍之后
备份文件句柄还开着，接着裁剪旧备份就报 `PermissionError: [WinError 32] 另一个程序正在使用此文件`。
改成 `contextlib.closing()` 显式关。这个问题只有"验完立刻删/裁剪"才会暴露，`tests/test_backup_restore.py`
的裁剪用例正好撞上了。

**测试**：`tests/test_backup_verify.py` 9 例（真备份通过、截断文件、0 字节、文件不存在、备份缺表、
行数差异只报不判失败、结论落盘、`latest_backup`、CLI 三个退出码）；`tests/test_daily_check.py` 新增
2 例（没验过被提醒、验不过把原因带出来）。全量 **1257 passed, 2 skipped**。

---

## §35 把操作手册当合同来验（第 20 轮）

### 35.1 为什么做这件事

前几轮的经验是：**文档写错一个参数名，人就会在第一步失败，而且会怀疑是系统坏了**。
手册里的命令是给"第一次上手的人"看的，可它从来不参与任何测试——代码改了参数名、
删了一个脚本，手册照旧写着旧名字，没人会发现。

所以这一轮把手册当**合同**：手册里写了什么命令、什么接口，代码里就必须真有。

### 35.2 怎么验的（不靠人眼）

`tests/test_operator_manual.py` 解析 `v6_0_operator_manual.md` 里的两类引用：

| 引用类型 | 正则 | 怎么验 |
| --- | --- | --- |
| 脚本命令 | `backend\.scripts\.([a-z_]+)` | 脚本文件必须存在；同一行写到的每个 `--flag` 必须出现在 `python -m backend.scripts.X --help` 的真实输出里 |
| 接口路径 | `/api/[\w{}\-/]+` | 必须能匹配上 `create_app().openapi()` 的某条模板（`/api/research-tasks/1/resume` → `/api/research-tasks/{task_id}/resume`） |

两个细节值得记：

- **PowerShell 续行**是行尾反引号，多行命令很常见（`mix_bgm` 就是）。不先把续行接起来，
  第二行写的 `--volume / --track / --out` 会被漏掉——漏掉就等于没验。
- **反空转断言**：解析结果少于 8 个脚本、20 个参数、10 个接口引用时直接判失败。
  否则哪天正则写坏了，用例还是"0 个问题所以通过"，比没有还危险。

### 35.3 顺带抓到的真 bug（手册没错，代码错了）

手册 §1.3 写着"想用别的数据库：`-Database sqlite:///C:/.../demo.db`"，而 `scripts/start.ps1`
把 `-Database` 当**路径**用，于是拼出：

```
C:\Users\25803\finance-media-ai\sqlite:\\\C:\Users\25803\AppData\Local\Temp\fmai_clean_run\fresh.db
```

→ `alembic upgrade head` 报 `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file`
→ 脚本打印「建表失败，见上方 alembic 输出。」后 `exit 1`。**照手册敲的第一条命令就失败。**

修法（`scripts/start.ps1` 第 3 段）：`-Database` 两种写法都收——以 `sqlite:///` 开头就按 URL 解析
（去前缀、去掉 `sqlite:///C:/...` 里多出来的那个斜杠），否则按路径；`:memory:` 直接拒绝并说明
"这个脚本要一个真的库文件"；路径分隔符统一成 `\` 再和受保护库比较（否则 `-Database sqlite:///.../finance_media.db`
能绕过保护）。

### 35.4 顺手改掉的另一处"假动作"

`stop.ps1` 原来对每个进程都先 `CloseMainWindow()` 再等 3 秒，超时才强杀。但 `start.ps1`
起的是**隐藏窗口的控制台进程**，`CloseMainWindow()` 对它永远无效——那 3 秒是空转，日志还会
输出「3 秒内没有正常退出，强制结束」，让人以为服务卡住了。

现在按 `$proc.MainWindowHandle` 分流：有窗口的照旧先礼后兵；没有窗口的直接如实说
「控制台进程没有窗口，直接结束（SQLite 事务已提交，不会半写）」。

### 35.5 真机验证（空环境首跑，两种写法各一次）

```
[start] 工作库不存在，创建并按迁移建表：C:\Users\25803\AppData\Local\Temp\fmai_clean_run\fresh.db
[start] 按 -NoWorker 跳过后台执行器：任务会停在原地，需要手动跑 python -m backend.scripts.run_worker
2026-11-14 14:31:22 | INFO | backend.app.main | starting finance-media-ai v0.1.0 (env=development, database=sqlite:///C:/Users/25803/AppData/Local/Temp/fmai_clean_run/fresh.db)
2026-11-14 14:31:22 | INFO | backend.app.main | 启动自检：8/8 项通过，error=0，warning=0
GET /health -> 200

GET /api/system/status -> 200
{"dialect":"sqlite","url":"sqlite:///fresh.db","reachable":true,"table_count":32,
 "migration_revision":"b6_v6_operations","journal_mode":"delete","busy_timeout":"5000"}

[stop] 控制台进程没有窗口，直接结束（SQLite 事务已提交，不会半写）
[stop] 端口 8065 已释放
[stop] 已停止 1 个服务进程；跳过 0 个非本项目进程。
```

- `-Database sqlite:///...`（URL 形式，端口 8064）：建库成功、32 张表、`b6_v6_operations`、状态 200。
- `-Database C:\...\path_form.db`（路径形式，端口 8065）：同样成功，说明两种写法都通。
- 两次 `stop.ps1` 后端口监听数均为 0，无残留进程。

### 35.6 本轮改动清单与验证

| 文件 | 改动 |
| --- | --- |
| `tests/test_operator_manual.py`（新增） | 4 例：脚本存在、参数被接受、接口存在、必备命令在手册里；解析出 12 脚本 / 31 参数 / 12 接口 |
| `tests/test_scripts_encoding.py` | +2 例：`start.ps1` 必须认 `sqlite:///` 前缀且拒绝 `:memory:`；`stop.ps1` 必须按 `MainWindowHandle` 分流 |
| `scripts/start.ps1` | `-Database` 收 URL 与路径两种写法；`:memory:` 拒绝；路径分隔符归一 |
| `scripts/stop.ps1` | 无窗口的控制台进程不再空等 3 秒，如实说明后直接结束 |
| `docs/research_hub/v6_0_operator_manual.md` | §1.3 写明两种写法与"库不存在会自动建表" |

全量 `pytest -o addopts="" -q` → **1257 passed, 2 skipped, 1 warning**（唯一 warning 仍是
`agent_runs ↔ research_subtasks` 双向外键环，属设计内）。

另外记一条操作纪律：`edit`/`write` 工具保存 `.ps1` **不会带 BOM**，而 PowerShell 5.1 会按 GBK
解析无 BOM 的中文脚本 → 语法错误（这一轮就这么把 `start.ps1` 改坏过一次，报
`字符串缺少终止符`）。改完必须用 `%TEMP%\fmai_restore_bom20.py` 补 BOM 并跑
`tests/test_scripts_encoding.py`。

---

## §36 导出之前，先问一句"这片子还作数吗"（第 21 轮）

### 36.1 一句话

导出是"要把东西发出去"的动作，而**发出去就收不回来**。所以导出比别处更该严格：
旧版 `export_entry` 只看"有没有脚本"，压根不看排期背后那条片子还作不作数。

### 36.2 真实反例（仓库里就有）

| 任务 | 片子 | 依据的结论 | 旧版导出会怎样 |
| --- | --- | --- | --- |
| `v6_accept_company_v2` | 5.34 MB / 29.3 秒 | 没被撤回 | 正常导出 ✅ |
| `v6_accept_industry` | 9.04 MB / 37.8 秒 | **人工把核查记录 1 的确认撤回到 rejected** | **照样把文案写出来**，人拿着作废的片子去发 ❌ |

### 36.3 改法：把"底片就绪度"变成导出的闸门

`backend/app/services/export_service.py` 新增：

```python
BLOCKING_STATUSES = frozenset({"draft_invalid", "film_missing", "job_missing"})
_WARN_STATUSES = frozenset({"no_gate"})
```

- 排期绑了 `video_job_id` → 调 `job_readiness(...)` 读磁盘事实；
- 命中 `BLOCKING_STATUSES` → `raise ExportError(status, "这条排期背后的片子不能发（<人话>）：<下一步>")`
  → 接口 422 + `details.reason = draft_invalid`（前端直接把这句话显示出来）；
- 命中 `no_gate`（片子写了但查不到 `job.json`，成稿闸门机制之前产的）→ **允许导出**，
  但清单里明写「查不到成稿闸门记录，需人工核对」——不能因为查不到就当没问题；
- 没绑任务 → 照常导出，清单写「这条排期没绑视频任务：只有文案」。

**什么都不写**这点很重要：拒绝时连导出目录都不创建（测试里断言 `entry-<id>/` 不存在），
否则磁盘上留着半份文件，人以为导出成功过。

### 36.4 新增 `publish_checklist.md`：照着做就能发

导出的目录里现在有三样东西：`douyin.md`/`xiaohongshu.md`/`wechat.md`（纯文案，直接粘）、
`meta.json`（机器读）、`publish_checklist.md`（人读）。清单长这样：

```markdown
# 发布清单 · 储能为什么打价格战

## 一、片子
- 文件：`content/jobs/v6_accept_company_v2/output/final.mp4`
- 大小：5.34 MB（5600359 字节）
- 规格：1080×1920 · 878 帧 · 29.3 秒
- 技术质检：通过（黑帧/冻结/静音/分辨率/响度等）
- 底片就绪度：**片子就绪**

## 二、这几个平台的文案
- `douyin.md` —— 直接复制到平台后台（标题、正文、标签都在里面）
...

## 三、发完回来要填的（不填等于没发）
1. 排期表点「标已发布」，**链接必填**…
2. 进度推到「已导出/已发布」，两个字段不会自己同步…
3. 有数据了就回填：`POST /api/calendar/{id}/metrics`…
```

片子的事实（时长/分辨率/帧数/技术质检失败项）来自 `qc/render_run.json`；读不到就写
「没找到渲染回执，请自己看一遍片子」——**不猜**。质检有失败项时写
「技术质检：**未通过** —— audio_present」这种，而不是笼统一句"通过"。

### 36.5 真机验证（真实任务目录，未渲染、未调模型、零费用）

```
[v6_accept_industry] 拒绝导出：draft_invalid —— 这条排期背后的片子不能发（结论确认被撤回，片子已失效）：
                     有结论被撤回了确认：重新确认结论后重跑渲染，别拿旧片子发。
[v6_accept_company_v2] 导出成功 readiness=ready 文件=['douyin.md','xiaohongshu.md','wechat.md']
  - 文件：`...\content\jobs6_accept_company_v2\outputinal.mp4`
  - 大小：5.34 MB（5600359 字节）
  - 规格：1080×1920 · 878 帧 · 29.3 秒
  - 技术质检：通过（黑帧/冻结/静音/分辨率/响度等）
[（不绑任务）] 导出成功 readiness= 文件=['douyin.md','xiaohongshu.md','wechat.md']
  - 这条排期**没绑视频任务**：只有文案，发什么由你决定。
```

探针用的是工作库的**副本**（`%TEMP%\fmai_export_probe\probe.db`），真实库未触碰。

### 36.6 测试与改动清单

| 文件 | 改动 |
| --- | --- |
| `backend/app/services/export_service.py` | 导出闸门（`BLOCKING_STATUSES`）、`_film_facts()`、`_checklist()`、返回值新增 `checklist_path`/`readiness`，`meta.json` 记 `video_job_id`/`readiness`/`film` |
| `tests/test_export_gate.py`（新增） | 7 例：撤回的片子被拒且不写文件、没渲出来被拒、`no_gate` 允许但要写明、清单含片子路径/大小/规格/质检通过、质检失败项如实列出、没绑任务说明、接口层 422 带 `reason=draft_invalid` |
| `web/app.js` | 导出成功提示里加上「先看目录里的 publish_checklist.md」 |

全量 `pytest -o addopts="" -q` → **1257 passed, 2 skipped, 1 warning**。

---

## §37 报告列表看不出"往下走到哪一步"（第 23 轮）

### 37.1 问题：两个真实的浪费

报告列表页原来只有「核验状态」和「成稿闸门」两列，看不出这份报告**已经走到哪**：

1. 脚本早就写好了（`scripts` 表里 v2），又去点一次「建视频任务」——
   这次点下去拿到的是**按规则排**的分镜，而不是按脚本排的；人点之前无从判断；
2. 反过来，片子早就渲好了、已经排进日历，人还在想"这份报告下一步做什么"。

一句话：**页面不告诉你它走到哪一步，你就只能靠记忆。**

### 37.2 做法：把三项事实摆到列表上

`backend/app/services/report_progress.py`：

| 事实 | 来源 |
| --- | --- |
| 脚本几条、最新版本 | `scripts` 表按 `research_report_id` 分组（一次 `group_by`） |
| 视频任务几个、成不成 | 磁盘 `content/jobs/*/job.json` 的 `report.report_id` |
| 能不能发 | 直接问 `calendar_readiness.job_readiness()`，**不另写一套判定** |

`GET /api/research-reports` 的每一项多一个 `progress`：

```json
"progress": {
  "report_id": 1,
  "script_count": 1,
  "latest_script_version": 1,
  "latest_script_id": 1,
  "job_count": 5,
  "jobs": [{"job_id": "v6_accept_company_v2", "readiness_status": "ready",
            "usable": true, "film_path": "output/final.mp4", ...}, ...],
  "next_step": "已有能发的成片（v6_accept_company_v2）：去「视频预览」看片，然后排进内容日历。"
}
```

三条纪律：

1. **不做 N+1**：整页一次脚本分组查询 + 一次目录扫描。有计数器用例
   （`test_the_list_route_scans_the_jobs_directory_once_per_page`）钉住，
   以后谁把它改成"每份报告扫一次"都会红。
2. **判定只写一遍**：`progress_of()` 就是 `progress_map()` 的单元素版本，
   有 `test_progress_of_uses_the_same_judgement_as_the_page` 比对两者输出完全相同。
3. **读坏的文件不拖垮整页**：某个 `job.json` 是坏 JSON 只记 warning 跳过
   （`test_broken_job_json_does_not_break_the_whole_page`），
   没有 `job.json` 的历史任务不出现在统计里——宁可少数，不编造。

### 37.3 顺手改掉一处"文档在说谎"

`POST /api/research-reports/{id}/video-job` 的 OpenAPI 描述原写"**用纯规则**排一份分镜草稿、
`script/` 仍然留空"。脚本驱动分镜上线（§26）之后这句就不对了。现在改成如实说明
`outline.source` 的两种取值（`script` / `rules`），并写明脚本排不出来时**不会悄悄换成规则分镜**。
`backend/app/video/handoff.py` 的模块 docstring 同步改掉。
API 参考文档因此重新生成（`Page_ResearchReportOut_` → `Page_ResearchReportListItemOut_`）。

### 37.4 前端

报告列表新增「往下走到哪一步」列：`脚本 v1` / `无脚本` + `能发 <任务号>` / `N 个任务都不能发`
（鼠标悬停显示 `next_step`）。按钮按事实换字：

| 情况 | 按钮 |
| --- | --- |
| 有脚本、没建过任务 | 用脚本建视频任务 |
| 建过任务但都不能发 | 重开视频任务 |
| 其它 | 建视频任务 |

### 37.5 真机验证（探针库 + 仓库里真实的 5 个任务目录）

```
GET /api/research-reports → items[0].progress
  script_count=1  latest_script_version=1  job_count=5
  jobs: gate_demo_after_confirm       film_missing    不能发
        v5_flow_acceptance_v2         ready           能发
        v6_accept_chain_11131719      ready           能发
        v6_accept_company_v2          ready           能发
        v6_accept_industry            draft_invalid   不能发（结论确认被撤回）
  next_step: 已有能发的成片（v6_accept_company_v2）：去「视频预览」看片，然后排进内容日历。
```

页面（Playwright 无头 + 系统 Chrome）：表头出现「往下走到哪一步」，
首行标签 `['unverified', '未做语义核查', '脚本 v1', '能发 v6_accept_company_v2']`，
按钮文字 `用脚本建视频任务`，`page errors: []`。

探针库是用**真实迁移**建的临时库（`alembic upgrade head`），真实开发库与工作库都只读。

### 37.6 改动清单与测试

| 文件 | 改动 |
| --- | --- |
| `backend/app/services/report_progress.py`（新增） | `progress_map` / `progress_of` / `_script_facts` / `_job_index` / `_next_step` |
| `backend/app/schemas/report_progress.py`（新增） | `JobProgressOut` / `ReportProgressOut` |
| `backend/app/schemas/research.py` | 新增 `ResearchReportListItemOut(ResearchReportOut)`，多一个 `progress` |
| `backend/app/api/routes/research_reports.py` | 列表返回 `Page[ResearchReportListItemOut]`；video-job 描述改写 |
| `backend/app/video/handoff.py` | 模块 docstring 改掉"纯规则"的旧说法 |
| `web/app.js` | 新增「往下走到哪一步」列 + `progressCell()` + `videoButtonLabel()` |
| `tests/test_report_progress.py`（新增） | 9 例 |

全量 `pytest -o addopts="" -q` → **1257 passed, 2 skipped, 1 warning**。

### 38 渲染前预检：别让人白等十几分钟

#### 38.1 为什么加这一层

渲染一条 40 秒的竖屏要十几分钟。可失败的原因里，**大多数根本不需要引擎**：素材清单没写、
分镜引用的素材没登记、文件被删了、组合画板 JSON 坏了、要配音但一句旁白都没有。
这些原来都要等引擎起来、`prepare_clips` 跑到那一条时才炸，而且报错位置离真正的原因很远
（典型：报 `empty_clip`，实际是画板 JSON 少了 `canvas`）。

现在开渲之前先做一遍**纯文件与 schema 的检查**：不连引擎、不开 ffmpeg、不联网、不写文件。

#### 38.2 查什么

| 级别 | 检查码 | 含义 |
| --- | --- | --- |
| error | `missing_storyboard` | 还没有分镜文件 |
| error | `invalid_storyboard` | 分镜不合法（帧对齐、字段、枚举），带校验器的原因码 |
| error | `missing_manifest` | 有非纯色素材，但没有素材清单（这一条会**吞掉**逐条的 `unknown_asset`，不制造噪音） |
| error | `unknown_asset` | 分镜用了清单里没有的素材（允许 `S03A → S03` 这一种退回，别的不猜） |
| error | `missing_asset_file` | 清单里写的文件不存在 / 没写路径 |
| error | `empty_asset_file` | 文件是 0 字节 |
| error | `asset_outside_job` | 路径跑到任务目录之外 |
| error | `bad_composition` | 画板不是合法 JSON / 缺 `canvas.w`·`canvas.h` |
| error | `license_denied` | 清单写的是「不可商用」之类的值 |
| error | `license_unclear` | A 类真实素材没写商用授权（授权不明一律不渲） |
| error | `no_narration_text` | 要配音，但一句旁白都没有 |
| warning | `composition_canvas_mismatch` | 画板画布与分镜不一致，会被缩放 |
| warning | `bad_solid_colour` | 纯色底颜色不是 `#rrggbb`，会退回默认深色 |
| warning | `missing_narration_text` | 部分镜头没旁白，那些镜头会是静音画面 |
| warning | `license_unclear` | 自绘素材没写商用授权（能渲，发之前补上） |

分级原则：**现在开渲必然失败的算 error，结果可能不是你想要的算 warning**。
预检的 error 一出现，`render_job` 直接拒绝，并沿用渲染器自己的原因码
（`RenderError(reason=..., ...)`），所以排查时看到的码与以前一致，只是提前了十几分钟。

#### 38.3 接了哪三个地方

1. `render_job(..., preflight=True)`：开渲第一步就跑，有 error 立刻抛；warning 进 `notes`，
   回执里多一个 `preflight` 段（`qc/render_run.json` 里能看到）。
2. `python -m backend.scripts.render_job --job <id> --check`：只预检，不渲、不写状态文件；
   退出码 `0` 能渲 / `1` 有硬问题 / `2` 任务目录不存在。
3. `GET /api/workspace/jobs/{job_id}/preflight?narration=false`，视频页多一张「渲染前预检」卡：
   硬问题与提醒逐条列出（带镜头号），有硬问题时**渲染按钮直接禁用**，标题写「先解决预检里的硬问题」；
   卡片上有一个「按要配音的标准再查一遍」按钮，用 `?narration=true` 重查（配音要求更严）。

接口返回里**只有一个 `ok`**，含义就是「这个任务能不能开渲」——再套一层传输状态会让
`ok=false` 到底指接口失败还是任务不合格说不清。

#### 38.4 拿仓库里 11 个真实任务目录跑一遍

```
20261110_jgb_gold_repricing   missing_storyboard  （历史手工作品，没有分镜表）
gate_demo_after_confirm       通过  4 镜头 / 18.0 秒 / 素材 2 个
media_toolchain_check         missing_storyboard
phase2_media_engine_demo      通过  7 镜头 / 42.5 秒 / 素材 7 个
ui_acceptance                 missing_storyboard
v5_flow_acceptance_v2         通过  8 镜头 / 89.3 秒 / 素材 2 个
v5_research_film              通过  8 镜头 / 71.67 秒 / 素材 5 个
v6_accept_chain_11131719      通过  3 镜头 / 27.87 秒 / 素材 1 个
v6_accept_company_v2          通过  4 镜头 / 29.27 秒 / 素材 2 个
v6_accept_industry            通过  3 镜头 / 37.83 秒 / 素材 1 个
video_engine_demo             missing_storyboard
```

六个成片任务全部通过（说明预检没有误报），四个没有分镜表的目录如实报 `missing_storyboard`。

页面（Playwright + 系统 Chrome，探针库是工作库副本、真实开发库未触碰）：
`#/video?job=v6_accept_company_v2` 显示「渲染前预检 能开渲 · 4 个镜头 · 29.266667 秒 · 1080×1920 @ 30fps ·
非纯色素材 2 个」，渲染按钮可用；点「按要配音的标准再查一遍」后卡片原地重查，`page errors: []`。
另把一份"有硬问题"的载荷喂给同一段模板渲染，确认列表可读：
`missing_asset_file CARD_sh02：文件不存在 …（镜头 sh02）` /
`license_denied CARD_sh03：清单里写的是「editorial_only」…（镜头 sh03）`。

#### 38.5 改动清单与测试

| 文件 | 改动 |
| --- | --- |
| `backend/app/video/preflight.py`（新增） | `preflight_job` / `PreflightReport` / `PreflightFinding` / `to_markdown` / `PreflightError` |
| `backend/app/video/render.py` | `render_job(..., preflight=True)`；`RenderReport.preflight` 进回执 |
| `backend/scripts/render_job.py` | 新增 `--check`（只预检，三个退出码） |
| `backend/app/api/routes/workspace.py` | 新增 `GET /jobs/{job_id}/preflight` |
| `backend/app/api/router.py` | 权限清单加一行（操作数 73 → 74） |
| `web/app.js` | 新增 `preflightCard()` / `preflightBlocked()`、预检卡、渲染按钮按预检禁用、重查按钮 |
| `tests/test_video_preflight.py`（新增） | 23 例（含 CLI 三个退出码、"关掉预检就会走到起引擎那一步"） |
| `tests/test_workspace_api.py` | +4 例（未知任务 404、没分镜、文件消失、就绪任务带 narration） |
| `tests/test_ui_copy.py` | +1 例（视频页必须先预检、有硬问题时按钮禁用） |

全量 `pytest -o addopts="" -q` → **1257 passed, 2 skipped, 1 warning**。

**没做的事**：预检不判断内容对不对（那是内容质检），不猜路径，不替人补文件。

### 39 发出去之后回来填链接：这一段以前是断的

#### 39.1 断在哪

一条片子从"确认结论"走到"导出文案"都能一步接一步，但**导出之后就断了**：系统不代发
（V6.0 任务书第⑦条：发布不可逆，必须人来点），于是要靠人自己记得"发出去"、
再回内容日历把链接填上。这段路有两个真实的坑：

1. **链接随手填也能过**。旧规则只要求 `published_url` 非空，`abc`、`123`、
   `javascript:alert(1)` 全都能存下来；而这个字段在页面上是直接塞进 `<a href>` 的，
   等于"已发布"这件事随时可以被一个垃圾字符串冒充。
2. **导出完没人提醒**。片子躺在 `output/` 里、进度停在「已导出」，人可以一直以为
   "已经做完了"。平台数字回填、效果分析全都建立在"哪天真发了"之上，
   这一格空着，后面的分析就是空的。

#### 39.2 只拦必然错的，可疑的只提醒

新增 `backend/app/services/publish_links.py`：

| 判断 | 结果 | 为什么 |
|---|---|---|
| 空值 | 422 `empty_url` | 没发就是没发 |
| 不是 `http` / `https` | 422 `bad_scheme` | 这个字段会被渲染成链接，`javascript:` 之类点不开还可能有害 |
| 没有主机名（`https://`） | 422 `no_host` | 多半只贴了后半截 |
| 内含空格 | 422 `has_space` | 同上，复制不完整 |
| 超过 1000 字符 | 422 `too_long` | 这不像一个发布页地址 |
| 域名不像这个平台 | **只提醒** | 平台会换域名、有短链（`v.douyin.com`、`b23.tv`）；猜错了会把人挡在门外 |

域名表只覆盖已知平台（抖音/小红书/公众号/B 站/视频号/YouTube），`other` 不做比对——
不知道就不猜。提醒写进 `consistency.warnings`，跟"数据回填了却没标已发布"放在一起：

```
已标已发布，但链接的主机名是「www.bilibili.com」，看着不像抖音：
可能是复制错了，也可能发在了别的平台——确认一下再算这条账
```

校验在服务层的 `update()` 里，所以**接口、页面、脚本三条路都过同一道闸**；前端另外
有一份同名判断（`publishLinkComplaint`），先在本地拦一道，省一次往返——两份判断的
触发条件写在测试里，参数化用例把十个坏值逐个钉住。

#### 39.3 导出之后有人提醒

`backend/app/services/daily_service.py` 新增 `publish_section()`：统计
`publish_state != 'published'` 且进度已经到 `rendered` / `exported` 的排期，
进每日体检（CLI 与首页卡片共用同一份判断）与 `attention`：

```
1 条排期的片子已经做好了，但还没标已发布：发出去之后回内容日历填链接（系统不代发，也不会替你标）
```

老库（19 张表，没有 `content_calendar`）走 `_tolerant`，只记"这块查不了"，不炸。

#### 39.4 真机验证（工作库副本，真实开发库未触碰）

```
junk url        -> 422 {'field': 'published_url', 'reason': 'bad_scheme'}
wrong platform  -> 200 ['已标已发布，但链接的主机名是「www.bilibili.com」，看着不像抖音：…']
daily publish   -> {"count": 1, "items": [{"entry_id": 2, "title": "另一条也还没发", "platform": "bilibili", "status": "rendered", …}]}
attention       -> ['1 条排期的片子已经做好了，但还没标已发布：…']
```

页面（Playwright + 系统 Chrome，`#/operations`）：把 `window.prompt` 改成返回 `abc`
后点「标已发布」→ 页面提示「链接要以 http:// 或 https:// 开头…」，**网络面板里 0 个
PATCH 请求**，`page errors` 为空。也就是说坏值在本地就被挡住了，不会白跑一趟服务端。

#### 39.5 改动清单

| 文件 | 改了什么 |
|---|---|
| `backend/app/services/publish_links.py`（新增） | `normalise_url()` / `host_of()` / `looks_like_platform()` / `PLATFORM_HOSTS` |
| `backend/app/services/operations_service.py` | `update()` 里 `_checked_url()`；未发布时先存的链接也要过闸 |
| `backend/app/api/routes/operations.py` | `_consistency_of()` 增加域名不符提醒 |
| `backend/app/services/daily_service.py` | 新增 `publish_section()`、`attention` 一条、文本体检多一节 |
| `backend/app/schemas/monitoring.py` | `DailyPublishOut` / `DailyPublishItemOut`，`DailySummaryOut.publish` |
| `web/app.js` | `publishLinkComplaint()`；标已发布前本地预检 |
| `tests/test_publish_links.py`（新增） | 31 例（链接本身 10 个坏值、域名匹配 10 组、服务层与接口 4 例、体检 1 例） |
| `tests/test_ui_copy.py` | +1 例（标已发布前必须本地预检、拦住就不再发请求） |

**没做的事**：不自动发布、不自动标已发布、不抓平台页面去核对链接是否真存在（那需要
账号或爬虫，属于 BLOCKED 的"云渲染与发布"）。这里只保证"人填进来的东西不像假的"。

### 40 把台账当账本查：DONE 得站得住，数字得对得上

#### 40.1 为什么查自己的文档

需求追踪矩阵（`docs/research_hub/v6_0_requirements_matrix.md`）是这个项目"做完了什么"的唯一台账，
89 行需求、87 行标 `DONE`，全靠手写。手写台账最大的风险不是写漏，而是**写着写着就飘了**：
引用的文件后来被改名、状态写成六种以外的词、某行其实只有计划没有代码、表头的规模数字在加功能时忘了跟着改。

这一轮把台账当账本查了一遍，**真查出两处漂移**：

| 漂移 | 台账里写的 | 实际 | 后果 |
| --- | --- | --- | --- |
| API 操作数 | 73 个 | **74 个**（`len(ROUTE_CALLERS)` 与 `create_app().openapi()` 都是 74，路径 58 条） | 上一轮新加的 `GET /api/workspace/jobs/{job_id}/preflight` 没记进表头 |
| 技术质检 | 「技术 QC 十项」，位置写 `video/qc.py` | **这个文件不存在**；技术质检是 `backend/app/video/render.py` 里的 `QC_THRESHOLDS` 与 `checks` 字典，**七项**（画布/帧率/时长/有无音轨/黑帧/冻结/静音） | 顺着台账去找代码的人会找不到，还会以为有十项 |

两处都改了文档（台账是我的账，写错了就改账，不是改测试让它变绿）。另外把 7 处 `**DONE**` 的加粗状态
统一成纯 `DONE`——语义一样，但账本要能机器读。

#### 40.2 怎么查：`backend/app/services/matrix_lint.py`

| 检查码 | 级别 | 判什么 |
| --- | --- | --- |
| `unknown_status` | error | 状态不在六种取值里 |
| `missing_path` | error | 引用的仓库路径不存在 |
| `done_without_evidence` | error | 标了 `DONE`，但这一行引用的东西一个都找不到 |
| `bold_status` | info | 状态写成 `**DONE**`（能读，但不统一） |

引用的东西分三类认：**文件路径**（含台账常用的简写 `core/config.py`、`execution/{runtime,worker}.py`、
`config/*.yaml`）、**裸文件名**（`tokens.py`）、**源码里以字面量出现过的符号或接口**
（`executor._skill_notes()`、`GET /api/system/daily`、`GET /api/system/{costs,providers,agents}`）。

第一版只认"以仓库前缀开头的完整路径"，跑出 **53 个假错**——台账大量用简写，那种判法只会逼人忽略报警。
补上简写解析降到 26，再补"文件名 / 源码字样"索引后归零。

**一条防止自己骗自己的规矩**：含斜杠的引用只按"路径"与"整个字符串在源码里出现过"来判，**不许**退化成
"里面某个单词出现过"——那样 `app` 这种片段到处都是，什么都能算证据，这个校验就白做了
（反向测试里 `backend/app/编的模块.py` 必须报错，就是为了钉住这条）。

#### 40.3 还有一条：表头数字必须与代码一致

`tests/test_matrix_lint.py` 直接把表头的四项规模数字与代码实测比对：
`len(Base.metadata.tables)`、`create_app().openapi()` 里的操作数、`web/app.js` 里的页面数、
`len(TOOL_NAMES)`，并额外断言 `len(ROUTE_CALLERS) == operations`（权限清单不能漏端点）。
只取**最后一次**出现的数字：矩阵开头留着一条"审计基准"，写的是当时的历史值（29 张表 / 58 个操作），
对得上代码的应该是后面那条"最近一次刷新"。

#### 40.4 一条命令 + 一个诚实边界

```powershell
.venv\Scripts\python.exe -m backend.scripts.matrix_lint          # 人看的版本
.venv\Scripts\python.exe -m backend.scripts.matrix_lint --json   # 机器读的版本
```

实测输出（本轮结束态）：

```
矩阵：docs\research_hub\v6_0_requirements_matrix.md
89 行需求，BLOCKED 1、DONE 87、NOT_IMPLEMENTED 1，引用文件 58 个（逐个核对 46 个），没有发现问题
```

退出码 `0` 没有硬问题 / `1` 有硬问题 / `2` 读不到矩阵。

**它证明不了什么，写在模块 docstring 里，也写进操作手册**：它只验证"这东西确实在仓库里"，
不验证功能真能用——那是 1300 多例测试与真机验收的事。把绿灯当验收通过，就会重演"报告比实际好看"。

#### 40.5 改动清单与测试

| 文件 | 内容 |
| --- | --- |
| `backend/app/services/matrix_lint.py`（新增） | 解析、状态归一、简写/文件名/符号索引、`lint()`、`render_text()` |
| `backend/scripts/matrix_lint.py`（新增） | 命令行，`--matrix` / `--root` / `--json`，退出码 0/1/2 |
| `tests/test_matrix_lint.py`（新增） | 13 例：解析、状态归一、占位符、简写解析、三类证据、整份矩阵 0 错、所有路径存在、加粗只剩提示、**反向测试**（伪造 DONE 报两错）、未知状态、表头数字与代码一致、命令行两个退出码 |
| `docs/research_hub/v6_0_requirements_matrix.md` | 两处漂移改正（74 个操作、技术质检七项与正确位置）；7 处加粗状态统一 |
| `docs/research_hub/v6_0_operator_manual.md` | §6.1 每周一次的账本自查（含"别把绿灯当验收"） |
| `README.md` | 测试数与脚本清单 |

全量 `pytest` **1350 passed, 2 skipped**。

---

## §41 跨版本升级演练：带数据整库升上来

### 41.1 为什么以前没验过

`tests/test_migrations.py` 有 14 条用例，覆盖表数、逐批可逆、部分索引、单表回填、`alembic check`——
但它们**全部从空库起步**，每批只验自己那一摊。真实风险不在这儿：

- 老库里**有数据**，新列要能吃到值（`server_default` 或迁移里显式 `UPDATE`）；
- 老数据的枚举值要能被新约束接受；
- 降级再升级，回填得**重做一遍**。

`alembic check` 只比结构，这三件事它一句都不说。

### 41.2 演练做了什么

`backend/app/services/upgrade_drill.py` + `python -m backend.scripts.upgrade_drill`：

| 步骤 | 查什么 |
|---|---|
| `copy_source` | 有源库时只读复制，**源库 sha256 前后必须一致** |
| `source_revision` | 源库当前版本（决定从哪一级开始升） |
| `prepare` | 没有源库时把空库升到起点版本 `de8148733a9b` |
| `seed` | 塞 7 张老表的行（topic_columns / topics / sources / research_reports / evidence / scripts / agent_runs），**枚举值全部 import 自 enums 模块** |
| `upgrade` | 升到 head |
| `revision` | 入库版本 == 脚本 head |
| `integrity` | `PRAGMA foreign_key_check` 为空、`integrity_check=ok`、业务表 31 张 |
| `no_drift` | `alembic check` 没有待生成迁移 |
| `backfill` | 老行的 `column_id` → `topic_collections(is_primary=1)`；`content_status=published` → `status=content_produced`；`origin_type` 有值 |
| `data_preserved` | 7 张表行数不变 + 三个关键字段**逐字**比对（报告摘要、证据原文摘录、选题标题） |
| `roundtrip` | 降回 `de8148733a9b` 验老表幸存 → 再升回来，数据与回填都还在 |

### 41.3 查出来的真问题：`topics.origin_type` 在老行上是 NULL

第一次跑通链路（不是"跑通"，是"跑到能报错"）时，9 步里 7 步 OK、2 步 FAIL：

```
[FAIL] backfill: 新列 origin_type 没有给已有行默认值，实际是 None
[FAIL] roundtrip: 降级再升级之后回填没重做：新列 origin_type 没有给已有行默认值…
```

对照三处定义，是一个**三方不一致**：

| 位置 | 原来写的 | 实际效果 |
|---|---|---|
| `docs/research_hub/database_design.md:837` | `Enum TopicOrigin` **NOT NULL default manual** | 设计要求非空 |
| `backend/migrations/versions/20261110_1700_b1_topic_taxonomy.py:274-290` | `nullable=True`，无 `server_default` | 老行升级后是 `NULL` |
| `backend/app/models/topic.py:105` | `Mapped[TopicOrigin \| None]`、`nullable=True` | 类型上"允许空"，没人会注意 |

修法（新增迁移 `b7_topic_origin`，父版本 `b6_v6_operations`）：

1. `UPDATE topics SET origin_type='manual' WHERE origin_type IS NULL OR origin_type=''`
   —— 老选题都是人在界面上建的，标 `manual` 是当时的事实，不是随手挑的默认值；
2. `batch_alter_table` 把该列收紧为 `NOT NULL` + `server_default='manual'`（SQLite 不支持
   `ALTER COLUMN`，batch 模式重建表；重建会带上反射到的 CHECK 约束）；
3. ORM 同步成 `Mapped[TopicOrigin]` + `default=TopicOrigin.MANUAL` + `server_default=TopicOrigin.MANUAL`。

改完复跑：`de8148733a9b → head，共 9 步`，**9 步全 OK**，`revision=b7_topic_origin`。

### 41.4 两个假失败（都是我自己的错，写下来免得重犯）

1. **演练数据自己违反 CHECK 约束**：第一版 `_seed_rows()` 里手写了
   `knowledge_domain="finance"`、`source_type="official"`、`license_status="public"`、
   `support_type="direct"`、`claim_nature="quantitative"`、`template="ak-standard"`——
   全是编的，直接 `sqlite3.IntegrityError: CHECK constraint failed: ck_topics_knowledge_domain`。
   改成全部 `from backend.app.models.enums import …` 取真值后通过。
   **教训：演练数据要么引用真枚举，要么别写。**
2. **从"已经是新版本"的库演练时，回填检查在查一件不会发生的事**：拿
   `data/finance_media.work.db`（已在 `b6_v6_operations`）复制演练，`from_revision` 就是 b6，
   B1 的回填在源库里早发生过 → 硬查会报「column_id 没回填、status 还是 candidate」。
   现在按情况分流：`--no-seed` 时跳过并写明；源库版本晚于起点版本时写明
   「回填在源库里发生过，这一项由 roundtrip 覆盖」（roundtrip 会真的把回填重跑一遍）。

### 41.5 实测（两条路都跑了）

```
# 从初版空库 + 塞数据（回填真的跑）
升级演练通过：de8148733a9b → head，共 9 步
[OK  ] backfill: column_id=1 → topic_collections(is_primary=1)；content_status=published → status=content_produced；新列 origin_type 默认 manual
[OK  ] roundtrip: 降回 de8148733a9b 再升到 head，7 张老表的行数不变、回填重做成功

# 拿工作库副本（已在 b6）演练，源库只读
升级演练通过：b6_v6_operations → head，共 10 步
[OK  ] copy_source: …\finance_media.work.db → …\drill_copy.db
[OK  ] backfill: 跳过：源库已是 b6_v6_operations，回填在源库里发生过；这一项由 roundtrip 覆盖
源库：…\finance_media.work.db（只读复制，源库 sha256 前后一致）
```

真实开发库 `data/finance_media.db` **全程没有被读过结构也没被复制**（它停在 `de8148733a9b`，
是受保护对象；`is_protected()` 会认出它并在报告里标注"只读复制"）。

### 41.6 诚实边界

- 演练只用 **SQLite**（项目当前就是 SQLite）；换成别的数据库要重写 `_connect` 与 `PRAGMA` 那几处。
- `data_preserved` 只比对 7 张表的行数和三个字段，**不是全表逐行 diff**——它的目标是"数据没丢、没被改写"，
  不是"逐字节相同"。
- `roundtrip` 只走到起点版本，不是"逐级回退到每一个历史版本"。
- 演练证明的是"迁移能升、数据能活、回填会发生"，**不证明**新功能在旧数据上表现正确
  （那要靠真实使用与验收）。

---

## §42 空库到能用：一条命令验完第一脚

### 42.1 缺口在哪

`doctor.py` 查**环境**（Python、依赖、ffmpeg、配置能不能读），`upgrade_drill.py` 查**迁移**
（老库能不能升上来），`daily_check.py` 查**运行中**的库有没有欠账。三件都有人验，但
"**一个空库文件能不能长成一个能用的库**"没人按顺序走过一遍——而这才是第一次上手的第一步。
这条路上有四个坑，每个单独都有测试，连起来没有：

1. 迁移没跑（表都没有）；
2. 栏目没种子 → 建选题 422（`column_id` 不存在）；
3. 启动自检不过（配置/角色/Skill/路由任何一处不一致）；
4. worker 起来抢不到活，或者抢到却因为没开搜索/没配模型直接判死。

### 42.2 一条命令

```powershell
python -m backend.scripts.bootstrap_check            # 临时库，跑完即删
python -m backend.scripts.bootstrap_check --keep     # 保留库，自己接着点
python -m backend.scripts.bootstrap_check --json     # 机器可读
```

九步，每步都打 `[OK  ]`/`[FAIL]`，退出码 0 全过 / 1 有失败 / 2 目标库不可用（受保护库、非 SQLite）。

| 步骤 | 验什么 |
|---|---|
| `migrate` | 空库 → `alembic upgrade head`，报 revision 与业务表数 |
| `seed` | `config/content_taxonomy.yaml` 六个栏目写进 `topic_columns` |
| `startup` | 起一个真 app，启动自检全绿（**并把模型密钥从进程里摘掉**） |
| `api` | 走真 HTTP：`/health`、`/api/system/status` |
| `topic` | `POST /api/topics`（带真实 `column_id`）→ 201 |
| `task` | `POST /api/research-tasks` → 201，报出子任务数 |
| `worker` | worker 真推一格（`topic.discover`），**0 次模型调用** |
| `honest_stop` | 把检索开关临时关掉再往下推：采集阶段必须**如实停下** |
| `no_spending` | `model_calls` 必须 0 行，事件流必须有记录 |

### 42.3 这一步顺手抓到的真问题：开关关掉会把任务**判死**

第一版跑出来是这样：

```
research.collect: failed（attempts=2，failure_reason=no_page_fetched:search:feature_disabled）
research.extract / analyze / synthesize / verify / script: blocked（upstream_failed）
task: failed（failed=6/9）
```

`features.search=false` 时每个检索都返回 `feature_disabled`，而 `_collect` 把"一页都没抓到"
一律当成**可重试失败**：耗掉两次重试，再把下游六个子任务连坐成 `upstream_failed`，
整个任务判死。用户唯一的错是"开关没开"，代价是整个任务作废——还得重新建、重新花钱。

修法（`backend/app/execution/executor.py::_collect`）：**能力开关关掉不是抓取失败**。
当所有错误都是 `feature_disabled` 时返回 `status="blocked"`：

- `reason="feature_disabled"`，任务转 `paused`（可恢复），不消耗重试；
- `output.next_step` 直接写"把 `features.search` 改成 true 再点「继续跑」"；
- 顺带记 `output.errors[:5]`，让人看得到当时到底拒了什么。

改完同一条路变成：

```
[OK] honest_stop: 采集阶段如实停下（human_review: feature_disabled），任务状态 paused
```

### 42.4 两个工程细节（都跟"这是别人的进程"有关）

1. **不许花钱**：脚本运行期间从进程里摘掉 `DEEPSEEK_API_KEY` 等四个键并设 `FM_SKIP_DOTENV=1`，
   于是规划走规则拆词（`planner="keywords"`）。跑完**原样放回**——它是别人（可能是常驻终端）的进程。
2. **`--json` 的干净输出**：应用启动会把 logging 配到 stdout，直接打 JSON 会混进
   `INFO | backend.app.main | starting ...`。`--json` 期间把 `sys.stdout` 临时指到 stderr，
   跑完再换回来，JSON 里就只有 JSON。

### 42.5 实测

```
空库到能用：全部通过（9 步，失败 0 步）
[OK  ] migrate: 迁移到 head：revision=b7_topic_origin，业务表 31 张
[OK  ] seed: 栏目种子：{'total': 6, 'created': 6, 'updated': 0, 'unchanged': 0}
[OK  ] startup: 启动自检 8/8 项通过；模型密钥已从进程移除
[OK  ] api: /health=200，/api/system/status=200
[OK  ] topic: 建选题 -> 201（栏目 1·金融知识）
[OK  ] task: 建研究任务 -> 201（task=1，9 个子任务）
[OK  ] worker: 第一格完成（子任务 1）
[OK  ] honest_stop: 采集阶段如实停下（human_review: feature_disabled），任务状态 paused，其间放行人工闸门 1 次
[OK  ] no_spending: model_calls=0 行，事件 21 条，/api/system/costs=200
```

### 42.6 诚实边界

- 只覆盖 **SQLite**；
- `honest_stop` 是**故意**把检索关掉跑出来的，它证明的是"会停"，不是"能抓到东西"——
  真抓取请用 `python -m backend.scripts.flow_check`；
- 不验证模型调用（脚本明确不让它花钱），模型的真实链路看 `python -m backend.scripts.model_e2e_check`；
- 用的是 `TestClient`（进程内真 HTTP），不是外网可达的 uvicorn；端口、TLS、反代这些不在这条路上。

## §43 闸门之前，得先看得见东西

### 43.1 问题：看不见的闸门等于盖章

这条流水线的核心承诺是"人工确认之后才能进成稿"，为此在每个关键处设了人工闸门
（`topic_approval` 人工立项、`fact_check_gate` 事实核查、`script_approval` 脚本审批）。
但第 29 轮真跑一条研究任务时发现：

- 任务在**做完两格 `topic.discover`** 之后停下（`paused`），界面上只有一行"任务 paused"；
- 这一步的产物（选题拆解 `discovery.md` / `discovery.json`）**只落在磁盘上**
  （`content/research/task-<id>-<指纹>/content/`），工作台里没有任何入口能看；
- 于是"看完再放行"变成了"闭着眼睛点继续"。

这正是我自己写过的纪律的反面：**程序只判技术指标，判断交人**——可人没有材料可判。

### 43.2 补了什么

**一、产物看得见**：新增 `GET /api/research-tasks/{task_id}/artifacts`

- 只读列举该任务产物目录，**只列最新那一份**（同一 id 下可能有多份，用研究目标指纹区分；
  混着列会把两次研究的输出摆在一起，看着像同一条任务做的）；
- 文本类（`.md/.json/.csv/.txt/.yaml`）顺手读一段，**每个文件 8000 字符、整页 24000 字符封顶**，
  超了就标 `truncated=true` 并在 `note` 里写"正文合计到 N 字符就停了"——不静默截断；
- 二进制只报大小与类型；一次最多列 20 个，多出来的如实说"另有 N 个没列出"。

**二、闸门说人话**：`GET /api/research-tasks/{task_id}` 新增 `gate` 字段

```
{"gate_id": "topic_approval", "label": "人工立项", "subtask_id": 2,
 "after_sequence": 2, "next_step": "看完「这一步产出了什么」里的选题拆解，觉得题目立得住，就点「继续跑」……"}
```

判定口径（写错一次才定下来的）：闸门标记由编排器写在**被挡住的那一格**上——
"做完这格 → 停下来等人"，不是写在下一格。所以取"已成功、带 `require_human_review`、
序号最大"的那一格；任务状态不是 `paused` 时一律返回 `null`，免得拿一道早就过去的门当现状。

**三、页面上真的能看**：任务详情页新增两张卡

- 「卡在人工闸门：人工立项（topic_approval）」：`等你放行`、`挡在第 N 步`、下一步做什么，
  以及一个「看完，放行并继续跑」按钮（先 `resume` 再 `run(3)`，两步分别报错）；
- 「这一步产出了什么」：文件清单（类型/路径/大小/是否只显示开头），文本可展开看正文。

顺手修一处会写错地方的旧代码：自动刷新原来按 `.card` **下标**取卡片
（`querySelectorAll('.card')[1]`），前面多插一张卡就会把进度写进别的卡里——改成 `#progress-card` / `#events-card`。

### 43.3 真机实测（临时库、零花费）

真跑一条「商业银行净息差为什么一路往下」：

```
round 1: completed=[1] -> running
round 2: completed=[2] -> paused
gate: {"gate_id": "topic_approval", "label": "人工立项", "after_sequence": 2, ...}
artifacts: 200，共 2 个产物文件
  task-1/content/discovery.md     markdown   743 B  truncated=False
  task-1/content/discovery.json   json      1655 B  truncated=False
```

`discovery.md` 开头就是可读内容（研究框架：行业研究 / 要回答的六个问题），
人看完再点放行——这才叫"确认"。本次全程 **0 次模型调用、0 元**。

### 43.4 测试

`tests/test_task_artifacts.py` **11 例** + `tests/test_ui_copy.py` 加 2 例：

- 只列最新目录（老目录不算）、文本带正文、二进制不带、超长截断且标记、总量封顶后不多读、
  目录不存在只提示不抛错、目录按 mtime 排序；
- 闸门指着"被挡住的那一格"、任务没 paused 时不报、多道门取最新、未知门回落通用话术；
- 接口层：列表 200 带正文、新任务没有闸门、未知任务 404。

**诚实边界**：这两样只解决"看不看得见"，不判断产物好坏；`gate` 只报"哪道门、看哪里"，
不替人做决定——放行仍然要点按钮，且成稿闸门只认人工确认过的结论。

### 43.5 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/services/task_artifacts.py` | 新增：产物只读列举（最新目录、文本预算、截断标记） |
| `backend/app/services/task_gate.py` | 新增：闸门判定与人话（`topic_approval`/`fact_check_gate`/`script_approval`…） |
| `backend/app/schemas/research_task.py` | 新增 `GateOut` / `ArtifactFileOut` / `ArtifactBundleOut`，详情补 `gate` |
| `backend/app/api/routes/research_tasks.py` | 新增 `/artifacts` 端点，详情补闸门 |
| `backend/app/api/router.py` | `ROUTE_CALLERS` +1（75 个操作 / 59 条路径） |
| `web/app.js` | 任务详情页两张新卡 + 刷新改用元素 id |
| `tests/test_task_artifacts.py` | 新增 11 例 |
| `tests/test_ui_copy.py` | +2 例（闸门卡与产物卡必须在、刷新不许按下标取卡） |

## §44 点完「推进执行」，这一轮到底发生了什么

### 44.1 问题：接口回了，但等于没说

`POST /api/research-tasks/{id}/run` 一直回得很详细（`completed=[1,2]`、`stopped_reason`、
`idle`、`task`），但界面上只剩一句"这一轮做完了 2 格"。于是：

- 推进了**哪几个阶段**？看不出来；
- 是不是**又停在另一道门**上？看不出来；
- 现在**该干什么**？看不出来。

回执里全是子任务号，人得自己去阶段表里对。而且推进完 1.4 秒后页面整页重渲染，
刚出现的总结会被冲掉——**还没读完就没了**。

### 44.2 补了什么

**一、一份人话总结（服务端算，界面/命令行共用）**

`RunReportOut` 新增 `summary`：

```
{"headline": "推进 2 格：又停在人工闸门「人工立项」，这一步的产物在下面，看完再决定放不放",
 "lines": ["做完了 第 1 格 topic.discover（succeeded）",
           "做完了 第 2 格 topic.discover（succeeded）"],
 "stopped_at": "gate",
 "next_step": "看完「这一步产出了什么」里的选题拆解……"}
```

`stopped_at` 只有六种取值：`gate` / `completed` / `failed` / `paused` / `max_iterations`
（到本轮上限，**不是出错**）/ `idle`（没活可干）。措辞只写一遍，
`backend/app/services/run_summary.py` 同时被 API 与 `python -m backend.scripts.research_task run` 使用。

**二、已经在门上时不许说"又停在"**

第一版连"推进 0 格"也写成"又停在人工闸门"——人以为自己推进过。现在只有真推进了才用"又"，
零推进时写"任务还停在人工闸门「X」：这一轮没有可推进的格子（先放行，或者换个任务）"。

**三、刷新不冲掉总结**

推进/放行之后改成**原地刷新**（阶段进度、事件流、闸门卡、状态标签四处），不再整页重渲染。
顺带给状态行加了 `#task-status-row`、闸门卡加了 `#gate-slot`——否则刷新之后标签还是旧的
`running`，明明已经 `paused` 了。

### 44.3 顺带查出的一处"张冠李戴"

产物目录名是 `task-<id>` 与 `task-<id>-<指纹>`，而 **id 只在一个数据库里唯一**：
演示库的 `task-3` 和工作库的 `task-3` 会落到同一个 `content/research/` 下。
原来的规则是"按修改时间取最新那份"，于是真机截图里，一条刚建的任务下面显示的是
**另一次研究**的 `digest.json` / `analysis.md`——比"看不到"更糟。

改法：`collect_artifacts(..., objective=...)` 带上研究目标，优先挑 `digest.json` 里目标一致的那份；
还没有 digest 的（早期阶段只写了 `discovery.md`）算"未标注"次选；全都不匹配才退回最新那份，
**并在 `note` 里明写"可能来自另一个数据库、不一定是这条任务产出的"**。

### 44.4 实测（临时库、真搜索、零模型调用）

```
task_id: 3
task-action 渲染出来：
  推进 2 格：又停在人工闸门「人工立项」，这一步的产物在下面，看完再决定放不放
  做完了 第 1 格 topic.discover（succeeded）
  做完了 第 2 格 topic.discover（succeeded）
  下一步：看完「这一步产出了什么」里的选题拆解，觉得题目立得住，就点「继续跑」……
状态标签同步成 paused；闸门卡与产物卡同屏可见；page errors: []
```

命令行同一条路：

```
$ python -m backend.scripts.research_task run --task-id 1 --max-iterations 2
{ "iterations": 0, "idle": true, "stopped_reason": "no_ready_subtask",
  "summary": {"headline": "任务还停在人工闸门「人工立项」：这一轮没有可推进的格子（先放行，或者换个任务）", ...}}
```

### 44.5 测试

`tests/test_run_summary.py` **10 例**（停闸门点名、逐格命名带状态、阻塞原因原样带出、
失败不与阻塞混为一谈、重试单列、跑完指路报告、未知子任务号不崩、零推进不谎报、
`as_dict` 可序列化、接口真跑两格返回 `gate` 总结）；
`tests/test_task_artifacts.py` **+3 例**（别的库的同名目录不被当成这条任务、目标一致压过时间更新、
全不匹配时 note 里说清）。

**诚实边界**：这段总结只描述"发生了什么"，不评价产物质量；`next_step` 是建议不是承诺，
再点一次也可能停在同一道门上。
