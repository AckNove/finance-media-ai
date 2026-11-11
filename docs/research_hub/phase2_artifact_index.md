# Phase 2 交付物索引（冻结版）

> 路径一律是**仓库相对路径**，全部在本地真实存在（本文件写完后逐条 `Test-Path` 核对过）。
> 「纳入 Git」三态：
> - **是** = 已被 Git 跟踪（本轮会一并提交）
> - **否（忽略）** = 存在但被 `.gitignore` 排除，属可再生或过大资产
> - **否（本地）** = 按冻结任务 §五.3「不把 MP4 提交到 Git」的口径，本地保留、不入库
>
> 「可再生」= 重跑命令即可重建（时间成本已注明）。

## 1. 最终成片

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4` |
| 用途 | Phase 2 最终交付成片（42.5 s / 1080×1920 / H.264+AAC） |
| 大小 / 哈希 | 20,097,925 B / `0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9` |
| 纳入 Git | **否（本地）**——本轮按冻结任务 §五.3 取消跟踪，文件仍在磁盘 |
| 可再生 | 是。`build_phase2.py --stage render`，约 5 分钟（画板帧已缓存）；冷启动约 20 分钟 |

> 恢复入库只需两步：删掉 `.gitignore` 中 `# 成片改由本地保留` 那行注释下的规则，
> 然后 `git add -f content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4`。

## 2. 分镜与时间轴

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/storyboard/storyboard.csv` |
| 用途 | **唯一分镜数据源**（16 列强校验）：7 镜 / 42.5 s / 1275 帧，含 `narration` 与 `subtitle` 两列 |
| 大小 | 3,325 B |
| 纳入 Git | 是 |
| 可再生 | 否（人工创作的数据，属源文件） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/project/timeline.json` |
| 用途 | 实际渲染用的时间轴（7 个 clip、7 个镜头、6 条字幕、8 条音轨、6 种转场） |
| 大小 | 7,041 B |
| 纳入 Git | 是 |
| 可再生 | 是。`--stage timeline`（由分镜 + 画板 clip 映射生成） |

## 3. 素材清单与来源登记

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/assets_manifest.csv` |
| 用途 | A/B/C 三类素材的总账：16 行（4 个真实源 + 6 个原创视觉/配乐 + 6 个音效），含授权、商用判定、署名、用途、用于哪些镜头 |
| 大小 | 4,891 B |
| 纳入 Git | 是 |
| 可再生 | 是。`--stage evidence` |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/source_register.csv` |
| 用途 | **素材白名单登记表**（13 个溯源字段 + license / commercial_use / credit），结尾卡署名就是从这里读的 |
| 大小 | 2,322 B |
| 纳入 Git | 是 |
| 可再生 | 否（人工核验后登记，属证据） |

## 4. 质检与一致性

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/qc_report.md` |
| 用途 | 17 项自动质检的人读版报告 |
| 大小 | 2,210 B |
| 纳入 Git | 是 |
| 可再生 | 是。`--stage qc`（约 1 分钟） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/qc_metrics.json` |
| 用途 | 17 项质检的机读结果（`all_checks_ok: true`） |
| 大小 | 3,388 B |
| 纳入 Git | 是 |
| 可再生 | 是。同上 |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/audio_consistency.json` |
| 用途 | **音频四口径一致性回执**：events 6 / files 6 / music 2 / tracks 8，`ok: true`，含 `timeline_revision` |
| 大小 | 1,169 B |
| 纳入 Git | 是 |
| 可再生 | 是。`--stage timeline`（不通过会直接中断构建） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/verification.json` + `qc/verification.md` |
| 用途 | `verify_closure.py` 的只读复算结果：分镜 / 素材哈希 / 清单 / 时间轴 / 音频口径，`VERDICT: PASS` |
| 大小 | 4,151 B + 420 B |
| 纳入 Git | 是 |
| 可再生 | 是。`python content\jobs\phase2_media_engine_demo\script\verify_closure.py` |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/visual_direction.md` |
| 用途 | 七镜职责表、运镜与转场理由、A/B/C 素材策略、声音策略、明确没做的事 |
| 大小 | 6,416 B |
| 纳入 Git | 是 |
| 可再生 | 否（创作意图文档） |

## 5. 视觉审查材料

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/review/`（18 张关键帧 + `contact_sheet.png`） |
| 用途 | 人工复核材料：`00_00_opening`、`01–07_shot_shNN`、`08–13_trans_shNN`、`14–16_anim_*`、`17_99_credits`（署名卡） |
| 大小 | 18 帧约 15.7 MB + 联系表 3,321,835 B |
| 纳入 Git | **否（忽略）**——`.gitignore` 第 66 行全局 `*.png` |
| 可再生 | 是。`--stage review`（约 1 分钟，逐帧 ffmpeg 抽取 + `tile=4x5` 拼图） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/review_index.md` + `qc/review_index.json` |
| 用途 | 每张关键帧的时间点、所属镜头、转场/动画类型索引；外加六个音效的分窗电平对比 |
| 大小 | 3,178 B + 5,458 B |
| 纳入 Git | 是 |
| 可再生 | 是。同上 |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/qc/frames/`（7 张关键帧） |
| 用途 | `--stage evidence` 抽的每镜关键帧（供文档引用） |
| 纳入 Git | 否（忽略，`*.png`） |
| 可再生 | 是。`--stage evidence` |

## 6. 校验脚本与构建脚本

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/script/verify_closure.py` |
| 用途 | **只读**复算交付物之间的一致性，不改任何文件；输出 `qc/verification.*` |
| 大小 | 8,956 B |
| 纳入 Git | 是（本轮新增） |
| 可再生 | 否（代码） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/script/build_phase2.py` |
| 用途 | 分镜 → 配乐/音效 → 画板 → 导入 → 时间轴 → 渲染 → 质检 → 复核 → 清单 的编排（纯胶水） |
| 大小 | 65,443 B |
| 纳入 Git | 是 |
| 可再生 | 否（代码） |

| 项 | 内容 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/raw/_work/`（`engine_calls.jsonl`、`audio.json`、`clips.json`、`timeline*.json`、`render.json` 等） |
| 用途 | 构建过程回执：引擎调用日志（57,144 B，含每次 CLI 调用的工具名/耗时/错误）与各阶段事实快照 |
| 纳入 Git | 是（小体积、纯文本，作为交付证据） |
| 可再生 | 是（重跑构建会重写） |

## 7. Phase 2 相关文档（`docs/research_hub/`）

| 文件 | 用途 | 纳入 Git | 可再生 |
| --- | --- | --- | --- |
| `phase2_media_adapter_report.md` | 引擎适配层设计与契约 | 是 | 否 |
| `phase2_import_test_report.md` | 真实素材导入测试结果 | 是 | 否 |
| `phase2_storyboard_schema.md` | 分镜 16 列 schema 与校验规则 | 是 | 否 |
| `phase2_timeline_integration.md` | 分镜 → 时间轴的映射与契约 | 是 | 否 |
| `phase2_asset_provenance_report.md` | 素材溯源方法与清单说明 | 是 | 否 |
| `phase2_video_demo_report.md` | 演示片构成与逐镜说明 | 是 | 否 |
| `phase2_qc_report.md` | 交付时 16 项质检（历史版本） | 是 | 否 |
| `phase2_decision_register.md` | 交付时决策登记 | 是 | 否 |
| `phase2_storyboard_semantics_report.md` | **本轮**：narration / subtitle 职责与测试 | 是（本轮新增） | 否 |
| `phase2_audio_consistency_report.md` | **本轮**：四个音频口径、room tone 决策、门禁 | 是（本轮新增） | 否 |
| `phase2_credit_verification_report.md` | **本轮**：素材授权与署名逐条核验（含三项待核实） | 是（本轮新增） | 否 |
| `phase2_visual_review_report.md` | **本轮**：18 帧复核、转场亮度、音效可闻度、响度说明 | 是（本轮新增） | 否 |
| `phase2_final_qc_report.md` | **本轮**：最终成片 17 项质检汇总 | 是（本轮新增） | 否 |
| `phase2_final_decision_register.md` | **本轮**：D-01–D-11 最终决策与残留风险 | 是（本轮新增） | 否 |
| `phase2_closure_remediation_report.md` | **本轮**：整改总报告（问题 → 修复 → 证据） | 是（本轮新增） | 否 |
| `phase2_handover_report.md` | **冻结**：交接总说明 | 是（本轮新增） | 否 |
| `phase2_artifact_index.md` | **冻结**：本文件 | 是（本轮新增） | 否 |
| `phase2_remaining_issues.md` | **冻结**：遗留问题清单 | 是（本轮新增） | 否 |

## 8. 不在交付物内、但与本任务相关的目录

| 路径 | 说明 | 纳入 Git |
| --- | --- | --- |
| `content/jobs/phase2_media_engine_demo/project/c322cb9a56d9/.openkatana/` | 引擎工作区：画板帧序列、导入帧序列（**约 3.8 GB**） | 否（忽略） |
| `content/jobs/phase2_media_engine_demo/project/c322cb9a56d9/plates/` 与 `media/` | `import_media` 生成的帧序列与转码副本 | 否（忽略） |
| `content/jobs/phase2_media_engine_demo/project/c322cb9a56d9/timeline-<hash>.json` | 引擎保存时间轴时留下的历史快照 | 否（本轮加入忽略规则） |
| `content/jobs/phase2_media_engine_demo/input/approved_sources/` | 白名单目录里的 4 个原始文件（NASA 图/视频、Apache-2.0 配乐） | 是（体积小，可核验哈希） |
| `tools/ffmpeg/` | FFmpeg 8.1.2 二进制（`bin/` 被忽略，只留 `VERSION.json`/`SOURCE.md`/`LICENSE`） | 部分是 |
| `tools/openkatana/` | OpenKatana 0.1.3 源码克隆 + 独立 venv（504 MB） | 否（忽略） |

## 9. 一句话复现

```powershell
# 只读核对（不改任何文件、不重新渲染）
.venv\Scripts\python.exe -X utf8 content\jobs\phase2_media_engine_demo\script\verify_closure.py
```

完整重建（会覆盖画板、时间轴、成片与质检结果，需约 20 分钟）：

```powershell
.venv\Scripts\python.exe -X utf8 content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage all
.venv\Scripts\python.exe -X utf8 content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage review
.venv\Scripts\python.exe -X utf8 content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage evidence
```
