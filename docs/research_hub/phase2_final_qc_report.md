# Phase 2 最终质检报告（收尾整改后）

> 本文件是对**最终成片**的一次完整复跑。生成它的命令：
> `.venv\Scripts\python.exe -X utf8 content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage qc`
> 原始产物：`content/jobs/phase2_media_engine_demo/qc/qc_report.md` 与 `qc_metrics.json`（本文件是给评审看的汇总版）。

## 1. 最终成片

| 项 | 值 |
| --- | --- |
| 路径 | `content/jobs/phase2_media_engine_demo/output/preview_30_45s.mp4` |
| 字节数 | 20,097,925 B |
| **SHA-256** | `0aa4dde0e483a09566d48c4b04c32ff7cac2fae7e71bfd24db73cc2069909ea9` |
| 时长 | 42.500 s（1275 帧 @ 30 fps） |
| 画面 | 1080×1920 / 30/1 / H.264 |
| 声音 | AAC / 48 kHz / 2 ch / 42.500 s |
| 时间轴 revision | `4737bce9b992…`（由 `--stage timeline` 输出，绑定本次渲染的 request_id `phase2-4737bce9`） |

与上一版（40.0 s、sha256 `b64562e6…`）的差异：结尾卡加长 2.5 s 以容纳逐条署名、
`sh07` 去掉重复字幕、六个音效重新平衡（其中 `whoosh_tail` 由静音修成真实音效）。

## 2. 十七项自动检查：全部通过

| 检查 | 结果 | 实测 |
| --- | --- | --- |
| canvas | 通过 | 1080×1920（期望 1080×1920） |
| frame_rate | 通过 | 30/1 |
| video_codec | 通过 | h264 |
| audio_codec | 通过 | aac |
| duration_matches_storyboard | 通过 | 42.500 s vs 分镜 42.500 s |
| av_equal_length | 通过 | 视频 42.500 s / 音频 42.500 s |
| no_black_over_0.45s | 通过 | 0 段，最长 0.00 s |
| no_freeze_over_1.0s | 通过 | 0 段，最长 0.00 s |
| no_silence_over_1.5s | 通过 | 0 段，最长 0.00 s |
| no_clipping | 通过 | 峰值 −6.3 dBFS |
| audible | 通过 | 平均 −24.5 dBFS |
| shot_count | 通过 | 7 个镜头（要求 ≥6） |
| real_media | 通过 | 真实视频 2 个镜头 / 真实图片 2 个镜头 |
| transition_variety | 通过 | 6 种转场 |
| camera_variety | 通过 | 5 种运镜 |
| multi_track_audio | 通过 | 时间轴 8 条音轨 |
| **audio_counts_consistent**（本轮新增） | 通过 | 6 音效事件 / 6 音效文件 / 2 配乐 / 8 总音轨 |

## 3. 响度与电平

| 指标 | 值 |
| --- | --- |
| 集成响度 | −22.27 LUFS |
| 真峰值 | −6.27 dBTP |
| 响度范围（LRA） | 5.60 LU |
| 平均电平 | −24.5 dBFS |
| 峰值电平 | −6.3 dBFS |

**未做响度归一**（任务书禁止擅自改动音频标准）。当前比 −14 LUFS 的流媒体目标低约 8 LU，
播放器可能需要调高音量；动态范围稳定（LRA 5.6 LU），不存在忽大忽小。详见
`phase2_visual_review_report.md` §6.3。

## 4. 结构核验（只读复算，独立于质检脚本）

`content\jobs\phase2_media_engine_demo\script\verify_closure.py` 重算分镜、素材哈希、
清单、时间轴与音频口径，输出 `qc/verification.json` / `qc/verification.md`：

```
- storyboard: 通过
- sources: 通过
- manifest: 通过
- timeline: 通过
- audio: 通过

- 分镜：42.5 s / 1275 帧 / 7 镜
- 素材：4 条，sha256 全部匹配 = True
- 清单：16 行，分类 {'A': 5, 'B': 10, 'C': 1}
- 时间轴：1275 帧 / 7 镜 / 6 个转场 / 6 条字幕 / 8 条音轨
- 音频口径：6 事件 / 6 文件 / 2 配乐 / 8 总音轨

VERDICT: PASS
```

## 5. 指标对照（任务 6 的硬要求）

| 要求 | 要求值 | 实际 | 判定 |
| --- | --- | --- | --- |
| 镜头数 | ≥6 | 7 | 通过 |
| 真实视频片段 | ≥2 | **1 个独立来源、2 个镜头入点** | **部分完成**（同一 NASA 文件的两个入点，未伪装成两个来源） |
| 真实图片 | ≥2 | 2（阿波罗17号、蓝色弹珠） | 通过 |
| 动态数据图表 | ≥1 | 1（sh04，五柱生长 + 数值浮现） | 通过 |
| 动态结构图 | ≥1 | 1（sh05，节点落位 + 连线生长） | 通过 |
| 字幕段落 | ≥1 | 6 条（`sh01`–`sh06`） | 通过 |
| 音轨数 | ≥2 | 8（2 配乐 + 6 音效） | 通过 |
| 转场种类 | ≥3 | 6 | 通过 |
| 运镜种类 | ≥2 | 5 | 通过 |
| 时长区间 | 30–45 s | 42.5 s | 通过 |
| 竖屏 9:16 | 必须 | 1080×1920 | 通过 |

## 6. 与上一版质检的差异

| 项 | 上一版 | 本版 | 原因 |
| --- | --- | --- | --- |
| 检查项数 | 16 | **17** | 新增 `audio_counts_consistent` |
| 时长 | 40.000 s | 42.500 s | 结尾卡放逐条署名 |
| 平均电平 | −25.5 dBFS | −24.5 dBFS | 六个音效重新平衡后整体略升 |
| 峰值 | −13.5 dBFS | −6.3 dBFS | 同上（`whoosh_low`、`whoosh_tail` 有宽频噪声峰） |
| 集成响度 | −23.62 LUFS | −22.27 LUFS | 同上 |
| 音效可闻度 | 4/6 被配乐掩盖，1 个静音文件 | 6/6 可闻且不刺耳 | 见 `phase2_visual_review_report.md` §6 |

## 7. 这份报告不能说明什么

- 不能说明「片子好看」。所有结论都是可测量的技术事实。
- 不能说明「字幕位置在真机上舒服」——那是人看片的事。
- 不能说明「音效风格合适」——分窗电平只证明它可闻，不证明它合适。
- 不能替代完整播放验收。请播放 42.5 秒全片后再判断。
