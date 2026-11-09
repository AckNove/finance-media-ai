# subtitles/ —— 字幕规范（第一阶段仅样式与格式约定）

竖屏平台（抖音、小红书）字幕要点：**一行 14 字以内、底部安全区 180px 以上**。

## 样式

```yaml
style: bottom_center
font_family: "思源黑体 Medium"
font_size_px: 64
line_height: 1.25
max_chars_per_line: 14
max_lines: 2
safe_margin_bottom_px: 180
outline_px: 3
outline_color: "#000000"
text_color: "#FFFFFF"
emphasis:
  enabled: true
  method: color_highlight      # 关键词变色，不使用放大抖动
  highlight_color: "#F59E0B"
```

## 文件格式

- 交付 `SRT`（UTF-8 无 BOM），文件名 `{script_slug}-v{version}.srt`。
- 时间码 `HH:MM:SS,mmm --> HH:MM:SS,mmm`，单条持续 1.2s–6s。
- 数字与单位保持原样，不做四舍五入改写；与口播不一致视为缺陷。

## 断行规则

1. 不把数字与其单位拆到两行（"1600" 与 "万英镑" 必须同行）。
2. 不把专有名词拆行。
3. 每行 ≤ 14 字，超出必须改写句子而不是缩小字号。

## 尚未实现

- 自动断行脚本、自动对齐、字幕烧录：均未实现（见 `../renderers/README.md`）。
