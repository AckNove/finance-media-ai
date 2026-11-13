# 视频任务交接：结论整合：2025 年储能装机仍在增长，但电芯与系统价格持续下行、部分企业毛利转负。请研究：这个行业当前处在什么阶段，市场规模与增速、供需结构、竞争格局、驱动因素与政策变化分别是什么，主要风险在哪里？请给可核对的公开数据。

## 这个目录是什么

从一份**带证据链**的研究报告开出来的视频任务。研究材料已经搬进来，
脚本与分镜故意留空——它们要基于这些材料来写，不能凭空生成。

## 已经就位

- `research/report.md`（sha256 `037eafc5bddde5cbcd4a4ac81e5a9828eb3cd3a91061f35173df2be55455f45e`）
- `research/evidence.json`（424 条证据）
- `research/sources.json`（22 个来源）
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

分镜已按规则排好（storyboard/storyboard.csv，逐镜来源见 storyboard/OUTLINE.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job v6_accept_industry --narration 出片；要换成模型写的脚本，配好密钥后重排分镜。
