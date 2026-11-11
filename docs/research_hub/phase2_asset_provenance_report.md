# Phase 2 · 素材溯源与授权报告

产物：`content/jobs/phase2_media_engine_demo/assets_manifest.csv`（**15 行数据、15 列**，由
`backend/app/media/manifest.py` 生成，不是手抄）。本文件解释这份清单背后的判定过程——尤其是**哪些
素材被拒绝、为什么拒绝**，因为拒绝记录比采用记录更能说明这套流程有没有在起作用。

## 1. 三类素材的分工

| 类 | 定义 | 本片用到 | 行数 |
| --- | --- | --- | --- |
| A | 真实素材（拍摄/卫星/官方影像），须有明确商用许可 | NASA 两张图 + 一段视频 + 一段授权配乐 | 4 |
| B | 原创（本仓库生成的画板与音效） | 3 块画板 + 1 段合成配乐 + 6 个音效 | 10 |
| C | 辅助（字体等运行资源，不进画面主体） | Noto Sans SC（OFL-1.1） | 1 |

A 类**必须**通过 `ApprovedSources.assert_renderable()`：`commercial_use` 不是 `allowed`、或许可证名
为空，直接抛 `rights_unclear`，渲染阶段拿不到素材。B/C 类同样登记，只是校验阈值不同（原创素材的
来源写「本任务作者」而不是外部 URL）。

## 2. 采用的 A 类素材（逐条可核）

| ID | 文件 | 字节 | SHA-256 | 来源 | 许可 | Credit |
| --- | --- | --- | --- | --- | --- | --- |
| S01 | `nasa_apollo17_earthrise.jpg` | 265,324 | `13419b34…9bbe3f93` | NASA SVS | NASA public-domain media-use policy | Earth Science and Remote Sensing Unit, NASA JSC |
| S02 | `nasa_blue_marble_starfield.jpg` | 161,444 | `7dbf717a…b7c2ab` | NASA SVS | 同上 | NASA SVS; Cheng Zhang |
| S03 | `nasa_earth_rotation.mp4` | 3,823,126 | `6f436803…0bb2ae7` | NASA SVS | 同上 | NASA SVS; Amy Moran and Jesse Allen |
| S04 | `openkatana_orbit_score.wav` | 705,644 | `d0c03550…3731219e` | OpenKatana v0.1.3 | Apache-2.0 | OpenKatana contributors |

三条 NASA 素材的哈希与 `tools/openkatana/examples/quick-edit/assets/sources.json` 记录的哈希**逐字节一致**，
说明用的是原文件而不是二次转码的半成品。NASA 的授权是「public-domain media-use policy」，
**不是 CC0**——所以 credit 一栏必须逐条写清机构和作者，这一点在素材清单里是硬字段。

## 3. 被拒绝的素材（关键部分）

| 候选 | 为什么不许用 | 判定依据 |
| --- | --- | --- |
| `docs/assets/koji-edit.mp4` | 画面是 CC BY 动画「Koji」，但配乐来自 Suno，**音乐授权状态不明** | 授权不明确 → 不用（任务书决策 D） |
| `examples/comfy/assets/ltx25-source.mp4` | 由 LTX-2.5 生成，模型许可为 LTX-2.x Community License，其 `rights_review` 字段原文要求「Review exact model and encoder terms and output before publication」 | 需逐案复核 → 本轮不用 |
| 桌面上任何个人文件、旧任务目录 `content/jobs/20261110_jgb_gold_repricing/` 内的一切 | 全局隔离：旧数据不得进入新任务 | 任务书最高优先级约束 |
| 任何未登记的本地视频 | 白名单只允许 `input/approved_sources/` 内登记过的文件 | `job_paths.PathNotAllowedError` |

**因此「真实视频片段的来源数 = 1」**（S03，用了两个入点 S03A/S03B）。任务书指标写的是「≥2 个真实
视频片段」，来源数量上只满足 1；按「不得伪造」的要求，本报告如实标为**部分满足**，不拿生成视频补位。

## 4. B 类原创素材

| 资产 | 文件 | 生成方式 | 备注 |
| --- | --- | --- | --- |
| C_CHART | `assets/compositions/chart.json` | 手写组合文档 → OpenKatana 渲染 210 帧 | 数值取自本任务分镜统计，不是外部行情 |
| C_DIAGRAM | `assets/compositions/diagram.json` | 同上，210 帧 | 五张流程卡 + 连线 |
| C_OUTRO | `assets/compositions/outro.json` | 同上，165 帧 | 结尾卡四行 |
| B01 | `audio/score_bed.m4a` | OpenKatana `synthesize_score`（确定性合成，`credits_used: 0`） | 40 秒，零成本可商用 |
| B02–B07 | `audio/*.m4a` | ffmpeg 程序合成（正弦/噪声 + 包络） | 6 个切点音效 |

B 类全部标注 `license="原创（本仓库内容）"`。这里有个容易被忽略的点：**程序合成的音频也有版权归属**，
所以它们一样要进清单、一样要标 allowed，不能因为「自己生成的」就跳过登记。

## 5. C 类：字体

Noto Sans SC（`fonts/NotoSansSC-VF.ttf`，项目内副本，SHA-256 `763146584cf0710223441356b4395e27…`，
SIL OFL-1.1）。字体是渲染必需资源，且中文字形并非所有开源字体都有——OpenKatana 自带字体全套无中文
字形，这一点在 V2.1 已经踩过，所以本片在项目目录内放了 17.7 MB 的可再分发副本。

## 6. 溯源字段怎么被机器使用

15 列里真正在流程里起作用的：

- `sha256`：`load_register()` 在渲染前**重算**并比对，不一致抛 `not_registered`（防「登记一个文件、
  渲染另一个文件」）。
- `commercial_use`：`assert_renderable()` 的判定依据。
- `credit`：必须随片出现（本片在抽帧与报告中保留机构与作者名）。
- `used_in_shots`：把素材和镜头对上，便于复核「这一镜的画面来自哪一行」。
- `verification_status`：本片全部 `verified`；若将来有 `pending`，该行不得通过渲染闸门。

## 7. 复现

```powershell
.venv\Scripts\python.exe content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage sources
.venv\Scripts\python.exe content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage evidence
```

`--stage evidence` 会重新生成 `assets_manifest.csv`；若有人手工改坏了 A 类的 `commercial_use`，
下一次 `--stage evidence` 会在 `assert_renderable()` 处失败，而不是悄悄生成一份带风险清单。
