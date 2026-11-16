# AGENTS.md — 本仓库的协作约定

> 这一页只写「怎么在这个仓库里干活」，不重复 `docs/research_hub/` 里的设计与交付内容。
> 规则优先级：**用户在当轮对话里的直接指令 > 本文件 > 历史任务书**。

## 1. Git 同步（2026-11-11 起生效）

每一轮改动完成后，**都要提交并推送到 GitHub**，不要攒着。

- 远程：`origin` = https://github.com/AckNove/finance-media-ai.git，分支 `main`。
- 提交粒度：**一个可验证的改动一次提交**（一个功能、一份文档、一次修复），不要把互不相关的东西塞进
  同一个 commit。
- 每次提交后立刻 `git push origin main`，并核对 `HEAD == origin/main`、`git status --porcelain` 为空。
- 提交信息用中文，形如 `type(scope): 一句话说清做了什么`，正文写「为什么改」和「影响到谁」；
  类型沿用 `feat / fix / docs / chore / test / refactor`。
- 历史任务书里「不 commit 不 push」的限制**已被用户撤回**；若某一轮又出现该限制，以当轮对话为准，
  并在提交前确认一次。

### 绝不入库的东西

`.gitignore` 已经覆盖，但别依赖它兜底：

- 密钥与环境变量（`.env*`、`*.pem`、`*.key`、`secrets/`）；
- 数据库文件（`*.db`、`*.db-wal`、`*.db-shm`）与 `data/backups/**`；
- 渲染中间产物（`.openkatana/`、`plates/`、`media/`、`fonts/`、`history/`），它们以 GB 计且可再生；
- 第三方二进制与克隆仓库（`tools/ffmpeg/{bin,doc,presets}`、`tools/openkatana/`）；
- 旧任务目录 `content/jobs/20261110_jgb_gold_repricing/`（含从本机桌面复制的原始数据）。
- 成片 `*.mp4` 与渲染帧：`.gitignore` 已覆盖，但**任务目录里的文本产物要入库**——
  `job.json`、`script/`、`storyboard/`、`assets/compositions/*.json`、`project/timeline.json`、
  `qc/`、`assets_manifest.csv`、`audio/narration/*.json|*.srt`。它们是"这条片子可复核"的部分，
  合计只有百 KB 级；成片可以随时用它们原样重渲。


入库前自查一条命令：`git diff --cached --name-only` 里不应出现上面任何一类。

## 2. 数据与身份边界

- **真实开发库 `data/finance_media.db` 只读**：不做迁移、不开 WAL、不 seed、不回填。改动一律在
  临时库上演练，并留下可复跑的证据。
- `model_calls` 表在接入真实模型之前必须保持 0 行。
- 外部请求永远不能靠 `X-Caller` 自报身份；agent 身份只能由服务端内部构建。

## 3. 视频与素材任务

- 素材必须先进白名单登记（`backend/app/core/job_paths.py`），授权不明（`commercial_use != allowed`）
  一律拒绝渲染，**不得伪造来源**。
- 每个任务的产物写在自己的 `content/jobs/<job_id>/` 下；不复用旧任务的原始资料与衍生数字。
- 程序只判技术指标（分辨率、音画等长、黑帧、响度……），**不判断「好不好看」**；视觉质量交人看片。

## 4. 验证纪律

- 声称完成前必须真跑一遍：`pytest -o addopts=""`（注意 `pyproject.toml` 的 `addopts` 已带 `-q`，
  想看清用例数就用 `-o addopts=""`）。**但不必每改一个小文件都跑全量**——三级测试见协议 §4：
  改完先跑受影响模块，模块完成跑相关集成，一轮收尾才跑全量。
- 报告里区分「已验证」「未验证」「做不到」，不要把只有接口或目录的东西写成已实现。

## 5. 执行协议与四份状态文件（2026-11-15 起生效）

干活的方式写在 `docs/dev_protocol.md`（高效开发执行协议 V1.0 的本仓库落地版）。核心几条：

- **开工先看状态，不从头扫仓库**：`git` 四连（分支 / 最近提交 / 工作区 / 与远端差距）+ 读四份状态文件。
- **一轮一个业务交付目标**；顺手发现的问题写进 `TASK_BACKLOG.md`，不塞进本轮。
- **状态数字必须现算，不许照抄上一轮**：`python -m backend.scripts.state_measure --md`。
- **台账自查**：`python -m backend.scripts.matrix_lint`（DONE 行引用的文件必须真在）。

每轮结束更新这四份文件（都在仓库根）：

| 文件 | 记什么 |
| --- | --- |
| `PROJECT_STATE.md` | 现在是什么样（数字可由命令复算） |
| `TASK_BACKLOG.md` | 还欠什么（P0–P3 + 阻塞原因 + 验收方式） |
| `DECISION_LOG.md` | 为什么这么做（含被否决的方案） |
| `KNOWN_ISSUES.md` | 哪里还疼（证据 + 是否阻断） |

**状态与代码不一致时以代码为准，并当场改状态文件。**
