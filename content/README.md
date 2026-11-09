# content/ —— 内容生产工作区

存放从选题到发布之间的所有产出物。数据库记录（`topics`/`scripts`/`publications`）
保存结构化字段与状态；本目录保存可人工编辑的正文文件。

## 子目录

| 目录 | 内容 |
| --- | --- |
| `topics/` | 选题卡（问题、受众、类比要求、优先级） |
| `scripts/` | 脚本 Markdown（与 `scripts` 表版本对应） |
| `storyboards/` | 分镜表（每个镜头的时间、画面、屏上文字） |
| `assets/` | 图表、图示、音视频中间素材（大文件不入库，见 `.gitignore`） |
| `published/` | 已发布内容的快照与效果数据 |

## 命名约定

```text
scripts/{topic_slug}/{template}-v{version}.md
storyboards/{topic_slug}/{template}-v{version}.yaml
topics/{topic_slug}.md
published/{platform}/{YYYY-MM-DD}-{topic_slug}.md
```

## 状态流转（与 `content_rules.yaml` 一致）

```text
idea → researching → researched → reviewed → scripted → approved
     → storyboarded → produced → qc_passed → published
```

硬规则：

- `approved` 之前不得进入分镜制作。
- `published` 只能由人工操作，任何 Agent 不得写发布状态。
- 同一脚本版本的 `approved_at` / `approved_by` 必须记录在 `scripts` 表中。

## 素材与 Git

`assets/` 下的媒体大文件默认被 `.gitignore` 忽略（只保留目录结构与本说明）。
需要版本化的素材请放在外部存储，并在 `media_assets` 表中登记 `checksum` 与 `file_path`。
