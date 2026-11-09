# video_engine/ —— 视频生产基础（第一阶段）

**状态：仅目录、配置与规范。没有渲染引擎。**

本阶段不实现视频生成。这里定义的是"将来渲染器要读的规格"：

- 四类视频模板（`templates/*.yaml`）
- 默认画布规格 1080×1920 / 30fps / 9:16
- 渲染器接口占位说明（`renderers/README.md`）
- 字幕、音频、质检的字段约定

任何"已生成视频"的说法在本阶段都是不成立的：`/api/system/status` 中
`features.video_rendering` 恒为 `false`。

## 子目录

| 目录 | 用途 | 第一阶段 |
| --- | --- | --- |
| `templates/` | 四类视频模板定义 | DONE（YAML 定义） |
| `renderers/` | 渲染器实现（FFmpeg/Matplotlib 驱动） | NOT IMPLEMENTED（仅接口说明） |
| `subtitles/` | 字幕样式与 SRT/ASS 规范 | PARTIAL（仅样式规范） |
| `audio/` | 配音与 BGM 规范 | PARTIAL（仅规范） |
| `quality_control/` | 自动质检项定义 | DONE（检查项清单） |

## 默认规格

```yaml
canvas:
  width: 1080
  height: 1920
  fps: 30
  aspect_ratio: "9:16"
output:
  container: mp4
  video_codec: h264
  audio_codec: aac
  bitrate_kbps: 6000
  loudness_lufs: -14
```

## 依赖声明（尚未安装）

渲染器需要 `FFmpeg`（可执行文件，非 Python 包）与 `Matplotlib`（图表）。
当前 `requirements.txt` **不含** Matplotlib，它列为 `pyproject.toml` 的可选依赖
`charts`，安装方式：

```bash
pip install -e ".[charts]"
```

FFmpeg 需另行安装并加入 PATH；本阶段未验证其可用性，因此不声称支持渲染。

## 与 Agent 的关系

`agents/video_producer/PROMPT.md` 负责产出 `render_spec` 与 `qc` 报告，
本目录的模板是它填写规格时的依据。二者字段必须一致。
