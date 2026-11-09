# 内容与视频生产工作流（第一阶段）

第一阶段**只建立流程、目录、配置与质检清单**，不实现渲染引擎。
下表每一行的"状态"指的是代码/脚本层面的完成度，不是文档层面的。

## 1. 十四步标准工作流

```
选题 → 研究任务 → 原始资料 → 事实证据 → 研究报告 → 专业审核 → 脚本 → 人工审批
     → 分镜 → 图表与素材 → 配音与字幕 → 视频合成 → 自动质检 → 人工终审 → 人工发布
```

| # | 阶段 | 产物 | 落地位置 | 关联数据 | 代码状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | 选题 | 选题记录 | `content/topics/` + `topics` 表 | `content_status=idea` | DONE（接口） |
| 2 | 研究任务 | `agent_runs(status=pending)` | `agent_runs` 表 | `topic_id` | PARTIAL：只登记，不执行 |
| 3 | 原始资料 | 来源记录 + 摘录 | `data/raw/` + `sources` 表 | `content_hash` | DONE（接口） |
| 4 | 事实证据 | 证据记录 | `evidence` 表 | `source_id` + 目标 | DONE（接口） |
| 5 | 研究报告 | 报告（findings/limitations） | `knowledge/research_reports/` + `research_reports` 表 | `topic_id` | DONE（接口） |
| 6 | 专业审核 | `review_records` | `review_records` 表 | `(target_type,target_id)` | PARTIAL：表已建，无审核接口 |
| 7 | 脚本 | 脚本 Markdown + `scripts` 行 | `content/scripts/{topic_slug}/` | `topic_id`/`research_report_id` | PARTIAL：表已建，无写入接口 |
| 8 | 人工审批 | `approved_at/approved_by` | `scripts` 表 | — | NOT IMPLEMENTED（无审批接口） |
| 9 | 分镜 | 分镜表 | `content/storyboards/` | 模板 | NOT IMPLEMENTED |
| 10 | 图表与素材 | 图片/图表 | `content/assets/` + `media_assets` 表 | `script_id` | NOT IMPLEMENTED（matplotlib 未安装） |
| 11 | 配音与字幕 | 音频/SRT | `video_engine/audio/`、`video_engine/subtitles/` | `media_assets` | NOT IMPLEMENTED |
| 12 | 视频合成 | MP4 | `video_engine/renderers/` | `media_assets` | NOT IMPLEMENTED（FFmpeg 未安装） |
| 13 | 自动质检 | 质检报告 | `video_engine/quality_control/` | 12 项检查 | NOT IMPLEMENTED（清单已定义） |
| 14 | 人工终审 + 发布 | `publications` 行 | `content/published/` | `script_id + script_version` | NOT IMPLEMENTED（表已建，无发布接口） |

### 第 1 步之前：先定合集与形式

选题进入流程前，先按 `config/content_taxonomy.yaml` 确定：

- `collections`：1–3 个合集 + 1 个 primary（六大合集：金融知识 / 财经时事 / 产业链研究 /
  公司商业解剖 / 足球金融与商业 / 金融与商业案例）；
- `audience_level`：`mass` / `learner` / `researcher`（对应库字段 `beginner`/`intermediate`/`advanced`）；
- `forms`：本次要做 60–120s 短视频、2–5min 知识视频、5–15min 纪录片、图文、报告、系列、
  会员内容还是课程——**形式决定模板，模板不是形式本身**。

数据库当前只有单选 `column_id`，多合集的真实存储是二期任务（见 `docs/content_strategy.md` §3）。

## 2. 状态机

- 选题内容状态：`idea → research → drafting → reviewing → approved → storyboard →
  producing → qa → published`（`archived` 为终态旁支）。
- 研究状态：`pending → in_progress → completed | failed`。
- 脚本状态：`draft → in_review → approved | rejected`；`approved` 之后才能 `storyboard_ready
  → produced → published`。
- Agent 状态：`pending → running → succeeded | failed | cancelled`。

硬规则：**未 `approved` 的脚本不得进入分镜；`published` 只能由人工触发。**

## 3. 四类视频模板

模板定义在 `video_engine/templates/*.yaml`，`structure` 的 `share` 之和为 1.0
（测试会校验）。

| 模板 | 用途 | 时长（min/default/max，秒） |
| --- | --- | --- |
| `football_explainer` | 用足球场景切入金融知识 | 90 / 150 / 180 |
| `financial_mechanism` | 机制讲解（不依赖足球也可成立） | 60 / 150 / 240 |
| `industry_research` | 产业链与利润分配研究 | 90 / 180 / 300 |
| `case_documentary` | 案例纪录片式复盘 | 120 / 210 / 360 |

每个模板包含：`canvas`、`target_duration_seconds`、`structure`（段落 key/label/share/
requirement）、`analogy_policy`、`review_gates`。

### 内容形式 ↔ 模板映射

| 内容形式（`config/content_taxonomy.yaml#forms`） | 时长 | 可用模板 |
| --- | --- | --- |
| `short_news` 热点短视频 | 60–120s | `financial_mechanism`、`football_explainer` |
| `knowledge_video` 知识视频 | 120–300s | `financial_mechanism`、`football_explainer`、`industry_research` |
| `documentary` 深度纪录片 | 300–900s | `case_documentary`、`industry_research` |
| `article` 图文 / `research_report` / `series` / `member_research` / `course` | — | 不渲染视频（报告与课程可再派生上面三种） |

注意时长口径差异：模板的 `target_duration_seconds` 是**单条视频的实现范围**，
形式的时长是**产品定义的硬边界**，取交集使用（例如 60s 的短视频只能用
`financial_mechanism` 的下限区间，不能套 300s 的产业链模板）。

默认视频规格（全局）：**1080×1920、30fps、9:16 竖屏**，适配抖音与小红书
（`config/settings.yaml` 的 `video`，`rendering_enabled: false`）。

## 4. 人工闸门

1. 专业审核（内容准确性）—— 由 `fact_checker` 结论 + 人工复核共同决定。
2. 合规审核（不构成投资建议、无收益承诺）—— `config/content_rules.yaml`
   的 `human_review_triggers` 命中即必须人工。
3. 脚本审批（`approved_at/approved_by`）。
4. 视频终审（成片）。
5. 发布（人工点击，系统永不自动发布）。

## 5. 质检清单（12 项，`video_engine/quality_control/qc_checks.yaml`）

`canvas`（1080×1920/30fps/9:16）、`duration`（±10%）、`subtitle_presence`、
`subtitle_line_length`（≤14 字/行）、`subtitle_safe_area`（底部 ≥180px 安全区）、
`audio_sync`（≤200ms）、`loudness`（-14 LUFS ±2，true peak ≤ -1 dBTP）、
`number_consistency`（口播数字与图表一致）、`disclaimer_present`、
`analogy_boundary_spoken`（类比边界必须被念出来）、`hook_has_substance`、
`investment_advice`（出现买卖建议即 blocker）。

`auto: true` 的项未来可由 ffprobe/规则脚本判定；`auto: false` 必须人工确认。
**当前没有任何脚本读取这个文件**，它是规范与未来的实现目标。

## 6. 命名与存放约定

- 脚本：`content/scripts/{topic_slug}/{template}-v{version}.md`（与
  `scripts` 表的 `(topic_id, template, version)` 唯一约束对应）。
- 分镜：`content/storyboards/{topic_slug}/{template}-v{version}.md`。
- 素材：`content/assets/{topic_slug}/...`（大文件被 `.gitignore` 忽略，只提交清单与小图）。
- 知识条目：`knowledge/{domain}/{slug}.md`，8 个子目录与 `knowledge_domain` 对应。

## 7. 下一阶段接线顺序建议

1. 脚本接口（POST/PATCH `/api/scripts`）+ 审批接口 → 打通 #7/#8。
2. `review_records` 写入接口 → 打通 #6 的可审计链路。
3. 字幕生成（离线 Whisper 或 TTS 时间戳）+ `media_assets` 登记 → #11。
4. FFmpeg 渲染器 + `qc_checks.yaml` 的自动项 → #12/#13。
5. 发布记录接口（仅记录，不调平台 API）→ #14。
