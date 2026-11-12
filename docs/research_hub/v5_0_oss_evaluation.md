# v5.0 开源项目评估：外采候选清单

> 本文是 `docs/research_hub/` 下的只读调研产物，不改变任何代码。
> 评估对象为 11 个指定项目 + 6 个自选补充项目，共 17 个。
> 所有字段以 2026-11-12（UTC+8）实际抓取到的仓库页面、`commits.atom`、`git ls-remote`、raw 文件与 LICENSE 原文为准；取不到的写「未取到」，未验证的写「未验证」。

---

## 0. 文档信息与方法

### 0.1 调研范围

- **必评 11 个**：MoneyPrinterTurbo、OpenCut、Remotion、OpenMontage、HyperFrames、Pixelle-Video、video-use、video-shotcraft、seedance2-skill、chengfeng-videocut-skills、chengfeng-videocut。
- **自选补充 6 个**：trafilatura、edge-tts、Apache ECharts、LiteLLM、AKShare、pdfplumber。
- 补充项的选择依据是「本项目已声明但尚未实现的缺口」（真实搜索/抓取、模型网关、TTS、动态图表、中文财经数据、财报解析），不是按热度挑选。

### 0.2 取证方式（可复核）

本轮取证使用四条互相独立的通道，避免单点错误：

1. **HEAD SHA**：`git ls-remote https://github.com/<owner>/<repo> HEAD`，取 40 位 SHA 与默认分支名。
2. **最近提交时间**：`https://github.com/<owner>/<repo>/commits.atom`，读第一个 `<entry>` 的 `<updated>`。
3. **License 名称**：仓库页内嵌 JSON 的 `"preferredFileType":"license"` 与 `"tabName"`，再对非 SPDX 的（Remotion、edge-tts、LiteLLM）直接抓 `LICENSE` 原文核对。
4. **文本与入口**：按 pin 住的 SHA 抓 `raw.githubusercontent.com/<owner>/<repo>/<sha>/README.md`，以及从仓库页 JSON 提取的顶层目录树，用于判断技术栈、入口文件名与 Windows 证据。

说明：`api.github.com` 在本机返回 **403**，因此没有使用 GitHub REST API，星数是仓库页内嵌 JSON 的 `stargazerCount` 字段，属于实时值而非缓存。`commits.atom` 的 `<updated>` 一般是提交时间（committer date），可能晚于或早于 push 时刻，本文统一按 UTC 原样记录，不换算、不推测。

### 0.3 判定口径

- **重复实现**：本项目已有同类模块，且职责边界基本重合（典型：分镜、时间轴、素材清单）。
- **补缺口**：本项目尚未实现，或只有接口/空壳。
- **Windows 可**：必须有仓库内的明确证据（`.bat` 启动脚本、Windows 安装说明、`Operating System :: Microsoft` 分类器、Windows 下载入口）。只看到「Node.js 项目、理应跨平台」一律记「未验证」。
- **采用方式**取值含义：
  - `DEPENDENCY`：作为包依赖直接被 Python/Node 代码 import。
  - `SIDECAR`：独立进程/独立运行时，通过 CLI 或 stdin-stdout 交互，不进主进程依赖树。
  - `COMPONENT`：取其源码/构建产物作为本仓库的一部分。
  - `SKILL`：纯文本能力包，进 `skills/` 由 Skill 注册表加载。
  - `REFERENCE`：只读参考设计，不引入代码或依赖。
  - `REJECTED`：明确不采用，理由写在条目内。

---

## 1. 本项目缺口与候选映射

先固定「缺口 → 候选」的对应关系，后面的条目才有落点。

| 缺口 | 现状 | 候选 | 结论走向 |
|---|---|---|---|
| 真实搜索/抓取 | 无 | trafilatura（+ 上游搜索需另选） | DEPENDENCY |
| 模型网关 | 无，`budget.py` 目前只有计价与闸门 | LiteLLM | DEPENDENCY |
| Agent 执行器 | 无 | video-use 的方法论、OpenMontage 的流水线编排 | REFERENCE |
| 动态图表与动画 | 无（`timeline.py` 只排时间轴） | ECharts + HyperFrames | DEPENDENCY / SIDECAR |
| TTS | 无 | edge-tts；MoneyPrinterTurbo 的 provider 抽象 | DEPENDENCY / REFERENCE |
| 前端工作台 | 无 | OpenCut、chengfeng-videocut Studio | REFERENCE |
| 中文财经数据 | 无 | AKShare | DEPENDENCY |
| 财报解析 | 无 | pdfplumber | DEPENDENCY |
| AI 视频提示词 | 无 | seedance2-skill | SKILL |

注意一个结构性事实：**11 个必评项目里没有一个是「财报解析」或「中文财经数据」类项目**。它们全部是视频生产/剪辑侧的工具，其中多数与本项目已有的 `storyboard.py` / `timeline.py` / `ffmpeg.py` 职责重叠。真正补缺口的反而是自选的 6 个库。这一点决定了「综合建议」的整体倾向。

---

## 2. 必评项目（11 个）

### 2.1 MoneyPrinterTurbo

- **仓库**：https://github.com/harry0703/MoneyPrinterTurbo
- **License**：MIT（仓库页 license 标签为 `MIT`，`pyproject.toml` 内 `license = { text = "MIT" }`，两处一致）
- **最近提交**：`2026-11-12T03:39:46Z`；`HEAD` = `dd096584f1642f3d52b8b2aa7c39df84bb2066bf`
- **Star**：142,665（约 14.3 万量级）
- **技术栈**：Python `>=3.11`；uv + `uv.lock`（也保留 pip 路径）；FastAPI `0.136.3` + uvicorn + Streamlit `1.66.1`；moviepy `2.2.1`；faster-whisper `1.1.0`；edge-tts `7.3.1`；litellm `1.103.1`；`imageio-ffmpeg` 自带 FFmpeg 二进制。`pyproject.toml` 版本 `1.3.10`，PyPI 包名 `mpt`（实测 latest = `1.3.10`）
- **Windows 兼容性**：**可**。README 有「Windows 一键启动包」章节（`start.bat` / `update.bat`）、仓库根有 `webui.bat`，并写明建议系统为 Windows 10 及以上
- **付费依赖**：**需要**。脚本与文案走 OpenAI / Kimi / Gemini / DeepSeek / 通义 等 LLM API；素材走 Pexels / Pixabay / Coverr（免费 key）；配音可选 Edge TTS（免 key）或 Azure / ElevenLabs / MiniMax 等付费服务。无本地大模型权重需求
- **可编程接口**：`cli.py` + `pyproject.toml` 的 `[project.scripts] mpt = "app.launcher:main"`（CLI）；`main.py`（FastAPI 服务）；`webui/Main.py`（Streamlit）
- **商业限制要点**：代码 MIT。但 `[tool.hatch.build.targets.wheel.force-include]` 把 `resource/fonts/STHeitiMedium.ttc`、`MicrosoftYaHeiBold.ttc`、`BeVietnamPro-*.ttf` 等字体打进 wheel —— **字体授权不受 MIT 覆盖**，商用再分发要单独核对
- **与现架构关系**：**重复实现**。`backend/app/media/storyboard.py`、`timeline.py`、`manifest.py` 已覆盖它「主题→脚本→分镜→素材→合成」的主干；它是整包应用而非可嵌入的库
- **采用方式**：`REFERENCE`。值得抄的是它的「多 TTS provider 统一配置」形态（一个下拉覆盖 Edge/Azure/SiliconFlow/ElevenLabs 等），以及 `config.example.toml` 的自举方式；不值得抄的是 moviepy + Streamlit 的重栈
- **固定版本**：仅作对照时可 `git clone --depth 1 https://github.com/harry0703/MoneyPrinterTurbo && git fetch origin dd096584f1642f3d52b8b2aa7c39df84bb2066bf && git checkout dd096584f1642f3d52b8b2aa7c39df84bb2066bf`；不安装

### 2.2 OpenCut

- **仓库**：https://github.com/OpenCut-app/OpenCut
- **License**：MIT（README 徽章 `license-MIT`，`LICENSE` 存在）
- **最近提交**：`2026-09-24T08:58:46Z`；`HEAD` = `e668010778568641babef2cc40be4703ae6916d6`
- **Star**：98,139（约 9.8 万量级）
- **技术栈**：Rust 核心（`Cargo.toml`、`Cargo.lock`、`crates/media/`）+ TypeScript 前端；`apps/web`、`apps/api`、`apps/desktop` 三端；包管理 `bun`（`bunfig.toml`）；任务编排用 `moon` + `proto`（`.prototools`、`moon.yml`）
- **Windows 兼容性**：**可**（开发环境）。README 的 Development 段给出 Windows PowerShell 安装 `proto` 的 `irm ... | iex` 命令，并提示必要时 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- **付费依赖**：无核心付费项；赞助方 fal.ai 属可选生成服务
- **可编程接口**：**目前没有稳定接口**。README 明说正在 ground-up 重写，Editor API、第三方插件、MCP server、headless mode 均在「coming」列表；现在可用的入口是 `moon run web:dev` / `api:dev` / `desktop:dev`
- **商业限制要点**：MIT，无附加条款。注意它当前 main 分支**不是可用产品**，经典版在 `opencut-app/opencut-classic`
- **与现架构关系**：**补缺口，但当下不可用**。它对应「前端工作台」缺口，但重写期没有 API 可接
- **采用方式**：`REFERENCE`。只记录它的「Editor API + plugin-first + Rust 单核多端」路线，作为我们未来前端工作台的形态参考；不作为依赖，也不 fork
- **固定版本**：不接入。若需跟踪，`git clone --depth 1 https://github.com/OpenCut-app/OpenCut` 后 `git checkout e668010778568641babef2cc40be4703ae6916d6`

### 2.3 Remotion

- **仓库**：https://github.com/remotion-dev/remotion
- **License**：**Remotion License（双档，非 OSI 标准许可）**。`LICENSE.md` 原文：个人、≤3 名雇员的营利组织、非营利组织可免费（含商用）；「a company license is required for for-profit organizations of a certain size」
- **最近提交**：`2026-11-11T19:23:21Z`；`HEAD` = `eeb428f095529f52d359ecc709a174eff978642d`
- **Star**：72,601（约 7.3 万量级）
- **技术栈**：Node.js + React + TypeScript；npm 包 `remotion`（当前 `4.0.543`）、`@remotion/cli`、`@remotion/renderer`；渲染依赖自带 headless Chrome 与 FFmpeg；monorepo 用 bun + turbo
- **Windows 兼容性**：**未验证**。官方安装文档的 System requirements 只列了「macOS 15 (Sequoia) 或更高」与「Linux 需 glibc ≥ 2.35，Alpine 与 nixOS 不支持」，**没有出现 Windows 字样**；本轮未取到任何 Windows 支持声明
- **付费依赖**：**需要（按主体规模）**。≤3 人营利主体可免费商用；超过则必须购买 company license。另 Lambda 渲染是 AWS 付费服务
- **可编程接口**：`npx remotion render`（`@remotion/cli`）；`@remotion/renderer` 提供 Node 侧 `renderMedia` / `renderStill`；`@remotion/player` 供浏览器内播放
- **商业限制要点**：这是本文 17 个项目里**唯一「按公司人数收费」**的许可证。对本项目这种要长期商用、且未来可能扩编的主体，等于把渲染层变成了订阅成本；另外 `LICENSE.md` 已写明 Remotion 5.0 会再改一次条款
- **与现架构关系**：**补缺口**（动态图表/动画渲染），但与 HyperFrames 功能重叠
- **采用方式**：`REJECTED`。理由有两条且都硬：①许可证随主体规模变化，商业路径不可控；②官方系统要求未覆盖 Windows，而本仓库运行在 Windows。同能力的 HyperFrames 是 Apache-2.0 且有 Windows 下载入口
- **固定版本**：不接入。若有人坚持评估，pin `remotion@4.0.543` + `@remotion/cli@4.0.543`，并先做许可证法务确认

### 2.4 OpenMontage

- **仓库**：https://github.com/OpenMontageInc/OpenMontage
- **License**：**AGPL-3.0**（`LICENSE`，README 徽章 `license-AGPLv3`）
- **最近提交**：`2026-11-09T06:23:54Z`；`HEAD` = `fecb9a1e3cc23ef84de30b272059457a427f16f8`
- **Star**：79,750（约 8 万量级）
- **技术栈**：Python `3.10+` + FFmpeg + Node.js（README 要求 22.x 的 `22.23.4+` / 24.x 的 `24.22.1+` / 26.x 的 `26.12.1+`「patched release」）+ Remotion composer（`remotion-composer/`，`npm install`）+ `piper-tts`。目录含 `pipeline_defs/`、`tools/`、`skills/`、`backlot/`、`schemas/`
- **Windows 兼容性**：**可**。README 给出 Windows PowerShell 一行的 venv + `pip install -r requirements.txt` + `cd remotion-composer; npm install` 流程，并记录了 `npm install` 报 `ERR_INVALID_ARG_TYPE` 时的绕法
- **付费依赖**：**可选**。README 明确有「zero API keys」路径（免费真实素材纪录片流程）；接图像/视频 provider 后单条成本约 `$0.15–$1.50`，全量约 `$1–$3`
- **可编程接口**：`python -m backlot open`（本地看板）；`pipeline_defs/`（流水线定义）、`tools/`（工具实现）、`scripts/`、`Makefile`
- **商业限制要点**：**AGPL-3.0**。只要我们把它的代码并入后端并以网络服务形式对外提供，就触发源码开放义务；对一个「本地优先但可能对外提供服务」的产品，这是不可接受的法律面
- **与现架构关系**：**重复实现 + 架构高度同构**。它同样有 tool registry、budget controls、quality gates、decision audit trail —— 这几样我们已经分别对应 `capabilities/tools.py`、`capabilities/budget.py`、`skills/registry.py`。差别是它把这些做成了面向 Agent 的完整流水线
- **采用方式**：`REJECTED`（代码层面不采用）。**但它的「预算闸门 + 质量门 + 决策审计」三件套值得作为设计参考**，我们已在 `budget.py` 有 `evaluate_model_call`，缺的是把决策落审计表
- **固定版本**：不接入。只读参考时 `git clone --depth 1` 后 checkout `fecb9a1e3cc23ef84de30b272059457a427f16f8`

### 2.5 HyperFrames

- **仓库**：https://github.com/heygen-com/hyperframes
- **License**：**Apache-2.0**（`LICENSE`，README 徽章 `license-Apache 2.0`）
- **最近提交**：`2026-11-12T05:02:47Z`；`HEAD` = `245904d55a916d5dc6b186dd90790c6e10a74c13`
- **Star**：83,019（约 8.3 万量级）
- **技术栈**：Node.js `>=22`（README 徽章 `node-%3E%3D22`）；npm 包 `hyperframes`（实测 `0.9.37`，bin 为 `bin/hyperframes.mjs`）；视频用 HTML + `data-start` / `data-duration` / `data-track-index` 描述，动画用 GSAP / CSS / Lottie / Three.js / Anime.js / WAAPI；渲染 = headless Chrome 逐帧 seek + FFmpeg 编码
- **Windows 兼容性**：**可**。README 直接给出 `hyperframes.studio/studio/download?os=windows` 下载入口，配图 alt 写明「free for Mac, Windows and Linux」；CLI 本身是 Node 包
- **付费依赖**：**否**。Studio 免费；Pro 计划只是可选地接入 HeyGen 的模型
- **可编程接口**：CLI `npx hyperframes`（`bin/hyperframes.mjs`，另有 `hyperframes-localize-fonts`）；`packages/` 为库；`registry/` 为可复用块（含 `data-chart` 类目）；`skills/` 为 Agent skill
- **商业限制要点**：Apache-2.0，可商用、可修改、需保留 NOTICE。注意它渲染出的成片里若用了自带字体/素材，仍受各自许可约束（仓库有 `CREDITS.md`）
- **与现架构关系**：**补缺口**（动态图表与动画）。当前 `timeline.py` 只能排「已有素材」的时间轴，无法生成「数据驱动的动态画面」；HyperFrames 恰好把「HTML 动画 → 确定性 MP4」这段补上，且产物可以直接回灌我们的 FFmpeg 合成流程
- **采用方式**：`SIDECAR`。以子进程方式调用 `npx hyperframes render`，输入是 JSON 数据 + 模板，输出是 mp4/帧序列；不进 Python 依赖树，也不改我们的 Python 运行时
- **固定版本**：`npm i hyperframes@0.9.37`（或 `npx hyperframes@0.9.37`）；若要跟源码，clone 后 checkout `245904d55a916d5dc6b186dd90790c6e10a74c13`

### 2.6 Pixelle-Video

- **仓库**：https://github.com/ATH-MaaS/Pixelle-Video （`AIDC-AI/Pixelle-Video` 会 302 到同一仓库，页面 `og:url` 已确认规范地址为 `ATH-MaaS`）
- **License**：**Apache-2.0**（`LICENSE` 存在，另有 `NOTICE`）
- **最近提交**：`2026-06-14T12:40:58Z`；`HEAD` = `848b054e4fae40dabc62ec58e960b573e83793ac`
- **Star**：29,996（约 3 万量级）
- **技术栈**：Python + uv（`pyproject.toml`、`uv.lock`）+ FFmpeg；目录 `pixelle_video/`、`api/`、`web/`、`workflows/`、`templates/`、`bgm/`；视觉生成走 ComfyUI / RunningHub 工作流，或直连 DashScope / OpenAI / Seedream / Seedance / Kling 的 HTTP API；TTS 支持 Edge-TTS、Index-TTS
- **Windows 兼容性**：**可**。README 首推「Windows 一键整合包」（`packaging/windows/`），写明「无需安装 Python、uv 或 ffmpeg」；仓库根有 `start_web.bat`
- **付费依赖**：**需要**。要么有 GPU 跑 ComfyUI（本地或 RunningHub 租用 48G 显存机），要么用付费图像/视频 API；LLM API 另计
- **可编程接口**：`api/`（HTTP 服务）、`pixelle_video/`（Python 包）、`config.example.yaml`、`start_web.bat`、`start_web.sh`
- **商业限制要点**：Apache-2.0 + NOTICE。真正要注意的是它**把 ComfyUI 工作流 JSON 和模型权重当外部依赖**，模型权重自带许可（如 Nano Banana、Seedream 的服务条款）不在 Apache-2.0 内
- **与现架构关系**：**重复实现 + 少量补缺口**。它做的是「主题→文案→配音→画面→成片」，与 `storyboard.py` + `timeline.py` + `ffmpeg.py` 主干重合；差异化在数字人口播与图生视频
- **采用方式**：`REFERENCE`。它的「原子能力可替换」（图像/视频/TTS/VLM 各自可换 provider）与我们的 capability 抽象同源，但引入它意味着引入 ComfyUI 运行时与 GPU 依赖，与本仓库「本地优先、无 GPU」的定位冲突
- **固定版本**：不接入。参考时 checkout `848b054e4fae40dabc62ec58e960b573e83793ac`

### 2.7 video-use

- **仓库**：https://github.com/browser-use/video-use
- **License**：MIT（`LICENSE`，`pyproject.toml` 存在）
- **最近提交**：`2026-10-08T22:16:59Z`；`HEAD` = `43cfc566833548093455c9d4a46bd69913b7e552`
- **Star**：30,927（约 3.1 万量级）
- **技术栈**：Python（`pyproject.toml`，`uv sync`） + FFmpeg + yt-dlp（可选）；转写依赖 **ElevenLabs Scribe API**；叠加动画可调 HyperFrames / Remotion / Manim / PIL；运行形态是 Claude Code / Codex 之类的宿主 Agent + `SKILL.md`
- **Windows 兼容性**：**未验证**。安装步骤用 `brew install ffmpeg`、`ln -sfn ... ~/.claude/skills/...`，全是 macOS/POSIX 路径；未取到 Windows 说明
- **付费依赖**：**需要**。README 明确要求 `ELEVENLABS_API_KEY`，转写按量计费；托管版 `video-use.com` 另售
- **可编程接口**：`SKILL.md`（Agent 入口）、`helpers/`（剪辑脚本）、`pyproject.toml`；没有 HTTP API
- **商业限制要点**：MIT。但它把 ElevenLabs 当硬依赖，实际商用成本与条款由 ElevenLabs 决定；自评回路里会把素材上传到 ElevenLabs，与「本地优先」有张力
- **与现架构关系**：**补缺口（方法论层面）**。它最有价值的是「不让 LLM 看视频，而是让 LLM 读结构化文本」—— 逐字稿打包成 ~12KB 的 `takes_packed.md`，需要时再出 `timeline_view` 合成图。这正是我们缺的「Agent 执行器如何理解素材」的答案
- **采用方式**：`REFERENCE`。借鉴 `Transcribe → Pack → LLM Reasons → EDL → Render → Self-Eval` 这条链与「每刀边界自检」的做法；不自建 ElevenLabs 依赖
- **固定版本**：不接入。参考时 checkout `43cfc566833548093455c9d4a46bd69913b7e552`

### 2.8 video-shotcraft

- **仓库**：https://github.com/Vincentwei1021/video-shotcraft
- **License**：**Apache-2.0**（`LICENSE`，README 徽章 `license-Apache--2.0`）
- **最近提交**：`2026-11-11T16:30:35Z`；`HEAD` = `6700db2ba80572ebc777b99d72049679fed9840c`
- **Star**：18,822（约 1.9 万量级）
- **技术栈**：Node.js + npm；FFmpeg/ffprobe；puppeteer（仅网页捕获，按需安装）；`uv`（仅 BGM 节拍分析跑一次性 Python 脚本）；**渲染依赖 Remotion**（`Remotion` 会自行下载 headless Chrome）
- **Windows 兼容性**：**未验证**。README 的 Requirements 写「Tested on macOS; Linux works with the notes below」，备注里只有 headless Linux/CI 的三个坑（`--concurrency=1`、需 `chrome-headless-shell`、remotion.media 被墙时传 `--browser-executable`），**没有 Windows 段落**
- **付费依赖**：无自有付费项；但渲染链路依赖 Remotion，间接继承 Remotion 的许可证约束
- **可编程接口**：`SKILL.md`（Agent 入口）、`references/shots/<category>/<card>.md`（124 张镜头卡）、`references/pipeline.md`、`workbench/scripts/open.mjs`（浏览器工作台）、`package.json`
- **商业限制要点**：本体 Apache-2.0，但**成片渲染走 Remotion**，所以「用 shotcraft 产出的片子」仍要过 Remotion License 那一关；这条链路是隐藏的许可证耦合，容易漏
- **与现架构关系**：**补缺口（视觉配方层）**。我们的 `storyboard.py` 只有「镜号/画面/台词」这类结构性字段，缺「镜头怎么拍」的配方；它的 124 张镜头卡 + 帧级 QA 清单是可迁移的知识资产
- **采用方式**：`REFERENCE`。迁移它的镜头分类法与 QA 检查项到我们自己的分镜 schema；不引入 Remotion 渲染链
- **固定版本**：不接入。参考时 checkout `6700db2ba80572ebc777b99d72049679fed9840c`

### 2.9 seedance2-skill

- **仓库**：https://github.com/dexhunter/seedance2-skill
- **License**：MIT（`LICENSE`，README 末尾 `[MIT](LICENSE)`）
- **最近提交**：`2026-09-19T18:18:49Z`；`HEAD` = `516284d5bab58361bfdfed3cc96cee3e837d4c44`
- **Star**：4,462（约 4.5 千量级）
- **技术栈**：**纯 Markdown**，无运行时。内容为 `SKILL.md`（英文）+ `zh/SKILL.md`（中文）+ `assets/`
- **Windows 兼容性**：**可**（就内容而言）。纯文本，无平台依赖；仅安装示例用 POSIX `cp`，Windows 手动复制即可
- **付费依赖**：**是（使用侧）**。它只是提示词写法，要真正出片得用字节跳动即梦 Seedance 2.0 服务
- **可编程接口**：无代码接口，只有 `SKILL.md` 这一份 Agent 可读的约定
- **商业限制要点**：MIT。但内容是**依据字节跳动官方文档整理的**（README 给了两个 Lark 文档链接），即梦服务的商用条款另行适用
- **与现架构关系**：**补缺口（AI 视频提示词）**。我们 `skills/registry.yaml` 目前没有视频生成类 skill
- **采用方式**：`SKILL`。整份 `zh/SKILL.md` 可直接裁剪进 `skills/`，由 `SkillRegistry.load_for` 按 task_type 加载；需在 `registry.yaml` 增加条目并注明来源与版本
- **固定版本**：`git clone --depth 1 https://github.com/dexhunter/seedance2-skill && git fetch origin 516284d5bab58361bfdfed3cc96cee3e837d4c44 && git checkout 516284d5bab58361bfdfed3cc96cee3e837d4c44`

### 2.10 chengfeng-videocut-skills

- **仓库**：https://github.com/Agentchengfeng/chengfeng-videocut-skills
- **License**：**Apache-2.0**（`LICENSE`，另有 `NOTICE.md`、`CITATION.cff`）
- **最近提交**：`2026-09-20T12:07:02Z`；`HEAD` = `a33b43e08c34fa8beeb5e78498e852f4e80975a5`
- **Star**：3,206（约 3.2 千量级）
- **技术栈**：Node.js `18+` + Git + **Codex CLI**（需支持 `codex plugin` 命令）；`bin/install.cjs` 安装器 + `installer-manifest.json` 固定 marketplaceRef/pluginRef；不包含 Runtime 本体
- **Windows 兼容性**：**未验证**。README 只声明 Node 18+/Git/Codex CLI 前提，未给 Windows 步骤；其配套 Runtime 的自动依赖准备明确写「macOS arm64」，其他平台「不在本次验证范围」
- **付费依赖**：否（纯本地安装器与技能包）
- **可编程接口**：`bin/install.cjs`（`install --dry-run` / `install` / `doctor`）、`package.json`、`installer-manifest.json`
- **商业限制要点**：Apache-2.0。它把「安装身份」写死成具体 commit（如 Plugin 内容提交 `d4580d8a...`），这种**可复现安装**的做法值得学
- **与现架构关系**：**部分重复（Skill 分发层）**。我们有 `skills/registry.yaml` + `SkillRegistry`，它是把 Skill 打包成 Codex Plugin 并做来源回执
- **采用方式**：`REFERENCE`。借鉴其「安装器只读来源、核对 commit、拒绝覆盖不明来源」的校验思路；不引入 Codex Plugin 体系
- **固定版本**：不接入。参考时 checkout `a33b43e08c34fa8beeb5e78498e852f4e80975a5`

### 2.11 chengfeng-videocut

- **仓库**：https://github.com/Agentchengfeng/chengfeng-videocut
- **License**：**Apache-2.0**（自有代码 + HyperFrames 派生代码）；**另捆绑 GSAP 3.15.0 与 MotionPathPlugin，受 GSAP Standard License 约束**（README 明示「不适用本项目的 Apache-2.0 许可」）
- **最近提交**：`2026-09-14T15:41:46Z`；`HEAD` = `e6089ef285af452f74303ec2037b46baaed77950`
- **Star**：18（两位数，成熟度极低）
- **技术栈**：Node.js `20+` 或 Bun；`install.cjs`；Studio 为本地 Web 服务，默认绑 `127.0.0.1`；`vendor/hyperframes-timeline-0.7.60`；派生自 HyperFrames `0.7.54`
- **Windows 兼容性**：**未验证**。INSTALL.md 只对 macOS arm64 的 Bun 准备做了说明，并明说「其他平台的自动依赖准备、Desktop 安装包、媒体依赖与常驻服务不在本次验证范围」
- **付费依赖**：否
- **可编程接口**：`apps/cli`（`chengfeng-videocut --version` / `doctor`）、`install.cjs`、`package.json`、`docs/architecture.md`
- **商业限制要点**：自有代码 Apache-2.0；**GSAP 单独许可**是真实风险点——GSAP 的免费范围有边界，捆绑分发需自行判断；另有 `THIRD_PARTY_LICENSES.md`、`MODIFICATIONS.md` 需一并保留
- **与现架构关系**：**重复实现（工作台 + 时间线）**。它的「浏览器里对照视频与逐字稿审核剪切 + CLI 提交剪切」正好对应我们缺的前端工作台，但规模与成熟度（18 star、版本 0.4.11 预发布）不足以作为底座
- **采用方式**：`REFERENCE`。它的「剪切选择 + 项目修订号」乐观并发校验（避免多写入者互相覆盖）是一个具体可抄的机制
- **固定版本**：不接入。参考时 checkout `e6089ef285af452f74303ec2037b46baaed77950`

---

## 3. 补充调研项目（6 个）

### 3.1 trafilatura

- **仓库**：https://github.com/adbar/trafilatura
- **License**：Apache-2.0（`LICENSE`，仓库页 `tabName=Apache-2.0`）
- **最近提交**：`2026-11-11T17:21:19Z`；`HEAD` = `9c27981f4b7034a3249c77c4c06aaaed719fae66`
- **Star**：7,122（约 7 千量级）
- **技术栈**：纯 Python，`requires-python = ">=3.10"`；无系统库依赖；自带爬取 + 抽取 + 元数据
- **Windows 兼容性**：**可**。`pyproject.toml` 分类器显式含 `Operating System :: Microsoft`
- **付费依赖**：否。不依赖任何云服务
- **可编程接口**：`from trafilatura import fetch_url, extract`（库）；CLI `trafilatura`；输出 JSON/CSV/HTML/MD/TXT/XML
- **商业限制要点**：Apache-2.0，无附加限制
- **与现架构关系**：**补缺口（真实抓取）**。这是本项目「真实搜索/抓取」缺口里「抓回来怎么变成干净正文」的那一半；「搜什么」还需要另配搜索源
- **采用方式**：`DEPENDENCY`。它天然适合挂在 `capabilities/tools.py` 的 `ToolRegistry.register` 下做一个 `web_extract` 工具，输出结构化 JSON 进 `manifest.py` 的来源登记
- **固定版本**：`pip install trafilatura==2.3.1`（PyPI 实测 latest，与仓库当前 tag 一致）

### 3.2 edge-tts

- **仓库**：https://github.com/rany2/edge-tts
- **License**：**LGPLv3**（`LICENSE` 原文首行：「The MIT license is used for 'src/edge_tts/srt_composer.py' only. All remaining files are licensed under the LGPLv3.」）
- **最近提交**：`2026-10-14T09:30:52Z`；`HEAD` = `bd44c1d1aaebdc1db7f47456ee12431b42ec7cac`
- **Star**：12,520（约 1.25 万量级）
- **技术栈**：Python `>=3.7`；`setup.cfg` 声明 `Operating System :: OS Independent`；依赖 aiohttp；`edge-playback` 在 Windows 上不需要 mpv
- **Windows 兼容性**：**可**。分类器 `OS Independent`，且 README 明确「`edge-playback` 需要 mpv，**Windows 除外**」
- **付费依赖**：**否，但有隐性成本**。它调用的是微软 Edge 浏览器的在线 TTS 端点（非官方公开 API），不需要 key
- **可编程接口**：`edge_tts.Communicate`（Python 模块）；CLI `edge-tts --text ... --write-media ... --write-subtitles ...`；`--list-voices` / `--rate` / `--volume` / `--pitch`
- **商业限制要点**：**LGPLv3** —— 以独立进程或动态链接方式使用可保持我们的代码闭源，但修改其源码需开源；更需要注意的是**服务侧条款**：这是非官方接口，微软可随时变更或封禁，不适合作为唯一 TTS 通道
- **与现架构关系**：**补缺口（TTS）**。零成本拿到中文多音色 + SRT 时间戳，正好补 `timeline.py` 缺的配音轨
- **采用方式**：`DEPENDENCY`（**隔离为可替换 provider**）。必须包一层 `TtsProvider` 抽象，edge-tts 只是其中一个实现，避免单点失效；LGPL 风险通过「独立进程调用 CLI、只消费 mp3/srt 产物」进一步降低
- **固定版本**：`pip install edge-tts==7.3.1`（PyPI 实测 latest）

### 3.3 Apache ECharts

- **仓库**：https://github.com/apache/echarts
- **License**：Apache-2.0（`LICENSE` + `NOTICE`，ASF 项目）
- **最近提交**：`2026-10-04T10:59:16Z`；`HEAD` = `34f4d927eafe251f26f63afd3aeefa30f2feba30`
- **Star**：67,703（约 6.8 万量级）
- **技术栈**：纯 JavaScript/TypeScript，基于自研 canvas 库 zrender；npm 包 `echarts`（实测 `6.1.0`）；浏览器渲染，另有 `ssr/client` 服务端渲染入口
- **Windows 兼容性**：**可**。纯 JS 库，无平台专属依赖
- **付费依赖**：否
- **可编程接口**：`echarts.init(dom).setOption(option)`；npm 包；`ssr/client` 供 Node 侧出图
- **商业限制要点**：Apache-2.0。注意 `theme/` 与示例里的地图数据、字体有其自身来源与许可
- **与现架构关系**：**补缺口（动态图表）**。财报/宏观数据的可视化底座；与 HyperFrames 组合即可把「静态 option」变成「逐帧动画」
- **采用方式**：`DEPENDENCY`（前端/渲染侧）。在 Python 侧只传递 ECharts `option` JSON，不把 JS 逻辑写进后端
- **固定版本**：`npm i echarts@6.1.0`

### 3.4 LiteLLM

- **仓库**：https://github.com/BerriAI/litellm
- **License**：**MIT，但 `litellm/enterprise/` 目录单独许可**（`LICENSE` 首段原文：「All content that resides under the "litellm/enterprise/" directory ... is licensed under the license defined in "litellm/enterprise/LICENSE"」）
- **最近提交**：`2026-11-12T05:05:41Z`；`HEAD` = `7c7e02b06fa9fbf74d1e3c5064e5921e62aeb51a`
- **Star**：63,135（约 6.3 万量级）
- **技术栈**：Python `>=3.10, <3.16`；`uv.lock`；可选 Proxy Server（Docker 部署）；README 提到核心已含 Rust 组件
- **Windows 兼容性**：**未验证**。未取到书面平台声明；SDK 为纯 Python，Proxy 需另起进程
- **付费依赖**：**否（网关本身）**；它转发的上游模型按各家计费
- **可编程接口**：`litellm.completion()`（Python SDK，OpenAI 格式统一 100+ provider）；Proxy Server 提供 `/chat/completions`、`/embeddings`、`/images`、`/audio`、`/batches` 等端点；自带 **virtual keys、spend tracking、guardrails、load balancing**
- **商业限制要点**：MIT（enterprise 目录除外）。`enterprise/` 下的功能不要用，其余可商用
- **与现架构关系**：**补缺口（模型网关）**。而且和 `backend/app/capabilities/budget.py` 是天然一对：`evaluate_model_call` 目前只能「事前拦」，LiteLLM 的 spend tracking 能提供「事后账」，两者拼起来才是完整成本闸门
- **采用方式**：`DEPENDENCY`（先用 SDK 模式，Proxy 留作可选 `SIDECAR`）。SDK 模式改动最小：在现有 provider 调用点包一层 `litellm.completion`，把 `usage` 回写 `budget.py`
- **固定版本**：`pip install "litellm==1.109.1"`。注意仓库 `pyproject.toml` 已写 `1.110.0`，但 PyPI 实测 latest 为 `1.109.1` —— **以 PyPI 已发布版本为准，不要 pin 一个未发布的号**

### 3.5 AKShare

- **仓库**：https://github.com/akfamily/akshare
- **License**：MIT（`LICENSE`，README 徽章 `license-MIT`）
- **最近提交**：`2026-11-11T03:48:04Z`；`HEAD` = `f5324effb9419cba50bba423b4bd33073c79bdc2`
- **Star**：23,306（约 2.3 万量级）
- **技术栈**：Python `>=3.11`（README 要求 64 位）；`requires-python = ">=3.11"`；分类器 `Operating System :: OS Independent`；依赖 pandas/requests
- **Windows 兼容性**：**可**。分类器 `OS Independent`
- **付费依赖**：否（抓公开数据源，有频率限制）
- **可编程接口**：`import akshare as ak` + 大量 `ak.<函数>`；配套 `aktools` 可起 HTTP/Streamable 服务；README 有 MCP 说明
- **商业限制要点**：代码 MIT。**但数据本身不是 MIT** —— 它抓的是交易所、东财、新浪等公开站点，各站点有各自的使用条款与限流，商用前需逐源确认
- **与现架构关系**：**补缺口（中文财经数据）**。这是「中文财经研究」这个定位里最直接的一块，且与视频管线无关，属于研究侧基础能力
- **采用方式**：`DEPENDENCY`。挂在 `ToolRegistry` 下做成只读数据工具，结果落 `manifest.py` 的来源登记（保证「不伪造来源」）
- **固定版本**：`pip install akshare==1.20.37`（PyPI 实测 latest）

### 3.6 pdfplumber

- **仓库**：https://github.com/jsvine/pdfplumber
- **License**：MIT（`LICENSE.txt`）
- **最近提交**：`2026-06-15T03:34:34Z`；`HEAD` = `4c64b92d5caccd71c645e98e0fabb0c4dba7ff45`
- **Star**：10,894（约 1.1 万量级）
- **技术栈**：纯 Python，`python_requires=">=3.8"`，CI 覆盖 3.10–3.14；基于 `pdfminer.six`
- **Windows 兼容性**：**可**。`setup.py` 分类器 `Operating System :: OS Independent`
- **付费依赖**：否
- **可编程接口**：`pdfplumber.open(path)` → `page.extract_text()` / `page.extract_tables()`；CLI `pdfplumber --format csv|json|text`
- **商业限制要点**：MIT，无附加限制
- **与现架构关系**：**补缺口（财报 PDF 表格解析）**。财报的资产负债表/利润表基本是 PDF 表格，`extract_tables` 是把它变成结构化 JSON 的最短路径
- **采用方式**：`DEPENDENCY`。OCR 不在它的职责内（扫描版财报需另配 OCR），这一点要在接入时写清
- **固定版本**：`pip install pdfplumber==0.11.10`（PyPI 实测 latest）

---

## 4. 横向对比

### 4.1 许可证分层

按「能不能安全商用」分三档，这是本轮最容易踩坑的地方。

**第一档：宽松，可直接商用**

| 项目 | 许可 | 备注 |
|---|---|---|
| trafilatura / ECharts / HyperFrames / Pixelle-Video / AKShare / pdfplumber | Apache-2.0 / MIT | 需保留 NOTICE/版权声明 |
| MoneyPrinterTurbo / OpenCut / video-use / seedance2-skill | MIT | 但 MoneyPrinterTurbo 捆绑字体例外 |
| LiteLLM | MIT | `litellm/enterprise/` 除外 |
| video-shotcraft / chengfeng-videocut* | Apache-2.0 | 但间接依赖 Remotion / GSAP |

**第二档：有条件的「免费」**

- **Remotion**：按主体规模两档。个人与 ≤3 人营利组织免费（含商用），超过需买 company license。这不是开源许可，是商业许可。
- **GSAP 3.15.0**（被 chengfeng-videocut 捆绑）：单独许可，不随 Apache-2.0 走。
- **edge-tts**：LGPLv3（`srt_composer.py` 除外为 MIT）。库本身可商用，但**服务端是非官方接口**，稳定性与条款都不由我们控制。

**第三档：不可并入**

- **OpenMontage（AGPL-3.0）**：只要以网络服务形式提供，就触发源码开放义务。对本项目是硬阻断。

一个容易漏的点：**许可证会沿着依赖链传染**。video-shotcraft 本体是 Apache-2.0，但它渲染成片必须用 Remotion —— 也就是说「用 shotcraft 做的片子」仍然受 Remotion 双档许可约束。同理 chengfeng-videocut 的 GSAP。评估单个仓库的 LICENSE 不够，要看它运行时拉起了什么。

### 4.2 Windows 兼容性汇总

本仓库跑在 Windows 上，这一列直接决定「能不能用」。

**有明确证据可用的（8 个）**

| 项目 | 证据 |
|---|---|
| MoneyPrinterTurbo | `webui.bat` + Windows 一键启动包（`start.bat`/`update.bat`） |
| OpenCut | README 给 Windows PowerShell 装 `proto` 的命令 |
| HyperFrames | `hyperframes.studio/studio/download?os=windows` 下载入口 |
| Pixelle-Video | Windows 一键整合包 + `start_web.bat` |
| OpenMontage | README 给 Windows PowerShell 安装流程 |
| trafilatura | 分类器 `Operating System :: Microsoft` |
| edge-tts / pdfplumber / AKShare | 分类器 `OS Independent` |
| ECharts | 纯 JS，无平台依赖 |

**未验证的（6 个）**

| 项目 | 原因 |
|---|---|
| Remotion | 官方 System requirements 只列 macOS 15+ 与 Linux（glibc 2.35+），**未出现 Windows** |
| video-use | 安装步骤全为 `brew` / `ln -sfn`，无 Windows 说明 |
| video-shotcraft | README 写「Tested on macOS」，只补了 Linux 备注 |
| chengfeng-videocut-skills | 只声明 Node 18+/Git/Codex CLI，无平台段 |
| chengfeng-videocut | INSTALL 明说 macOS arm64 之外「不在本次验证范围」 |
| LiteLLM | 未取到书面平台声明（SDK 纯 Python，Proxy 需另起进程） |

「未验证」不等于「不能用」，但按本仓库的验证纪律，未在本机跑通之前不能写成「已支持」。

### 4.3 可编程接口形态

接口形态决定了接入方式，不是所有「有 CLI」的都适合 `DEPENDENCY`。

- **Python 库/包（最省事）**：trafilatura（`extract`）、pdfplumber（`open`）、AKShare（`ak.*`）、LiteLLM（`completion`）、edge-tts（`Communicate`）。这五个都能直接 `import`，走 `ToolRegistry.register` 即可。
- **Node CLI（适合 SIDECAR）**：HyperFrames（`npx hyperframes`）、Remotion（`npx remotion render`）。它们的产物是文件，走子进程比走 FFI 干净。
- **整包应用（只适合 REFERENCE）**：MoneyPrinterTurbo（Streamlit + FastAPI）、Pixelle-Video（WebUI + ComfyUI）、OpenMontage（backlot + pipelines）、OpenCut（重写中）。这些没有「库」形态，嵌不进来。
- **纯文本 Skill（只适合 SKILL）**：seedance2-skill、chengfeng-videocut-skills。
- **没有稳定接口**：OpenCut 的 Editor API / MCP / headless 全在 roadmap 上，现在拿不到。

---

## 5. 综合建议

### 5.1 必须接入的 5 个

按「缺口刚性 × 许可证安全 × Windows 可用」三条同时满足来筛，只留下这 5 个：

1. **trafilatura**（`DEPENDENCY`，`pip install trafilatura==2.3.1`）
   解决「真实抓取」。Apache-2.0、纯 Python、Windows 分类器明确。它是「抓回来变干净正文」的唯一合理选择，没有替代品的性价比能比。

2. **LiteLLM**（`DEPENDENCY`，`pip install "litellm==1.109.1"`）
   解决「模型网关 + 成本账」。MIT（除 enterprise）、Python SDK、天然对接 `budget.evaluate_model_call`。**注意 pin 到 PyPI 已发布的 1.109.1，不要 pin 仓库里那个还没发的 1.110.0。**

3. **edge-tts**（`DEPENDENCY`，`pip install edge-tts==7.3.1`）
   解决「TTS 从 0 到 1」。零成本拿到中文音色 + SRT 时间戳。**必须包一层 provider 抽象**，它是 LGPLv3 且走非官方接口，不能当唯一通道。

4. **Apache ECharts**（`DEPENDENCY`，`npm i echarts@6.1.0`）
   解决「动态图表」的数据与样式层。Apache-2.0、纯 JS、生态成熟。Python 侧只产出 `option` JSON。

5. **HyperFrames**（`SIDECAR`，`npm i hyperframes@0.9.37`）
   解决「动画/动态画面 → 确定性 MP4」。Apache-2.0、Node ≥22、有 Windows 下载入口、渲染是 headless Chrome + FFmpeg。它是 Remotion 在许可证上唯一合格的替代。

这 5 个共同的优点：**都不要求 GPU、都不绑定云服务、都能在 Windows 上跑、许可证都不设规模门槛**。这正好对上本仓库「本地优先」的定位。

第二批（强烈建议，但不必本轮就动）：**pdfplumber**（财报表格）、**AKShare**（中文财经数据）。这两个补的是「研究侧」而非「生产侧」，可以并行推进，风险也低。

### 5.2 明确拒绝的

- **OpenMontage —— 拒绝（AGPL-3.0）**。功能上它几乎是我们想做的事的完整版（pipelines + tools + budget + quality gates），正因如此才危险：任何代码级借鉴都可能把 AGPL 传染进来。只允许读它的设计，不允许拷它的代码。
- **Remotion —— 拒绝（双档商业许可 + Windows 无声明）**。7.3 万星、生态最好，但「>3 人营利组织要买 license」这条对一个要长期商用的产品是定时炸弹，且官方系统要求里没有 Windows。用 HyperFrames 顶替。
- **Pixelle-Video —— 不接入（REFERENCE）**。不是不好，是依赖 ComfyUI/GPU，与「本地优先、无 GPU」冲突。若将来要做数字人，再单独评估。
- **MoneyPrinterTurbo / OpenCut / video-use / video-shotcraft / chengfeng-videocut(*) —— 不接入（REFERENCE）**。它们要么是整包应用嵌不进来，要么依赖付费服务/受限许可，要么还没有稳定接口。价值在于设计，不在于代码。
- **chengfeng-videocut-skills —— 不接入（REFERENCE）**。它绑定 Codex Plugin 体系，与本仓库的 `SkillRegistry` 不是一套东西。

### 5.3 接入优先级排序表

| 项目 | 采用方式 | 解决哪个缺口 | 风险 | 优先级 |
|---|---|---|---|---|
| trafilatura | DEPENDENCY | 真实抓取 → 干净正文 | 低（Apache-2.0，纯 Python） | **P0** |
| LiteLLM | DEPENDENCY | 模型网关 + 成本可观测 | 中（provider 路由需压测；enterprise 目录勿用） | **P0** |
| edge-tts | DEPENDENCY | TTS + SRT 时间戳 | 中高（LGPLv3 + 非官方接口，需 provider 抽象兜底） | **P1** |
| ECharts | DEPENDENCY | 动态图表数据层 | 低（纯 JS，Apache-2.0） | **P1** |
| pdfplumber | DEPENDENCY | 财报 PDF 表格 → 结构化 | 低（MIT；扫描件需另配 OCR） | **P1** |
| AKShare | DEPENDENCY | 中文财经数据源 | 中（数据源条款与限流，非代码许可） | **P1** |
| HyperFrames | SIDECAR | HTML 动画 → MP4 | 中（引入 Node ≥22 + Chrome 运行时） | **P2** |
| seedance2-skill | SKILL | AI 视频提示词 | 低（纯 Markdown，MIT） | **P2** |
| OpenMontage | REJECTED | —（设计参考） | 高（AGPL-3.0 传染） | — |
| Remotion | REJECTED | —（用 HyperFrames 顶替） | 高（按人数收费 + Windows 无声明） | — |
| Pixelle-Video | REFERENCE | 数字人（暂不需要） | 中（ComfyUI/GPU 依赖） | — |
| MoneyPrinterTurbo | REFERENCE | 多 TTS provider 形态 | 低（只读） | — |
| OpenCut | REFERENCE | 前端工作台形态 | 低（重写中，不可用） | — |
| video-use | REFERENCE | Agent 读素材的方法论 | 低（只读） | — |
| video-shotcraft | REFERENCE | 镜头配方 + 帧级 QA | 中（渲染链绑定 Remotion） | — |
| chengfeng-videocut-skills | REFERENCE | 可复现安装/来源回执 | 低（只读） | — |
| chengfeng-videocut | REFERENCE | 剪切乐观并发校验 | 低（只读，18 star） | — |

### 5.4 落地顺序与验收

**第 1 步（P0，可并行）**
- 接 trafilatura：新增 `web_extract` 工具，注册进 `ToolRegistry`；验收 = 给定 3 个财经资讯 URL，输出含 title/author/date 的 JSON，且 `manifest.py` 有来源登记。
- 接 LiteLLM：在现有模型调用点包一层，把 `usage` 回写 `budget.py`；验收 = `model_calls` 表仍为 0 行（未真正调用），但 dry-run 能算出预估成本。

**第 2 步（P1）**
- edge-tts：先定义 `TtsProvider` 协议，edge-tts 作为首个实现；验收 = 一段中文文案 → mp3 + srt，且时长与 `timeline.py` 的音轨槽位对齐。
- ECharts + pdfplumber：财报 PDF → 表格 JSON → ECharts `option` → 静帧 PNG；验收 = 一张图能复现 PDF 里的数字。

**第 3 步（P2）**
- HyperFrames：以子进程方式渲染一段 5 秒动态图表；验收 = 输出 mp4 可被现有 FFmpeg 合成流程直接消费，且同一输入两次渲染帧一致。
- seedance2-skill：裁剪中文版进 `skills/`，在 `registry.yaml` 登记；验收 = `SkillRegistry.load_for` 能按 task_type 命中。

**不要做的事**
- 不要为了「功能看起来全」而引入 OpenMontage 或 Remotion 的任何代码。
- 不要在 `model_calls` 表还是 0 行的时候就把 LiteLLM 接到真实 key 上。
- 不要把 edge-tts 当成唯一 TTS —— 它的接口是非官方的。

### 5.5 未取到 / 未验证清单

按本仓库「不把未验证写成已实现」的纪律，把这些单独列出来，避免后续被当成结论引用：

- **未取到**：无。17 个仓库的 40 位 SHA 全部通过 `git ls-remote` 取到（包括最初一次失败的 `dexhunter/seedance2-skill`，重试后取得）。
- **未验证**：Remotion 的 Windows 支持（官方系统要求无 Windows 字样）；LiteLLM 的 Windows 支持（无书面声明）；video-use / video-shotcraft / chengfeng-videocut-skills / chengfeng-videocut 的 Windows 支持。
- **未核实**：各项目的实际运行表现（本文全程只读，未安装、未运行任何候选项目）；OpenMontage「12 production pipelines / 100+ tools」为其仓库简介自述，本轮未逐个数。
- **数据口径**：Star 数为抓取时刻的 `stargazerCount`，会随时间变化；提交时间为 `commits.atom` 的 `<updated>`，UTC，未做时区换算。
