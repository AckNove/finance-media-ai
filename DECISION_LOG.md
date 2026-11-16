## D-18 模型密钥的解析要能自己读 `.env`，不能只靠"父进程导过"

**决定**：`capabilities/model/providers.py` 的 `_env()` 在进程环境里查不到时，
直接去仓库根的 `.env` 查（`core/config.py::dotenv_value`）。**但**只要调用方显式传了
`environ`（测试/单次覆盖）就只认那一份，不去读文件。

**为什么**：实际发生的事——服务被某个"启动器 Python"再拉起一次，子进程没走到
`load_dotenv_into_environ()`，于是 `ready_providers()` 返回空，脚本阶段静默退回规则版，
而人看到的只是"这条是规则写的"。密钥来源仍然只有环境变量与 `.env` 两处，没有新增来源。

**否决了什么**：否决"让 start.ps1 把 env 显式塞给子进程"——那只修一条启动路径，
直跑 `python -m uvicorn` 的人照样踩。

**代码落点**：`backend/app/core/config.py::dotenv_value`、
`backend/app/capabilities/model/providers.py::_env`。

## D-19 测试一律不读仓库里的真 `.env`

**决定**：`tests/conftest.py` 加 session 级 autouse fixture，把 `FM_SKIP_DOTENV` 置 1。

**为什么**：D-18 的兜底一上线，"缺密钥"用例就真的拿到真密钥、真的发出请求、真的花钱
（实测一次 ¥0.017747）。测试必须与真人凭据绝缘，否则它既慢又不确定，还会在别人机器上
花别人的钱。

**否决了什么**：否决"在每条用例里各自删环境变量"——漏一条就复发。

**代码落点**：`tests/conftest.py::_no_real_dotenv`、`backend/app/core/config.py::dotenv_value`。

# DECISION_LOG.md — 为什么是这个做法

> 只记**会影响后面怎么写代码**的决定，每条包含：决定 / 为什么 / 被否决的方案 / 代码落点。
> 改主意了就追加一条新记录并标注"取代 D-xx"，**不要删旧的**。

---

## D-01 价目未知就不发请求（不是"先跑再算钱"）

- **决定**：模型价目缺失或未核对时，`pricing_policy: reject_unknown` → 直接 `blocked`，一个字节都不发。
- **为什么**：先跑再补价 = 账单不可控。用户明确要求"未知价目禁止请求、不得产生未授权费用"。
- **否决**：①按 0 元记账（会让当日预算闸门形同不存在）②用"上次价格"猜。
- **落点**：`config/models.yaml`（`pricing_policy`）、`backend/app/capabilities/model/gateway.py`、
  启动自检的 `pricing` 检查（缺价目 → warning，不阻断启动但拦住调用）。

## D-02 只有人工确认的结论能进成稿

- **决定**：`human_review_status == confirmed` 才算可成稿；模型判定只是意见，永远不会自动变成人工确认。
- **为什么**：User said (m15463)：「结论必须 confirmed 才能进入成稿吧」。程序能判断"有没有出处"，
  判断不了"这个口径该不该这么说"。
- **否决**：模型判定 supported 就直接放行（省事，但等于把事实责任交给模型）。
- **落点**：`backend/app/research/draft_gate.py`（`assert_draft_ready` 拦四个出口：建视频任务、建脚本、
  `produce_video`、交接稿分节）。反例留证：`content/jobs/v6_accept_industry`（确认被撤回后成片即判失效）。

## D-03 规则与模型分离：排不出来就说排不出来

- **决定**：`content.script` 用模型改写成片脚本；**模型排不出来时只记 `outline.reason`，不退化成规则分镜**。
- **为什么**：静默降级会让人以为"模型写的"，实际是模板拼的——这是最坏的一种谎。
- **否决**：失败后用规则兜底并标 `planner=rules`（容易被忽略，等于默认接受低质量）。
- **落点**：`backend/app/video/script_film.py`、`backend/app/video/script_board.py`。

## D-04 产物路径一律写相对路径

- **决定**：`job.json` 里的 `storyboard` / `notes` 写相对任务目录的路径。
- **为什么**：2026-11-15 搬家时发现 5 个任务记着 `C:\Users\<用户名>\...` 的绝对路径——搬完就全指错，
  而且产物里带用户名本身就是信息泄露。
- **否决**：搬家时批量改写历史 JSON（那是"当时在哪儿生成"的记录，改了等于伪造）。
- **落点**：`backend/app/video/outline.py`（`OutlineResult.as_dict()`）+ 护栏测试
  `tests/test_video_outline.py::test_recorded_paths_are_relative_so_the_job_can_move`。

## D-05 同号不同库：一律用指纹做第二道校验

- **决定**：`report_id` / `task_id` 只在一个库里唯一，凡是跨"库 + 目录"的地方，都要再用
  标题 / objective 指纹比对一次。
- **为什么**：临时库里的 `report #1` 和仓库里的 `report #1` 完全不是一回事。这条踩过两次：
  临时库跑一次把真实底稿覆盖了（`content/research/task-1`），链路页把别人的成片算成了我的。
- **落点**：`backend/app/services/content_chain.py`（`_same_report`）、`backend/app/execution/executor.py`
  （`_stage_dir` 落 `task-<id>-<sha8>`）、`backend/app/video/handoff.py`。

## D-06 多平台账号 API 不做，改人工回填 CSV

- **决定**：抖音/视频号/B 站/小红书不接开放平台，发布后人工回填数据（`content_calendar` + CSV 导入）。
- **为什么**：User said (m28360 前后) 选择人工；且这些平台开放能力基本要企业资质，
  人工回填每天十条以内完全够用。
- **重估条件**：每天发布 10 条以上，再回来评估（记在 `TASK_BACKLOG.md` B-11）。

## D-07 永不自动发布

- **决定**：系统不自己有发布动作；`published` 状态必须有 `published_url` 才算数。
- **为什么**：V6.0 任务书硬约束（不得未经批准自动发布）。技术上也不成立——没有平台授权。
- **落点**：`content_calendar` 的发布接口校验链接；日历页明写"系统不会自己发布"。

## D-08 git 推送用 `-c` 覆盖，不动全局配置

- **决定**：每次推送都带
  `git -c http.proxy= -c https.proxy= -c http.https://github.com.proxy= push origin main`。
- **为什么**：全局 `~/.gitconfig` 里三条代理键指向 `http://127.0.0.1:7890`，但本机常年没有代理在监听；
  User said (m28360 前后) 选 **A：不动全局配置**。直连 GitHub 时通时不通，失败就挂后台每 60 秒重试。
- **否决**：全局改 `http.proxy=""`（用户明确不想动全局环境）。

## D-09 仓库整体搬到 D 盘，代码不留绝对路径

- **决定**：仓库落在 `D:\finance-media-ai`；代码与配置里**不写任何绝对路径**，一律从 `PROJECT_ROOT` 推。
- **为什么**：C 盘当时只剩 2.52 GB（写临时文件直接 `ENOSPC`）。搬迁后 C 盘回到 20 GB 以上。
- **前提**：`.venv` 可以整体搬（`sys.prefix` 跟着目录走），本项目脚本全走 `python -m` 形态，
  不碰内嵌旧路径的 `Scripts\*.exe`。
- **落点**：`README.md`（「换机器 / 换目录」）、本文件 D-04。

## D-10 真实开发库只读，演练一律临时库

- **决定**：`data/finance_media.db` 不迁移、不 seed、不回填、不开 WAL；所有演练在 `%TEMP%` 的库里做，
  并每轮给出"未改动"证据（大小 + sha256 + 表数 + 迁移版本）。
- **为什么**：V6.0 安全红线。库文件里只有 `alembic_version` 一行，但它是判断"真实环境有没有被污染"的标杆。
- **落点**：`backend/app/core/startup_checks.py`（启动只探测不改）、`backend/scripts/*` 一律拒绝该路径。

## D-11 模型耗时单独列，不参与加减

- **决定**：任务耗时分 `总时长 / 干活 / 等人 / 模型调用` 四块；**模型调用与"干活"重叠，不参与加减**。
- **为什么**：模型调用发生在某个格子里，算两遍会让人以为任务跑了双倍时间。
- **否决**：把模型耗时算进"干活"（数字看似自洽，实际重复计）。
- **落点**：`backend/app/services/task_timing.py` + `tests/test_task_timing.py` 钉死
  "等待时长不可能超过总时长"。

## D-12 台账自查只改文档，不改测试

- **决定**：`matrix_lint` 抓到"表头写的 83 个操作，代码里是 84 个"时，永远改文档。
- **为什么**：台账是用来对齐现实的下游产物，测试才是现实的守门人；反过来改测试就是自欺。
- **落点**：`backend/app/services/matrix_lint.py`、`tests/test_matrix_lint.py`。

## D-13 一次搬一个可验证的小改动

- **决定**：每轮一个能验证的改动 + 提交 + 推送（`AGENTS.md` §1）。
- **为什么**：这个项目一轮里往往同时动"服务 + 接口 + 前端 + 文档 + 台账"，
  混在一个大提交里出问题时无法回退到"上次能跑"的状态。

## D-14 一键启动必须先比库版本：落后就升级，领先就拒绝

- **决定**：`scripts/start.ps1` 每次启动都先跑 `python -m backend.scripts.db_revision`，
  按结果分五种状态处理——`current`/`fresh` 直接起；`behind` **自动 `alembic upgrade head`
  并复检**；`ahead`/`unknown` **拒绝启动**（不自动降级）；想拦着不动就加 `-NoMigrate`。
- **为什么**：原来的逻辑只在"库文件不存在"时建表，库一旦存在就直接用。换分支、拉新代码、
  搬机器之后工作库可能还停在两个版本以前，服务照样起得来，只是碰新表的页面会在查询时报
  `no such column`（历史真事：`topics.is_active`）。本轮实测就撞上了：D 盘工作库停在
  `b6_v6_operations`，代码已到 `b8_calendar_tags`。
- **否决**：①"库比代码新就自动降级"——降级会丢数据，宁可拒绝启动让人换回代码；
  ②"启动时静默迁移、什么都不打印"——迁移是要紧事，必须出现在启动日志里。
- **落点**：`backend/scripts/db_revision.py`（只读判断 + 五种状态 + 退出码）、
  `scripts/start.ps1` 的 3.5 段、`tests/test_db_revision.py`（10 例）、
  `tests/test_scripts_encoding.py`（脚本编码与 BOM 仍守着）。

## D-15 重试阶梯只减"问几条"，不许减"记几条"

- **决定**：模型因 `finish_reason=length` 截断而重试时，缩小的是**这一轮问模型几条结论**；
  落库的核查结果始终按**原始结论条数**生成，没问到的补 `verdict=unverifiable` +
  `notes=("missing_from_reply",)`，并在 notes 里写 `coverage:2/6`。
- **为什么**：原来重试后 `items = used_items`，没问到的结论直接从结果里消失——页面上看是
  "6 条结论全核查过、4 条有判定"，实际是 2 条被抹掉、没人会去数。核查是成稿闸门的唯一依据，
  少一条就等于让一条没人看过的结论混进成稿。
- **否决**：①"重试失败就整批失败"——模型偶尔截断，整批重来白花钱；②"没问到的默认判为支持"——
  这是拿机器的沉默冒充证据。
- **落点**：`backend/app/research/semantic.py`（`requested_items` + `_report_notes`）、
  `tests/test_semantic_check.py::test_findings_dropped_by_the_retry_still_get_a_row`、
  `backend/app/research/draft_gate.py`（`readiness_of` 额外报 `findings_total` / `unchecked`，
  并在 note 里写明「另有 N 条结论没有任何核查记录，不会进成稿」）、
  `tests/test_draft_gate.py::test_unchecked_findings_are_reported_not_hidden`。

## D-16 人工闸门放行之后，界面必须自己动起来

- **决定**：任务详情页的轮询由 `startAutoRefresh()` 统一管；**放行闸门（passGate）与推进（act）
  成功后都要开轮询**，而且放行提示写进持久的 `#task-action` 区域，不写在会被清空的闸门卡里。
- **为什么**：真事——用户在 `paused` 页点「继续跑」，后端真的跑起来了（库里 4 格 succeeded、
  花了 ¥0.0237），但页面一直停在旧画面：轮询只在"进页面时已是 running"才启动；
  而放行提示写在闸门卡内，任务一恢复、闸门卡被清空，提示跟着消失。人看到的就是"点了没反应"，
  然后去建第二条任务，把同一批抓取做两遍、钱花两遍。
- **否决**：①"让人手动点刷新"——状态机是后台推进的，手动刷是反人性的；②"放行后就跳转离开"——
  人想看的就是接下来那几格怎么跑。
- **落点**：`web/app.js` 的 `isActive()` / `startAutoRefresh()` / `passGate()` / `act()`、
  `tests/test_ui_copy.py` 两条护栏（passGate 与 act 内都必须出现 `startAutoRefresh();`）。

---

## D-17 重新核查只重判、不重新检索，且不许覆盖人工结论

- **决定**：`POST /api/research-reports/{id}/checks/recheck` 用**报告已有的入库证据**把全部结论重判一遍，
  整批替换核查行；人工已确认/驳回过的那几条，按 `finding_index` 把 `human_review_status`、`human_note`、
  `human_reviewed_at` 原样继承。
- **为什么**：核查漏条（截断重试把没问到的结论丢了）真实发生过，报告里会留下"6 条结论只有 3 条核查"的残局。
  没有补判入口，人只能重建任务、重花一遍检索钱；而重判只用已有证据，几分钱就能补齐。
- **否决了什么**：①重新联网检索（那是新的一次研究，成本和风险都不一样，不该藏在"补判"按钮后面）；
  ②把人工结论清空重来（人核对过原文的判定，不该被模型再判一次就抹掉）。
- **落点**：`backend/app/research/recheck.py`（`recheck_report` / `_check_findings` / `_evidence_units`）、
  `backend/app/research/persistence.py`（`record_checks` 本来就是整批替换 + 继承人工状态）、
  `backend/app/api/routes/research_reports.py`、`web/app.js`（`pageChecks` 的 `gapRow` 与 `#checks-recheck`）、
  `tests/test_recheck.py`（7 例）。

## D-20 画面：没有素材也要有画面，且只动色块不动字形

**决定**：规则分镜与脚本分镜里，**每个镜头都必须是一块画板**（`composition`），不再有「纯色底 + 底部字幕」；
带单位数字的镜头用数字滚动，无数字的句子用整句大字，画板底色用会漂移的柔光圆，持续运动交给**非文字元素**
（色条、圆、条形）承担。画板样式只写 `background-color`，不写 `background`。

**为什么**：用户看完第一条成片说「做出来的质量极差」——8 个镜头里 7 个是深色底 + 底部字幕，等于会动的 PPT。
另外两个真问题：①OpenKatana 的样式白名单不认 `background`，写了直接 `ContractError: unsupported fields: background`；
②给整块文字加跨整片的 `scale`（0.97→1.03）会报 `Composition is not deterministic when seeking backwards`，
因为每帧的字形对齐都在变，引擎「倒着放一遍比对」必然不一致。

**否决了什么**：否决「文字整体缓慢放大」这条防冻结套路（改成色条 `scale`）；否决「没素材就铺底色加字幕」；
否决「用生成图片/视频素材顶上」（真实素材仍待用户提供，C 类生成素材不许冒充真实场景）。

**代码落点**：`backend/app/video/kinetic.py`（数字滚动/对比条/机制图/柔光底）、
`backend/app/video/cards.py::headline_document`（整句大字 + `accent` 色条）、
`backend/app/video/script_board.py`（逐镜选画板、分开统计 `card_count`/`text_count`）、
`tests/test_script_board.py::test_headline_never_scales_the_text_block`、`test_every_composition_obeys_the_engine_track_rules`。

## D-21 数据读数走二维层，三维只负责场景与气氛

**决定**：`cinema` 引擎里，"哪根柱子代表什么、涨了多少"这类读数用 DOM 二维面板画；三维只负责空间、透视与气氛，两层叠在同一张画布上。

**为什么**：三维里柱子的颜色与明暗随光照和视角变化，同一根柱子在两个角度下亮度不同，观众无法据此读"谁高谁低"；而数据必须一眼能读。先做过三维柱列，实测被点光源打成灰白、语义丢失。

**否决了什么**：否决"全部用三维表达数据"（更炫但读不出量），也否决"全部用二维"（画面立刻退回 PPT 观感）。

**代码落点**：`backend/app/video/cinema/scene.js` 的 `buildPanels/updatePanels`；用例见 `build_cinema_film.py` 的 `stage_s2/stage_s4`。

## D-22 字幕自己写 ASS，不用 SRT + force_style

**决定**：烧字幕一律生成 `.ass`，显式写 `PlayResX/PlayResY` 与 `Style`，不用 SRT 配 `force_style`。

**为什么**：SRT 没有分辨率概念，libass 按默认 288 高度换算字号，实测把字幕放大约 6.7 倍、烧到画面中部并压住画面文字。

**否决了什么**：否决"调 MarginV/FontSize 硬凑"（换分辨率就崩），也否决"用 drawtext 手画"（换行与描边都要自己算）。

**代码落点**：`backend/scripts/build_cinema_film.py` 的 `_write_ass/_ass_time`。
