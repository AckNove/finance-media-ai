# Phase 2 分镜字段语义报告：narration 与 subtitle

> 本轮（V2.2 Phase 2 收尾整改，问题 2）只处理一件事：把 `narration` 和 `subtitle`
> 两个字段的职责写进代码契约、写进测试，并修掉分镜里唯一一处真实重复。
> 所有结论都能用 `tests/test_media_timeline.py` 与 `tests/test_media_storyboard.py` 复跑。

## 1. 两个字段现在的定义

| 字段 | 定义 | 谁消费 | 会不会出现在画面上 |
| --- | --- | --- | --- |
| `narration` | 口播稿文本。写给未来的真人配音或 TTS 引擎读的整句，包含语气词、停顿提示 | 目前**没有任何渲染消费者**；`build_phase2.py --stage audio` 只在 `audio.json` 里记一条 `narration: "none — no TTS was called and no human recording exists for this demo"` | 不会。除非显式传 `source="narration"` |
| `subtitle` | 屏幕上真正出现的字幕文本。要求能被 3 秒内读完 | `backend/app/media/timeline.py` 的 `subtitle_events()` 默认来源；结果写进时间轴 `text[]` 的 `caption` 事件 | 会 |

一句话：**旁白是「将来谁念」，字幕是「现在屏幕上写什么」，两者没有隐式映射。**

## 2. 代码契约（改造前 → 改造后）

改造前：`subtitle_events()` 内部直接读 `shot.subtitle`，「narration 不会变成字幕」是一条
**隐含行为**——没有开关、没有文档、没有测试，任何人改一行就可能悄悄变成双字幕。

改造后（`backend/app/media/timeline.py`）：

```python
SUBTITLE_SOURCES = ("subtitle", "narration", "none")

def subtitle_events(storyboard, *, family, size, source="subtitle"): ...
```

- `source="subtitle"`（默认）：只读 `subtitle`，空字符串的镜头不产生字幕事件。
- `source="narration"`：显式 opt-in，把口播稿直接当字幕用（只有确认「不另配真人声」时才该这样做）。
- `source="none"`：字幕总开关，返回空列表。
- 其他任何值抛 `TimelineError("unsupported_source")`，**不会静默回退到默认值**。

`build_phase2.py` 的 `build_document()` 现在显式传 `source="subtitle"`，把「默认值」变成
「写死在调用点上的一次表态」，避免未来读代码的人靠猜。

`backend/app/media/storyboard.py` 的模块 docstring 也补了同一句话，并新增
`SOUND_EFFECT_NONE = "none"`、`SoundEffectEvent`、`Storyboard.sound_effect_events()`
（见 `phase2_audio_consistency_report.md`）。

## 3. 当前成片里的用法

- 7 个镜头，`narration` **7 条全部填写**（是给人看的完整口播稿）。
- `subtitle` **6 条**：`sh01`–`sh06` 各有 1 条，`sh07` 留空。
- **修掉的真实重复**：改造前 `sh07` 的 `subtitle` 是「分镜 → 时间轴 → 成片」，而结尾卡
  第一行大字就是同一句——即同一句话在同一秒出现两次（卡面 + 字幕）。现在 `sh07` 的
  `subtitle` 为空，画面只有卡面文字。
- 本 Demo **没有配音**：没有调用 TTS、没有录音、也没有把字幕当配音。`audio.json` 与
  `qc 报告` 都明写「narration 未渲染」。这一条是任务书禁止项，不是遗漏。

## 4. 测试覆盖（新增 7 条 + 保留原有 1 条）

文件：`tests/test_media_timeline.py`

| 用例 | 覆盖的场景 | 断言 |
| --- | --- | --- |
| `test_captions_read_the_subtitle_column_only` | narration 非空 + subtitle 非空且**内容不同** | 只产生 1 条字幕，文本等于 `subtitle` |
| `test_empty_subtitle_renders_nothing_even_when_narration_exists` | subtitle 空 + narration 非空 | 0 条字幕（不偷偷用 narration） |
| `test_narration_becomes_captions_only_when_explicitly_asked` | 同上，但显式 `source="narration"` | 1 条字幕，文本等于 narration |
| `test_both_columns_filled_with_different_text_yields_one_caption` | 两镜都同时填两个字段 | 逐镜只有 1 条字幕，**没有双字幕** |
| `test_both_columns_empty_yields_no_caption` | 两者均为空 | 0 条字幕 |
| `test_subtitle_source_none_is_an_off_switch` | `source="none"` | 0 条字幕 |
| `test_unknown_subtitle_source_is_rejected` | `source="automatic"` | `TimelineError.code == "unsupported_source"` |
| `test_subtitles_follow_the_safety_zone`（原有） | 三种安全区 | 位置 `[0.5, 0.16] / [0.5, 0.5]`、帧区间正确 |

## 5. 兼容性

- CSV 列没有增删，`REQUIRED_COLUMNS` 仍是 16 列；旧分镜文件原样能读。
- `timeline.json` 结构不变（`text[]` 里的 `caption` 事件字段没动）。
- 唯一的可见变化：`sh07` 少了一条与卡面重复的字幕，总镜头时长从 40.0 s 变为 42.5 s
  （结尾卡需要放下逐条署名，见 `phase2_credit_verification_report.md`）。

## 6. 仍未做的事

- 没有实现 TTS。要接真人配音或 TTS 时，`narration` 已经是现成的口播文本源，需另行实现
  「narration → 音频 → 时间轴 audio.tracks」这一段，本 Demo 不做。
- 没有做「字幕断句」逻辑：`subtitle` 按人工写的整句显示，不自动在标点处切分。
