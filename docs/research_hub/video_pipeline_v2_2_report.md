# AK Research Hub — V2.2 视频生产系统报告

**任务版本：** V2.2
**前置版本：** V2.1（已完成阶段性技术验收）
**当前授权范围：** 仅 Phase 1（FFmpeg 独立升级与基础音视频能力）
**本文件状态：** Phase 1 已完成，等待人工验收；Phase 2–6 未开始
**记录时点：** 2026-11-11
**仓库基线：** HEAD = `7ec1283`（M2.0 成果仍未提交，本轮未 commit 未 push）

> 本文只记录**真跑过**的东西。文档里写了但代码没实现、代码写了但没跑过、跑过但没验证输出正确，都会被明确标成对应的状态，不会因为"看起来合理"就写成已通过。

---

## 0. 一页纸结论

| 问题 | 结论 |
|---|---|
| 独立 FFmpeg 装好了吗？ | 装好了。`tools/ffmpeg/bin/ffmpeg.exe` = **8.1.2-full_build-www.gyan.dev**，未覆盖系统 4.2.2、未改 PATH |
| 装的是建议的 7.x 吗？ | **不是**。gyan.dev 已不再提供 7.x 版本化安装包（`ffmpeg-7.1.1-full_build` 返回 404），退到稳定版 8.1.2 并在第 10 节记录偏差 |
| 任务书列的十类能力都在吗？ | 26 项能力自检全 OK（16 必需 + 10 推荐），覆盖 H.264 编解码、AAC、libass、MP4 导入、混音、裁剪、拼接、帧率、元数据、音量/响度 |
| 能证明不是"只装了不会用"？ | `backend/scripts/media_selftest.py` 12 步真跑，产出真实视频/音频文件，第 7 节有逐项数字 |
| 真实素材剪辑工作流验证了吗？ | **没有**。任务书明确要求不得把升级写成素材工作流已验证；本阶段只做能力与工具链，见第 10 节 |
| 破坏原有系统了吗？ | 没有。M2.0 测试从 279 → **297 passed, 1 skipped**；真实开发库 SHA-256 与基线完全一致 |
| 动了 Git 吗？ | 没有 commit / push / reset / clean；43 个已跟踪文件修改保持原样 |

---

## 1. 阶段目标（Phase 1 原文要求 → 本阶段实际做法）

| 任务书要求 | 实际做法 |
|---|---|
| 检查现有 FFmpeg 版本、路径及调用方式 | 全仓搜索确认 4 个调用点，逐一记录（见 §2） |
| 选择可信的 Windows 完整构建 | gyan.dev `full_build`（`--enable-gpl --enable-version3`，含 libx264/libass/lame 全套） |
| 核对发行来源、许可证和构建信息 | 记录归档 URL、字节数、SHA-256、上游提交 `38b88335f9`、gcc 16.1.0 (MSYS2)、GPL-3.0-or-later |
| 记录完整版本号及 SHA256 | 见 §3.3 与 `tools/ffmpeg/VERSION.json` |
| 安装到项目独立目录 | `tools/ffmpeg/`（bin / doc / presets / LICENSE / README.txt） |
| 不覆盖系统 FFmpeg | 系统 4.2.2 仍在 `D:\DeliveryOptimization\miniconda\Library\bin`，未触碰 |
| 不修改系统 PATH | 未改 PATH；解析顺序写在代码里（见 §4.2） |
| 项目配置中指定 ffmpeg / ffprobe 路径 | `config/settings.yaml` 的 `video.ffmpeg_dir` + `video.ffmpeg_min_version` |
| 增加自动检测和版本校验脚本 | `install_ffmpeg.py --verify`、`check_media_toolchain.py`、`media_selftest.py` 三个脚本 |
| 检查十类音视频能力 | `probe_capabilities()` 26 项 + 12 步真跑自检 |

---

## 2. 开始时的项目状态（Phase 1 动手前实测）

### 2.1 机器上的 FFmpeg（三个，各不相同）

| 位置 | 版本 | 可用性 |
|---|---|---|
| `D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe`（PATH 默认） | **4.2.2** | 有 libass / libx264 / aac，但**没有 `-fps_mode`**（需 ≥5.1），OpenKatana 的部分渲染路径会直接报错 |
| `C:\Users\25803\AppData\Local\Programs\Trae CN\resources\app\bin\ffmpeg.exe` | 6.1.1 | 支持 `-fps_mode`，但是**精简构建**：无 aac 编码器，只有 14 个滤镜，**没有 ass/subtitles/drawtext** |
| 项目内 | 无 | —— |

结论：两个现成 FFmpeg 互补但都不满足"完整构建"，这正是 Phase 1 存在的理由。

### 2.2 代码里调用 FFmpeg 的地方（4 处）

| 文件 | 调用方式 |
|---|---|
| `backend/scripts/build_video_job.py` | `find_ffmpeg()` 用 `shutil.which("ffmpeg")`，走 PATH |
| `content/jobs/video_engine_demo/script/build_demo.py:29` | 硬编码 miniconda 路径 |
| `content/jobs/video_engine_demo/script/qc_demo.py:20-21` | 硬编码 miniconda 路径 |
| `config/settings.yaml` | `video.ffmpeg_binary: ffmpeg`（字符串，无目录概念） |

### 2.3 其他前置事实

- `.venv` Python 3.12.10；`tools/` 下只有 `openkatana`（432 MB，V2.1 引入）。
- C 盘剩余 27.9 GB（够装 full build 并保留下载包，但下载包事后已删，见 §9）。
- `.gitignore` 当时**没有**任何 `tools/` 相关规则，直接 `git add` 会把 432 MB 二进制拖进仓库。

---

## 3. 修改文件列表

### 3.1 新增源码 / 脚本 / 测试

| 文件 | 行数 | 作用 |
|---|---|---|
| `backend/app/capabilities/media/ffmpeg.py` | 322 | 工具链解析、版本解析、26 项能力探测、`run_ffmpeg/run_ffprobe` |
| `backend/app/capabilities/media/__init__.py` | 37 | 统一导出，业务代码不直接摸子模块 |
| `backend/scripts/install_ffmpeg.py` | 242 | 固定版本安装：校验 SHA-256、解包、写 `VERSION.json` / `SOURCE.md`、`--verify` |
| `backend/scripts/check_media_toolchain.py` | 86 | 环境检测：来源、版本、能力清单，JSON / `--text` 两种输出 |
| `backend/scripts/media_selftest.py` | 556 | 12 步真机自检，产出真实视频音频 + QC 报告 |
| `tests/test_media_toolchain.py` | 174 | 18 个用例，含两个真 bug 的回归 |

### 3.2 修改的既有文件

| 文件 | 改动 |
|---|---|
| `.gitignore` | 新增 `tools/_staging/`、`tools/ffmpeg/{bin,doc,presets}/`、`tools/openkatana/{.venv,projects}/`、`tools/openkatana/**/node_modules/`，并用 `!` 反向保留 `VERSION.json` / `SOURCE.md` / `LICENSE` |
| `config/settings.yaml` | `video:` 段新增 `ffmpeg_dir: tools/ffmpeg/bin`、`ffmpeg_min_version: "5.1"`，并注释说明解析顺序与"不覆盖系统 FFmpeg" |

### 3.3 新增工具与记录（二进制不入库）

| 路径 | 内容 |
|---|---|
| `tools/ffmpeg/bin/{ffmpeg,ffprobe,ffplay}.exe` | 8.1.2 可执行文件（`bin/` 已被 gitignore） |
| `tools/ffmpeg/{LICENSE,README.txt,doc/,presets/}` | 上游原件 |
| `tools/ffmpeg/VERSION.json` | 版本行、上游提交、许可证、归档 URL/字节/SHA-256、`hash_matches_declared: true`、`not_on_path: true`、`system_ffmpeg_untouched: true`、`archive_retained: false`、`verify_commands` |
| `tools/ffmpeg/SOURCE.md` | 来源说明、为什么选 8.1.2、重装命令 |
| `content/jobs/media_toolchain_check/` | 12 步自检产物：`work/`、`audio/`、`output/`、`qc/toolchain_report.{json,md}`、`qc/frame_subs_1s.png` |

---

## 4. 新增依赖及版本

### 4.1 Python 依赖

**零新增。** 本阶段只用标准库（`subprocess` / `hashlib` / `urllib` / `pathlib` / `json`）与既有的 `sqlalchemy`、`pytest`，没有装任何新包。

### 4.2 外部二进制（唯一新增运行时）

| 项 | 值 |
|---|---|
| 版本 | `8.1.2-full_build-www.gyan.dev` |
| 归档 | `https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-full_build.7z` |
| 归档字节 | 166,721,853 |
| 归档 SHA-256 | `0fff188997a499b5382e0f66e845d4556c48c54f0113ebed4853d556dbdd7059` |
| 上游提交 | `38b88335f9` |
| 编译 | gcc 16.1.0 (MSYS2) |
| 许可证 | **GPL-3.0-or-later**（`--enable-gpl --enable-version3`） |
| 安装位置 | `tools/ffmpeg/`（不在 PATH，不覆盖系统） |
| 解包工具 | Windows 自带 `C:\WINDOWS\system32\tar.exe`（bsdtar 3.8.9，支持 7z），**未安装 7-Zip** |
| 许可判断 | 仅本地运行、不分发二进制，故 GPL 不构成分发义务；若将来随产品分发，需按 GPL 提供源码或改用 LGPL 构建（见 §10） |

### 4.3 工具链解析优先级（写入代码而非依赖环境变量）

```
显式参数 explicit_dir
  > config/settings.yaml 的 video.ffmpeg_dir
  > 项目 tools/ffmpeg/bin
  > 系统 PATH
```

每个候选都会记录到 `MediaToolchain.candidates`，`check_media_toolchain` 会同时列出"机器上还有哪些 ffmpeg"，避免出现"以为用的是 8.1.2、其实是 4.2.2"。

---

## 5. 实际执行命令

```powershell
# 1) 环境勘察
git rev-parse --short HEAD
Get-ChildItem tools
& "D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe" -version
& "D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe" -filters | Select-String -Pattern "ass|drawtext"

# 2) 下载（Invoke-WebRequest 失败后改用 curl，见 §8.1）
curl.exe -L --fail --retry 5 -C - -o tools\_staging\ffmpeg-8.1.2-full_build.7z `
  https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-full_build.7z

# 3) 安装（校验哈希 → 解包 → 写记录）
$env:PYTHONPATH='.'
.venv\Scripts\python.exe -m backend.scripts.install_ffmpeg --archive tools\_staging\ffmpeg-8.1.2-full_build.7z
.venv\Scripts\python.exe -m backend.scripts.install_ffmpeg --verify

# 4) 能力检测
.venv\Scripts\python.exe -m backend.scripts.check_media_toolchain --text --require-project

# 5) 12 步真机自检
.venv\Scripts\python.exe -m backend.scripts.media_selftest

# 6) 回归
.venv\Scripts\python.exe -m pytest -o addopts=""
```

---

## 6. 实际测试过程

1. **单元层**：`tests/test_media_toolchain.py` 覆盖标记解析、能力清单完整性、候选优先级、版本读取与 `VERSION.json` 一致性、异常路径。
2. **能力层**：`check_media_toolchain --text --require-project` 逐项打印 26 项能力，`overall ok: True`。
3. **真机层**：`media_selftest` 依次做转码 → 解码抽帧 → 裁剪 → 帧率转换 → 拼接 → 混音 → 合片 → 字幕烧录 → 响度分析 → 音量分析 → 质检滤镜 → 元数据，全部落真实文件到 `content/jobs/media_toolchain_check/`。
4. **肉眼层**：从烧录字幕的成片抽帧 `qc/frame_subs_1s.png`，确认中文字形、位置、安全区。
5. **回归层**：全量 `pytest`，确认 M2.0 的 279 个用例一个没坏。
6. **保护层**：只读打开真实开发库，比对 SHA-256、表数、迁移版本、journal_mode、附属文件。

---

## 7. 测试结果

### 7.1 能力自检（26 项，全 OK）

必需 16 项：`h264_decode`、`h264_encode`、`aac_encode`、`ass_filter`、`subtitles_filter`、`mp4_mux`、`mp4_demux`、`audio_mix`、`video_trim`、`audio_trim`、`video_concat`、`concat_demux`、`fps_convert`、`volume`、`loudnorm`、`ffprobe_metadata`。
推荐 10 项：`drawtext`、`scale`、`overlay`、`xfade`、`afade`、`zoompan`、`blackdetect`、`freezedetect`、`silencedetect`、`volumedetect`。

> `drawtext` 被刻意列为**推荐项**而非必需项：中文字幕走 libass 更可靠（需要显式传字体目录），缺 drawtext 不应让整体判定失败。

### 7.2 12 步真机自检（全 OK）

| 步骤 | 结果 |
|---|---|
| 编码 | 3.00 s，h264 + aac，1080×1920 |
| 解码抽帧 | 90 帧 |
| 裁剪 | 1.00 s |
| 帧率转换 | 24/1 |
| 拼接（concat 滤镜） | 4.02 s（两段 2.00 s 期望 4.00，含关键帧对齐误差） |
| 混音（amix） | 2.50 s |
| 合片（视频+音频 mux） | 2.52 s |
| 字幕烧录（libass + 微软雅黑） | 2.52 s |
| 响度 | integrated **−18.75 LUFS**，true peak **−12.09 dBTP** |
| 音量 | mean −18.1 dB，max −12.1 dB |
| 质检滤镜 | black=0，freeze=0，silence=0 |
| 元数据 | streams=2 |

产物：`content/jobs/media_toolchain_check/output/*.mp4`、`qc/toolchain_report.{json,md}`、抽帧图 `qc/frame_subs_1s.png`（已肉眼确认单行中文落在安全区，两行重叠问题已改为 0.20–1.30 / 1.30–2.45 顺序显示）。

### 7.3 单元测试与全量回归

- `tests/test_media_toolchain.py`：**18 passed**。
- 全量：**297 passed, 1 skipped, 1 warning in 38.83s**（Phase 1 之前是 279 passed + 1 skipped）。
- 唯一 warning 是 M2.0 已记录的非阻断项：`agent_runs ↔ research_subtasks` 外键环的 `SAWarning`。
- 跳过 1 例是 Windows 无符号链接权限的既有用例。

### 7.4 保护性核验

| 检查项 | 结果 |
|---|---|
| 真实开发库 SHA-256 | `2df764e565c2b89644b70e3abc9143088761730fccf438f801683afa7fb48203`（与基线**一致**） |
| 文件大小 | 540,672 B |
| 表数 | 19（无 `tags` / `topic_collections` / `research_tasks` / `model_calls` / `tool_calls`） |
| 迁移版本 | `de8148733a9b` |
| journal_mode | `delete`（未开 WAL） |
| `-wal` / `-shm` | 均不存在 |
| Git | HEAD 仍 `7ec1283`；43 个已跟踪文件修改保持原样；未 commit / 未 push |

---

## 8. 失败原因及修复情况

### 8.1 下载被中断：`Invoke-WebRequest` 报 "从传输流收到意外的 EOF 或 0 个字节"

- **原因**：gyan.dev 对大文件的分块响应在 PowerShell 5.1 的 `Invoke-WebRequest` 下会提前断流。
- **修复**：改用 `curl.exe -L --fail --retry 5 -C -`（断点续传 + 重试），3.7 MB/s，41 秒完成；落地后立刻用 SHA-256 与官方声明值比对，`hash_matches_declared: true`。

### 8.2 能力探测误报三项缺失（真 bug）

- **现象**：`mp4_mux` / `mp4_demux` / `concat_demux` 被判为缺失，`overall ok` 变 false。
- **原因**：`-muxers` / `-demuxers` 列表的标记列只有**一个字母**（`E` / `D`），而解析正则写的是 `^[A-Z.|]{2,}$`，单字母被跳过。
- **修复**：改为 `^[A-Z.]{1,8}$`，并补了单字母/多字母两种标记的回归用例。

### 8.3 版本号只显示两位数（真 bug）

- **现象**：`--text` 输出 `8.1`，与 `VERSION.json` 的 `8.1.2` 不一致。
- **原因**：`(\d+)\.(\d+)` 只取两段。
- **修复**：`^(\d+)\.(\d+)(?:\.(\d+))?`，返回完整三段元组，并加用例断言与 `VERSION.json` 一致。

### 8.4 字幕两行重叠

- **现象**：首版自检抽帧里两条中文字幕压在一起。
- **原因**：ASS 两个 Dialogue 事件的起止时间重叠。
- **修复**：改为 0.20–1.30 / 1.30–2.45 顺序显示，重新烧录后抽帧确认。

### 8.5 收尾脚本把自己写进了错误的根目录

- **现象**：清理脚本用 `Path(__file__).resolve().parents[2]`，而脚本放在 `tools/` 下，`parents[2]` 解析成了 `C:\Users\25803`，导致 159 MB 下载包**没被删除**、`VERSION.json` 补丁也没写。
- **修复**：改用绝对路径常量重写，二次执行后确认 `removed_archive_bytes 166721853`、`record_updated`、`staging_removed`。
- **教训**：脚本内的项目根不得靠"文件在第几层"推断，要么显式传参，要么固定常量。

### 8.6 `.gitignore` 中文注释被写坏

- **现象**：新增的三行中文注释变成乱码（PowerShell 内联写文件时按 GBK 处理）。
- **修复**：改用 Python 以 UTF-8 定点替换那三行，`read` 复核显示正常中文。
- **教训**（本项目已第三次踩到）：**不要用 PowerShell 管道/重定向改含中文的 UTF-8 文件**，一律用编辑工具或 Python 显式 `encoding="utf-8"`。

---

## 9. 资源和磁盘占用

| 目录 | 体积 | 说明 |
|---|---|---|
| `tools/ffmpeg/` | **740.4 MB** | full build（`bin` 三个 exe + `doc/` + `presets/`）；`bin/` 与 `doc/`、`presets/` 已 gitignore |
| `tools/openkatana/` | 529.3 MB | V2.1 引入，独立 `.venv` + `projects/`，已 gitignore |
| `content/jobs/media_toolchain_check/` | 30.1 MB | 本阶段 12 步自检产物 |
| `content/jobs/video_engine_demo/` | 1222.8 MB | V2.1 演示（含渲染帧），本阶段未改动 |
| `data/` | 0.5 MB | 真实开发库未变 |

- 下载包 166,721,853 B 在安装校验后**已删除**（`archive_retained: false`，重装命令写在 `SOURCE.md` / `VERSION.json` 里）。
- 若后续磁盘吃紧，可安全删除 `tools/ffmpeg/doc/` 与 `presets/`（约 500 MB），不影响渲染与自检。

---

## 10. 已知问题

1. **版本偏差：装的是 8.1.2，不是任务书建议的 7.x。** gyan.dev 的 packages 目录已无 7.x 版本化安装包（`ffmpeg-7.1.1-full_build` 返回 404，只剩 8.1.2 与 git 快照）。为满足"不使用浮动版本"，选择稳定版 8.1.2 而非 git 快照；如需严格 7.x，需要另行授权从其他可信源取，或自建。
2. **许可证是 GPL-3.0-or-later。** 仅本地使用不分发时无义务；一旦随产品分发二进制，需按 GPL 提供对应源码或改用 LGPL 构建。这一点必须在做产品化决策时重新评估。
3. **真实素材导入路径尚未验证。** `mp4_demux` / `concat_demux` 能力探测为 OK，但没有跑过"导入真实拍摄视频并剪辑"的完整链路——任务书明确禁止把本阶段写成素材工作流已验证。该验证归 Phase 2 / Phase 4。
4. **subtitle 字体依赖显式 `fontsdir`。** libass 不会自动用系统字体；`media_selftest` 的做法是把 `C:\Windows\Fonts\msyh.ttc` 复制到工作目录并在滤镜里传 `fontsdir=fonts`。后续流水线必须沿用这一做法，否则中文会掉字。
5. **`ffplay.exe` 未使用**（740 MB 里它占一份）。当前脚本只依赖 ffmpeg + ffprobe，保留仅为完整性。
6. **`build_video_job.py` 与 `build_demo.py` 仍走各自的老路径。** 老脚本尚未切换到新的 `resolve_toolchain()`；本次只保证新代码（`backend/app/capabilities/media/`）与检查/自检脚本使用统一解析。切换属于 Phase 2 适配层的工作。
7. **未做 Windows 之外平台的验证**（`.exe` 路径、`tar.exe`、字体目录都是 Windows 假设）。

---

## 11. 未完成事项（明确不属于 Phase 1）

- OpenKatana 统一适配层（`initialize/health_check/create_project/...`）——Phase 2。
- 真实素材导入、镜头切分、素材登记与追溯表——Phase 4。
- 中文旁白（本地开源 TTS 试听 + 付费方案调研）——Phase 3。
- 叙事驱动分镜（`storyboard.csv` 驱动渲染，禁止硬编码在 Python 里）——Phase 4。
- 完整流水线与 60–90 秒测试片——Phase 5 / Phase 6。
- 响度标准化到 −14 LUFS 的成片级调音（本阶段只验证 `loudnorm` 可用并有 −18.75 LUFS 的实测值）——Phase 5。

---

## 12. 下一阶段依赖（Phase 2 需要本阶段提供什么）

1. **`-fps_mode` 兼容性问题已解除**：8.1.2 实测 `rc=0`，OpenKatana 的 `render.py` 不会再因 4.2.2 报 `Unrecognized option 'fps_mode'`。
2. **统一入口已就绪**：`backend/app/capabilities/media` 的 `resolve_toolchain()` / `probe_capabilities()` / `run_ffmpeg()` / `run_ffprobe()` 可直接被 Phase 2 的 Video Engine Adapter 复用。
3. **能力基线已固化**：Phase 2 适配层若需要某个滤镜（如 `xfade` / `zoompan`），可在启动自检里断言，缺了就报错而不是静默降级。
4. **配置位已留好**：`video.ffmpeg_dir` / `video.ffmpeg_min_version`，Phase 2 无需再改配置结构。

---

## 13. 是否需要额外授权

**Phase 1 自身：不需要。** 全程无付费、无外网业务数据抓取、无系统级安装、无管理员权限。

**进入 Phase 2 前建议确认（4 项）**：

| 编号 | 待确认 | 影响 |
|---|---|---|
| A | `tools/` 是否纳入 Git（当前只保留 `VERSION.json` / `SOURCE.md` / `LICENSE`，二进制忽略） | 影响仓库体积与可复现性 |
| B | 是否允许把 `tools/ffmpeg/doc` 与 `presets` 删除（省约 500 MB） | 纯磁盘问题 |
| C | 是否需要严格 7.x（若需要，请指定可信发行源） | 与"不用浮动版本"的约束冲突时如何取舍 |
| D | Phase 2 的示例素材用 OpenKatana 自带（NASA public-domain / Apache-2.0）还是另行授权素材 | 决定素材登记表怎么填 |

---

## 14. 完成状态分级（按任务书 §4.2 的词表）

| 能力 | 状态 |
|---|---|
| 独立 FFmpeg 安装 | **已实现 · 已运行 · 已测试 · 已通过** |
| 系统 FFmpeg 未被覆盖 | **已实现 · 已验证**（4.2.2 仍在原位，`doctor` 输出可证） |
| PATH 未被修改 | **已实现 · 已验证**（`not_on_path: true`） |
| 版本与许可证记录 | **已实现 · 已运行**（`VERSION.json` / `SOURCE.md` 实际落盘） |
| 自动检测与版本校验脚本 | **已实现 · 已运行 · 已测试 · 已通过** |
| 26 项能力探测 | **已实现 · 已运行 · 已通过** |
| 12 步真机自检 | **已实现 · 已运行 · 已通过**（产出真实音视频） |
| 真实素材导入与剪辑工作流 | **未实现 · 未测试**（Phase 2 / 4） |
| 中文旁白 | **未实现**（Phase 3） |
| 叙事驱动分镜 | **未实现**（Phase 4） |
| 完整成片流水线 | **未实现**（Phase 5） |
| 数据隔离机制 | **已存在（V2.1）· 本阶段未破坏**：本轮未读取任何历史任务数据，自检素材全部由 FFmpeg 自生成 |
| 真实开发数据库 | **未修改**（SHA-256 与基线一致） |
| Git commit / push | **未执行** |

---

## 15. 本阶段文件清单（人要看的东西）

```
docs/research_hub/video_pipeline_v2_2_report.md        ← 本文件（Phase 1 章节）
backend/app/capabilities/media/ffmpeg.py               322 行：工具链与能力
backend/app/capabilities/media/__init__.py              37 行：统一导出
backend/scripts/install_ffmpeg.py                      242 行：固定版本安装与校验
backend/scripts/check_media_toolchain.py                86 行：环境检测
backend/scripts/media_selftest.py                      556 行：12 步真机自检
tests/test_media_toolchain.py                          174 行：18 个用例
tools/ffmpeg/VERSION.json                                    版本/哈希/许可证记录
tools/ffmpeg/SOURCE.md                                       来源与重装说明
content/jobs/media_toolchain_check/qc/toolchain_report.md    自检报告
content/jobs/media_toolchain_check/qc/frame_subs_1s.png      中文字幕抽帧证据
content/jobs/media_toolchain_check/output/*.mp4              自检产出的真实视频
```

---

## 附：Phase 2–6 状态

| 阶段 | 状态 |
|---|---|
| Phase 1 FFmpeg 独立升级与基础音视频能力 | **已完成，等待验收** |
| Phase 2 OpenKatana 轻量化集成与引擎适配层 | 等待授权 |
| Phase 3 中文财经旁白系统 | 等待授权 |
| Phase 4 真实素材工作流与素材驱动分镜 | 等待授权 |
| Phase 5 完整视频生产流水线 | 等待授权 |
| Phase 6 完整测试片与人工验收 | 等待授权 |
