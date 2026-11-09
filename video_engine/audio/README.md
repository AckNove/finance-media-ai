# audio/ —— 音频规范（第一阶段仅约定）

## 配音

```yaml
voice:
  language: zh-CN
  style: calm_expert          # 冷静、专业、不煽动
  speed: 1.0
  pitch: 0
  sample_rate_hz: 48000
  channels: 1
  format: wav
```

口播节奏基准：中文约 3.5–4.5 字/秒。脚本字数与目标时长不匹配时，优先改脚本而不是变速。

## 混音

| 项目 | 目标 |
| --- | --- |
| 整体响度 | -14 LUFS ±2 |
| 峰值 | ≤ -1 dBTP |
| BGM 增益 | -18 dB（BGM 为可选，未授权不得使用） |
| 人声/背景差 | ≥ 12 dB |

## 素材纪律

- 未经授权的音乐不得使用；`bgm: null` 时保持静音，不要自行填充。
- 配音文件命名 `vo_{seq:03d}.wav`，与 `render_spec.timeline[].layers[].asset` 一致。

## 尚未实现

TTS 合成、降噪、响度归一脚本均未实现；本阶段只定义目标值。
