# V5.0 续跑备忘（暂停于 2026-11-12 16:57）

## 一、当前 Git 状态（干净，全部已推送）

- 分支 `main`，HEAD = `8053798`（`docs: README 与 V5.0 交付报告对齐当前终态`），与 `origin/main` 一致。
- 未跟踪文件只有 1 个：`backend/app/research/corroborate.py`（本轮半成品，见第三节）。
- 全量测试最后一次实测：**684 passed, 2 skipped, 1 warning in 83.53s**（唯一 warning 是既有
  `agent_runs↔research_subtasks` FK 环 SAWarning，设计内双向引用，见 phase2 遗留问题 A-01 之外的风险项）。
- API 操作数 41（`ROUTE_CALLERS` / 启动自检 / `tests/test_caller_permissions.py` 三处一致）。
- 受保护开发库 `data/finance_media.db` 全程只读，未被迁移或开 WAL。

## 二、本次暂停前刚完成的（已提交推送）

| 提交 | 内容 |
| --- | --- |
| `3d7782d` | 补齐最后五个工具（db_query/db_write/task_update/handoff/fact_check），十个工具全部有真实实现 |
| `cc11eaa` | 报告 → 视频任务交接接口 + 工作台实时进度与建任务按钮 |
| `f9f6691` | 表格数字 → 指标 + 可复现图表（`backend/app/analysis/`） |
| `e2cc128` | 分镜 → 时间轴 → 成片 → 质检流水线（`backend/app/video/render.py`） |
| `64a4bb9` | 组合素材走引擎合成、同文件多入点、成片时间轴不覆盖既有文件 |
| `e5abbbc` | 常驻 worker（`backend/scripts/run_worker.py`） |
| `88d56b6` | 后台渲染任务（`backend/scripts/render_job.py`）+ 两个进度接口 + `content/research/task-1/` 真实底稿 |
| `8053798` | README 与 V5.0 交付报告对齐终态 |

真实跑过的证据：7 镜 1275 帧时间轴（79 秒）、后台渲染进度从 `running/starting` 到
`done/timeline_ready`（08:40:59 → 08:42:22）、常驻 worker 真联网跑出 2 格 completed + 1 格
`blocked:model_not_configured`、`content/research/task-1/digest.md`（3 条真实来源、18 个候选数据点）。

## 三、半成品：`backend/app/research/corroborate.py`（未提交、未接线、无测试）

**它要解决什么**：模型不可用时，底稿里只有"原样摘录的数据点表"。人需要多两列机器可验证的信息——
①这个数字有没有别的独立来源也说（印证）；②同一件事不同来源说了不同的数（冲突，必须标出来）。

**已写的规则（写死在模块 docstring 里，可被第三人重算）**：
- 上下文 = 去掉数字、单位词、标点、空白后剩下的汉字/字母；
- 两条上下文的 4-gram Jaccard ≥ `min_overlap`（默认 0.5）→ 视为"在说同一件事"，并查集连成簇（只连不同来源）；
- 簇内某数字被 ≥ `min_sources`（默认 2）个不同来源说到 → 记为印证；
- 簇内两条数据点的**关键数字**集合完全不相交 → 记为冲突（只标不一致，不判谁对）；
- 关键数字 = 去掉纯年份（`19xx`/`20xx`），否则"2024"这种噪声会把冲突判据吃掉。

**当前实测**（`DataPoint` 5 条：S01/S02 说 1.52、S03 说 1.50、S04 无关、S05 无关）：
`checked=5 corroborated=1 conflicts=0 single=3` —— 印证只认到年份，冲突没检出。

**卡在哪**：我上一步刚加了 `_YEAR` 与 `key_numbers()`，但**簇内汇总与冲突判定仍在用 `point.numbers`**，
必须改成 `key_numbers(point)` 才能得到预期结果（S01 vs S03 → 一条冲突）。改完后预期：
`corroborated` 里不再出现年份，`conflicts` 至少 1 条（S01 ↔ S03，相似度接近 1.0）。

**接线（还没做）**：
1. `backend/app/research/digest.py`：`Digest` 增 `corroboration` 字段 + `to_markdown()` 增一节 +
   `as_dict()` 增键；`build_digest(..., corroborate=True)` 内部调用；
2. `backend/app/execution/executor.py` 的 `_analyze`/`_synthesize` 输出里带
   `corroboration{corroborated_count, conflict_count, single_source_count}`，让"有冲突"能在任务输出里看到；
3. `tests/test_research_corroboration.py`：同一数字两来源→印证、年份不算数、同题不同数→冲突、
   长句上下文不足→不误合并、单来源全落 single、`corroborate_from_dicts` 往返、markdown 里
   必须出现"不判断谁对"这句话（防止文案被改成结论）；
4. 跑全量 pytest 后提交（建议信息：`feat(research): 跨来源交叉印证与冲突标注，规则可重算`）。

## 四、下一步顺序（续跑时照此推进）

1. 收尾第三节的 `corroborate.py`（改判据 → 接线 → 测试 → 提交推送）。
2. 图表面向视频的接线：把 `backend/app/analysis/charts.py` 产出的 PNG 注册进
   `assets_manifest.csv`，让它能直接被分镜引用（现在分镜只能引用已登记素材）。
3. 真实研究闭环的"非模型部分"再补一块：把底稿里的印证结果写进 `research_reports` 的
   `limitations`（哪些数字只有单一来源），这样接上密钥后模型拿到的输入就是带证据分级的。
4. 真实模型仍是唯一硬阻塞：无 `.env`、无 `DEEPSEEK_API_KEY`、本机无模型服务。
   解除三步写在 `README.md` 与 `docs/research_hub/v5_0_delivery_report.md` §F。
