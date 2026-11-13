# 视频任务交接：结论整合：请沿光伏产业链拆解上游硅料、中游硅片电池组件、下游电站与辅材设备的成本结构与价值分配：各环节的钱是怎么赚的、当前瓶颈在哪、利润为什么会在不同环节之间迁移？请给出可核对的公开数据与来源。

## 这个目录是什么

从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，
脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。

## 已经就位

- `research/report.md`（sha256 `5316d30f92fc315da3620aa0437b86bd400085dee71045485eb856e6fe07d023`）
- `research/evidence.json`（440 条证据）
- `research/sources.json`（23 个来源）
- `storyboard/storyboard.csv`（3 镜，13.5 秒）
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

分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job v6_accept_chain_11131719 --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。
