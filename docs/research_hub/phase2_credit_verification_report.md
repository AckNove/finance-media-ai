# Phase 2 素材授权与署名核验报告

> 本轮（V2.2 Phase 2 收尾整改，问题 1）。修复前成片结尾只有一句
> 「素材：NASA 公共领域影像」，把三个不同作者、不同可视化项目的素材压成一句空话。
> 现在结尾卡**逐条**列出实际使用素材的出处，且每一行都是从
> `content/jobs/phase2_media_engine_demo/source_register.csv` 的 `credit` 列**原样抄进画面**的。

## 1. 修复前后对比

| | 修复前 | 修复后 |
| --- | --- | --- |
| 结尾卡署名 | 1 行：「素材：NASA 公共领域影像」 | 6 行：S01 / S02 / S03 / S04 各 1 行 + 字体 1 行 |
| 与登记表的关系 | 无对应关系，是概括说法 | 逐条对应，代码层面强制：`outro_credit_lines()` 读登记表，`credit` 为空直接构建失败 |
| 许可结论 | 隐含「公共领域 = 随便用」 | 明确写「依 NASA 媒体使用政策使用，第三方内容可能另有版权限制」+「NASA 未审查、未背书」 |
| 能否核对 | 不能 | 能：卡片每行的 `source_id` 与登记表 `source_id` 一一对应 |

代码位置：`content/jobs/phase2_media_engine_demo/script/build_phase2.py` 的
`outro_composition()` / `outro_credit_lines()` / `CREDIT_LABELS`。

```python
register = ApprovedSources(JOB_ROOT).load_register()
for source_id, label in CREDIT_LABELS:
    entry = register.assert_renderable(source_id)      # 授权不明直接抛错
    credit = entry.credit.strip()
    if not credit:
        raise SystemExit(f"{source_id}: the register has no credit line; the card may not invent one")
```

这句 `SystemExit` 是这次整改的关键：**卡片无法凭空写出一个作者名**。要改署名只能改登记表。

## 2. 逐条核验

数据来源：`content/jobs/phase2_media_engine_demo/source_register.csv`（4 行，全部
`verification_status=verified`、`commercial_use=allowed`），并与上游
`tools/openkatana/examples/quick-edit/assets/sources.json` 逐字节比对过 sha256。

### S01 阿波罗17号拍摄的地球（静帧）

| 项 | 值 |
| --- | --- |
| 文件 | `input/approved_sources/nasa_apollo17_earthrise.jpg`（265,324 B） |
| SHA-256 | `13419b34c17722be52b4a71a7f0beb3ecc8673183c8a2cee8572d5cd9bbe3f93` |
| 原始来源 | `https://svs.gsfc.nasa.gov/vis/a030000/a030600/a030613/blue_marble_apollo_17_19721207_print.jpg` |
| 发布机构 | NASA Scientific Visualization Studio |
| 拍摄时间 | 1972-12-07（阿波罗 17 号任务） |
| 署名 | Earth Science and Remote Sensing Unit, NASA Johnson Space Center |
| 许可 | NASA public-domain media (media-use policy applies) |

### S02 蓝色弹珠（星空背景）静帧

| 项 | 值 |
| --- | --- |
| 文件 | `input/approved_sources/nasa_blue_marble_starfield.jpg`（161,444 B） |
| SHA-256 | `7dbf717a55aa845cb5446685d89cfca07e74ff3f7c05ee7b43b0c8b779b7c2ab` |
| 原始来源 | `https://svs.gsfc.nasa.gov/vis/a000000/a004500/a004550/BlueMarble_starfield_4k_2160p30_v2.01000_print.jpg` |
| 发布机构 | NASA Scientific Visualization Studio（可视化项目 4550） |
| 署名 | NASA Scientific Visualization Studio; Cheng Zhang |
| 许可 | NASA public-domain media (media-use policy applies) |

### S03 VIIRS 地球自转片段（一个文件、两个入点）

| 项 | 值 |
| --- | --- |
| 文件 | `input/approved_sources/nasa_earth_rotation.mp4`（3,823,126 B） |
| SHA-256 | `6f436803b09f1fc93ec648ec441921c1fd537959af3ce09561927d3800bb2ae7` |
| 原始来源 | `https://svs.gsfc.nasa.gov/vis/a030000/a030700/a030763/R_earth_viirs_360p.mp4` |
| 发布机构 | NASA Scientific Visualization Studio（可视化项目 30763） |
| 署名 | NASA Scientific Visualization Studio; Amy Moran and Jesse Allen |
| 实测 | ffprobe：23.967 s / 640×360 / 30 fps / **无音轨** |
| 用法 | 分镜里的 `S03A`（0–6 s）与 `S03B`（12–17 s）**是同一个文件的两个入点**，不是两段独立素材 |

**关于「两个真实视频片段」的如实说明**：任务 6 的指标写「≥2 个真实视频片段」，本片实际
只有 **1 个独立视频来源**（S03），用两个不同入点各占一个镜头。本轮任务书明确「不要求
强行补充第二个独立来源，但不得把同一文件的两个入点描述为两个独立来源」。因此本项结论是
**部分完成**，且此说明同时写进了 `phase2_video_demo_report.md` 与最终 QC 报告。

### S04 配乐（OpenKatana 原创合成）

| 项 | 值 |
| --- | --- |
| 文件 | `input/approved_sources/openkatana_orbit_score.wav`（705,644 B） |
| SHA-256 | `d0c03550b432975d1432f8aa49337843c8be9b1cf9fbd1925e3bdeed3731219e` |
| 来源 | `https://github.com/rk0neki/OpenKatana/blob/v0.1.3/examples/quick-edit/assets/orbit.wav` |
| 许可 | **Apache-2.0**（上游 `sources.json` 标注「Original synthesized score, no external recordings or samples」） |
| 署名 | OpenKatana contributors, Apache-2.0 |
| 用法 | `-stream_loop` 铺满 42.5 s，作为第二配乐层（音量 0.35） |

### 额外一层：字体

| 项 | 值 |
| --- | --- |
| 文件 | `<project>/fonts/NotoSansSC-VF.ttf`（17.7 MB，来自 `C:\Windows\Fonts\`） |
| 许可 | SIL Open Font License 1.1（允许嵌入、商用、再分发） |
| 卡面署名 | 字体 Noto Sans SC · SIL OFL 1.1 |

字体不属于 `source_register.csv` 的素材范畴（它是渲染资源，不是内容素材），因此这一行由
`outro_credit_lines()` 在素材署名之后追加，属于固定声明而非从登记表读取。

## 3. 素材使用政策的依据

- 登记表里记录的政策入口：`https://www.nasa.gov/nasa-brand-center/images-and-media/`
  （与上游 `sources.json` 的 `policy` 字段一致）。
- 上游 `sources.json` 的原文备注：**"Editorial example. NASA does not endorse OpenKatana.
  Media is not licensed as CC0."** —— 这句话本身就是对「公共领域＝无条件商用」的否定，
  所以本次整改把它落到了画面上（「NASA 未审查、未背书本片」）。
- 三条 NASA 素材均为 NASA 自有可视化项目（SVS）出品的图像/视频，SVS 页面记录的作者是
  NASA 内部团队或个人（JSC 地球科学与遥感组、Amy Moran、Jesse Allen、Cheng Zhang）。

## 4. 明确标注「待核实」的三项

本轮**没有**联网逐条核对 NASA SVS 页面上的单条条款，也没有核对数据提供方是否有附加条款。
以下三项如实标注为待核实，**不作为已证实的授权结论**：

1. **NASA SVS 逐页条款未复核**：本轮授权范围是离线核验（登记表 + 上游 `sources.json` +
   sha256 + ffprobe），未联网抓取 NASA 页面。正式发布前必须人工打开三个 SVS 页面确认
   当页是否有额外使用限制。
2. **S03 的数据来源方条款未核实**：VIIRS 是 NASA/NOAA JPSS 联合任务的仪器，影像由 NASA
   SVS 处理发布。美国联邦政府作品通常不受版权保护，但**未逐条核实 NOAA/JPSS 一侧是否
   另有条款**。发布前需人工确认。
3. **画面内是否含第三方素材未逐帧核对**：三条素材都是 NASA 制作，但未逐帧确认画面里是否
   出现第三方商标、地图边界主张或其他受版权保护的插入内容。按 NASA 政策，这类内容可能
   另有版权限制——卡面已经用一句话把这条风险写明。

上述三条都不影响「本轮 Demo 可以渲染、可以内部评审」，但**都影响「能不能对外发布」**。
本报告不给出「可无条件商用」的结论。

## 5. 成片中的验证证据

- 结尾卡最后一帧抽帧：`content/jobs/phase2_media_engine_demo/qc/review/` 下的
  `99_credits` 帧（`--stage review` 生成），可见 6 行署名全部完整、未裁切、未被字幕遮挡。
- 文字未溢出：结尾卡版式为 `justify-content: center` + 90px 左右内边距，两条长署名
  （S01、S03）自动折成两行，仍落在 1080×1920 安全区内。
- 卡片内容与登记表的对应关系可由脚本复现：
  `.venv\Scripts\python.exe content\jobs\phase2_media_engine_demo\script\build_phase2.py --stage compositions`
  会重新读登记表并打印三条 `S01/S02/S03` 的 credit。

## 6. 结论

| 验收标准 | 结果 |
| --- | --- |
| 登记表与成片署名逐项对应 | **通过**（5 行署名，代码强制从登记表读取） |
| 署名不被裁切、不被遮挡 | **通过**（抽帧确认，长行自动折行仍在安全区内） |
| 不存在未经核实的作者、机构或授权声明 | **通过**（作者全部来自登记表；不确定的三项已在第 4 节标为待核实，未写进画面） |
| 是否可无条件商用 | **不作此结论**。按 NASA 政策与署名要求使用；发布前需完成第 4 节三项人工复核 |
