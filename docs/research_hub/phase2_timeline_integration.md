# Phase 2 · 时间轴集成报告

本文件回答一件事：**分镜表是怎么变成一条真正能渲染的多轨时间轴的**，以及这条路线上被引擎纠正了哪些
地方。产物：`content/jobs/phase2_media_engine_demo/project/timeline.json`（引擎侧的权威副本）。

## 1. 数据流

```
storyboard/storyboard.csv        16 列、7 镜、40.0 s
        │  load_storyboard()            硬校验：缺列/多列/断裂/词表/画布一致
        ▼
Storyboard (frozen)              .counts() → shots 7 / real_video 2 / real_image 2 / composition 3
        │  build_timeline(storyboard, clips, audio=…, fonts=…)
        ▼
timeline dict                    canvas / clips / shots / fonts / text / audio
        │  engine.validate_timeline()   {"valid": true, "frames": 1200, "warnings": []}
        │  engine.save_timeline()       revision 45fa15aade3b
        ▼
start_render → get_render ×215 → output/preview_30_45s.mp4
```

## 2. 时间轴结构（实测，非设计稿）

顶层键只有六个：`canvas / clips / shots / fonts / text / audio`（多一个键引擎就拒绝）。

```json
"canvas": {"w":1080, "h":1920, "fps":30, "frames":1200, "bg":"#0b1220"}
"clips":  S01(1帧) S02(1帧) S03A(180) S03B(150) C_CHART(210) C_DIAGRAM(210) C_OUTRO(165)
"fonts":  [{"family":"AKSC", "url":"fonts/NotoSansSC-VF.ttf"}]
"text":   7 条 caption
"audio":  8 轨（见第 4 节）
```

七个镜头（`fit:"cover"` + `cover:true` 全部显式写上）：

| # | 帧区间 | 秒 | clip | 相机 | 入场转场 |
| --- | --- | --- | --- | --- | --- |
| 1 | 0–150 | 0–5.0 | S01 | `push v=0.03` | — |
| 2 | 150–330 | 5.0–11.0 | S03A | `pan dx=0.05` | `dissolve 0.4` |
| 3 | 330–465 | 11.0–15.5 | S02 | `push v=0.03` | `push / left 0.4` |
| 4 | 465–675 | 15.5–22.5 | C_CHART | `punch v=0.06 d=0.25` | `glitch 0.4` |
| 5 | 675–885 | 22.5–29.5 | C_DIAGRAM | `pan dy=0.05` | `zoom 0.4` |
| 6 | 885–1035 | 29.5–34.5 | S03B | `handheld amp=5 hz=0.6` | `slide / up 0.4` |
| 7 | 1035–1200 | 34.5–40.0 | C_OUTRO | `static` | `dip 0.4` |

覆盖检查：`0..1200` 无空洞、无重叠，`validate_timeline` 返回 `warnings: []`。

## 3. 任务 2 的十项，逐项对应到时间轴

| 要求 | 本片怎么实现 | 证据 |
| --- | --- | --- |
| PNG/JPG 导入 | S01/S02 单帧 plate，镜头层 `loop:"loop"` 铺满 | 第 1、3 镜抽帧 |
| MP4 导入 | S03A/S03B 两个入点，180/150 帧 | 第 2、6 镜抽帧 |
| WAV/AAC 导入 | S04 音频导入后转 `orbit_loop.m4a`；全部音轨是 m4a | `audio/` 目录 9 个文件 |
| 裁剪 | `start/duration` 精确到帧 | 180 = 6×30、150 = 5×30 |
| 缩放适配 | 640×360 源进 1080×1920 画布，`fit:cover` 裁切 | 第 2 镜满屏无黑边 |
| 推拉平移 | `push / pan` 四种组合（S01 push、S03A pan.dx、S02 push+push、S05 pan.dy） | 镜头表 |
| 多素材时间轴 | 7 clip 连续排布 | 全文 |
| 转场 | 6 种：dissolve / push / glitch / zoom / slide / dip | 镜头表 |
| 字幕叠加 | 7 条 caption，`pos:[0.5,0.86]`，不遮挡主体 | 抽帧 |
| 多音轨混合 | 8 轨 + `loudness` 归一 | 第 4、5 节 |

## 4. 音频：8 轨怎么叠而不糊

| 轨 | 源 | 入点 | 电平 | 作用 |
| --- | --- | --- | --- | --- |
| 1 | `score_bed.m4a`（OpenKatana 合成 40 s） | 0.0 | 0.55 | 全片底床 |
| 2 | `orbit_loop.m4a`（Apache-2.0，循环铺满） | 0.0 | 0.35 | 第二层和声 |
| 3 | `whoosh_low.m4a` | 5.0 | 0.6 | 第 2 镜切入 |
| 4 | `impact_soft.m4a` | 11.0 | 0.55 | 第 3 镜切入 |
| 5 | `digital_rise.m4a` | 15.5 | 0.6 | 图表开始生长 |
| 6 | `riser.m4a` | 22.5 | 0.55 | 结构图推进 |
| 7 | `whoosh_tail.m4a` | 29.5 | 0.6 | 回真实素材 |
| 8 | `soft_hit.m4a` | 34.5 | 0.55 | 收尾落点 |

实测混音结果：`integrated −23.63 LUFS`、`true peak` 未超限、`max_volume −13.5 dB`（无削波）、
`mean_volume −25.4 dB`、静音段 0 个。底床不压过音效，靠的是 0.55/0.35 的电平分层加 loudness 归一，
不是靠人耳反复试。

## 5. 被引擎纠正的四处（都写进了代码注释，避免下一个人再踩）

1. **`clips` 项不接受 `loop`**（`ContractError: clips.S01: unsupported fields: loop`）：
   `SUPPORTED_CLIP_KEYS` 收紧为 `{frames_dir, pattern, n, fps, w, h}`，`loop` 改放镜头层，
   单帧 clip 自动写 `loop:"loop"`。
2. **转场属于入场镜头**：`transition` 放在后一镜，不是前一镜的「出场效果」。
3. **不同转场的 dir 词表不同**：`push` 只认 `left/right/up/down`，`wipe` 只认 `rtl/ltr/ttb/btt`。
   `timeline.py` 的 `_TRANSITION_DIRS` 固化这张表。
4. **`Project changed since it was opened. Reload before saving`**：渲染会改动项目 revision，
   `save_timeline_with_retry()` 重新取 `timeline_revision` 后重试，最多 4 次、间隔 1 秒。

另外把两条 V2.1 时代踩过的坑写进设计：`cam.pan.dx` 的单位是「每秒多少屏宽」（0.05 → 5 秒移动
0.25 屏，足够看清但不至于把内容推出画外）；`save_composition(revision=None)` 是「仅新建」。

## 6. 渲染

- `request_id = "phase2-45fa15aa"`（**绑定时间轴 revision**，复用旧 ID 会被拒绝）。
- job `5d7086caf37e57ddbe30429e572ab6b1`，`start_render` + 215 次 `get_render` 轮询，**约 19 分钟**。
- 产物 key 是 `video`（相对项目目录 `.openkatana/jobs/<id>/render/video.mp4`），业务侧按项目目录
  拼绝对路径后复制到 `output/preview_30_45s.mp4`。
- 成片：19,868,025 B，sha256 `b64562e6323dd750467af36b846c4c28fd09f173f12d0ddc85a5059c70bcea6f`。

## 7. 测试

`tests/test_media_timeline.py`（19 例）覆盖：帧覆盖完整性、clip 缺失（`missing_clip`）、
未知字段（`unsupported_field`）、相机词表（`unknown_camera`）、音轨校验（`bad_track`/`empty_audio`）、
单帧 plate 必须在镜头层声明 `loop`、转场 dir 词表、相机块对非 solid 镜头补 `fit/cover`。
