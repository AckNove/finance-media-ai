# 技能与模块盘点（V2.0 轻量化整改）

盘点日期：2026-11-17
盘点依据：`backend/app` 实际代码、`config/*.yaml`、`skills/registry.yaml`、`agents/*/PROMPT.md`、`web/app.js` 路由表。
方法：**先查调用关系再判断**（`grep` 全仓引用 + 读入口函数），不按名字猜。

分类定义（按任务书第八节）：
- **A 核心必需**：主链路跑不掉，删除会直接断链。
- **B 可复用**：有用，但实现比需要的重，保留并简化。
- **C 当前无用**：主链路与页面都不调用，且没有历史数据依赖。
- **D 归档**：现在不用、但属于历史成果或有数据在库里，**不物理删除**，标记退出主链路。
- **E 重复**：与别的模块功能重叠，合并到一个出口。

---

## 一、工具（ToolRegistry 共 11 个，`backend/app/capabilities/tools.py`）

| 技能 | 代码位置 | 作用 | 当前调用者 | 主链路 | 测试 | 删除影响 | 决定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `web_search` | `capabilities/search/tool.py` | 搜索引擎抓结果页 | `AgentExecutor._collect` | ✅ 是 | `test_search_*` | 研究断链 | **A** |
| `fetch_url` | `capabilities/search/fetcher.py` | 抓正文+快照+robots | `AgentExecutor._collect` | ✅ 是 | `test_search_*` | 研究断链 | **A** |
| `llm_complete` | `capabilities/model/tool.py` | 统一模型出口（含计费/限流/缓存） | `_analyze`/`_script`/`_verify` | ✅ 是 | `test_model_*` | 所有模型任务断链 | **A** |
| `task_update` | `capabilities/tools.py` | 子任务状态回写 | `runtime` 内部 | ✅ 是 | `test_execution_pipeline` | 任务系统断链 | **A** |
| `handoff` | `capabilities/tools.py` | agent 之间交接产物 | `_synthesize`/`_script` | ✅ 是 | `test_agent_contracts` | 阶段间传参断链 | **A** |
| `fact_check` | `capabilities/tools.py` | 只查本地快照核对原句 | `_verify` | ✅ 是 | `test_fact_check` | 核查退化为人工 | **A** |
| `parse_pdf` / `parse_xlsx` / `parse_docx` | `capabilities/adapters/` | MarkItDown 文档转文本 | 研究阶段按需 | ⚪ 按需 | `test_markitdown_adapter` | 只影响本地文档研究 | **B**（保留；pdf/xlsx 已实测，docx 未实测） |
| `db_query` | `capabilities/storage/tool.py` | 只读查询（强制 LIMIT） | 研究阶段查历史库 | ⚪ 偶发 | `test_tool_registry` | 影响历史资料复用 | **B** |
| `db_write` | `capabilities/storage/tool.py` | 三层闸门写入 | 目前**没有调用者**（研究结果走 `persistence.py` 直接落库） | ❌ 否 | `test_tool_registry` | 无 | **C**（停用，不删代码） |

## 二、Agent 角色（9 个，`agents/*/PROMPT.md` + `config/agent_routing.yaml`）

| 角色 | 作用 | 主链路 | 删除影响 | 决定 |
| --- | --- | --- | --- | --- |
| `orchestrator` | 拆题、规划、汇总 | ✅ | 研究链断 | **A** |
| `financial_researcher` / `industry_researcher` | 采集与抽取 | ✅ | 采集断 | **A** |
| `fact_checker` | 逐条核查 | ✅ | 核查断 | **A** |
| `scriptwriter` | 口播稿 | ✅ | 脚本断 | **A** |
| `visual_designer` | 分镜/视觉说明 | ✅ | 分镜断 | **A** |
| `video_producer` | 制作计划与质检口径 | ✅ | 制作阶段断 | **A** |
| `case_analyst` | 案例库分析 | ❌ 无任何 pipeline / 页面引用 | 无 | **C**（停用） |
| `football_researcher` | 足球商业研究 | ❌ 无引用 | 无 | **D**（归档：库里有 `football_cases` 表与历史枚举，不物理删） |

> 说明：`football_*` 相关内容散落在 `models/cases.py`、`models/enums.py`、`schemas/topic.py`（`football_analogy_required`）、
> `services/knowledge_service.py` 的加权表里。它们**不参与视频主链路**，但删列要改迁移、会动到历史数据，
> 按任务书"重要历史成果先归档"处理：**标记退出，不动表结构**。

## 三、task_type（10 个，`backend/app/execution/task_types.py`）

`topic.discover` / `research.plan` / `research.collect` / `research.extract` / `research.analyze` /
`research.synthesize` / `verify.claims` / `content.script` / `content.visual` / `content.produce`

全部在主链路上（`config/pipelines.yaml` 的两条产线按顺序用到），**全部 A**。

## 四、视频模块（`backend/app/video/` 16 个文件）

| 模块 | 作用 | 主链路 | 决定 |
| --- | --- | --- | --- |
| `cinema/`（`blueprint.py`/`page.py`/`scene.js`/`v2_three_d.py`/`photo.py`/`audio.py`） | V2 真实感引擎：蓝图、逐帧渲染、实拍/三维/数据层、声音合成 | ✅ 出片主力 | **A** |
| `render.py` / `preflight.py` | 老引擎（OpenKatana 组合+时间轴）出片、渲染前 15 项预检 | ⚪ 仍被 `render_job.py` 用 | **B**（保留：老任务还能重渲；新片走 `cinema`） |
| `outline.py` / `cards.py` / `kinetic.py` / `chart_shots.py` / `script_board.py` | 规则分镜与画板（V1 观感，用户已否"质量极差"） | ⚪ 只在老任务里 | **D**（归档：新片不再走规则分镜） |
| `narration.py` | edge-tts 配音 + 剪静音 + 超长记录 | ✅ 无真人录音时的兜底 | **B** |
| `content_qc.py` / `asset_audit.py` | 内容质检（来源、镜头重复率）、素材授权审计 | ✅ | **A** |
| `bgm.py` | 背景音乐曲库（`config/bgm_library.yaml` 空） | ❌ 0 首曲子 | **C**（停用） |
| `chain.py` | 产业链示意图 | ⚪ 老流程 | **D** |
| `generated.py` / `textfit.py` | 生成素材登记、文字自适应 | ✅ | **A** |
| `handoff.py` | 报告→视频任务交接 | ✅ | **A** |

## 五、Workbench 页面（20 个，`web/app.js`）→ 收敛为 3 个

保留（新三页，合并现有能力）：

| 新页面 | 由哪些旧页合并 |
| --- | --- |
| **① 研究与脚本** | `/topics` 选题、`/tasks` 与 `/tasks/:id` 任务、`/search` 搜索、`/sources` 资料、`/evidence` 证据、`/checks` 核查、`/reports` 报告、`/scripts` 脚本 |
| **② 素材与镜头** | `/assets` 素材、`/storyboard` 分镜、`/planning` 内容策划 |
| **③ 视频预览与导出** | `/video` 视频预览、`/review` 审核、成片导出 |

配置只保留：DeepSeek 状态、Pexels 状态、FFmpeg 状态、输出目录、画幅、画质（合并 `/models` `/providers` `/costs` `/settings`）。

归档（`D`，代码保留、路由从导航隐藏，深链仍可访问）：
`/operations` 内容日历与平台数据、`/planning`、`/review` 的旧版审核流。

理由：这些页面背后有 `content_calendar` / `platform_metrics` 表与已验证逻辑，删代码会删掉可用能力；
按任务书"可以删除、隐藏或合并，但必须先检查路由依赖"——本轮选择**隐藏导航 + 保留深链**，不破坏已有数据。

## 六、服务层（`backend/app/services/` 32 个）

保留（A/B）：`agent_registry` `content_chain` `daily_service` `environment` `export_service`
`matrix_lint` `model_readiness` `provider_guide` `report_progress` `research_service` `review_service`
`run_summary` `script_service` `source_service` `system_service` `task_artifacts` `task_budget`
`task_gate` `task_timing` `topic_intake` `topic_research` `topic_service` `content_tags` `publish_links`

归档（D）：`calendar_readiness` `metrics_import` `operations_service` `knowledge_service`
`backup_service`（运维用，保留但不在产品导航）`upgrade_drill`（迁移演练，保留）

## 七、重复与合并（E）

| 重复项 | 处理 |
| --- | --- |
| 三套出片路径（`render.py` OpenKatana / `script_film.py` / `cinema/`） | 新片只走 `cinema/`；另两套标 D 归档，不再新增功能 |
| 两套分镜生成（`outline.py` 规则分镜 vs `script_board.py` 脚本分镜） | 新链路统一走"口播稿 → 镜头表"，两套都归档 |
| 四个配置页（models/providers/costs/settings） | 合并成"设置"一页的三个状态卡 |
| 两套音色/配音（`narration.py` edge-tts、`capabilities/tts`） | 保留出口唯一：`capabilities/tts` 为底层，`narration.py` 为视频层封装 |

## 八、本轮不动的东西（避免"为重构而重构"）

- 数据库表结构：一张不删、一列不改（`football_*`、`content_calendar`、`platform_metrics` 全部保留）。
- 已有 API：85 条路由全部保留，只在新三页里挑需要的用。
- 已有测试：不删测试；被归档模块的测试保留（跑得过就继续跑）。
- `data/finance_media.db`：只读，不迁移、不开 WAL。

## 九、本轮要新建的能力（对照任务书）

1. **Pexels 素材检索**（`backend/app/capabilities/media/pexels.py`）：官方 API、11 字段登记、署名、无 Key 时 BLOCKED。
2. **口播稿→素材语义匹配**（`backend/app/video/script_materials.py`）：逐段拆解语义、关键词、素材类型、时长预算，
   对候选素材打分，不足就换词/换场景/退回动画。
3. **真人录音接入**（`backend/app/video/voiceover.py`）：导入用户音频、按段对齐、字幕以实测量为准，不默认用 TTS 顶替。
4. **三页工作台**（`web/app.js`）：研究与脚本 / 素材与镜头 / 视频预览与导出。

每项完成后补测试与实证，再更新本文档状态。
