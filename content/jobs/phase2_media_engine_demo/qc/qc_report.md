# Phase 2 质检报告

- 生成时间：2026-11-11T03:51:43+00:00
- 文件：`output\preview_30_45s.mp4`（19,868,025 B）
- 时长：40.000 秒
- 画面：1080×1920 / 30/1 / h264
- 声音：aac / 48000 Hz / 2 ch / 40.000 秒
- 响度：mean -25.4 dB，max -13.5 dB，integrated -23.63 LUFS

## 逐项检查

| 检查 | 结果 | 实测 |
| --- | --- | --- |
| canvas | 通过 | 1080x1920 (expected 1080x1920) |
| frame_rate | 通过 | 30/1 (expected 30/1) |
| video_codec | 通过 | h264 |
| audio_codec | 通过 | aac |
| duration_matches_storyboard | 通过 | 40.000s vs storyboard 40.000s |
| av_equal_length | 通过 | video 40.000s / audio 40.000s |
| no_black_over_0.45s | 通过 | 0 segment(s), longest 0.00s |
| no_freeze_over_1.0s | 通过 | 0 segment(s), longest 0.00s |
| no_silence_over_1.5s | 通过 | 0 segment(s), longest 0.00s |
| no_clipping | 通过 | max_volume -13.5 dB |
| audible | 通过 | mean_volume -25.4 dB |
| shot_count | 通过 | 7 shots (need >= 6) |
| real_media | 通过 | real_video 2, real_image 2 |
| transition_variety | 通过 | 6 transition types |
| camera_variety | 通过 | 5 camera motions |
| multi_track_audio | 通过 | 8 audio tracks in the timeline |

## 分镜事实（来自 storyboard.csv）

- 镜头 7 个；真实视频 2、真实图片 2、动态合成 3
- 字幕 7 条；转场 6 种；运镜 5 种

## 黑帧 / 冻结 / 静音明细

- 黑帧段：无
- 冻结段：无
- 静音段：无

> Programmatic checks only. Whether the film is good is not measurable here.
