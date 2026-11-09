# 脚本撰写模板（writing）

## 输入

- 选题：`{{topic_title}}`
- 研究报告：`research_report_id={{research_report_id}}`（必须已通过 `fact_checker` 审核）
- 视频模板：`{{template}}`（见 `video_engine/templates/`）
- 目标时长：{{target_duration_seconds}} 秒
- 受众：`{{audience_level}}`

## 结构（按模板）

{{structure}}

## 写作要求

1. 前 10 秒必须出现具体信息（数字、条款、时点），禁止寒暄与自我介绍。
2. 中文口播 3.5–4.5 字/秒；段落时长合计在目标时长 ±10% 内。
3. 每个 `segment` 必须带：
   - `narration`：口播原文
   - `duration_seconds`
   - `on_screen`：屏上文字（≤14 字/行）
   - `evidence_refs`：该段依据的证据 id（观点段可为空但必须标 `nature_note=opinion`）
4. 推断用"这说明/可能意味着"，观点用"我的看法是"，事实用陈述句 + 时点。
5. 若含足球类比：必须有一段口播说明"哪里像、哪里不像"。

## 输出

```json
{
  "summary": "...",
  "template": "{{template}}",
  "target_duration_seconds": {{target_duration_seconds}},
  "hook": "...",
  "segments": [{"seq": 1, "role": "hook", "narration": "...", "duration_seconds": 10, "on_screen": "...", "evidence_refs": [1], "nature_note": "fact"}],
  "disclaimer": "本内容为知识讲解，不构成投资建议。数据以标注时点为准。",
  "word_count": 0,
  "limitations": "...",
  "open_questions": [],
  "handoff": {"next_agent": "visual_designer", "reason": "..."}
}
```

## 禁止

- 引入研究报告之外的新事实或新数字。
- 删除免责声明或类比边界。
- 出现收益暗示、绝对化表述、标的推荐。

## 自检

- [ ] 每段有 `evidence_refs` 或标注观点
- [ ] 时长合计符合目标
- [ ] 数字与研究报告完全一致
- [ ] 免责声明存在
