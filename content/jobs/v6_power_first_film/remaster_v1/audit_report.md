# 现有系统审计（remaster_v1）

> 目的：在动手重制之前，把"现在到底有什么、能用什么、必须换什么"钉成一张表。
> 依据是**实测**（跑命令、看代码、看产物），不是上几轮的叙述。
> 审计时间：2026-11-16。仓库 `D:\finance-media-ai`，HEAD `9551ddf`，工作区干净。

## 1. 一句话结论

内容侧（研究 → 事实核查 → 人工确认 → 脚本）是**真的、可用的**；
画面侧现在只有"把文字和数字做成画板"这一条路，**没有视觉导演，没有制作蓝图，
没有三维，也没有作品质量评审**。所以这次要换的是画面生产这一段，不是推翻整个项目。

## 2. 逐模块结论

| 模块 | 实测现状 | 结论 |
| --- | --- | --- |
| 研究与事实核查 | 报告 #2（8? 见下）`research_reports` id=2；6 条结论、6 条 `finding_checks`；人工确认 3 条、`needs_more_evidence` 3 条；`draft-readiness.allowed=true` | **保留**。重制只能引用这 3 条已确认结论里的数字 |
| 内容脚本 | `scripts` id=3，`generated_by=model`，版本 3，状态 `approved`，6 句 / 158 字，花费 ¥0.016648 | **保留骨架、允许重写**：重制版可以改开头与顺序，但每句仍要能落到已确认结论 |
| 分镜系统 | `content/jobs/v6_power_first_film/storyboard/storyboard.csv`，8 镜 45.1 秒；字段齐（时间/目的/旁白/画面/素材/来源） | **保留格式**（它是可校验的接口），但**内容全部重做** |
| 视觉导演 | 不存在。现在是 `outline.py` / `script_board.py` 按规则挑"数据卡 / 整句大字" | **新建**。这是本次的核心缺口 |
| 画板与动画 | `cards.py`（数据卡、整句大字）、`kinetic.py`（数字滚动、对比条、机制图）；都编译成 OpenKatana 组合文档 | **降级为素材之一**：OpenKatana 样式白名单只认 `background-color` 等少数属性，没有渐变、没有图片、没有 CSS 动画、文字块还不许缩放——做不出电影感 |
| 三维 | **没有**。仓库里没有 three.js / WebGL / Blender | **新建**（three.js，走浏览器 WebGL，不装 Blender） |
| 配音 | `narration.py` + edge-tts，实测 8 段 `zh-CN-YunxiNeural`（`.mp3`），有词级时间与 SRT | **保留**，但要重新选音色、调速、重配（现有 8 段是给旧分镜写的） |
| 音乐 | `bgm.py` 是"曲目登记 + 授权检查"的框架，**`config/bgm_library.yaml` 不存在**，库里 0 首曲子 | **新建**：程序化生成原创配乐（可复现、零版权风险） |
| 音效 | **没有**（`sound_effect` 列恒为 `none`） | **新建**：程序化音效（whoosh / impact / tick / riser） |
| 合成与封装 | `render.py`（1024 行）：分镜 → 时间轴 → OpenKatana 渲染 → ffmpeg 拼接 → 技术 QC | **保留编排思路**，新增一条"电影感引擎"通路（逐帧 DOM/WebGL 截图 → ffmpeg） |
| 技术 QC | `content_qc.py`（445 行）+ `scan_media`：分辨率/帧率/编码/黑帧/冻结/静音/响度/音画等长；本轮实测 7 项全过 | **保留并扩充**：加"关键帧导出""单镜可重渲""响度 −16 LUFS" |
| 作品质量评审 | **不存在**。只有技术 QC | **新建**：六维评分（视觉 25 / 知识 25 / 叙事 20 / 视听 15 / 品牌 15）+ 逐镜理由 + 一票否决项 |
| 素材与授权 | `assets_manifest.csv` + `job_paths.ApprovedSources` 白名单（可商用 + 许可记录两条都要满足）；本片 8 块画板全是程序生成 | **保留**；本次新增的 3D/2D 资产一律"程序生成（原创）"，并在清单里登记 |
| 测试 | 全量 1575 passed / 2 skipped | **保留**；新增引擎与蓝图必须有测试 |
| Git | HEAD `9551ddf`，`origin/main` 一致，工作区干净 | **保留纪律**：每阶段一提交，提交后推送 |

## 3. 为什么"换引擎"而不是"继续加模板"

OpenKatana 的画板契约（`tools/openkatana/docs/COMPOSITIONS.md`）限定了：

- 样式只认白名单属性（`background-color`、`filter`、`position`…），**没有 `background-image`、没有渐变**；
- **不能跑 CSS 动画**，只能写 `tracks`（`opacity/reveal/translateX/translateY/rotate/scale`）；
- 文字块**不许跨整片缩放**（会触发 `Composition is not deterministic when seeking backwards`）。

也就是说：镜头里每个元素最多只能"淡入/位移/缩放"，没有灯光、没有材质、没有粒子、
没有真正的摄影机，更做不出"电力从发电端流到用户"这种空间叙事。继续在它上面堆模板，
天花板就摆在那里。

## 4. 环境实测（重制可用）

| 项 | 实测 |
| --- | --- |
| Python | 3.12.10（`.venv`） |
| Node | v24.14.0（本次不用它做渲染，只留作备选） |
| Chromium | `D:\playwright_browsers\chromium-1243\chrome-win64\chrome.exe`（Playwright 驱动） |
| Playwright | 在 `tools\openkatana\.venv`（项目 `.venv` 里没有） |
| FFmpeg | 项目自带 8.1.2（`tools\ffmpeg\bin`，不动它） |
| three.js | 本轮新增 `tools/vendor/three.min.js`（r147 UMD，607,784 B，sha256 `f34446bf…bc95`，MIT） |
| 磁盘 | D 盘可用 49.1 GB |

## 5. 重制范围与非目标

**做**：视觉导演、制作蓝图、电影感引擎（DOM/Canvas/WebGL 逐帧）、三维场景、程序化配乐与音效、
15–20 秒精品片段、整条视频重制、技术 QC + 作品质量评审、系统沉淀。

**不做**：不装 Blender（改用浏览器 WebGL，避免 GB 级依赖）、不买任何服务、不做通用视频平台、
不改真实数据库、不动 FFmpeg 8.1.2、不自动发布。
