# 视频任务交接：结论整合：2024 年以来光伏组件价格持续下跌，行业出现普遍亏损。请研究：组件价格战是怎么形成、又通过哪些环节影响龙头组件企业的现金流与资本开支的？请给出可核对的公开数据。

## 这个目录是什么

从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，
脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。

## 已经就位

- `research/report.md`（sha256 `3cab2d1c51b85e29e1c9554eeded0155f376ee72ac098f965c6eef0f89229be1`）
- `research/evidence.json`（416 条证据）
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
- 模型：`model_not_configured`

## 下一步

分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job gate_demo_after_confirm --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。
