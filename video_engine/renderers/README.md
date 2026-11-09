# renderers/ —— 渲染器（未实现）

**状态：NOT IMPLEMENTED。** 本目录当前只有这份接口说明，没有任何渲染代码。

## 未来接口草案（尚未实现，仅约定）

```python
# 尚未实现的伪接口，故意不落地为可调用代码，避免"看起来能用"
class Renderer(Protocol):
    name: str
    def render(self, spec: dict, workdir: Path) -> Path: ...   # 返回 mp4 路径
    def probe(self, video_path: Path) -> dict: ...             # 返回时长/分辨率/响度
```

## 计划中的实现路径

| 步骤 | 依赖 | 说明 |
| --- | --- | --- |
| 静帧与图表获取 | Matplotlib（可选依赖 `charts`） | 由 `visual_designer` 的图表规格驱动 |
| 时间线合成 | FFmpeg | 按 `render_spec.timeline` 拼图层 |
| 字幕烧录 | FFmpeg `subtitles` 滤镜 | 读取 `subtitles/*.srt` |
| 音频混合与响度归一 | FFmpeg `loudnorm` | 目标 -14 LUFS |
| 质检 | FFmpeg `ffprobe` + 自定义规则 | 见 `quality_control/qc_checks.yaml` |

## 为什么现在不做

- 用户约束：第一阶段预算优先，且不要求完成视频引擎。
- 无渲染引擎时任何 `/render` 接口都只能返回假状态，这违反"不伪装已实现"的要求。

因此：`/api/system/status` 的 `features.video_rendering` 恒为 `false`，
且没有注册任何渲染相关路由。
