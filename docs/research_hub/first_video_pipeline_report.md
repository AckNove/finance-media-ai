# 首条视频生产闭环 · 复用与实跑报告

任务：在不破坏 M2.0 成果的前提下，用开源能力 + 少量胶水，跑通「真实资料 → 研究笔记 → 脚本分镜 → 本地渲染 MP4」的最小闭环。
交付时间：2026-11-10｜执行环境：Windows + `.venv` Python 3.12.10｜状态：**已生成真实 MP4，未提交未推送**

---

## 0. 一页纸结论

| 问题 | 结论 |
| --- | --- |
| 是否复用了开源而不是自己造轮子 | 是。文档解析用 **MarkItDown 0.1.9**，视频合成/字幕/混流用**本机 FFmpeg 4.2.2**，技能格式用 **Agent Skills（SKILL.md）**，并 vendor 了 Anthropic 官方 `pdf`/`xlsx` 技能目录 |
| 是否解析了真实 PDF / Excel | 是。真实中文 PDF 36 页 → 143,465 字符；真实 XLSX 10 条新闻 → 1,648 字符；两份真实 CSV 各 500+ 行 |
| 转换结果是否被研究流程真正使用 | 是。`source_text/*.md` 被 `research_notes.md` 与 `fact_check.md` 逐条引用（S01–S04） |
| 是否有符合 AK 风格的脚本与分镜 | 是。五场景口播稿 + `storyboard.csv` + 三个 AK 自有 skill 作为判定依据 |
| 是否调本地视频工具实际渲染 | 是。5 段 mp4 → concat → ASS 烧字幕，产出 `draft.mp4` |
| 是否生成真 MP4（含真音轨） | **是**。1080×1920 / 30fps / H.264 + AAC / **163.01 秒** / **3.97 MB**，音轨来自本机 SAPI 实时合成的 5 个 wav || M2.0 是否被破坏 | 否。全量 `pytest` → **268 passed, 0 failed**；未改真实数据库、未开 WAL、未 commit |

---

## 1. 复用的第三方项目

| 项目 | 地址 | 许可证 | 固定版本 | 用途 | 是否改动 |
| --- | --- | --- | --- | --- | --- |
| Microsoft MarkItDown | https://github.com/microsoft/markitdown | MIT | `markitdown[pdf,docx,xlsx,pptx]` **0.1.9**（PyPI） | PDF/XLSX/CSV/HTML 等转 Markdown | 未改源码，仅作为库调用 |
| Anthropic Skills | https://github.com/anthropics/skills | Apache-2.0 | commit **`2c5958ecac7cee2b583967a2c4981d0f4a75c579`**（2026-11-06） | 借 `skills/pdf`、`skills/xlsx` 的方法学与脚本 | 未改内容，仅复制子目录 |
| Agent Skills 规范 | https://agentskills.io/specification（仓库 `agentskills/agentskills`） | 规范文本 | 按 2026-11 现行版本 | 技能目录格式：`<name>/SKILL.md` + YAML frontmatter | 未自定义扩展格式 |
| FFmpeg | https://ffmpeg.org | LGPL/GPL（发行版打包） | 本机 **4.2.2**（`D:\DeliveryOptimization\miniconda\Library\bin`） | 静帧成段、concat、libass 烧字幕、AAC 混流、ffprobe 核验 | 未改，直接调用 |
| Matplotlib | https://matplotlib.org | PSF-based | **3.11.2** | 生成 1080×1920 数据图与文字卡 | 未改 |

**引入方式与依据**：`git clone --depth 1` 后按目录复制，未使用 GitHub API（403 限流）；只取 `pdf`（12 文件 70 KB）与 `xlsx`（53 文件 1.1 MB），跳过 `docx`/`pptx`（各约 1.16 MB，本轮不做 Office 成品）；同时复制其 `THIRD_PARTY_NOTICES.md`。出处、许可证、commit、未引入项、体积、依赖、使用方式、安全边界、额外运行资源全部记录在 `skills/vendor/anthropic/PROVENANCE.md`。

**未引入（按任务书要求）**：Docling（MarkItDown 在本轮的真实文件上够用，表格与中文均正常）、Crawl4AI（本轮零联网抓取）、WhisperX（不做语音识别，音轨来自本地 TTS）。

**已知限制（诚实记录）**：Anthropic `xlsx` 技能里的 `recalc.py`、`soffice.py` 依赖 **LibreOffice**，本机未安装，本轮**未执行**这两个脚本；`pdf` 技能的脚本依赖 `pdfplumber`/`pypdf`，已随 `markitdown[pdf]` 一并安装，具备运行条件，但本轮实际解析走的是 MarkItDown 主路径（更简单，且被 ToolRegistry 统一封装）。

---

## 2. 技能：哪些是直接复用，哪些是 AK 自有

| 类别 | 位置 | 内容 | 说明 |
| --- | --- | --- | --- |
| 直接复用 | `skills/vendor/anthropic/pdf/`、`xlsx/` | 官方 SKILL.md 与脚本 | 未改一字，Apache-2.0，出处见 PROVENANCE.md |
| AK 自有 | `skills/ak/accessible-explainer/SKILL.md` | 四层解释结构（现象→机制→数字→边界）、类比必须成对交付 `holds`/`breaks_down` + 落地数字、可视化"一张图一件事"、6 条失败自检 | 从"通俗表达、生活化类比"提炼为可判定条款，未复制任何博主文案 |
| AK 自有 | `skills/ak/investigative-storytelling/SKILL.md` | 研究四件套（时间线/角色/资金与风险/利益冲突）、E1–E4 证据分级与**跨级禁用**、五幕故事结构、引用规则 | 依据"深度研究、故事结构、关键人物、商业逻辑" |
| AK 自有 | `skills/ak/ak-signature/SKILL.md` | 一句话标准、六条风格硬要求、开头 8 秒规则、3–5 分钟结构配方、语言习惯、合规红线、8 条定稿自检 | 依据"观点鲜明、犀利、有趣 + 研究深度、事实准确" |

`skills/README.md` 划了两类技能的边界与使用纪律；**未**做观众研究、内容策略体系、也**未**模仿任何博主的专有视觉资产或口头禅。

---

## 3. 依赖安装情况

| 安装 | 版本 | 方式 | 冲突 |
| --- | --- | --- | --- |
| markitdown[pdf,docx,xlsx,pptx] | 0.1.9 | `pip install` 进现有 `.venv` | 无；连带 pdfminer.six / pdfplumber / pypdfium2 / magika 1.0.3 / onnxruntime 1.31.1 / numpy 2.5.4 / pandas 3.1.1 / openpyxl 3.1.5 / python-pptx 1.0.2 / mammoth / lxml / Pillow 13 |
| matplotlib | 3.11.2 | `pip install` | 无 |
| 安装后回归 | — | `pytest` | **253 passed**（当时 M2.0 基线） |

未安装 Docling / Crawl4AI / WhisperX（任务书要求单独批准）；未触碰 `httpx2` 之外的既有依赖；未改 Secret。

---

## 4. 真实文件解析结果（可复核）

| 编号 | 文件 | 类型 | 等级 | 字节 | 转出字符 | 页数 | sha256（前 16 位） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | 日本国债收益率数据.csv（541 交易日） | CSV | E1 | 28,393 | 29,490 | — | `33f9ae4774a99e87` |
| S02 | 黄金价格数据.csv（519 交易日） | CSV | E1 | 35,134 | 36,187 | — | `38a3d67a99da8b4c` |
| S03 | 国际新闻_2026-03-23.xlsx（10 条含 URL） | XLSX | E3 | 7,179 | 1,648 | — | `4ed82471ad972f96` |
| S04 | 8.6-8.7椭圆与直线【基础】(解析).pdf | PDF | E0 | 1,184,534 | 143,465 | 36 | `80d31d166e631bf9` |

- 全部通过 `ToolRegistry.dispatch()` 经 **`parse_xlsx` / `parse_pdf`** 工具真实解析，产物落在 `source_text/`，元数据汇总在 `source_register.csv`（含 sha256、字节数、字符数、页数、parser 版本、用途）。
- **S04 用途声明**：与本主题无关，**只用来证明 PDF 解析链路可用**，在 `source_register.csv` 中标为 E0，研究笔记中明确"不作为任何事实依据"。
- **S03 用途声明**：二手转述（E3），且时间在数据窗口之后，只作背景，不作因果证据。

---

## 5. 工具层改动（增量，不推倒）

| 项 | 之前 | 现在 |
| --- | --- | --- |
| `parse_pdf` / `parse_xlsx` | `implemented=False`，调用恒返回 `not_implemented` | 由 `backend/app/capabilities/adapters/markitdown_adapter.py` 真实实现（`implemented=True`） |
| 其余 8 个工具 | `not_implemented` | **保持 `not_implemented`**（未假装实现） |
| 注册方式 | 无适配器 | `get_tool_registry()` 首次创建时懒加载并注册，避免循环导入 |
| 权限链路 | guard 校验白名单 + db scope | 不变；`agent_caller()` 用 `CallerContext.for_executor(...)` 走同一套闸门 |

新增测试：`tests/test_markitdown_adapter.py`（16 例）+ `tests/test_tool_registry.py` 增 3 例（声明集合与实际注册一致、默认注册表可达适配器、其余工具仍 `not_implemented`）。

---

## 6. 视频流程实跑

命令（工作目录 `C:\Users\25803\finance-media-ai`，需 `PYTHONPATH=.`）：

```
.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage collect
... --stage charts
... --stage audio
... --stage render
... --stage probe
```

| 阶段 | 做了什么 | 结果 |
| --- | --- | --- |
| `collect` | 读 `sources.json` → 工具派发 → MarkItDown 解析 → 写 `source_text/` + `source_register.csv` | 4 源成功，共 211,000+ 字符 |
| `charts` | Matplotlib 生成 3 张 1080×1920 深色图 + `_work/chart_stats.json` | JGB 0.628→2.079（×3.31，峰值 2.084 @2025-12-22）；金价 2,063.80→4,315.09（+109.1%） |
| `audio` | PowerShell SAPI（**Microsoft Huihui Desktop**，本地、零成本）逐场景合成 wav + ffprobe 量时长 | 5 段共 **162.92 秒**（29.78 / 34.39 / 20.38 / 42.79 / 35.58） |
| `render` | 静帧+音轨成段 → concat → ASS 烧字幕 → AAC 混流 | `draft.mp4`，`subtitles_burned=true`，5 段 |
| `probe` | ffprobe 核验 | 1080×1920 / 30fps / H.264 + AAC 44.1kHz 双声道 / **163.01 秒** / **3,968,663 字节** |

### 渲染中修掉的三个真问题（记录在案）

**问题 1：SRT 字幕被 libass 整体放大并跑到画面上半部。**
首版用 **SRT + `force_style`** 烧字幕，实测字幕被放大到约 107px 并跑到画面上半部——原因是 SRT 没有 `PlayRes`，libass 按默认 384×288 脚本尺寸把字号与 `MarginV` 整体放大（1920/288 ≈ 6.67 倍）。
修法：改为**直接生成 ASS**，显式声明 `PlayResX/PlayResY = 1080×1920`，`FontSize=54`、`MarginV=300`、`BorderStyle=3`；同时**卡片场景不再叠字幕**（卡片大字本身就是 caption，叠加会重影）。修复后逐帧抽检确认：字幕落在底部安全区，不遮挡图表正文与数据来源行。

**问题 2：每段比音轨长 1.9 秒，画面与口播逐段错位。**
只靠 `-shortest` 收尾时，`-loop 1` 的静帧输入会让每段比音轨长约 **1.92 秒**（实测 5 段：31.70/36.30/22.30/44.73/37.50 秒 vs 音轨 29.78/34.39/20.38/42.79/35.58 秒），成片被撑到 **172.57 秒**、末段有近 10 秒无人声。
修法：按 `audio_durations.json` 里 ffprobe 量出的真实音轨时长，给每段加 `-t <seconds>` 硬切。修复后逐段时长与音轨差 ≤0.03 秒，成片 **163.01 秒**，且 `volumedetect` 显示 mean −26.8 dB / max −3.5 dB（确为真实人声，非静音轨）。

**问题 3：卡片文案横向溢出画布。**
46pt 字号下单个汉字约 64px，17 字 ≈ 1,088px > 1,080px，且 matplotlib `figure.text(..., wrap=True)` **不会**按画布宽度断行，结果两端被裁掉（实测场景 5 首尾都缺字）。
修法：写 `_wrap_caption()` 按 11 字/行手动折行（先用空格/全角空格切意群，再按长度硬折），行数 ≥3 时降到 38pt，`linespacing=1.7`。修复后两张卡片均完整落在安全区内。

---

## 7. 产物清单（`content/jobs/20261110_jgb_gold_repricing/`）

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| `sources.json` | 1,376 B | 四源登记（含等级与用途） |
| `source_text/raw/` | 4 文件 | 原始文件只读副本（未改动） |
| `source_text/*.md` | 4 文件 | MarkItDown 转换结果 |
| `source_register.csv` | 1,501 B | 数据来源台账（sha256/字符数/页数/parser/用途） |
| `research_notes.md` | 4,919 B | 研究笔记：判断、时间线、角色、数据表、机制、反方证据、失效条件、未核实清单 |
| `fact_check.md` | 3,583 B | C1–C11 逐条核查 + 三条限定 + 合规检查 + 留白 |
| `script.md` | 3,698 B | 五场景口播稿（含自检注释与类比三件套注释块） |
| `storyboard.csv` | 2,380 B | 5 场景分镜（画面类型/素材/口播/字幕/目标时长） |
| `assets/*.png` | 3 图 + 2 卡 | 1080×1920 |
| `audio/S01–S05.wav` | 5 文件共 7.2 MB | SAPI 合成音轨 |
| `subtitles.ass` / `subtitles.srt` | 877 B / 257 B | 图表场景字幕（卡片场景不叠字幕） |
| **`draft.mp4`** | **3,968,663 B** | **成片草稿 163.01 秒** |
| `_work/` | — | 分段、concat 列表、时长表、timeline、核验帧 |

---

## 8. 当前阻塞与最少人工输入

| 阻塞 | 现状 | 需要人做什么（最少） |
| --- | --- | --- |
| 配音不是真人 | 本机 SAPI 中文女声，机械感明显、断句生硬 | 提供真人录音（或授权使用特定 TTS 音色），替换 `audio/*.wav` 后重跑 `render` 即可 |
| 画面是静帧数据图 | 3 张图表 + 2 张文字卡，无实拍、无 B-roll、无动画 | 提供出镜素材或授权补充画面；本轮**未**做转场与动效 |
| 无 BGM / 音效 | 未加 | 提供可商用音源 |
| 字幕只有图表场景 | 卡片场景靠画面大字，无独立字幕轨 | 若平台要求全程字幕，改一行开关即可 |
| 内容未经人工终审 | 画面角标固定为「草稿 · 未经人工终审」 | 人工终审 + 事实复核后去标 |
| 时长 163.01 秒（2 分 43 秒） | 落在 2.5–3 分钟目标区间内，但几乎没有余量 | 若要压到 2 分半，删减场景 4 的一半即可（改 `storyboard.csv` 后重跑 audio/render） |

**本轮明确没有做的事**：没有伪造音轨、没有把静音视频说成"成片"、没有用付费模型 API、没有联网抓取、没有部署、没有提交代码。

---

## 9. 验收对照（任务书七条）

| # | 要求 | 状态 | 证据 |
| --- | --- | --- | --- |
| ① | 至少一份真实 PDF 能解析 | ✅ | S04：36 页 / 143,465 字符 |
| ② | 至少一个真实 Excel/CSV 能读 | ✅ | S03 XLSX 10 条；S01/S02 CSV 各 500+ 行 |
| ③ | 转换结果能被研究流程使用 | ✅ | `research_notes.md` 与 `fact_check.md` 逐条引用 S01–S04 |
| ④ | 生成带来源的研究笔记 | ✅ | `research_notes.md`（含来源编号、单位、时点、未核实清单） |
| ⑤ | 生成符合 AK 风格的脚本与分镜 | ✅ | `script.md` + `storyboard.csv`，依据 `skills/ak/*` |
| ⑥ | 能调本地视频工具实际渲染 | ✅ | ffmpeg 5 段 → concat → ASS 烧字幕 |
| ⑦ | 有配音则输出真 MP4，否则说明阻塞 | ✅ | `draft.mp4` 163.01s，H.264+AAC，音轨为本地 TTS 真实合成（mean −26.8 dB） |

---

## 10. 回归与安全边界

- 全量 `pytest`：**268 passed, 0 failed**（`-p no:warnings -o addopts=""`）。
- 真实开发数据库：**未改动**——只读核验 `sha256=2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`（与 M2.0 整改轮基线一致）、`size=540672`、19 张表、`revision=de8148733a9b`、`journal_mode=delete`、无 `-wal`/`-shm` 附属文件；未迁移、未开 WAL、未写任何行。
- Git：**未 commit、未 push**；M2.0 的全部修改与未跟踪文件保持原样。
- 网络：仅两次只读访问 GitHub（clone anthropic/skills、PyPI 装包），**未做任何业务数据抓取**。

---

## 11. 复现命令

```powershell
cd C:\Users\25803\finance-media-ai
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage collect
.\.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage charts
.\.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage audio
.\.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage render
.\.venv\Scripts\python.exe backend\scripts\build_video_job.py --job 20261110_jgb_gold_repricing --stage probe
```
