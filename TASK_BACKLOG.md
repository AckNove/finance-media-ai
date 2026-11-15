# TASK_BACKLOG.md — 还欠什么

> 排序规则：P0 核心链路跑不跑得动 → P1 明显影响质量 → P2 体验 → P3 已明确推迟。
> 每条必须有**验收方式**，否则不算条目。状态只写：`待办` / `进行中` / `阻塞` / `已完成`。
>
> 最近更新：2026-11-15（B-06 完成后，用户决定转入"做第一个作品"）

## P0 — 影响核心链路能不能跑

| # | 事项 | 状态 | 验收方式 | 阻塞 |
| --- | --- | --- | --- | --- |
| B-01 | 落地高效开发协议与四份状态文件 | 已完成 | `docs/dev_protocol.md` + 四个根文件存在；`state_measure --md` 能复算 `PROJECT_STATE.md` 的数字 | — |
| B-02 | 搬迁后实跑一次 `scripts\start.ps1`（验证启动脚本没有 C 盘假设） | 已完成 | 2026-11-15 21:37 在 `D:\finance-media-ai` 实跑 `.\scripts\start.ps1 -Port 8011 -NoWorker -NoBrowser`：先判出工作库落后（`b6_v6_operations` → `b8_calendar_tags`）并自动升级，`/health` 200、`/api/system/status` 200（32 表 / `b8_calendar_tags` / `journal_mode=delete`）、`/app/` 200、启动自检 8/8；停服后无残留进程、受保护开发库 sha256 未变 | — |
| B-03 | 真实素材接入（第一份授权素材走通 `register_source` → 预检 → 进片） | 阻塞 | 用户给出文件 + 来源/链接/许可四项；`python -m backend.scripts.register_source ...` 后 `assert_renderable()` 通过，且成片里出现该素材 | **等用户给素材**（见 `KNOWN_ISSUES.md` K-02） |
| B-13 | 启动时把"工作库版本 vs 代码版本"当闸门（本轮新增并完成） | 已完成 | `python -m backend.scripts.db_revision --json` 五种状态；`start.ps1` 落后即升级并复检、领先/未知拒绝启动；`tests/test_db_revision.py` 10 例 | — |
| B-14 | 核查漏条：模型重试时把没问到的结论从核查结果里抹掉 | 已完成 | 报告 6 条结论只落 4 条核查。修 `backend/app/research/semantic.py`：缩梯子只减少"问模型几条"，结果集仍按**原始 6 条**落库，没问到的补 `verdict=unverifiable` + `notes=missing_from_reply`，notes 写 `coverage:2/6`。`tests/test_semantic_check.py::test_findings_dropped_by_the_retry_still_get_a_row`（6 条齐全 + 费用仍是两次之和） | — |
| B-15 | 闸门页点「继续跑」后界面不动（自动刷新没开、提示被自己抹掉） | 已完成 | 根因：轮询只在进入页面时状态已是 active 才启动；放行提示写在闸门卡内，任务一恢复闸门卡被清空 → 提示随之消失。修 `web/app.js`：抽 `isActive()`/`startAutoRefresh()`，放行与推进成功后都开轮询，提示改写到持久的 `#task-action`。`tests/test_ui_copy.py` 两条护栏钉死；真机 Playwright 实测进度卡自己变化、`page errors` 为空 | — |
| B-16 | 重新核查：漏判的结论能补判（只重判不重新检索） | 已完成 | `POST /api/research-reports/{id}/checks/recheck`；报告 #2 从 3/6 补到 6/6，花 ¥0.037209 |
| B-17 | 让「只写脚本」的任务真的用上模型（选题兜底 + 密钥解析 + token 阶梯） | 已完成 | 报告 `topic_id` 一直是 NULL → 记下来并三级兜底；密钥解析直接读 `.env`；脚本超限改阶梯 3000/4000，产出 `generated_by=model`、¥0.016648 |
| B-18 | 画面从「底色+字幕」升级为动态图表 / 数字动画 / 整句大字 | **已完成** | `backend/app/video/kinetic.py`、`cards.headline_document`、`script_board` 逐镜选画板；回执新增 `card_count`/`text_count`；两条契约护栏在 `tests/test_script_board.py` |

## P1 — 明显影响质量

| # | 事项 | 状态 | 验收方式 | 阻塞 |
| --- | --- | --- | --- | --- |
| B-04 | 语义核查拿到第一份真实 `contradicted` 样本 | 待办 | 换"两个权威来源口径直接打架"的主题（如不同口径的装机量/收益率），核查结果出现 `verdict=contradicted` 且证据片段逐字可回查 | 三次专门尝试都判成"证据不足"，见 K-01 |
| B-05 | `research.plan` 规则版 vs 模型版效果对比 | 待办 | 同一主题各跑一次，比较检索词命中率与后续 evidence 条数；结论写进 `DECISION_LOG.md` | 消耗真实额度，需用户点头 |
| B-06 | 视频画面：降低"数据卡+底色"占比，补真实图表/示意图镜头 | 已完成 | 新增 `backend/app/video/chart_shots.py`：同单位同指标、且横轴说得清"哪一期/哪个来源"的数字拼成柱状图；数据卡封顶 `MAX_CARDS=3`。实测 6 镜头：图表 1 + 数据卡 3 + 底板 2，数据卡占比 50%；`tests/test_chart_shots.py` 15 例 | — |
| B-07 | 逐页核对 20 个页面里还有哪些是"只读占位" | 待办 | 每页至少一次真实操作（点一下就走真接口），把没接真接口的记进本条 | — |

## P2 — 体验

| # | 事项 | 状态 | 验收方式 | 阻塞 |
| --- | --- | --- | --- | --- |
| B-08 | 首页"今天该发什么"补上"发完之后"的闭环提示 | 待办 | 排期标记已发布且填链接后，首页不再提示该条，并出现"回填数据"入口 | — |
| B-09 | 成本页按模型细分到"每个任务的每次调用" | 待办 | 展开某任务能看到每条 `model_calls`（含 `finish_reason`、耗时、金额） | — |

## P3 — 已明确推迟（用户决定）

| # | 事项 | 状态 | 说明 |
| --- | --- | --- | --- |
| B-10 | 云渲染与自动发布 | 推迟 | User said (m28360 前后)：云渲染与自动发布**先不做** |
| B-11 | 多平台账号 API 直连 | 不做 | 改为人工回填 CSV；每天发 10 条以上再考虑（`DECISION_LOG.md` D-06） |
| B-12 | 本地大模型（Ollama 等） | 推迟 | 未装、未经批准不装大型本地模型 |

## 已完成（只记最近 10 条，历史见 `docs/research_hub/v6_0_delivery_report.md`）

| 提交 | 事项 |
| --- | --- |
| `8f10d14` | 产物不再写绝对路径（`OutlineResult.as_dict()` 写相对路径）+ 仓库整体搬到 D 盘 |
| `80ee641` | 真实素材登记收成一条命令（`register_source`） |
| `10059df` | 时间都花在哪（从事件流推耗时：干活 / 等人 / 模型调用） |
| `dd65e33` | 整条链路七段（研究→报告→核查→脚本→成片→排期→导出） |
| `65d02d0` | 从选题直接建研究任务 |
| `ca65849` | 采纳选题建议时把来源一起带走 |
| `dfa71cb` | 服务商设置页说清"缺哪个变量、写在哪、写完怎么验" |
| `648a852` | 本机环境体检 25 项 |
| `2f5b1e0` | 模型设置页说清"为什么这个模型没用上" |
| `df41830` | 成本页按阶段 / 按任务说清钱花在哪 |
