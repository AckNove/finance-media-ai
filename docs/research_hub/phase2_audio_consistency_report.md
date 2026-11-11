# Phase 2 音频统计一致性报告

> 本轮（V2.2 Phase 2 收尾整改，问题 3）解决的问题：**同一个「音效数量」在四个地方
> 是三个不同的数字**。现在四个口径分开写、由代码互相校验，对不上就构建失败。

## 1. 修复前的状态（实测复现）

| 位置 | 当时的数字 | 来源 |
| --- | --- | --- |
| `storyboard.counts()["sound_effects"]` | **7** | 数 `sound_effect` 列非空的行 |
| 实际生成的音效文件 | **6** | `build_phase2.py` 里一个**硬编码列表** `sfx_schedule()` |
| 时间轴音效轨 | **6** | 跟着硬编码列表走 |
| `raw/_work/evidence.json` 里的 `manifest_rows` | **10** | `len(rows) + len(original_rows) + 1`，把 6 个音效算成 1 行 |
| `assets_manifest.csv` 实际行数 | **15** | 4 源 + 5 原创 + 6 音效 |

根因有两条，都是真实缺陷，不是记错：

1. **`sound_effect` 列里有一个从未生成过的值 `room tone`**（`sh01`）。它出现在分镜里，
   所以被 `counts()` 数成 1 个音效；但 `sfx_schedule()` 里根本没有它，也没有任何音频文件
   和音轨。**它从来没有进入过成片。**
2. **名字对不上**：分镜写的是 `low whoosh` / `soft impact` / `digital rise` / `riser` /
   `whoosh tail` / `soft hit`（空格分隔），代码里的滤镜表 `SFX_FILTERS` 用的是蛇形名
   `whoosh_low` / `impact_soft` / …。两边靠人肉约定对齐，没有任何代码强制。

## 2. 修复后的四个口径（现在同时出现在 QC 报告里）

| 口径 | 数量 | 定义 | 写在哪 |
| --- | --- | --- | --- |
| 音效事件数 | **6** | storyboard 里 `sound_effect` 非空且不等于 `none` 的镜头数 | `storyboard.counts()["sound_effects"]` |
| 独立音效文件数 | **6** | 实际用 ffmpeg 合成、转 AAC、复制进项目 `audio/` 的文件数 | `audio.json.counts.effect_events/ distinct_effects` |
| 配乐数 | **2** | 贯穿全片的背景层：原创合成 `score_bed.m4a` + Apache-2.0 的 `orbit_loop.m4a` | `audio.json.counts.music_tracks` |
| 时间轴总音轨数 | **8** | `timeline.json` 的 `audio.tracks` 条数 = 2 配乐 + 6 音效 | `qc/audio_consistency.json` |

四个数字的关系恒等式：`总音轨 = 配乐数 + 音效事件数`（8 = 2 + 6）。
`qc/audio_consistency.json` 每次构建都会重算并断言它。

## 3. 唯一真源：分镜表

`sfx_schedule()` 不再是硬编码列表，改为**从分镜派生**：

```python
def sfx_schedule(storyboard=None):
    board = storyboard or load_storyboard(STORYBOARD)
    for event in board.sound_effect_events():      # 名字、镜头、落点全部来自 CSV
        if event.name not in SFX_FILTERS:          # 写错名字直接构建失败
            raise SystemExit(...)
```

配套改动：

- `backend/app/media/storyboard.py`：新增 `SOUND_EFFECT_NONE = "none"` 与
  `SoundEffectEvent(shot_id, name, at)`；`Storyboard.sound_effect_events()` 跳过空值与
  `none`，`at` 取该镜头起始时间；`counts()` 的 `sound_effects` 改为
  `len(sound_effect_events())`，另加 `sound_effect_names`。
- `content/jobs/phase2_media_engine_demo/storyboard/storyboard.csv`：`sh01` 的
  `room tone` 改成 `none`（明确「这一镜没有音效」），其余 6 个名字改成蛇形并与
  `SFX_FILTERS` 一一对应。
- `assets_manifest.csv` 的音效行改为遍历 `sfx_schedule(storyboard)` 生成，行数直接等于
  音效事件数；`evidence.json` 的 `manifest_rows` 改为**真数出来的行数**，并拆成
  `manifest_source_rows / manifest_original_rows / manifest_sfx_rows` 三个分项。

## 4. room tone 的处理决定

**删除**，理由与证据：

- 从来没有生成过对应音频文件（`audio/` 下无 room tone 文件）。
- 从来没有导入过项目（`clips.json` 无此条目）。
- 从来没有进入时间轴（旧 `timeline.json` 的 8 条音轨里没有它）。
- 结论：它是一个**只存在于 CSV 文本里的空承诺**，计入音效数量属于虚报。

因此在 `sh01` 上写 `none`，把「没有音效」这件事显式表达出来。若将来确实需要室内底噪，
正确做法是：生成文件 → 登记进 `assets_manifest.csv` → 导入项目 → 写进 `audio.tracks` →
`audio_consistency` 通过，四步齐全才允许计数。

## 5. 自动一致性检查（新增，构建即门禁）

`backend/app/media/timeline.py` 新增 `audio_consistency(*, events, generated, music_srcs, audio)`，
返回四个计数 + `ok` + `problems[]`，问题码：

| 码 | 含义 |
| --- | --- |
| `missing_generated` | 分镜要求了某个音效，但没生成文件 |
| `missing_track` | 生成了文件，但时间轴里没有这条轨 |
| `duplicate_track` | 同一个音效文件在时间轴上出现多次 |
| `wrong_time` | 音轨落点与分镜声明的秒数不一致（容差 1e-6） |
| `unused_generated` | 生成了文件，但没有镜头引用它 |
| `missing_music` | 声明的配乐层不在时间轴里 |
| `unexpected_track` | 时间轴里出现既不是配乐也不是音效事件的轨 |
| `count_mismatch` | `总音轨 ≠ 配乐数 + 音效事件数` |

两个消费点：

1. `build_phase2.py --stage timeline` 在 `save_timeline` 之后调用它，写
   `qc/audio_consistency.json`；`ok=False` 直接 `SystemExit` 并打印 problems，**不允许带病出片**。
2. `--stage qc` 新增检查项 `audio_counts_consistent`，把 `ok` 变成 QC 结论的一部分；
   `multi_track_audio` 也改为按 `audio_tracks(storyboard)` 实际条数判断。

## 6. 本轮实测结果

```
[phase2_media_engine_demo] audio consistency -> events 6, files 6, music 2, tracks 8, ok=True
[phase2_media_engine_demo] validate_timeline -> {"valid": true, "frames": 1275, "warnings": []}
[phase2_media_engine_demo] save_timeline revision=e01048d3be88
```

`content/jobs/phase2_media_engine_demo/qc/audio_consistency.json`：
`events=6, distinct_effects=6, generated_files=6, music_tracks=2, effect_tracks=6, total_tracks=8, ok=true, problems=[]`，
`timeline_revision=e01048d3be8861b268a8fb5e90313e040c593999d6aad05d4cd9abb87fd4267f`。

逐条可追溯（镜头 / 音效 / 落点 / 文件 → 时间轴音轨）：

| 镜头 | 音效 | 落点 | 文件 | 时间轴 `at` |
| --- | --- | --- | --- | --- |
| sh02 | `whoosh_low` | 5.0 s | `audio/whoosh_low.m4a` | 5.0 |
| sh03 | `impact_soft` | 11.0 s | `audio/impact_soft.m4a` | 11.0 |
| sh04 | `digital_rise` | 15.5 s | `audio/digital_rise.m4a` | 15.5 |
| sh05 | `riser` | 22.5 s | `audio/riser.m4a` | 22.5 |
| sh06 | `whoosh_tail` | 29.5 s | `audio/whoosh_tail.m4a` | 29.5 |
| sh07 | `soft_hit` | 34.5 s | `audio/soft_hit.m4a` | 34.5 |

配乐两层：`audio/score_bed.m4a`（OpenKatana `synthesize_score` 合成，`credits_used: 0`）与
`audio/orbit_loop.m4a`（S04，Apache-2.0，`-stream_loop` + 淡入淡出到全片时长）。

## 7. 测试（新增 6 条 + 2 条 storyboard 用例）

`tests/test_media_timeline.py`：

- `test_audio_consistency_accepts_a_fully_traceable_mix`（正例，断言四个计数与 `total_tracks`）
- `test_audio_consistency_flags_a_cue_with_no_generated_file`（`missing_generated`，同时报 `count_mismatch`）
- `test_audio_consistency_flags_a_cue_that_never_reached_the_timeline`（`missing_track`）
- `test_audio_consistency_flags_a_cue_at_the_wrong_second`（`wrong_time`）
- `test_audio_consistency_flags_an_orphan_effect_and_a_missing_bed`（`unused_generated` + `missing_music` + `unexpected_track`）

`tests/test_media_storyboard.py`：

- `test_sound_effect_events_are_derived_from_the_rows`（`none` 与空值都被排除，`counts()` 与事件数一致）
- `test_a_shot_can_explicitly_declare_no_sound_effect`（`none` 表示「没有音效」，不是名叫 none 的音效）

## 8. 没有做的事

- 没有为了「让四个数字好看」而补一个假的 room tone 文件——任务书明确禁止，也确实没有补。
- 没有做响度标准化（LUFS 归一）。当前集成响度见 `phase2_final_qc_report.md`，本轮
  **不擅自改动音频标准**，只给出客观说明。
