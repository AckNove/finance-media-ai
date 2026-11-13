# 内容质检：v6_accept_industry

技术质检（分辨率/帧率/黑帧/静音）只管容器与信号；这一份管**画面上的数字与结论有没有出处**。它不能说明内容合格。

- 结论：未通过
- 硬错 3 条 / 提醒 1 条
- shots：3
- shots_with_narration：3
- distinct_numbers_on_screen：1
- numbers_in_material：128
- assets_listed：1

## 硬错（必须处理）

- `shot_without_source`（镜头 sh01）：这一镜没有挂任何来源编号，画面上的说法无法回溯
- `shot_without_source`（镜头 sh02）：这一镜没有挂任何来源编号，画面上的说法无法回溯
- `shot_without_source`（镜头 sh03）：这一镜没有挂任何来源编号，画面上的说法无法回溯

## 提醒（建议看一眼）

- `subtitle_not_from_narration`（镜头 sh03）：字幕不是旁白的片段：可能是另写的文案，需要人确认口播与字幕一致

---

本文件由 `backend/app/video/content_qc.py` 生成：只做可判定的核对，
不调用模型、不评价好坏。发布前仍需要人看完整片。

