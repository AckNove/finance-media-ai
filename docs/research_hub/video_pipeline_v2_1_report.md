# 视频生产 V2.1 交付报告：OpenKatana 接入与引擎能力演示

- 报告日期：2026-11-11
- 本轮任务：`视频生产 V2.1`（把「静态图表 + 文字卡 + SAPI + FFmpeg 拼接」升级为可支撑分镜、动态视觉、剪辑的工程链路）
- 本轮产物：`content/jobs/video_engine_demo/`（引擎能力演示，**不是财经内容**）
- Git：**未 commit、未 push**（M2.0 成果原样保留在工作区）
- 数据库：**未迁移、未开 WAL、未写入**（`data/finance_media.db` 只读核验，见 §11）

## 0. 一页纸结论

| 问题 | 结论 |
|---|---|
| 旧数据隔离做到了吗 | 做到了。新任务目录里没有任何旧任务/桌面/下载文件；旧任务 `20261110_jgb_gold_repricing` 未被读取、未被引用 |
| OpenKatana 接进来了吗 | 接进来了，v0.1.3 固定 commit，独立 venv，`doctor` 显示 `ready_to_render: true`，75 个工具可用 |
| 官方示例渲染成功了吗 | **没有**。示例需要 ffmpeg ≥5.1，本机是 4.2.2（`-fps_mode` 不支持）。已如实记录，未安装新 ffmpeg |
| 我们自己出片了吗 | 出了。`output/preview_20_30s.mp4`，26.022 秒，1080×1920@30，h264+aac，QC 9 项通过 |
| 是真剪辑还是拼图 | 真时间轴：6 个镜头、5 种表达类型、3 个动态场景、4 类运镜、5 种转场，全部由 OpenKatana 时间轴驱动 |
| 有配音吗 | **没有**。本片只有本地合成配乐，没有旁白；SAPI 只做过技术验证，未冒充正式配音 |
| 花钱了吗 | 没花钱。零云端调用、零模型 API、零付费素材，模型选型只做文档研究 |
| 现在的短板 | 官方示例跑不通（ffmpeg 版本）、没有中文 TTS、没有真实素材检索、响度未归一、磁盘占用大 |

## 1. 旧数据隔离措施（最高优先级）

**做了什么**

- 新建独立任务目录 `content/jobs/video_engine_demo/`，目录骨架为
  `input/approved_sources/`、`raw/`、`extracted/`、`evidence/`、`research/`、`script/`、`storyboard/`、
  `assets/`、`audio/`、`project/`、`output/`、`qc/`。
- 本轮**没有**向 `input/approved_sources/` 放任何文件——因为本轮没有批准任何财经数据源。
  因此 `research/`、`evidence/`、`storyboard/` 为空是**设计结果**，不是遗漏。
- 全程**未读取**以下任何路径：`content/jobs/20261110_jgb_gold_repricing/`（旧任务）、桌面/下载目录里的
  旧 CSV/XLSX/PDF、旧报告 `first_video_pipeline_report.md` 中的数据、旧 MP4、旧图表。
  特别说明：我只是**知道**旧任务存在，没有打开它的 `source_text/`、`research_notes.md`、`fact_check.md`、
  `script.md`、`storyboard.csv`、图表或视频。
- 演示片里的"数字"只有两类：**六个镜头的时长**（3.5/4/5/5/4/4.5 秒，用来演示柱状动画）与
  **工具数量 75**（OpenKatana 实测）。二者都是本任务自身产生的元数据，不是市场数据，
  片内已标"示意数据 · 引擎能力演示"。
- 素材全部为 B 类原创（程序生成的画板与配乐）+ 字体（NotoSansSC，OFL-1.1）。
  **没有 A 类真实素材，没有 C 类生成素材冒充真实场景。** 明细见 `assets_manifest.csv`。

**路径白名单（代码层，不只是纪律）**

新增 `backend/app/core/job_paths.py`：

- `ApprovedSources(job_root)`：只接受 `input/approved_sources/` 目录内的文件。
- `register(path, ...)`：登记时计算 SHA-256，并记录 13 个溯源字段
  （`source_id`、`source_name`、`original_filename`、`publisher`、`source_url`、`published_at`、
  `data_as_of`、`retrieved_at`、`unit`、`methodology`、`original_file_hash`、`verification_status`、`byte_size`）。
- `resolve(path)`：绝对路径规范化 → 越界（`..`）拒绝 → 符号链接拒绝 → 未登记拒绝 → 空文件拒绝 →
  登记后内容变化（SHA-256 不符）拒绝；每类拒绝有稳定原因码
  （`outside_job` / `traversal` / `symlink` / `not_registered` / `not_a_file` / `missing` / `empty_file` / `invalid_status`）。
- `write_register()`：把登记表写成 `source_register.csv`，列序固定。

测试：`tests/test_job_paths.py`（13 个用例，其中 1 个跳过——Windows 普通权限无法创建符号链接，
所以符号链接拒绝路径只在支持的系统上验证）。全量测试见 §11。

## 2. OpenKatana：版本、许可证、引入方式

- 仓库：https://github.com/rk0neki/OpenKatana
- 版本：**v0.1.3**，对应 commit **`f3b09107a9828ef770f5bf63116cd418197c15c2`**（`git clone --depth 1 --branch v0.1.3`）
- 许可证：**Apache-2.0**（`LICENSE`，11357 字节）；`NOTICE` 说明内含第三方软件/字体/人脸检测模型，
  其中 MIT 部分保留在 `licenses/OpenKatana-MIT.txt`
- 安全边界（引自其 `SECURITY.md`，已按此执行）：
  - 定位为**单用户本地机**工具，loopback 端口不得暴露公网；
  - 授权 JS / Blender 脚本属**可执行代码**，使用前需审阅；
  - 浏览器侧网络限制不是操作系统级沙箱。
- 位置：`tools/openkatana/`（**独立工具目录，没有塞进 `backend/` 包**）
- 依赖声明（`pyproject.toml`）：requires-python ≥3.11；运行时依赖
  `numpy`、`Pillow`、`playwright`、`comfy-sdk`；可选组 `text`（`uharfbuzz`+`fonttools`）、
  `vision`（`opencv-python-headless`）、`mcp`、`transcribe`、`local-comfy`、`hosted`。
  **不含 torch，不下载模型权重。**
- 我读并遵守了它的 `AGENTS.md`：不改无关 checkout、保留上游版权与许可、记录精确来源与哈希、
  **拒绝不支持的 timeline 字段而不是静默丢弃**、默认本地执行无云回退、不自动下载模型。

## 3. 实际安装环境

| 项 | 实测值 |
|---|---|
| 仓库 Python | 3.12.10（`.venv`，M2.0 用的那个，**未被改动**） |
| OpenKatana Python | 3.12（独立虚拟环境 `tools/openkatana/.venv`，由官方 `scripts/setup.py` 创建） |
| Python 启动器 | `py -3.12` |
| ffmpeg / ffprobe | **4.2.2**，`D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.EXE`（系统已有，未改动） |
| 备用 ffmpeg | `Trae CN` 自带 **6.1.1**，但是精简构建（无 aac 编码器、滤镜仅 14 个），**未采用** |
| 浏览器 | 系统 Chrome `C:\Program Files\Google\Chrome\Application\chrome.exe`（OpenKatana 用 Playwright 驱动它渲染画板） |
| Node | v24.14.0（未使用） |
| git | 2.53.0.windows.2 |

`scripts/setup.py` 实测产出：

- `tool_count: 75`、`missing_runtime: []`、`core_runtime_ready: true`、`agent_connected: false`
- 技能文件装到 `projects/my-first-film/.agents/skills/openkatana-video/SKILL.md` 与 `references/effects-playbook.md`

`openkatana doctor` 实测输出（节选）：

```json
{"version": "0.1.3",
 "ffmpeg": "D:\\DeliveryOptimization\\miniconda\\Library\\bin\\ffmpeg.EXE",
 "ffprobe": "...\\ffprobe.EXE",
 "chrome": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
 "dependencies": {"comfy-sdk": "0.5.0", "playwright": "1.65.0", "Pillow": "12.3.0",
                  "numpy": "2.5.4", "opencv-python-headless": "4.14.0.94", "mcp": null},
 "missing": [], "optional_missing": ["mcp"],
 "ready_to_render": true, "comfy_live_connection": "not_tested"}
```

**安装范围守住了**：没有装 ComfyUI、没有下载模型权重、没有装 Blender、没有装付费视频模型、
没有调云端生成、没有改系统 Python 或系统 ffmpeg、没有动仓库 `.venv`。

## 4. 实际运行测试（按发生顺序，含失败）

| # | 操作 | 结果 |
|---|---|---|
| 1 | `openkatana doctor` | 通过，`ready_to_render: true` |
| 2 | 官方示例 `examples/quick-edit/render.py` | **失败**：`RuntimeError: ffmpeg failed: Unrecognized option 'fps_mode'`（`openkatana/render.py:325` 需要 ffmpeg ≥5.1） |
| 3 | `composition init` + `composition render`（540×960，30 帧） | 通过，产出 30 张 PNG + `video.mp4`（1.0 秒） |
| 4 | `create_project` | 通过，项目落在 `content/jobs/video_engine_demo/project/video_engine_demo/` |
| 5 | `save_composition` + `render_composition` ×6 | 通过（中途 4 次契约报错，见下） |
| 6 | `synthesize_score`（26 秒，48 kHz） | 通过，`credits_used: 0` |
| 7 | `save_timeline` / `validate_timeline` / `preview_timeline` | 通过 |
| 8 | `start_render` / `get_render` | 通过，产出 `video.mp4`（26.022 秒） |
| 9 | 本机 ffmpeg 校验 + QC 脚本 | 通过（第二次出片后 9 项全过） |

**真实踩到并修掉的 6 个问题**（都是"只有真跑才会遇到"的）：

1. `composition` 文档内的 `name` 字段会被契约拒绝（未知字段一律报错）→ 文档顶层只留
   `canvas/fonts/root/schema/tracks`。
2. `easing` 只接受 `linear|smooth|step`（我先写了 `out3`/`outback`）→ 全部改 `smooth`。
3. 关键帧必须落在 `0..frames-1` → 加 `clamp_tracks()` 统一钳制去重。
4. `save_composition` 传 `revision=null` 是**仅新建**，文件已存在会报
   `Composition changed; reload before saving` → 改成"先试建，失败取 revision 再存"。
5. `pcm_s16le` 不能封装进 MP4 → 配乐先用本机 ffmpeg 转 AAC（`audio/score.m4a`）。
6. `start_render` 的 `request_id` 绑定时间轴 revision，复用旧 ID 会报
   `This request ID belongs to an earlier timeline` → 改为 `demo-preview-<revision[:8]>`。

另有两个契约细节值得未来的人知道：转场的 `dir` 词表**因类型而异**（`push` 认 `left/right/up/down`，
`wipe` 认 `rtl/ltr/ttb/btt`）；`pan.dx` 的单位是**每秒多少屏宽**（写成 0.05 会在 5 秒里移 0.25 屏，
把画面内容顶出安全区——本轮已被成片抽帧抓到并改回 0.012）。

## 5. 官方示例渲染结果（如实报告：失败）

- 命令：`tools\openkatana\.venv\Scripts\python.exe examples/quick-edit/render.py`
- 结果：**失败**，未产出 MP4。
- 原因：示例的时间轴准备阶段固定使用 `-fps_mode passthrough`，需要 ffmpeg ≥5.1；本机为 4.2.2。
- 我没有为此安装新 ffmpeg（超出本轮授权）。**替代验证**：用 OpenKatana 自己的渲染链路
  （`composition render` → `render_composition` → `start_render`）在本机 4.2.2 上完整出片成功，
  说明"图片帧 + 音频"这条主路径不受影响；受影响的只有**导入视频素材**（那是示例用到的）
  和任何依赖 `-fps_mode` 的合成路径。
- 结论：**示例没跑通，但引擎本身可用**。这条限制必须写进后续选型：若要做"真实素材 + 视频片段"的剪辑，
  需要先申请安装 ffmpeg ≥5.1（或改用带 libass/aac 的完整构建）。

## 6. Harness 能否经本地 CLI 调用 OpenKatana（成功）

- 调用方式：`python -m openkatana call <tool> --workspace <project_workspace> --arguments <json>`
  （CLI 与 MCP 共享同一套操作；本轮**未安装 MCP**，也不需要）。
- 实际跑通的工具：`doctor`、`create_project`、`save_composition`、`inspect_composition`、
  `render_composition`、`synthesize_score`、`save_timeline`、`validate_timeline`、`preview_timeline`、
  `start_render`、`get_render`、`get_project`。
- 全部调用都由 `content/jobs/video_engine_demo/script/build_demo.py` 与 `qc_demo.py` 发起，
  即 **Harness 侧脚本 → 本地 CLI → OpenKatana → 本机 ffmpeg**，全程无网络。
- 一个必须记住的坑：参数 JSON **不能带 BOM**。PowerShell 的 `Out-File -Encoding utf8` 会写 BOM，
  OpenKatana 直接报 `JSONDecodeError: Unexpected UTF-8 BOM`；改用无 BOM 的 UTF-8 写入即可。

## 7. 动态视觉能力验证

成片 `output/preview_20_30s.mp4`：**26.022 秒 / 1080×1920 / 30 fps / 780 帧**。

| 镜头 | 时间 | 职责 | 表达类型 | 动态手段 |
|---|---|---|---|---|
| S1 悬念页 | 0.0–3.5s | 立刻说明"这是什么" | 排版型 | 竖线生长 + 标题 reveal + 缓慢推近 |
| S2 数字页 | 3.5–7.5s | 单个数字独占一屏 | 数据强调型 | 数字 mask reveal + 光斑呼吸 + 强调线扫过 |
| S3 图表页 | 7.5–12.5s | 证明"数据能画" | 图表型 | 六根柱依次从基线生长 + 数值/标签延迟浮现 + 横移 |
| S4 结构页 | 12.5–17.5s | 证明"机制能画成链条" | 结构型 | 五节点依次落位 + 连接线逐段绘制 |
| S5 纯视觉页 | 17.5–21.5s | 证明"没有文字也成立" | 氛围型 | 双光斑反向缩放 + 持续推镜（1.22×） |
| S6 收束页 | 21.5–26.0s | 署名收尾 | 排版型 | 品牌行 reveal + 分隔线生长 + 极轻手持 |

- **镜头数 6**（>任务要求的 6，且每镜职责不同，没有"同一张图反复出现"）。
- **表达类型 5 种**（排版/数据强调/图表/结构/氛围），超过"至少 3 种"。
- **真正动态场景 3 个**：S3（数据生长）、S4（链条装配）、S5（光斑 + 推镜）。
- **运镜 4 类**：`push`（推近）、`pan`（横移）、`z`（定焦）、`handheld`（手持微抖）。
- **转场 5 种且不重复**：`dissolve`、`push(left)`、`wipe(btt)`、`glitch`、`dip`。
- **动态文字 / 数字揭示 / 图表分阶段展示 / 镜头推拉 / 视觉转场 / 结构动画 / 多镜头连续叙事** 全部落在
  OpenKatana 的 `tracks`（关键帧）+ `cam`（相机）+ `transition`（双镜转场）三套机制上，
  没有用"预渲染好的整段视频拼接"作弊——每一镜都是逐帧渲染出来的。

**做不到 / 没做的**（不让读者误判）：没有 3D 场景（`scene3d` 示例未接）、没有真实素材视频导入
（受 ffmpeg 4.2.2 限制）、没有逐词字幕（`make_captions` 需要词级时间戳，本轮无旁白所以未用）。

## 8. 音频能力与限制

**本轮实际用了什么**

- 配乐：OpenKatana `synthesize_score` **本地确定性合成**（三角波低音 + 正弦高音 + 每半秒噪声脉冲），
  26 秒 / 48 kHz，`credits_used: 0`，无版权素材、无模型。
- 封装：`score.wav` → 本机 ffmpeg 转 `audio/score.m4a`（AAC 192 kbps）后进时间轴。

**没有用什么（明确声明）**

- **没有旁白音轨**。本片是纯视觉 + 配乐演示，片尾已标"示意数据 · 无配音 · 待人工审核"。
- 没有把 SAPI 机器音当正式配音。系统里确实有中文声音 **Microsoft Huihui Desktop**（零成本本地 TTS），
  上一轮 SAPI 方案只用于技术验证；本轮**没有**使用它出片。

**限制**

- 响度未归一：实测 mean −26.7 dB / max −15.5 dB，正式交付应做 −14 LUFS 归一，本轮未做（见 §12）。
- 没有中文 TTS 候选实测（不调用付费服务的前提下，只有 SAPI 与本地开源模型两条路，后者未安装）。

## 9. 技术与运行成本（实测，非估算）

| 项 | 实测 |
|---|---|
| 云端调用 | **0 次**；模型 API 费用 **0 元** |
| 六块画板渲染 | 780 帧，约 5 分钟（每帧用系统 Chrome 截图） |
| 时间轴渲染 | 780 帧，约 80 秒（`start_render` → `get_render` 轮询 19 次、每次 4 秒） |
| 单次完整重出片 | 约 6–7 分钟（画板 + 时间轴），全本机 CPU |
| 磁盘占用 | 任务目录 **1,166 MB**（其中渲染帧 1,079 MB）；OpenKatana 工具目录 **432 MB**（含独立 venv） |
| 网络 | 仅一次 `git clone` 取仓库；运行期零联网 |

盘占用是**要立刻处理的问题**：`.openkatana/jobs/*/render/frames/` 与
`.openkatana/compositions/*/frames/` 保留全部 PNG，几次重渲染就会上 GB。
建议下一步加一个 `--stage clean`，只保留最新一次 job 的帧。

## 10. 模型候选比较（**只做文档研究，未调用、未充值**）

> 说明：以下是**选型研究**，价格来自公开资料，**必须在下单前以官方价目页复核**；
> 我没有注册、没有调用、没有产生任何费用。四类各给 2 个候选。

### 10.1 主模型（写脚本、研究整理，文本）

| 候选 | 官方地址 | 价格（公开资料，待复核） | 上下文/限制 | 中文质量 | 商用授权 | 联网 | 单条 26 秒视频成本 | 本地可用 |
|---|---|---|---|---|---|---|---|---|
| DeepSeek `deepseek-chat` | platform.deepseek.com | 约 ¥1–2 / 百万输入 token、¥8–16 / 百万输出 token | 64K 上下文级别 | 强（中文母语级） | 商用需遵守其服务条款 | 是 | 约 ¥0.02–0.05（按 3–6 万 token 计） | 否（权重开放但需自建） |
| 阿里 `qwen-max`（通义千问） | help.aliyun.com/zh/model-studio | 约 ¥2.4 / 千 token 量级（按档位浮动） | 长上下文档可选 | 强 | 商用需阿里云协议 | 是 | 约 ¥0.1–0.3 | 否（另有开源 Qwen 可本地） |

补充：本项目 `config/models.yaml` 里已登记 `deepseek-chat` / `deepseek-reasoner`，**价目故意留空**
（`pricing_policy: reject_unknown`），在把真实价格填进去之前，代码层会直接拒绝所有调用——这是 M1.2 的 Q8=B 决策。

### 10.2 视觉理解（多模态，用于画面质检/参考视频分析）

| 候选 | 官方地址 | 价格（公开资料，待复核） | 限制 | 中文 | 商用 | 联网 | 成本 | 本地 |
|---|---|---|---|---|---|---|---|---|
| `qwen-vl-max` | 阿里云百炼 | 按图/按 token 计费，量级 ¥0.02–0.05/千 token | 单图分辨率上限、并发限制 | 强 | 需协议 | 是 | 每条视频抽 20 帧 ≈ ¥0.2–0.5 | 否 |
| `glm-4v`（智谱） | open.bigmodel.cn | 同类量级，按 token | 图像尺寸/张数限制 | 强 | 需协议 | 是 | 同上量级 | 否（GLM 有开源版本可本地部署） |

### 10.3 TTS（配音）

| 候选 | 官方地址 | 价格（公开资料，待复核） | 中文自然度 | 商用 | 联网 | 成本（26 秒口播约 120 字） | 本地 |
|---|---|---|---|---|---|---|---|
| 火山引擎（豆包）语音合成 | volcengine.com | 按万字符计费，常见档位 ¥x/万字符 | 高（有情感/多音色） | 需协议 | 是 | 极低（人民币分级） | 否 |
| 本地开源（如 Piper / ChatTTS 类） | 各自 GitHub | 0 元 | 中（需试听评估） | 看权重许可 | **否** | 0 元 + 算力 | **是** |

结论：本轮**无自然中文配音**是硬限制，先把链路跑通；正式内容上线前必须在"付费 TTS"与"本地开源 TTS"之间做一次试听对比。

### 10.4 视频生成（图生视频/文生视频，用于 B 类动态素材）

| 候选 | 官方地址 | 价格（公开资料，待复核） | 画质/时长限制 | 商用 | 联网 | 成本 | 本地 |
|---|---|---|---|---|---|---|---|
| 可灵（Kling） | klingai.com | 按秒/按次计费 | 通常 5–10 秒/段 | 需协议 | 是 | 每段几元量级 | 否 |
| 即梦（Seedream/Dreamina） | 即梦官网 | 按次/按积分 | 同上 | 需协议 | 是 | 同上 | 否 |
| 本地 ComfyUI + 开源模型 | github.com/comfyanonymous/ComfyUI | 0 元 | 取决于显卡 | 看模型许可 | **否** | 显卡电费 | **是**（需 GPU，本轮未装） |

结论：本轮**没有引入任何视频生成**；OpenKatana 的 ComfyUI 接口保留为未来选项（`comfy_live_connection: not_tested`）。

## 11. 实际文件清单

### 11.1 本轮新增（可交付资产）

```
content/jobs/video_engine_demo/
├── output/preview_20_30s.mp4        3,360,166 B  sha256 6efb089705481625…0b0dee
├── project/timeline.json            时间轴（6 镜、相机、转场、音频）
├── project/video_engine_demo/       OpenKatana 项目（project.json、compositions/、history/、fonts/）
├── assets/compositions/*.json       六块画板的组合文档副本（s1–s6）
├── assets_manifest.csv             素材来源/版权/用途登记
├── audio/score.wav / score.m4a      本地合成配乐（原始 + AAC）
├── audio/score_document.json        配乐合成参数（可复现）
├── qc/qc_report.md                  质检报告
├── qc/qc_metrics.json               机读质检结果
├── qc/frames/final_*.png            成片抽帧
├── visual_direction.md              视觉方向与本次迭代记录
└── script/build_demo.py             出片脚本（画板 → 渲染 → 时间轴）
    script/qc_demo.py                质检脚本
```

### 11.2 本轮新增（代码，进入仓库）

- `backend/app/core/job_paths.py`（路径白名单 + 溯源登记表）
- `tests/test_job_paths.py`（13 用例，1 skipped）

### 11.3 本轮新增（第三方工具，**未纳入仓库提交**）

- `tools/openkatana/`（v0.1.3，独立 venv；`.gitignore` 未覆盖，是否入库需单独决定）

### 11.4 上一轮（V1）保留不动

- `skills/ak/*`（三个 AK 风格技能）、`skills/vendor/anthropic/{pdf,xlsx}`、`backend/app/capabilities/adapters/markitdown_adapter.py`、
  `backend/scripts/build_video_job.py`、`docs/research_hub/first_video_pipeline_report.md`、
  `content/jobs/20261110_jgb_gold_repricing/`（旧任务，**只保留、不复用**）

## 12. 回归、数据库与 Git 状态

- 全量测试：`pytest -o addopts=""` → **279 passed, 1 skipped, 1 warning**（唯一 warning 是
  `agent_runs ↔ research_subtasks` 外键环的 SAWarning，M2.0 已判定为非阻断）。
- 真实开发库（只读核验）：19 张表、revision `de8148733a9b`、`journal_mode=delete`、
  `sha256=2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`（与 M2.0 基线一致）、
  无 `-wal`/`-shm` 附属文件。
- Git：HEAD 仍 `7ec1283`；**未 commit、未 push**；M2.0 的 43 个已跟踪修改与全部未跟踪文件原样保留。

## 13. 剩余问题（按严重度）

| # | 问题 | 影响 | 建议动作 |
|---|---|---|---|
| 1 | ffmpeg 4.2.2 不支持 `-fps_mode` | 官方示例与"导入视频素材"路径不可用 | 申请安装 ffmpeg ≥5.1（完整构建，带 libass/aac） |
| 2 | 没有自然中文配音 | 无法出正式作品 | 做一次 TTS 试听对比（付费 vs 本地开源），再决定 |
| 3 | 磁盘占用 1.1 GB/次 | 重渲染几次就爆 | 加 `--stage clean` 只保留最新 job 帧 |
| 4 | 响度未归一（max −15.5 dB） | 手机上偏轻 | 接一条 `loudnorm` 到 −14 LUFS |
| 5 | 无 A 类真实素材 | 演示片"像信息图，不像纪录片" | 下一条真实选题时走 `approved_sources` 白名单取官方图 |
| 6 | `tools/openkatana` 是否入库未定 | 仓库体积 432 MB | 建议加 `.gitignore` 排除 venv，仅记录 commit 与 setup 命令 |
| 7 | 场景自动检测在深色画面上失效 | QC 的"镜头数"只能信时间轴 | 已在 `qc_report.md` 里写明判据来源 |

## 14. 下一步建议（不含未授权动作）

1. **先解决 ffmpeg 与 TTS 两个基础设施缺口**，否则下一条内容仍然只能出"纯视觉 + 配乐"。
2. 把 `build_demo.py` 的"画板 → 时间轴"拆成 `storyboard.csv` 驱动：脚本/SKILL 只写分镜表，
   出片脚本读表渲染。这样脚本与像素才真正解耦（本轮是脚本里直接描述画板）。
3. 真实选题开跑时，`approved_sources/` 只放官方 PDF/CSV，`research_notes.md` 必须逐条挂
   `source_id`（字段已在 `job_paths.py` 定义），没有来源的数字一律不写。
4. OpenKatana 的 `analyze_reference`（60 fps 逐帧分析参考片）与 `shot_sheet` 还没用起来，
   这是"学小Lin说/巫师财经的节奏"的正确工具，下一步应该用它分析 3–5 条对标视频的动作单元。
