# 视频任务交接（按脚本）：电力的第二张收入表：钱没变多，只是分法变了

## 这个目录是什么

从**已批准的脚本 # 3（第 3 版）**开出来的视频任务。
研究材料没有搬进来——这条片子只依据这份脚本；要看依据，回脚本页或来源报告。

## 已经就位

- `script/SCRIPT.md`（脚本原文快照）
- `script/script.json`（逐句结构，含来源标注）
- `storyboard/storyboard.csv`（8 镜，45.1 秒）

## 还缺什么

- assets_manifest.csv（数据卡由分镜阶段登记；外部素材必须先登记授权）
- audio/ 与 output/（跑 render_job 才会生成）
- 正式发布（系统不代发）

## 下一步

分镜已按脚本排好（storyboard/storyboard.csv，逐镜来源见 storyboard/FROM_SCRIPT.md）。先人工看一遍分镜，然后跑 python -m backend.scripts.render_job --job v6_power_first_film --narration 出片。
