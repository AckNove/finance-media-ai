# prompts/ —— 提示词库

`agents/*/PROMPT.md` 定义**角色**（我是谁、我负责什么）；
本目录存放**任务模板**（这次要做什么、按什么格式交活）。

二者配合使用：角色文件回答"角色边界"，任务模板回答"本次指令"。

## 目录

| 目录 | 内容 |
| --- | --- |
| `system/` | 全局系统提示：身份、口径、硬性规则、输出语言 |
| `research/` | 研究任务模板（工具/机制/案例/产业链） |
| `writing/` | 脚本撰写模板（四类视频结构） |
| `review/` | 审核模板（事实/一致性/类比/合规） |
| `production/` | 制作模板（分镜、渲染规格、质检） |

## 占位符约定

模板使用双花括号占位符，由调度器在运行时填充：

| 占位符 | 来源 |
| --- | --- |
| `{{task_id}}` | 调度器生成 |
| `{{agent_name}}` | `roster.yaml` 的 `key` |
| `{{topic_title}}` | `topics.title` |
| `{{research_report_id}}` | `research_reports.id` |
| `{{audience_level}}` | `topics.audience_level` |
| `{{jurisdiction}}` / `{{as_of_date}}` | 任务约束 |
| `{{sources}}` | 已登记来源列表 |
| `{{evidence}}` | 已登记证据列表 |
| `{{template}}` | 四类视频模板之一 |

## 纪律

- 模板里不写任何真实密钥、账号或内部路径。
- 模板不得指示模型"忽略事实核查"或"编造示例"。
- 修改模板必须与 `agents/PROTOCOL.md` 的输出结构保持一致。

## 第一阶段状态

这些模板是**纯文本资产**，当前没有任何代码调用它们（未接入大模型）。
调度器只把任务登记进 `agent_runs`，`output` 为空。
