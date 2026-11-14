# V6.0 需求追踪矩阵（自主接管审计）

> 本文件是 V6.0 总任务书第一部分~第三部分要求的**需求追踪矩阵**，按"原始需求 → 实现位置 →
> 验收标准 → 当前状态"逐条登记。状态取值只用任务书 4.5 定义的六种：
> `DONE` / `PARTIAL` / `TEST_ONLY` / `CODE_ONLY` / `BLOCKED` / `NOT_IMPLEMENTED`。
>
> 审计基准：`git 0090225`（2026-11-13），29 张表、58 个 API 操作、19 个前端页面、
> 10 个已实现工具、20 个运维脚本、`pytest` 850 passed / 2 skipped。
> 最近一次刷新（`017da91` 之后）：31 张表 / 73 个 API 操作 / 20 个页面 / 11 个工具 / **1196 passed**。
> 本文件随开发推进**就地更新**，不另开副本。
>
> **最近一次刷新**（V6.0 收尾提交，2026-11-14）：31 张表、73 个 API 操作、
> 20 个前端页面、11 个已实现工具、`pytest` 1196 passed / 2 skipped；三主题真实验收已跑通（见 §L），
> 内容质检在真片上从"3 条硬错"修到 `ok: true`（`content/jobs/v6_accept_company_v2`），
> 跨站转载合并与历史任务分页已补齐（`research/dedupe.py`、`#/tasks?offset=&status=`）。

## 0. 审计方法

| 手段 | 说明 |
| --- | --- |
| 代码 | 直接读 `backend/app/**`、`web/**`、`scripts/**`、`config/*.yaml` |
| 接口 | `create_app().openapi()` 打印全部路径，逐个核对是否接真数据 |
| 数据 | 29 张表的 ORM 定义与迁移文件逐条比对（`alembic check` 无漂移） |
| 运行 | 临时库 + 真 uvicorn + 真搜索 + 真模型跑端到端，读库核对行数 |
| 反向 | 未授权身份、非法输入、预算不足、模型超时、抓取失败、审核撤销等场景 |

## A. 基础设施与运行环境

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 目录与模块边界 | `backend/app/{api,core,capabilities,execution,research,video,services,models,schemas}` | DONE | — |
| 配置管理 | `config/*.yaml` + `core/config.py` + `core/config_schema.py`（全 `extra="forbid"`） | DONE | — |
| 数据库访问层 | `database/session.py`（连接级 3 条 PRAGMA）+ `models/` | DONE | — |
| 迁移框架 | `backend/migrations/versions/` 7 个版本（到 `b6_v6_operations`） | DONE | — |
| 日志与异常 | `core/logging.py` + `core/errors.py` + `api/middleware.py`（request_id） | DONE | — |
| 任务队列 / 状态持久化 / 并发 | `execution/{runtime,worker,orchestrator}.py`（租约 + attempt_token + 事件序号） | DONE | — |
| Windows 启动脚本 | `scripts/start.ps1`（BOM、端口检查、健康探测）+ **默认拉起后台执行器**（`run_worker --forever`，pid 与启动时间写 `logs\worker.pid`；`-NoWorker` 可关） | DONE | 真机两轮：8/8 自检、worker 起停干净、端口释放 |
| Windows 停止脚本 | `scripts/stop.ps1`（只杀 `uvicorn backend.app.main` 与自己启动的 worker——靠启动时间而不是 pid 判身份；`-Force` 才强杀） | DONE | 已实测杀掉真实监听 |
| 数据备份与恢复 | `backend/scripts/{backup_db,restore_db}.py`（在线备份 + sha256 说明 + 受保护库拒写 + 恢复前留副本） | DONE | `tests/test_backup_restore.py` 13 例 |
| 环境诊断 | `backend/scripts/doctor.py`（22 项：依赖/FFmpeg/字体/浏览器/端口/磁盘/库版本/配置/Provider） | DONE | 仍保留更轻的 `preflight.py` |
| 服务健康检查 | `GET /health`、`GET /api/system/status` | DONE | — |
| 服务自动恢复 | `scripts/watchdog.ps1`（健康探测 + 退避 3→6→12→24 封顶 60 秒 + 日志） | DONE | 8031 端口实测起服务并通过健康检查 |
| 系统运行监控 | `GET /api/system/{costs,providers,agents}` + `GET /api/system/history` + 工作台「运行历史」折线 | DONE | 历史按天给出调用/tokens/成本/新建任务/完成/失败/子任务失败/闸门次数，**缺的天补 0**；只有"当前库里的记录"，没有跨库归档（换库就看不到旧曲线） |
| 确认后「下一步」 | `GET /api/research-reports/{id}/pipeline`（任务状态、卡住的阶段与原因码、已确认/待确认条数、`can_resume`）+ 「语义核查」页的「下一步」卡与「继续跑」按钮 | DONE | **确认不会自动触发花钱**，跑不跑由人按键；页面与接口同一份判定；顺带修掉核查明细表 `class="grid"` 被 `.grid` 卡片样式拆成两半的问题；`tests/test_report_pipeline.py` 6 例 |
| 每日体检（命令行 + 接口 + 首页卡片） | 判断逻辑在 `backend/app/services/daily_service.py`（任务状态/暂停/失败 + 窗口内失败子任务 + 待确认核查 + 失效视频任务 + 今日与近 N 天真实花费 + 备份新鲜度 + 磁盘）；入口 `backend/scripts/daily_check.py`（薄 CLI）与 `GET /api/system/daily`（`HUMAN_ONLY`）；页面 `web/app.js` 的 `dailyCard()` | DONE | **两个入口共用同一份判断**，不会出现命令行与页面数字打架；只读、不联网、不调模型；退出码 0 干净 / 1 有事项 / 3 连不上库；老库缺表时如实说"这几块查不了"而不是抛异常；`tests/test_daily_check.py` 11 例 + `tests/test_daily_api.py` 5 例（含空库不崩、无头浏览器实拍首页卡片） |

## B. 模型系统

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| Model Gateway / Provider Registry / 配置 | `capabilities/model/{gateway,providers,tokens,tool}.py` + `config/models.yaml` | DONE | — |
| 输入输出分开计费 / 成本估算与实记 | `tokens.py` + `gateway.py`（复算后写 `model_calls`） | DONE | — |
| 预算管理（单次/任务/当日） | `capabilities/budget.py` + `execution` 预算列 | DONE | — |
| 限流 / 超时 / 重试 | `gateway.py`（`_LocalLimiter`、`timeout`、`retries`） | DONE | — |
| 未知价目阻止请求 | `gateway.py` → `budget_blocked/pricing_missing` | DONE | — |
| 审计与密钥脱敏 | `model_calls` 表 + `_redact` + `mask_secret` | DONE | — |
| 模型可用性检查 | `provider_status()` + `/api/system/providers` + `preflight` | DONE | — |
| 模型降级（主模型失败切备用） | `gateway.py` 的 `FALLBACK_CODES` + `_fallback_reply` + `_fallback_target`；`config/models.yaml` 的 `fallback_to` | DONE | 只降一跳；只对超时/传输失败/5xx/限流降级，鉴权失败与请求非法不降级；备用模型价目未核实则拒绝降级（`tests/test_model_guardrails.py` 4 例） |

## C. Agent / Skill / Tool / 编排

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| Agent Registry | `config/agent_routing.yaml` + `execution/routing.py` + 9 份 `agents/*/PROMPT.md` | DONE | — |
| Skill Registry | `backend/app/skills/registry.py` + `skills/registry.yaml`（5 个 Skill，含 sha256） | DONE | — |
| Skill 注入提示词 | `executor._skill_notes()` → `analysis.build_messages(skill_notes=…)`；只有 Agent 身份能加载，加载失败只记 warning、链路继续，`output.skills_used` 如实记录用了哪些 | DONE | 每个 Skill 正文最多带 1800 字进提示词（超出截断并标注） |
| Tool Registry | `capabilities/tools.py`（11 个工具全部 `implemented=True`） | DONE | — |
| Agent 权限 | `core/caller.py` + `capabilities/guard.py`（agent_only / db_scopes / 白名单） | DONE | — |
| Agent 执行状态 | `agent_runs` 表（`agent`、`task_id`、`subtask_id`、`status`、`cost_estimate`） | DONE | — |
| Agent 间协作 | `handoff` 工具（五型，`need_human` 会改状态） | DONE | — |
| TaskRuntime / Orchestrator / 依赖 / 动态任务 / 失败传播 / 重试 | `execution/{runtime,orchestrator,executor}.py` | DONE | — |
| 执行日志 / 运行成本 | `task_events` + `task.plan` + 预算列 | DONE | — |
| "不同 Prompt ≠ 多个 Agent" 这条反例检查 | `_handlers` 现在覆盖全部 10 个 task_type（含 `topic.discover` 与 `content.script/visual/produce`） | DONE | 9 个角色名册里仍有几个只靠 prompt 区分（见下方说明），但**每个 task_type 都有真实处理器**，不再有"没实现却报成功"的阶段 |

## D. 信息获取与研究资料

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 网络搜索 | `capabilities/search/{engine,safety}.py`（bing/sogou/so360） | DONE | — |
| 网页抓取 / 正文提取 / 快照 | `capabilities/search/fetcher.py` + `data/snapshots/` | DONE | — |
| PDF / Excel / CSV 解析与表格提取 | `capabilities/adapters/markitdown_adapter.py` + `structured.py` | DONE | — |
| Word(docx) 解析 | `capabilities/adapters/markitdown_adapter.py` 的 `parse_docx`（经 MarkItDown）+ 四个研究角色的 tools 已授权 | DONE | 只抽文字：**不保留排版、批注、修订痕迹**，所以不能拿它判断"原文是否强调过" |
| 来源记录 / 发布时间 | `sources` 表（`published_at`、`site_name`、`sha256`、`snapshot_path`） | DONE | — |
| 来源可信度管理 | `backend/app/research/source_quality.py`（`tier_of`：官方/一手/媒体/聚合/未知五档，按域名后缀与显式白名单） | DONE | 分级只提示"要不要回原文核对"，不代表内容已核实（`tests/test_source_quality.py` 20 例） |
| 数据时效性判断 | `source_quality.freshness_of`（30 天 fresh / 180 天 recent / 更早 stale / 无发布时间 unknown，可用 `as_of` 按底稿日期判） | DONE | 阈值是口径不是真理，写死在代码里可复核；发布时间解析不了就判 unknown，不猜 |
| 重复内容识别 | `backend/app/research/dedupe.py`（跨站转载合并）＋ `topics.dedupe_key`（选题域）＋ `corroborate.py`（数字冲突） | DONE | 只合并**正文几乎一样**的页面（归一化后指纹相同，或 4-gram 相似度 ≥ 0.90）；正文短于 120 字不参与判定，宁可漏判不误合并；转载页不算独立来源，底稿与提示词里都标出来 |
| 来源冲突识别 | `research/corroborate.py` | DONE | — |
| Evidence / Finding 管理 | `evidence` 表 + `finding_checks` + `research_reports.findings` | DONE | 结论的骨架节点（`section`）一路带到库里，下游才能按节点归行 |

## E. 行业 / 产业链 / 公司研究

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 通用研究闭环 | `research/{digest,analysis,verify,corroborate,persistence}.py` | DONE | — |
| 行业研究十维度（定义/阶段/规模/结构/供需/格局/驱动/周期/政策/风险） | `research/frameworks.py` 的 `INDUSTRY`（10 节，`market_size` 只允许 fact） | DONE | 已真跑：储能主题产出按节分组的分析 |
| 产业链研究十二维度（上中下游/原材料/设备/技术/环节/成本/供应链/价值分配/瓶颈/机会） | `frameworks.py` 的 `CHAIN`（11 节） | DONE | 已真跑：光伏产业链主题 |
| 公司研究十四维度（基本信息…最新动态） | `frameworks.py` 的 `COMPANY`（14 节，财务四节只允许 fact） | DONE | 已真跑：隆基绿能主题 |
| 事实 / 推断 / 观点区分 | `analysis.py` 的 `kind` + 语义核查 | DONE | — |
| 数据缺口 / 冲突 / 时效 / 局限 | 报告 `limitations` + `data_gaps` + `framework.checklist` 的覆盖率 + 底稿「来源可信度与时效」一节 | DONE | 时效只进底稿与 JSON（`source_quality`），还没回写进 `limitations` 文本 |

## F. 语义核查与人工审核

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 逐条核查 + 五档判定 | `research/semantic.py` | DONE | `contradicted` 无真实样本（TEST_ONLY 那一档） |
| 八类错误码 | `ISSUE_CODES` | DONE | — |
| 12 项留痕 | `finding_checks` 表 | DONE | — |
| 模型状态与人工状态分离 | `verdict` vs `human_review_status` | DONE | — |
| 只有人工确认才能成稿 | `research/draft_gate.py`（视频任务 + 脚本两个出口） | DONE | — |
| 视频任务锁定 Finding/Evidence/Source | `video/handoff.py`（`job.json.draft_gate`） | DONE | — |
| 审核撤销后已有任务失效处理 | `handoff.py` 的 `jobs_using_checks/invalidate_jobs` + 复核接口联动（写 `INVALIDATED.md`，**不删产物**） | DONE | `tests/test_review_invalidation.py` 5 例 |

## G. 内容策划与脚本

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 选题管理 / 优先级 / 合集 | `topics` 表 + `/api/topics/*` | DONE | 优先级只有 `score_total` 手工值 |
| 热点发现 / 选题推荐 | `backend/app/research/topic_suggestions.py` + `GET /api/topics/suggestions` + 工作台「选题建议」面板 | DONE | 只看**已入库**来源（不联网、不调模型）；五维打分（时效/来源层级/带单位的量/命中合集/多源共同报道）且每条都给理由；与已有选题相似度 ≥0.82 直接丢弃；壳标题（日报/免责声明）与 <6 字标题不做；采纳仍需人点 |
| 报告 → 内容策划（定位/受众/标题/钩子/故事线/结构/观点） | `backend/app/content/stages.py` + `content.script/visual/produce` 三个阶段 | DONE | 产物是 `script_draft.{md,json}`、`visual_plan.json`、`production_plan.json`（落 `content/research/task-<id>/content/`）；**只排计划不渲染**，`not_done` 里明写渲染/配音/素材/发布都没做 |
| 脚本生成（AK 风格） | `backend/app/content/stages.py#draft_script` + `build_script_messages/parse_script_draft` + `script` 状态机 + `agents/scriptwriter/PROMPT.md` | DONE | 有模型时把**人工确认过的结论**交给模型改写成口播初稿，逐句校验来源与数字（不在所引结论里的数字整句丢弃），模型不可用/写歪了退回规则版并在产物里写明原因；"一次成型"仍建议人工润色 |
| 多平台内容适配 | `backend/app/content/platform_adapt.py` + `POST /api/scripts/{id}/adapt` + 工作台「一稿三版」 | DONE | 只调长度与排版（抖音 18 字/行、小红书 24 字/行、公众号保留段落）；**不改事实、不编标签**（标签不够就明说"这里不替你编"）；切行时不会把 `19.3%` 切成 `1` + `9.3%`；导流词只提示不代删 |
| 内容审核 | `/api/reviews` + `review_service` | DONE | — |

## H. 视频生产

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 分镜 / 时间轴 / 转场 / 渲染 | `video/{outline,render,narration,cards}.py` + **`video/script_board.py`（按脚本排分镜）** + OpenKatana | DONE | 报告已经有脚本时，建视频任务**按脚本排分镜**（一句一镜、带单位的句子做成数据卡、`source_ids` 落到逐镜可追溯列），没脚本才退回规则分镜；两种来源都写进 `job.json.outline.source`，脚本排不出来时**不悄悄换规则分镜**；字幕字号按最长一句自适应（`render.caption_font_size()`，只降不升、下限 32），长度上限与规则分镜共用 `outline.MAX_CAPTION_CHARS = 18`，超长按标点收口 |
| 图表生成 | `analysis/charts.py` + `chart_from_table.py` | DONE | — |
| 中文 TTS / 字幕 | `capabilities/tts/`（edge-tts WordBoundary + sapi 兜底） | DONE | — |
| 素材搜索与获取（真实素材） | — | **NOT_IMPLEMENTED** | 只用自绘图与纯视觉底板（诚实记录） |
| 动态产业链图 / 动画 | `backend/app/video/chain.py`（三段示图 + `insert_chain_shot` / `resize_chains` + `SECTION_STAGE` 节点归行表）+ `backend/scripts/chain_diagram.py` | DONE | 上游/中游/下游三行按研究结论的 `section` 取节点（`raw_materials`/`equipment` 归上游，`tech_routes`/`cost_structure`/`value_split`/`supply_relations` 归中游，`opportunities` 归下游，`bottlenecks`/`policy`/`risks` 不归行）；逐行进入 + 箭头下沉 + 整块缓推；总结论不上图，空环节写「（本环缺证据）」；长标签在「等/的/年/元」处断开且不切断数字；**报告→视频任务时自动插入**（覆盖 ≥2 段才插，插在开场之后并重排时间轴）；配音重排后按新时长重写画板帧数；真机渲染已抽帧目视核验 |
| 背景音乐管理 | `backend/app/video/bgm.py`（曲库登记 + 授权白名单）+ `backend/scripts/mix_bgm.py`（先验授权再混音） | DONE | 曲库自带是空的（诚实状态）：默认走 `synthesize_score` 合成配乐；要加曲目必须登记授权，白名单外一律拒绝，cc-by 类强制写署名 |

## I. 视频质量控制

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 技术 QC 十项 | `video/qc.py`（黑帧/静帧/静音/时长/音画等长…） | DONE | — |
| 内容 QC（数字/单位/日期/图表与旁白一致/来源可追溯） | `video/content_qc.py`（8 类检查：上屏数字必须能在材料里找到、四舍五入只算提醒、每镜要有来源、字幕必须来自旁白、素材授权）+ `POST /api/workspace/jobs/{id}/content-qc` | DONE | 真片 `v6_accept_company_v2` 已从 3 条硬错修到 `ok: true`；仍不判断好坏（叙事/节奏/观感靠人看片） |
| 逐镜来源可机读 | `media/storyboard.py` 的 `source_ids` 列 + `video/outline.py` 写入 | DONE | 框景镜头允许空，落带单位的量就必须挂来源 |
| 字幕错字 / 节奏 / 开头吸引力 | — | BLOCKED | 需人看片，不做假装 |

## J. 前端工作台

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 19 个页面接真接口 | `web/app.js`（68 KB，无静态假数据） | DONE | — |
| 研究规划 / 子任务 / 成本 / 阻塞原因 | `pageTaskDetail` 的规划卡 | DONE | — |
| 语义核查 + 人工复核 | `pageChecks` | DONE | 真机点击已验证（`backend/scripts/ui_acceptance.py`） |
| 历史任务 / 错误信息页 | `pageTasks`（深链分页 + 状态下拉）+ `GET /api/research-tasks?status=&limit=&offset=` + `pageTaskDetail` 的失败原因 | DONE | 翻页靠深链（`#/tasks?offset=20&status=failed`），不做无限滚动 |

## K. 自媒体运营管理

| 需求 | 实现位置 | 状态 | 差距 |
| --- | --- | --- | --- |
| 内容日历 / 排期 / 状态 | `models/operations.py` 的 `content_calendar` + `/api/calendar/*`（7 个端点）+ `web/app.js#pageOperations` | DONE | 迁移 `b6_v6_operations`；`tests/test_operations.py` 14 例 |
| 排期背后的片子能不能发 | `backend/app/services/calendar_readiness.py` 的 `job_readiness()`（读 `content/jobs/<job_id>/`，六档：`no_job`/`job_missing`/`no_gate`/`film_missing`/`draft_invalid`/`ready`，只有 `ready` 可发）+ 四个日历接口返回里的 `readiness` 字段 + 页面「底片就绪度」列与「绑任务号」按钮 + 视频页「排进内容日历」卡（`POST /api/calendar` 带 `video_job_id`，已排过就只显示不再建） | DONE | 判定顺序是"先闸门后文件"：片子还在但结论被撤回确认时报 `draft_invalid`；目录在、片子也在却没有 `job.json` 时报 `no_gate`（不再混成"任务目录不在了"）；报告 id 在换库后查不到时退回"不绑报告"并在提示里写明（`tests/test_calendar_readiness.py` 10 例 + `tests/test_video_to_calendar.py` 6 例） |
| 今天该发什么（首页提醒） | `web/app.js` 的 `publishQueueCard()`：首页只读拉 `GET /api/calendar?limit=100`，列未发布且落在未来 24 小时内（含已过点）的排期，带底片就绪度与「过点未发」计数 | DONE | 真机实测三条排期（就绪 / 失效 / 已发布）只列出该发的那条；顺带修掉 naive UTC 被当本地时间解析的 8 小时时差（`parseTs()` + `when()` 本地化，`tests/test_ui_copy.py` 两条护栏） |
| 发布之后的账对不对得上 | `CalendarOut.consistency`（`metric_count` + `warnings`）：有快照却没标已发布、标了已发布但进度没跟上，都如实报出来；平台汇总的 `latest_*` 只算**已发布**排期 | DONE | 旧口径会让未发布排期的数据冒充平台表现；本次改动同时改了一条旧用例的口径（交付报告 §33 写明），并新增 `tests/test_calendar_consistency.py` 4 例 |
| 导出记录 / 发布记录 / 多平台适配 | `POST /api/calendar/{id}/export` 真写文件（三平台 `.md` + `meta.json`）并把路径写进 `export_path`、状态转 `exported`；`publish_state`/`published_url`/`published_at` 记发布痕迹（**发布必须有链接**，撤回留痕）；文案三版见 `backend/app/content/platform_adapt.py` | DONE | 发布回执（平台侧的真实链接与数据）仍需人工回填；系统不调任何平台 API |
| 账号数据导入 / 播放量 / 互动分析 | `platform_metrics` + `POST /api/calendar/{id}/metrics` + `POST /api/calendar/{id}/metrics/import`（CSV 批量）+ `GET /api/calendar/analytics`（取最新快照，不累加） | DONE | CSV 认中英列名与千分位；撞时间戳的跳过、坏行带行号报错、平台不一致直接拒（`tests/test_metrics_import.py` 13 例） |
| 素材清单与实际使用是否一致 | `backend/app/video/asset_audit.py` 的 `audit_job()`（清单 / 分镜表 / 时间轴 / 渲染回执四份对账）+ `render_job` 末尾写 `qc/asset_audit.{json,md}` + `GET /api/workspace/jobs/{id}` 的 `asset_audit` 字段（**请求时现算**）+ 素材页审计卡 + CLI `python -m backend.scripts.asset_audit` | DONE | 补登记纯色底板（`_register_plate`，以前底板只在回执里、不在清单上）；无音轨时不再让质检崩掉（`scan_media(has_audio=…)`）；`tests/test_asset_audit.py` 12 例 + `tests/test_asset_audit_api.py` 3 例 |

## 优先级与执行顺序（自主决定，不等确认）

1. **A 基础设施缺口**：停止脚本、备份/恢复、诊断、watchdog（无外部依赖，立刻可做）。
2. **K 运营模块**：内容日历与效果数据（需要 1 张新表 + API + 页面）。
3. **E + I**：行业/产业链/公司研究框架、内容 QC 程序化检查。
4. **B + D + F 的零散缺口**：模型降级、来源可信度与时效、审核撤销失效。
5. **三个真实研究主题验收 + 一条完整视频链路**。
6. 自审、反向测试、文档与最终验收报告。

> 每完成一项就在这里把状态改成 `DONE` 并补一节"证据"（命令 + 输出 + 文件路径）。
