# 视频任务交接：分析：商业银行靠什么赚钱、净息差为什么下降

## 这个目录是什么

从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，
脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。

## 已经就位

- `research/report.md`（sha256 `dc1ac4b726789cb78de42ec362bc05b9e5aec88650763f0d376fbfcb11c948c1`）
- `research/evidence.json`（17 条证据）
- `research/sources.json`（4 个来源）
- `storyboard/storyboard.csv`（8 镜，36.0 秒）
- `storyboard/OUTLINE.md`（逐镜来源，说明它是规则草稿）

## 还缺什么

- script/script.md（分镜已就位，但正式脚本仍需模型或人工）
- assets_manifest.csv（素材必须先登记授权；纯色底镜头不需要）
- audio/ 与 output/（跑 render_job 才会生成）

## 当前状态

- stage：`storyboard_ready` → 下一步 `render`
- blocked：`False`
- 模型：`model_not_configured`

## 下一步

分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job v5_flow_acceptance_v2 --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。
