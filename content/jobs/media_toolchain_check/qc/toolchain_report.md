# 音视频工具链自检报告（V2.2 Phase 1）

- 生成时间：2026-11-10T18:41:21+00:00
- 工具链来源：`config`
- ffmpeg：`C:\Users\25803\finance-media-ai\tools\ffmpeg\bin\ffmpeg.exe`
- ffprobe：`C:\Users\25803\finance-media-ai\tools\ffmpeg\bin\ffprobe.exe`
- 版本：`8.1.2`（下限 5.1）
- 总体结果：**通过**

## 逐项结果

| 项 | 结果 | 说明 |
| --- | --- | --- |
| `h264_aac_encode` 编码 H.264(libx264) + AAC 竖屏 1080×1920 | ✅ | 3.00s video=h264 audio=aac |
| `h264_decode` 完整解码 H.264 视频流 | ✅ | 解码 90 帧 |
| `trim` 视频裁剪 0.5s→1.5s | ✅ | 裁剪后 1.00s |
| `fps_convert` 帧率转换 30fps → 24fps | ✅ | avg_frame_rate=24/1 |
| `concat` 视频拼接（concat demuxer，两段参数一致） | ✅ | 拼接后 4.02s（期望 4.00s） |
| `audio_mix` 音频混合（amix 440Hz + 660Hz → AAC） | ✅ | 2.50s aac |
| `mux_av` 音视频合片（-map 视频 + 混合音轨） | ✅ | 2.52s 含音视频双流 |
| `subtitle_burn` libass 字幕烧录（.ass + 微软雅黑 + 安全区内） | ✅ | 烧字幕成功 2.52s（字体目录 work/fonts） |
| `loudness` 响度测量（loudnorm 两遍法第一遍 print_format=json） | ✅ | integrated=-18.75 LUFS, true peak=-12.09 dBTP |
| `volume_stats` 音量统计（volumedetect） | ✅ | mean=-18.1 dB, max=-12.1 dB |
| `qc_filters` 质检滤镜（blackdetect / freezedetect / silencedetect） | ✅ | black=0 freeze=0 silence=0 |
| `metadata_read` 元数据读取（ffprobe JSON） | ✅ | streams=2 |

## 产物

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| `work/clip_a.mp4` | 2,220,473 B | `4a544eab10d16a26…` |
| `work/clip_trim.mp4` | 597,200 B | `6a70df19d08fa05a…` |
| `work/clip_24fps.mp4` | 1,583,739 B | `489b75c7be2b48dc…` |
| `work/clip_joined.mp4` | 1,943,582 B | `f9db2cfcc3e868e4…` |
| `audio/mix.m4a` | 47,535 B | `1a659bc562e93f1f…` |
| `output/toolchain_check.mp4` | 1,273,866 B | `162c965112340191…` |
| `output/toolchain_check_subs.mp4` | 1,034,029 B | `849f4ad5ea6af19e…` |

## 结论边界

- 本自检证明的是「工具链能力」，不是「真实素材剪辑工作流」。
- 素材来源为 lavfi 合成画面与正弦音，不含任何真实拍摄素材或历史数据。
- 响度与音量数字只说明测量链路可用，不代表正式作品的混音标准。
