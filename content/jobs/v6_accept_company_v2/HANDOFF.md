# 视频任务交接：结论整合：请基于公开财报与公告研究隆基绿能 2024 年的经营情况：主营业务与商业模式、收入与利润结构变化、盈利能力与现金流、资产负债与存货减值、与同行的差异、以及主要风险。请标注数据口径与时点。

## 这个目录是什么

从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，
脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。

## 已经就位

- `research/report.md`（sha256 `a9be4c2d7f3e831f08fd561bbea1ad5b3ab78300d29ab1d5ef271e14965e3686`）
- `research/evidence.json`（344 条证据）
- `research/sources.json`（21 个来源）
- `storyboard/storyboard.csv`（4 镜，18.0 秒）
- `storyboard/OUTLINE.md`（逐镜来源，说明它是规则草稿）

## 还缺什么

- script/script.md（分镜已就位，但正式脚本仍需模型或人工）
- assets_manifest.csv（素材必须先登记授权；纯色底镜头不需要）
- audio/ 与 output/（跑 render_job 才会生成）

## 当前状态

- stage：`storyboard_ready` → 下一步 `render`
- blocked：`False`
- 模型：`deepseek`

## 下一步

分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job v6_accept_company_v2 --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。
