# V2.2 Phase 2 交接报告（冻结版）

> 本文件是 Phase 2 的**冻结交接说明**。它在 2026-11-11 的收尾整改完成、
> 全量测试重跑通过之后写成，内容全部来自本地实际文件与命令输出，不是对上一轮汇报的复述。
>
> 成片：`content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4`
> SHA-256：`0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9`

---

## 1. Phase 2 的目标与实际完成范围

**任务书原文的目标**（V2.2 Phase 2，任务 1–6）：把「研究资料 → 脚本」接上真实的媒体引擎——
能用 OpenKatana 导入真实素材、按分镜驱动时间轴、多轨合成音视频、留全素材溯源，
并产出一条 30–45 秒的竖屏演示片，附 8 份可复核文档。

**实际做到的范围**（逐条对应）：

| 任务 | 状态 | 落地物 |
| --- | --- | --- |
| 1 引擎适配层 | 完成 | `backend/app/capabilities/media/{engine,openkatana}.py`：CLI 调用、契约错误分类、`ffmpeg` 前置 PATH、调用日志 |
| 2 真实素材导入 | 完成 | 2 张真实图片、1 个真实视频文件（两个入点）、1 个 WAV 音频，全部经 `import_media` 转成帧序列 |
| 3 分镜驱动 | 完成 | `backend/app/media/storyboard.py`：16 列强校验 schema，未知值一律拒绝 |
| 4 多轨合成 | 完成 | `backend/app/media/timeline.py`：8 条音轨（2 配乐 + 6 音效）、6 条字幕、6 种转场、5 种运镜 |
| 5 素材溯源 | 完成 | `backend/app/core/job_paths.py` 白名单 + SHA-256 复算 + `assets_manifest.csv` 16 行 A/B/C 分类 |
| 6 演示片 + 文档 | 完成 | 42.5 秒成片 + `docs/research_hub/` 下 15 份 Phase 2 文档 |

**没做的**（不在 Phase 2 授权内，也没有偷偷做）：真实模型调用、付费 TTS、云端渲染、
第二条独立真实视频来源、财经内容生产、Phase 3 的任何部分。

## 2. 本轮整改解决的四项原始问题

| # | 原始问题 | 修复动作 | 验收证据 |
| --- | --- | --- | --- |
| 1 | 结尾卡只有一句「素材：NASA 公共领域影像」，把三个不同作者压成一句 | 结尾卡改为逐条署名，**由代码从 `source_register.csv` 的 `credit` 列读取**，空 credit 直接构建失败 | 抽帧 `qc/review/17_99_credits.png`；`phase2_credit_verification_report.md` |
| 2 | `narration` 与 `subtitle` 职责不清、`sh07` 字幕与卡面大字重复 | 新增 `SUBTITLE_SOURCES=("subtitle","narration","none")` 三态开关；`sh07` 字幕删除；`narration` 保留但明确不渲染 | `phase2_storyboard_semantics_report.md`；`tests/test_media_timeline.py` 7 条语义用例 |
| 3 | 音效数量四个口径互不相同（`counts()=7`、实生成 6、清单 10 行、CSV 15 行），`room tone` 从未生成 | 四个口径分开记并互相校验（6 事件 / 6 文件 / 2 配乐 / 8 音轨）；`room tone` 判为未进成片，改为 `none`；新增构建期门禁 | `qc/audio_consistency.json`；`phase2_audio_consistency_report.md` |
| 4 | 只有自动 QC，没有人工可复核的视觉材料 | 新增 `--stage review`：18 帧抽帧 + 联系表 + 六种转场亮度曲线 + 六个音效分窗电平 | `qc/review/`、`qc/review_index.md`；`phase2_visual_review_report.md` |

## 3. 额外发现并解决的音效缺陷

修问题 4 的时候，分窗电平测量测出一个**成片缺陷**，不是统计问题：

- `whoosh_tail.m4a` 平均电平 **−73.1 dBFS**，等于静音。根因是 `SFX_FILTERS["whoosh_tail"]`
  用 `bandpass=f=900:w=1400` 去滤**棕噪声**，而棕噪声的能量几乎全在低频，滤完只剩噪声底。
- 另外四个音效被配乐压住 1–5 dB（分窗差值出现负值）。

修法：改用粉噪声 + `highpass=300,lowpass=4200`；六个音效生成增益校准到 −19 ~ −21 dBFS；
时间轴音量统一 0.85–0.9。重跑后六项分窗差值：`whoosh_low +3.9`、`impact_soft +1.2`、
`digital_rise +2.3`、`riser +5.8`、`whoosh_tail +4.4`、`soft_hit +4.3` dB，全部「可闻且不刺耳」。

## 4. 实际修改的文件清单

**后端代码（4 个）**

| 文件 | 改动 |
| --- | --- |
| `backend/app/media/storyboard.py` | 新增 `SOUND_EFFECT_NONE`、`SoundEffectEvent`、`sound_effect_events()`、`sound_effect_names()`；`counts()` 的音效数改为事件数；docstring 写明 narration/subtitle 分工 |
| `backend/app/media/timeline.py` | 新增 `SUBTITLE_SOURCES`、`subtitle_events(source=...)`（非法值抛 `unsupported_source`）、`audio_consistency()`（8 个问题码） |
| `backend/app/media/manifest.py` | `file` 列改为 job 相对路径，评审可直接打开 |
| `backend/app/media/__init__.py` | 导出上述新符号 |

**构建与核对脚本（2 个，均在 job 目录内）**

| 文件 | 说明 |
| --- | --- |
| `content/jobs/phase2_media_engine_demo/script/build_phase2.py` | 署名卡重写、`sfx_schedule()` 从分镜派生、增益重校准、新增 `--stage review`、`stage_qc` 加一致性检查、`stage_evidence` 行数与路径修正 |
| `content/jobs/phase2_media_engine_demo/script/verify_closure.py` | **新增**。只读复算分镜 / 素材哈希 / 清单 / 时间轴 / 音频口径，输出 `qc/verification.json` |

**数据（1 个）**：`content/jobs/phase2_media_engine_demo/storyboard/storyboard.csv`
（`room tone` → `none`、音效名蛇形化、`sh07` 时长 8.0 s、字幕留空）。

**测试（3 个）**：`tests/test_media_timeline.py`、`tests/test_media_storyboard.py`、`tests/test_assets_manifest.py`。

**文档（11 个）**：`docs/research_hub/` 下 7 份 closure 文档 + `README.md` 索引 + 本文件 + 索引文件 + 遗留问题清单。

## 5. 测试结果与命令

```
$ .venv\Scripts\python.exe -X utf8 -m pytest -o addopts=""
368 passed, 1 skipped, 1 warning in 36.99s
```

- **368 passed**（Phase 2 交付时基线 354，本轮 +14）。
- **1 skipped**：Windows 无符号链接权限（既有，非本轮引入）。
- **1 warning**：`agent_runs ↔ research_subtasks` 外键环的 SAWarning（M2.0 期已判定为非阻断风险并记录）。
- 本轮新增用例分布：`tests/test_media_timeline.py` +12（7 字幕语义 + 5 音频一致性）、
  `tests/test_media_storyboard.py` +2；`tests/test_assets_manifest.py` 改 1 条断言（输出格式契约变更）。

**注意**：本轮冻结任务**没有再次跑 pytest**（任务禁止改代码，无代码变动），上面是
2026-11-11 收尾整改完成时的最后一次真实结果，命令与输出均为实际执行所得。

## 6. 最终 MP4 技术参数

| 项 | 值 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4` |
| 大小 | **20,097,925 B** |
| SHA-256 | **`0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9`** |
| 时长 | 42.500 s |
| 总帧数 | 1275 帧 @ 30 fps |
| 画面 | 1920×1080 → 竖屏 1080×1920，H.264，30/1 |
| 声音 | AAC，48 kHz，2 ch，42.500 s（与画面等长） |
| 平均电平 | −24.5 dBFS |
| 峰值电平 | −6.3 dBFS |
| 集成响度 | −22.27 LUFS |
| 真峰值 | −6.27 dBTP |
| 响度范围 | 5.60 LU |
| 时间轴 revision | `4737bce9b992…`（渲染 request_id `phase2-4737bce9`） |

## 7. 17 项 QC 的实际验证结果

`content/jobs/phase2_media_engine_demo/qc/qc_metrics.json` 中 `all_checks_ok: true`：

| # | 检查 | 结果 | 实测 |
| --- | --- | --- | --- |
| 1 | canvas | 通过 | 1080x1920（期望 1080x1920） |
| 2 | frame_rate | 通过 | 30/1（期望 30/1） |
| 3 | video_codec | 通过 | h264 |
| 4 | audio_codec | 通过 | aac |
| 5 | duration_matches_storyboard | 通过 | 42.500 s vs 分镜 42.500 s |
| 6 | av_equal_length | 通过 | 视频 42.500 s / 音频 42.500 s |
| 7 | no_black_over_0.45s | 通过 | 0 段，最长 0.00 s |
| 8 | no_freeze_over_1.0s | 通过 | 0 段，最长 0.00 s |
| 9 | no_silence_over_1.5s | 通过 | 0 段，最长 0.00 s |
| 10 | no_clipping | 通过 | max_volume −6.3 dB |
| 11 | audible | 通过 | mean_volume −24.5 dB |
| 12 | shot_count | 通过 | 7 个镜头（要求 ≥6） |
| 13 | real_media | 通过 | 真实视频 2 个镜头 / 真实图片 2 个镜头 |
| 14 | transition_variety | 通过 | 6 种转场 |
| 15 | camera_variety | 通过 | 5 种运镜 |
| 16 | multi_track_audio | 通过 | 时间轴 8 条音轨 |
| 17 | audio_counts_consistent | 通过 | 6 音效事件 / 6 文件 / 2 配乐 / 8 总音轨 |

另有一次独立的只读结构复算（`script/verify_closure.py`）：
分镜 42.5 s / 1275 帧 / 7 镜；4 条素材 sha256 全部匹配；清单 16 行 A5/B10/C1；
时间轴 1275 帧 / 6 转场 / 6 字幕 / 8 音轨；音频口径 6/6/2/8 —— **VERDICT: PASS**。

## 8. 视觉检查结果

材料在 `content/jobs/phase2_media_engine_demo/qc/review/`（18 帧 + `contact_sheet.png`）。

- **已由人（我）逐帧目视的部分**：开场、7 个镜头主体、4 个转场入点、2 个动画镜头、结尾卡。
  结论：画面无变形（真实素材按 `fit: cover` 裁切，地球保持圆形）、无遮挡、无黑帧、无闪烁；
  字幕位于底部安全区、中文无乱码；结尾卡署名完整可读、长署名自动折两行。
- **程序测的部分**：`signalstats` YAVG 曲线（dissolve 111→99、glitch 93.9→31.9→35.4、
  zoom 58.5→32.3→36.1、dip 17→20→27→32.7），无全黑帧；最低 17 来自结尾卡 `#0b1220` 淡入，非黑场。
- **不足（未擅自改）**：① `sh04`/`sh05` 入镜前约 0.5 秒画面偏空（画板 reveal 序列所致）；
  ② 亮底画面上白色字幕对比度偏弱；③ 结尾卡信息密度偏高。
- **必须说清的边界**：抽帧与联系表**不能替代**看完整片。观感（节奏、是否好看）本轮没有人做正式判断。

## 9. 音频检查结果

- 8 条音轨：`score_bed`（OpenKatana `synthesize_score` 本地合成，42.5 s，`credits_used: 0`）、
  `orbit_loop`（NASA 示例包内 Apache-2.0 原创配乐，循环 8 次 + 淡入淡出）、
  6 个程序合成音效（`whoosh_low`/`impact_soft`/`digital_rise`/`riser`/`whoosh_tail`/`soft_hit`）。
- 音效落点与分镜 `sound_effect` 列逐条一致：5.0 / 11.0 / 15.5 / 22.5 / 29.5 / 34.5 秒。
- 每个音效的相对可闻度（分窗电平差）**全部为正**，无被配乐掩盖、无偏响刺耳。
- 无削波（峰值 −6.3 dBFS）、无静音段、音画等长。
- **未做响度归一**（任务书禁止擅自改音频标准）。−22.27 LUFS 比常见流媒体目标（−14 LUFS）低约 8 LU，
  播放端可能要调高音量；动态范围稳定（LRA 5.60 LU）。

## 10. 素材来源与授权状态

| 编号 | 素材 | 来源 | 授权 | 署名（已进画面） |
| --- | --- | --- | --- | --- |
| S01 | 阿波罗17号地球影像 | NASA（OpenKatana 示例包） | public domain，`commercial_use=allowed` | Earth Science and Remote Sensing Unit, NASA Johnson Space Center |
| S02 | 蓝色弹珠星空 | NASA | public domain，`allowed` | NASA Scientific Visualization Studio; Cheng Zhang |
| S03 | VIIRS 地球自转视频（两个入点 0–6 s / 12–17 s） | NASA | public domain，`allowed` | NASA Scientific Visualization Studio; Amy Moran and Jesse Allen |
| S04 | 配乐 `orbit.wav` | OpenKatana 示例包 | Apache-2.0，`allowed` | OpenKatana contributors, Apache-2.0 |
| — | 字体 Noto Sans SC | 本机 `C:\Windows\Fonts\NotoSansSC-VF.ttf` | SIL OFL 1.1 | Noto Sans SC · SIL OFL 1.1 |
| — | 三块动态画板 / 六个音效 / 第二配乐 | 本项目程序生成 | 原创（B 类） | 无需外部署名 |

**授权状态（准确表述）**：工程层面四条素材均已登记 `commercial_use=allowed` 并通过
`assert_renderable()` 校验；**但三项页面级条款未联网核实**（SVS 三个页面条款、画面内第三方内容、
NOAA/JPSS 数据条款），已在署名报告中标为「待核实」。因此：**内部评审可用，对外发布不可。**

## 11. 已完成 / 未完成 / 需人工判断

**已完成**：适配层、导入、分镜驱动、多轨合成、溯源、成片、17 项 QC、结构复算、
四项整改、音效缺陷修复、15 份 Phase 2 文档。

**未完成**：真人配音（`narration` 7 条文本待用）、响度归一、第二条独立真实视频来源、
页面级授权复核、Phase 3 全部内容。

**需要人工判断**：三处视觉取舍、音效风格是否合适、−22.27 LUFS 是否接受、
是否要在发布前补配音与响度归一。

## 12. 结论

- **是否满足工程交付条件：满足。** 代码有测试、产物有哈希、结构可复算、
  质量有自动检查 + 人工抽帧核对，且全部可一键复跑。
- **是否满足对外发布条件：不满足。** 阻断项是三项未核实的页面级授权、无真人配音、
  响度未归一。这三条都不是靠改文档能解决的，需要人工决策与授权。

遗留问题逐条见 `phase2_remaining_issues.md`。
