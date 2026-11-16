# 复现说明：remaster_v1 怎么重新跑一遍

所有命令在仓库根目录（`D:\finance-media-ai`）执行。**不需要任何云端服务，不产生费用。**

## 0. 前置

| 依赖 | 位置 | 说明 |
|---|---|---|
| Python | `.venv\Scripts\python.exe` | 仓库虚拟环境，3.12.10 |
| Playwright + Chromium | `tools\openkatana\.venv\Scripts\python.exe` + `D:\playwright_browsers\chromium-1243\chrome-win64\chrome.exe` | 仓库 `.venv` 里没有 playwright，渲染脚本要用这一套 |
| FFmpeg | `tools\ffmpeg\bin\ffmpeg.exe` | 项目自带的 8.1.2，**不改系统里那份 4.2.2** |
| three.js | `tools\vendor\three.min.js` | r147 UMD，MIT，构建时内联进镜头页 |
| 字体 | `content\jobs\v6_power_first_film\fonts\NotoSansSC-VF.ttf` | OFL-1.1 |

## 1. 一条命令跑完整条链路

```powershell
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage all
```

`--stage` 可单独跑：`blueprint`（写蓝图）→ `scenes`（生成镜头页）→ `render`（逐帧渲染）→ `audio`（合成配乐与音效）→ `assemble`（拼片 + 烧字幕 + 切样片）。`--only s2,s3` 只重做指定镜头。

## 2. 分步命令（调试时用）

```powershell
# 蓝图：5 镜、47.7 秒，会校验结构（未知字段/缺证据/缺许可都会拒绝）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage blueprint

# 镜头页：把 three.js、字体、scene.js 写进 production/scenes/s*/（自包含）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage scenes

# 单镜试渲几帧（验证构图最快的方式）：注意每次换新的 --out 目录
tools\openkatana\.venv\Scripts\python.exe backend\scripts\cinema_render.py `
  --scene content\jobs\v6_power_first_film\remaster_v1\production\scenes\s2 `
  --out "$env:TEMP\s2_test" --fps 30 --frames-from 200 --frames-to 202 --quiet

# 全片渲染（5 镜 ≈ 1430 帧，本机约 25 分钟）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage render

# 音频（程序化配乐 + 14 个音效点，与旁白混音）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage audio

# 拼片（烧 ASS 字幕 + 暗角与颗粒 + loudnorm −16 LUFS + 切 16.47 秒样片）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.build_cinema_film --stage assemble

# 技术 QC（容器事实 + 黑帧/冻结/静音/响度 + 字幕重叠 + 每镜三帧关键帧）
.venv\Scripts\python.exe -X utf8 -m backend.scripts.cinema_qc
```

## 3. 改了哪里会影响什么

| 想改 | 改这里 | 之后要跑 |
|---|---|---|
| 旁白/字幕文本 | `build_cinema_film.py` 的 `CLIP_TEXT`（必须与已批准的 `script/SCRIPT.md` 一致） | `assemble` |
| 镜头构图、颜色、动画 | `build_cinema_film.py` 的 `stage_s1..stage_s5` | `scenes` → `render`（`--only sN`）→ `assemble` |
| 三维原语（塔、柱、流、面板） | `backend/app/video/cinema/scene.js` | `scenes` → `render` → `assemble` |
| 页面外壳与文字样式 | `backend/app/video/cinema/page.py` | `scenes` → `render` → `assemble` |
| 配乐/音效 | `backend/app/video/cinema/audio.py` | `audio` → `assemble` |
| 蓝图字段 | `backend/app/video/cinema/blueprint.py` | 全部 |

## 4. 已知坑（踩过的，别重踩）

1. **`--out` 目录一定要换新的**：渲染器同名帧存在时不会重画，改了代码却看到一模一样的图，就是读到旧帧。
2. **样式只认白名单**：OpenKatana 那套组合契约里 `background` 会被拒，必须写 `background-color`；本项目 cinema 引擎的 DOM 层同理（内联 style 用标准 CSS 属性名）。
3. **面板的 `y` 是"底边所在高度比例"**：底对齐靠 `transform: translateY(-100%)`，所以 `top` 只能写 `y`，不能写 `y + h`。
4. **字幕用 ASS、不用 SRT**：SRT + `force_style` 会被 libass 按 PlayResY=288 放大数倍；ASS 里显式写 `PlayResX/PlayResY` 才是像素级可控。
5. **响度必须后期归一**：直接拼接各段音频会落在 −21 LUFS 附近，最终编码要加 `loudnorm=I=-16:TP=-1.5:LRA=11`。

## 5. 校验一条片子合不合格

```powershell
Get-Content content\jobs\v6_power_first_film\remaster_v1\qc\technical_qc.json | Select-String '"failed"'
# 期望： "failed": []
```

再抽三帧看一眼（关键帧已在 `qc/keyframes/`），确认字幕没有压住画面文字、柱底落在基线上。
