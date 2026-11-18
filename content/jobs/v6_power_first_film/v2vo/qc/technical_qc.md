# 技术 QC：final.mp4

> 这份只回答「片子技术上有没有问题」，**不判断好不好看**（观感见 `scene_quality_review.md`）。
> 数字全部由 `python -m backend.scripts.cinema_qc` 从成片文件里量出来，命令可复跑。

## 1. 容器事实

| 项 | 值 |
|---|---|
| 文件 | `content/jobs/v6_power_first_film/v2vo/output/final.mp4` |
| 大小 | 77,788,976 字节（74.19 MB） |
| 时长 | 251.0 秒 |
| 画面 | h264 / 1080×1920 / 30.0 fps / yuv420p |
| 声音 | aac / 48000 Hz / 2 声道 |

## 2. 黑帧 / 冻结 / 静音 / 响度

| 指标 | 实测 | 判据 |
|---|---|---|
| 最长黑帧 | 0.0 秒（0 段） | ≤ 0.5 秒 |
| 最长冻结 | 0.0 秒（0 段） | ≤ 1.5 秒 |
| 最长静音 | 0.0 秒（0 段） | ≤ 2.0 秒 |
| 平均电平 | -19.0 dB | 参考 |
| 峰值 | -4.1 dBFS | ≤ -1 dBFS |
| 整体响度 | -16.2 LUFS | -16.0 ± 3.0 |

## 3. 字幕

| 项 | 值 |
|---|---|
| 字幕条数 | 67 |
| 第一条 | (0.0, 1.36) |
| 最后一条 | (248.099, 250.799) |
| 时间重叠 | 0 处 |

## 4. 逐项判定

| 检查项 | 结果 |
|---|---|
| resolution_1080x1920 | 通过 |
| fps_30 | 通过 |
| codec_h264 | 通过 |
| pixel_format_yuv420p | 通过 |
| audio_aac | 通过 |
| audio_48k_stereo | 通过 |
| duration_covers_blueprint | 通过 |
| no_long_black | 通过 |
| no_long_freeze | 通过 |
| no_long_silence | 通过 |
| loudness_near_target | 通过 |
| subtitles_do_not_overlap | 通过 |
| subtitles_cover_whole_film | 通过 |

**结论：全部通过**

## 5. 关键帧

每镜抽了「进入 / 中段 / 收尾」三帧，落在 `qc/keyframes/`，文件名 `s<镜号>_<in|mid|out>.png`，方便逐镜看构图。
