# FFmpeg 项目独立构建（V2.2 Phase 1）

本目录是**项目内**的完整 FFmpeg 构建，与系统上已安装的 FFmpeg **互不影响**：

- 不修改系统 PATH，不注册全局命令；
- 不覆盖、不删除 `D:\DeliveryOptimization\miniconda\Library\bin\ffmpeg.exe`（4.2.2）等既有安装；
- 项目代码按「配置 > 本目录 > PATH」的顺序解析工具链（见 `backend/app/capabilities/media/ffmpeg.py`）。

## 来源与版本

| 项 | 值 |
| --- | --- |
| 版本 | `8.1.2-full_build-www.gyan.dev` |
| 发行来源 | https://www.gyan.dev/ffmpeg/builds/ |
| 归档 | `ffmpeg-8.1.2-full_build.7z`（166,721,853 字节） |
| 归档 SHA-256 | `0fff188997a499b5382e0f66e845d4556c48c54f0113ebed4853d556dbdd7059` |
| 上游 FFmpeg 提交 | `38b88335f9` |
| 构建工具链 | gcc 16.1.0 (Rev2, Built by MSYS2 project) |
| 许可证 | GPL-3.0-or-later（https://www.gnu.org/licenses/gpl-3.0.txt） |
| 安装时间 | 2026-11-10T18:39:03+00:00 |

`ffmpeg -version` 首行：

```
ffmpeg version 8.1.2-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
```

## 许可证说明

该构建启用 `--enable-gpl --enable-version3`，按 **GPLv3** 分发（`LICENSE` 随包提供）。

本项目把它当作**独立可执行文件**调用（`subprocess`），不链接其库，因此本仓库源码不受 GPL 传染；
但**不要把 `bin/` 下的二进制提交进仓库**——一旦随仓库分发，就必须同时履行 GPLv3 义务。
`bin/`、`doc/`、`presets/` 已在 `.gitignore` 中排除，本目录只保留来源与版本记录。

## 为什么选 8.1.2 而不是 7.x

任务书建议目标版本为 7.x。实际操作时发行方（gyan.dev）已不再提供 7.x 的版本化安装包，
只提供当前稳定版 8.1.2 与 git 快照。选择 **8.1.2 稳定版**（而不是 git 快照）的理由：

1. 它是**带版本号的稳定发行**，符合"不用浮动版本"的要求（git 快照会每天变化）；
2. 8.1.2 是 7.x 的超集，OpenKatana 0.1.3 需要的 `-fps_mode`（5.1+）在其中可用（已实测）；
3. 完整构建包含 libass / libx264 / AAC 等本项目渲染链路必需组件（已逐项实测，见报告）。

## 重新安装

```powershell
python -m backend.scripts.install_ffmpeg --download      # 按 PINNED_BUILD 下载并校验
python -m backend.scripts.install_ffmpeg --verify        # 只校验
```
