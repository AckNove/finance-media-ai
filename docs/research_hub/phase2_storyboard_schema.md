# Phase 2 · 分镜表结构（storyboard schema）

任务书要求「脚本不得直接进 FFmpeg，必须先转分镜」。这条要求的技术含义是：**分镜是一份数据文件，
不是脚本里的常量**，它能被校验、被拒绝、被另一个程序读取。本文件定义这份文件的契约。

实现在 `backend/app/media/storyboard.py`（约 240 行），样例在
`content/jobs/phase2_media_engine_demo/storyboard/storyboard.csv`。

## 1. 为什么用 CSV

- 人可以直接改：一行一个镜头，用 Excel/记事本都能编辑，不需要跑代码。
- 机器必须校验：列名、词表、时间连续性是**硬校验**，写错就抛 `StoryboardError` 并指出哪一行哪一列。
- 差值可见：`git diff` 能看出「第 4 镜的转场从 glitch 改成 zoom」，JSON 嵌套结构看不出来。

## 2. 十六个必需列

| 列 | 含义 | 取值约束 |
| --- | --- | --- |
| `shot_id` | 镜头标识 | 非空、全表唯一 |
| `start_time` / `end_time` | 秒 | 必须连续、不得倒流、必须整帧对齐（`× fps` 为整数） |
| `narrative_purpose` | 这一镜在叙事里干什么 | 自由文本，进素材清单 |
| `narration` | 旁白/字幕文字 | 可空；非空即生成一条字幕事件 |
| `visual_description` | 画面描述 | 自由文本 |
| `asset_id` | 用哪个素材 | 必须是已导入的 clip id |
| `asset_type` | 素材性质 | `real_video / real_image / generated_image / composition / solid` |
| `asset_source` | 素材来源 | 自由文本，写清人和机构 |
| `license` | 许可 | 自由文本，与登记表一致 |
| `animation` | 元素级动画说明 | 自由文本（画板内部由组合文档负责） |
| `camera_motion` | 运镜 | `static / push / pull / pan_left / pan_right / pan_up / pan_down / punch / handheld / shake / roll` |
| `transition` | 进入本镜的转场 | 18 个值之一：`none / dissolve / fade / dip / flash / glitch / lens / light_leak / liquid / luma / pixel_sort / push / slide / spin / whip / wipe / zoom / datamosh` |
| `subtitle` | 字幕文本 | 可空 |
| `sound_effect` | 音效名 | 可空；非空表示这一镜的切点上有音效 |
| `safety_zone` | 文字安全区 | `top / center / bottom` |

可选列：`fps`、`canvas_width`、`canvas_height`、`notes`。前三个若填写，必须**全表一致**，否则报
`inconsistent_column`——竖屏/横屏混着写是必然事故。

## 3. 校验规则（写错就拒绝，不静默修正）

| 错误码 | 触发条件 |
| --- | --- |
| `missing_columns` | 缺必需列（消息里列出缺哪几列） |
| `unknown_columns` | 出现未定义列（拼写错误的列名不会被忽略） |
| `empty` | 只有表头没有数据行 |
| `duplicate_shot` | `shot_id` 重复 |
| `bad_time` | 时间不是数字、`end <= start`、或不是整帧 |
| `non_contiguous` | 第 N 镜的 `end_time` 不等于第 N+1 镜的 `start_time` |
| `unknown_vocabulary` | `asset_type` / `camera_motion` / `transition` / `safety_zone` 不在词表内 |
| `bad_number` | 帧率等数字字段无法解析 |
| `inconsistent_column` | fps/画布尺寸在行间不一致 |
| `missing_value` | 必需列有空值 |

设计原则与 OpenKatana 自己的 AGENTS.md 一致：**拒绝而不是静默丢弃**。一个拼错的 `transition`
如果被忽略，成片会少一个转场，而没有任何人知道。

## 4. 本片的分镜（事实）

```
delivered: 7 shots, duration 40.0 s, frames 1200 @30fps
counts: {shots 7, real_video 2, real_image 2, composition 3,
         subtitles 7, transitions 6, camera_motions 5, sound_effects 7}
```

| # | shot | 时间 | 素材类型 | 运镜 | 转场 |
| --- | --- | --- | --- | --- | --- |
| 1 | sh01 | 0–5.0 | real_image | push | — |
| 2 | sh02 | 5.0–11.0 | real_video | pan_left | dissolve |
| 3 | sh03 | 11.0–15.5 | real_image | push | push |
| 4 | sh04 | 15.5–22.5 | composition | punch | glitch |
| 5 | sh05 | 22.5–29.5 | composition | pan_up | zoom |
| 6 | sh06 | 29.5–34.5 | real_video | handheld | slide |
| 7 | sh07 | 34.5–40.0 | composition | static | dip |

## 5. 从分镜到时间轴的映射

`backend/app/media/timeline.py` 的 `build_timeline(storyboard, clips, audio=…, fonts=…)` 做纯函数转换：

- `start_time × fps` → `f0`，`end_time × fps` → `f1`；相邻镜头首尾相接，覆盖全部 1200 帧。
- `camera_motion` → `cam` 块（例如 `pan_left` → `{"pan": {"dx": 0.05}}`）；非 `solid` 镜头一律
  补 `fit:"cover"` + `cover:true`。
- `transition` → **入场镜头**上的 `transition` 块（引擎规定转场属于后一镜）。
- 单帧 clip（`n<=1`）→ 该镜头 `loop:"loop"`。
- `subtitle`/`narration` → `text` 事件（`type:"caption"`, `pos:[0.5, 0.86]`, 字号 44）。
- 未知字段一律抛 `TimelineError`（`missing_clip / coverage / unsupported_field / unknown_camera / bad_track / empty_audio`）。

## 6. 测试（`tests/test_media_storyboard.py`，13 例；`tests/test_media_timeline.py`，19 例）

分镜侧覆盖：16 列常量、两镜连续解析（duration 9.5 / frames 285 / `frame_range(30) == (120, 285)`）、
`counts()` 统计、缺列/多列/空表/重复 shot_id/时间断裂/时间倒流/未知词表/画布不一致/中文往返磁盘/
文件不存在。时间轴侧覆盖：帧覆盖完整性、clip 缺失、未知字段、相机词表、音频轨校验、单帧循环声明等。
