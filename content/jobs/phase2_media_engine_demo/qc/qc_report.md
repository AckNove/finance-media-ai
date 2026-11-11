# Phase 2 质检报告

- 生成时间：2026-11-11T09:02:28+00:00
- 文件：`output\preview_30_45s.mp4`（20,097,925 B）
- 时长：42.500 秒
- 画面：1080×1920 / 30/1 / h264
- 声音：aac / 48000 Hz / 2 ch / 42.500 秒
- 响度：mean -24.5 dB，max -6.3 dB，integrated -22.27 LUFS

## 逐项检查

| 检查 | 结果 | 实测 |
| --- | --- | --- |
| canvas | 通过 | 1080x1920 (expected 1080x1920) |
| frame_rate | 通过 | 30/1 (expected 30/1) |
| video_codec | 通过 | h264 |
| audio_codec | 通过 | aac |
| duration_matches_storyboard | 通过 | 42.500s vs storyboard 42.500s |
| av_equal_length | 通过 | video 42.500s / audio 42.500s |
| no_black_over_0.45s | 通过 | 0 segment(s), longest 0.00s |
| no_freeze_over_1.0s | 通过 | 0 segment(s), longest 0.00s |
| no_silence_over_1.5s | 通过 | 0 segment(s), longest 0.00s |
| no_clipping | 通过 | max_volume -6.3 dB |
| audible | 通过 | mean_volume -24.5 dB |
| shot_count | 通过 | 7 shots (need >= 6) |
| real_media | 通过 | real_video 2, real_image 2 |
| transition_variety | 通过 | 6 transition types |
| camera_variety | 通过 | 5 camera motions |
| multi_track_audio | 通过 | 8 audio tracks in the timeline |
| audio_counts_consistent | 通过 | 6 处音效事件 / 6 个音效文件 / 2 条配乐 / 8 条总音轨 |

## 分镜事实（来自 storyboard.csv）

- 镜头 7 个；真实视频 2、真实图片 2、动态合成 3
- 字幕 6 条；转场 6 种；运镜 5 种

## 音频计数口径（四个数字分开记）

| 口径 | 数量 | 含义 |
| --- | --- | --- |
| 音效事件 | 6 | storyboard 里 `sound_effect` 非 `none` 的镜头数 |
| 独立音效文件 | 6 | 实际用 ffmpeg 合成并导入的 音效文件数 |
| 配乐轨 | 2 | 贯穿全片的配乐层（原创合成 + Apache-2.0 授权曲） |
| 时间轴总音轨 | 8 | 时间轴 audio.tracks 的条数 = 配乐 + 音效 |

- 一致性：通过（口径不同的 6 个独立音效名，详见 `qc/audio_consistency.json`）

## 黑帧 / 冻结 / 静音明细

- 黑帧段：无
- 冻结段：无
- 静音段：无

> Programmatic checks only. Whether the film is good is not measurable here.
