# Phase 2 · 真实素材导入测试报告

任务书任务 2 要求逐项证明十件事：PNG/JPG、MP4、WAV/AAC 导入、裁剪、缩放适配、推拉平移、
多素材时间轴、转场、字幕叠加、多音轨混合。本文件只写**导入**这一段（其余见
`phase2_timeline_integration.md` 与 `phase2_qc_report.md`），且全部是引擎真实返回，不是设计意图。

## 1. 输入清单（全部经白名单登记 + 商用授权判定）

| 源 | 文件 | 字节 | SHA-256 | 来源与许可 |
| --- | --- | --- | --- | --- |
| S01 | `nasa_apollo17_earthrise.jpg` | 265,324 | `13419b34…9bbe3f93` | NASA PD（ESRS/JSC），商用允许 |
| S02 | `nasa_blue_marble_starfield.jpg` | 161,444 | `7dbf717a…b7c2ab` | NASA PD（SVS / Cheng Zhang），商用允许 |
| S03 | `nasa_earth_rotation.mp4` | 3,823,126 | `6f436803…0bb2ae7` | NASA PD（SVS / Amy Moran & Jesse Allen），商用允许 |
| S04 | `openkatana_orbit_score.wav` | 705,644 | `d0c03550…3731219e` | Apache-2.0（OpenKatana 原创合成），商用允许 |

四个文件都来自 `tools/openkatana/examples/quick-edit/assets/`，逐个与它自带的 `sources.json` 比对过
哈希。**被拒绝的两个**见 `phase2_asset_provenance_report.md`（`koji-edit.mp4` 音乐授权不明、
`ltx25-source.mp4` 模型许可要求逐案复核）。

## 2. 导入结果（`import_media` 真机返回）

| 资产 | 调用参数 | 引擎返回 | 判定 |
| --- | --- | --- | --- |
| S01 图片 | `rights="public_domain"` | `kind=image`，`clip={frames_dir: plates/…, n:1, fps:30, w:1024, h:1024}` | 通过 |
| S02 图片 | `rights="public_domain"` | `kind=image`，`n:1, 1024×576` | 通过 |
| S03A 视频入点 A | `start=0.0, duration=6.0, max_width=1080` | `kind=video`，`n:180, fps:30.0, 640×360` | 通过 |
| S03B 视频入点 B | `start=12.0, duration=5.0, max_width=1080` | `kind=video`，`n:150, fps:30.0, 640×360` | 通过 |
| S04 音频 | `audio_only=True` | `kind=audio`，`duration=8.0`，`clip=null` | 通过 |

逐项对应任务要求：

- **PNG/JPG 导入**：两张 JPG，转成单帧 `plates/<id>/00001.png`；时间轴上单帧 clip 必须声明
  `loop:"loop"` 才能铺满 5 秒（否则只出现 1/30 秒）。
- **MP4 导入**：3.8 MB / 640×360 / 23.97 秒源片被切出两个入点，**这是 ffmpeg 8.1.2 前置 PATH 之后
  才打通的路径**（4.2.2 下 `-fps_mode` 不存在，导入必失败）。
- **裁剪**：`start=0.0/duration=6.0` → 180 帧；`start=12.0/duration=5.0` → 150 帧，帧数与
  `duration × 30` 精确相等，说明裁剪按帧对齐，不是「大致截一段」。
- **缩放适配**：源片 640×360（16:9）要放进 1080×1920（9:16）画布。引擎按 `max_width=1080` 保留
  原始像素（不放大），时间轴层用 `fit:"cover"` + `cover:true` 裁切铺满；实际成片抽帧可见地球
  满屏、无黑边、无拉伸。
- **WAV 导入**：`orbit.wav` 以 `audio_only=True` 导入，引擎返回 `duration=8.0`，随后被转成
  `audio/orbit_loop.m4a`（40 秒循环铺底）。**为什么要转**：OpenKatana 的 mux 无法把 PCM 塞进 MP4
  （V2.1 实测 `Could not find tag for codec pcm_s16le in stream #1`），所以所有音频输入统一先
  转 AAC 192k。

## 3. 同一素材两个入点 ≠ 两段素材

`S03A`（0–6 s）与 `S03B`（12–17 s）来自同一个 NASA 文件的两个时间窗。它们生成两个独立 asset
（不同 `frames_dir`、不同哈希），在时间轴上确实是两个镜头，但**必须如实说明**：本轮没有找到第二个
「授权明确、可以商用」的真实视频来源，任务书要求的「≥2 个真实视频片段」在**来源数量**上只满足 1 个。
按任务书「不得伪造」的要求，这里不拿生成视频或来源不明素材补位，用程序生成的画板补足镜头数。

## 4. 导入失败的两次（保留，作为契约证据）

| 报错 | 触发原因 | 结论 |
| --- | --- | --- |
| `RuntimeError: ffmpeg failed: Unrecognized option 'fps_mode'` | 引擎用了 PATH 上的 4.2.2 | 适配层必须前置 `tools/ffmpeg/bin`（已修，见适配层报告） |
| `create_project: unsupported arguments: title, workspace` | 项目创建参数名写错 | 只发 `{"name": title}` |

## 5. 复现命令

```powershell
cd C:\Users\25803\finance-media-ai
.venv\Scripts\python.exe content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage sources
.venv\Scripts\python.exe content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage import
```

`--stage import` 的产物记录在 `content/jobs/phase2_media_engine_demo/raw/_work/clips.json`
（每个资产的 `frames_dir`、帧数、宽高、fps、哈希、调用参数）。
