# V2.2 Phase 2 收尾整改报告

> 本轮（Phase 2 Closure Remediation）不是新功能，也不是 Phase 3。它只做一件事：
> 把 Phase 2 验收里被打回的四个问题修好，并把每一条修复都落成**可复算的证据**。
>
> 最终成片：`content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4`
> SHA-256 `0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9`（20,097,925 B / 42.500 s）

## 1. 四个问题：修复前 → 修复后

| # | 问题 | 修复前（实测） | 修复后（实测） | 证据 |
| --- | --- | --- | --- | --- |
| 1 | NASA 素材署名 | 结尾卡只有一句「素材：NASA 公共领域影像」，三个不同作者被压成一句 | 逐条 6 行署名，全部由代码从 `source_register.csv` 的 `credit` 列读取，credit 为空直接构建失败 | `phase2_credit_verification_report.md`、抽帧 `qc/review/17_99_credits.png` |
| 2 | narration / subtitle 职责 | 「旁白不会变成字幕」只是隐含行为，无开关无测试；`sh07` 卡面文字与字幕重复 | `SUBTITLE_SOURCES` 三态开关 + 7 条测试；`sh07` 字幕删除 | `phase2_storyboard_semantics_report.md`、`tests/test_media_timeline.py` |
| 3 | 音效统计口径 | 4 处不同数字：`counts()=7`、实生成 6、清单写成 10 行、CSV 实为 15 行；`room tone` 从未生成过 | 四个口径分开记并互相校验：6 事件 / 6 文件 / 2 配乐 / 8 音轨；`audio_consistency()` 在构建期强制 | `phase2_audio_consistency_report.md`、`qc/audio_consistency.json` |
| 4 | 视觉 / 听觉人工验收 | 只有自动 QC，没有抽帧材料，也没有人看过 | 18 帧抽帧 + 联系表 + 六种转场亮度曲线 + 六个音效分窗电平；并由此**发现并修掉一个真缺陷**（`whoosh_tail` 是静音文件） | `phase2_visual_review_report.md`、`qc/review/` |

第 4 项的意外收获值得单独说：抽帧与分窗测量本来是「给人看的复核材料」，结果测出
`whoosh_tail.m4a` 平均电平 **−73.1 dBFS**（等于静音），另外四个音效被配乐压住 1–5 dB。
这不是口径问题，是**成片缺陷**，所以本轮修了并重渲染了一次。

## 2. 代码改动（6 个文件）

| 文件 | 改了什么 | 为什么 |
| --- | --- | --- |
| `backend/app/media/storyboard.py` | 新增 `SOUND_EFFECT_NONE`、`SoundEffectEvent`、`Storyboard.sound_effect_events()` / `sound_effect_names()`；`counts()` 的音效数改为事件数；模块 docstring 写清 narration 与 subtitle 的分工 | 「音效事件」必须能从分镜机械推导，不能再有硬编码列表 |
| `backend/app/media/timeline.py` | 新增 `SUBTITLE_SOURCES=("subtitle","narration","none")` 与 `subtitle_events(source=...)`；新增 `audio_consistency()`（8 个问题码） | 把字段语义变成契约；把「四个数字对不上」变成构建期错误 |
| `backend/app/media/manifest.py` | `file` 列改为 job 相对路径（`input/approved_sources/xxx.jpg`），只有当文件不在 job 内才退回原文件名 | 评审要能直接打开这一行指向的文件，不该靠猜目录 |
| `backend/app/media/__init__.py` | 导出 `SOUND_EFFECT_NONE`、`SUBTITLE_SOURCES`、`SoundEffectEvent`、`audio_consistency` | 统一出口 |
| `content/jobs/phase2_media_engine_demo/script/build_phase2.py` | 署名卡重写；`sfx_schedule()` 从分镜派生；音效生成增益重校准；`--stage review` 新增；`stage_qc` 加一致性检查；`stage_evidence` 行数与路径修正 | 本轮的四个问题都落在这里 |
| `content/jobs/phase2_media_engine_demo/script/verify_closure.py`（新增） | 只读复算分镜 / 素材哈希 / 清单 / 时间轴 / 音频口径，输出 `qc/verification.json` | 让评审不必相信散文，直接看机器复算结果 |

数据层改动：`storyboard/storyboard.csv`（`room tone` → `none`、音效名蛇形化、`sh07` 时长 8.0 s、
字幕留空）。

## 3. 测试改动

| 文件 | 新增 | 覆盖 |
| --- | --- | --- |
| `tests/test_media_timeline.py` | +12 例 | 7 条字幕语义（narration/subtitle 各自非空、两者为空、内容不同、`none` 开关、非法来源）+ 5 条音频一致性（正例 + 缺文件 / 未进时间轴 / 落点错 / 孤儿文件 + 缺配乐） |
| `tests/test_media_storyboard.py` | +2 例 | `sound_effect_events()` 由行派生；`none` 表示「没有音效」而不是名叫 none 的音效 |
| `tests/test_assets_manifest.py` | 改 1 条断言 | `file` 列现在是 job 相对路径（`input/approved_sources/earth.jpg`） |

**没有为了让测试通过而修改任何已有断言**——除了上面这条：它断言的是被我有意改变的输出格式，
属于契约变更而非掩盖失败。

## 4. 全量测试

```
$ .venv\Scripts\python.exe -X utf8 -m pytest -o addopts=""
368 passed, 1 skipped, 1 warning in 71.40s
```

- 上一轮基线 354 passed → 本轮 368 passed（+14）。
- 跳过 1 条：Windows 无符号链接权限（既有，非本轮）。
- 唯一 warning 仍是既有的 FK 环 SAWarning（`agent_runs ↔ research_subtasks`，已作为非阻断风险记录）。
- 分模块：`tests/test_media_storyboard.py tests/test_media_timeline.py tests/test_assets_manifest.py tests/test_media_engine.py` → 69 passed。

## 5. 构建链实跑（本轮真实执行过的命令）

```powershell
# 1) 分镜 → 配乐/音效 → 三块画板 → 素材导入 → 时间轴（含一致性门禁）
build_phase2.py --stage audio           # synthesize_score 42.5 s, credits_used: 0
build_phase2.py --stage compositions    # C_CHART / C_DIAGRAM / C_OUTRO(240 帧)
build_phase2.py --stage import          # S01 S02 S03A S03B + S04 audio
build_phase2.py --stage timeline        # consistency ok=True, revision 4737bce9b992
# 2) 渲染 → 质检 → 复核材料 → 清单与关键帧
build_phase2.py --stage render          # → output/preview_30_45s.mp4
build_phase2.py --stage qc              # 17/17 checks passed
build_phase2.py --stage review          # 18 帧 + contact_sheet.png + 6 个音效窗口
build_phase2.py --stage evidence        # manifest 16 行 + 7 张关键帧 + timeline 快照
# 3) 只读结构核验
verify_closure.py                       # VERDICT: PASS
```

时间轴保存时的门禁输出：

```
audio consistency -> events 6, files 6, music 2, tracks 8, ok=True
validate_timeline -> {"valid": true, "frames": 1275, "warnings": []}
save_timeline revision=4737bce9b992
```

## 6. 最终成片参数

| 项 | 值 |
| --- | --- |
| 文件 | `content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4` |
| 大小 | 20,097,925 B |
| SHA-256 | `0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9` |
| 时长 / 帧数 | 42.500 s / 1275 帧 |
| 画面 | 1080×1920，30/1，H.264 |
| 声音 | AAC 48 kHz 2ch，42.500 s（与画面等长） |
| 电平 | 平均 −24.5 dBFS，峰值 −6.3 dBFS，集成 −22.27 LUFS，TP −6.27 dBTP，LRA 5.60 LU |
| 质检 | 17/17 通过；黑帧 0 段、冻结 0 段、静音 0 段、无削波 |

## 7. 仍未关闭的问题

1. **对外发布的授权复核未做**（最重要）。NASA SVS 三个页面的当页条款、NOAA/JPSS 数据条款、
   画面内是否含第三方内容，本轮都**没有联网核对**，已在署名报告里逐条标为「待核实」。
   内部评审不受影响，对外发布前必须人工确认。
2. **真实视频来源只有 1 个**。`S03A`/`S03B` 是同一个 NASA 文件的 0–6 s 与 12–17 s 两个入点，
   指标「≥2 个真实视频片段」记为**部分完成**，没有伪造第二来源。
3. **没有配音**。`narration` 7 条文本完整保留，但未调用任何 TTS、也没有真人录音，
   不把字幕当配音。
4. **响度偏低**（−22.27 LUFS，比流媒体目标低约 8 LU）。属已知选择，本轮按任务书要求
   **未擅自做 loudnorm**；需批准后另开一轮。
5. **三处视觉不足**：两个镜头入镜前 0.5 s 偏空、亮底白字幕对比度偏弱、结尾卡信息密度高。
   都是创作取舍，已写进视觉报告待人工决定。
6. **本轮未 commit / 未 push**（任务书第 13 条优先于 `AGENTS.md` 的每轮推送约定），
   42 项改动留在工作区，需下一轮授权后同步。

## 8. 本轮新增 / 更新的交付文档

`docs/research_hub/` 下：

| 文件 | 内容 |
| --- | --- |
| `phase2_closure_remediation_report.md` | 本文件 |
| `phase2_credit_verification_report.md` | 逐条素材授权与署名核验 + 三项待核实 |
| `phase2_storyboard_semantics_report.md` | narration / subtitle 契约与测试 |
| `phase2_audio_consistency_report.md` | 四个音频口径、room tone 决策、一致性门禁 |
| `phase2_visual_review_report.md` | 18 帧复核、转场亮度、音效可闻度、响度说明 |
| `phase2_final_qc_report.md` | 最终成片 17 项自动检查与指标对照 |
| `phase2_final_decision_register.md` | D-01–D-11 决策与残留风险 |

机读产物（`content/jobs/phase2_media_engine_demo/`）：`qc/qc_metrics.json`、`qc/qc_report.md`、
`qc/audio_consistency.json`、`qc/review_index.json`、`qc/review_index.md`、`qc/verification.json`、
`qc/review/`（18 帧 + 联系表）、`assets_manifest.csv`、`project/timeline.json`、
`storyboard/storyboard.csv`。

## 9. 结论

| 维度 | 判定 |
| --- | --- |
| 四个问题是否修好 | 全部修好，且有代码 + 测试 + 产物三层证据 |
| 是否引入新缺陷 | 无。反向验证：全量测试 368 passed；结构复算 PASS；质检 17/17 |
| 是否可交人工验收 | 可以，材料齐全（成片 / 抽帧 / 联系表 / 清单 / 时间轴 / 质检 / 授权） |
| 是否可以对外发布 | **不可以**。授权复核三项待核实、无配音、响度未归一 |

最终结论见下一次汇报的 M 节：**PARTIAL PASS**——工程与验证部分通过，发布条件未满足的部分已如实列出。
