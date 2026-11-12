# skills/ — Agent Skills 目录

本目录遵循 [Agent Skills](https://agentskills.io/specification) 开放格式：一个 skill 是一个目录，
目录里至少有一个 `SKILL.md`，开头是 YAML frontmatter（`name`、`description`，可选 `license` 等），
正文是给 Agent 的操作说明。

**不发明新的 Skill 文件格式。** 目录仍遵循开放规范；`registry.yaml` 为运行时增加固定摘要、
来源、版本、任务类型、Agent、依赖和工具权限的机器可读登记。

## 目录结构

```
skills/
├── registry.yaml            # 固定摘要与权限映射
├── ak/                      # AK 自有技能（本仓库原创，随仓库发布）
│   ├── accessible-explainer/SKILL.md
│   ├── investigative-storytelling/SKILL.md
│   └── ak-signature/SKILL.md
└── vendor/                  # 第三方技能（原样复用，不改写内容）
    └── anthropic/
        ├── pdf/             # Anthropic document-skills · pdf
        ├── xlsx/            # Anthropic document-skills · xlsx
        ├── THIRD_PARTY_NOTICES.md
        └── PROVENANCE.md    # 来源 / 许可证 / commit / 依赖 / 安全边界
```

## 两类技能的边界

| 类型 | 目录 | 谁写的 | 能不能改 |
| --- | --- | --- | --- |
| 自有 | `skills/ak/` | AK 团队 | 可以改，改完要更新本文件的说明 |
| 第三方 | `skills/vendor/` | 上游项目 | **不改内容**；需要改造就在自己的代码里包一层，并保留原许可证 |

## 与 AK Research Hub 的关系

- **Skill 负责"怎么做"**（方法、结构、判据），**Tool 负责"能做"**（真实 I/O）。
- 技能目录本身不执行任何东西；它由 Agent（或人）在写研究笔记 / 写脚本 / 做分镜时读取并遵守。
- 工具侧的落点是 `backend/app/capabilities/tools.py` 的 `ToolRegistry`，与技能目录解耦。
- `backend/app/skills/registry.py` 负责发现、校验和按任务加载 Skill；解析类工具通过
  `ApprovedSources` 再验一次 job 白名单与哈希。加载/工具调用只写状态审计日志，
  不记录输入或凭据。Skill 本身不是模型执行器；真实 Agent 执行仍需后续接入。

## 使用纪律

1. 写脚本前必须先读 `skills/ak/ak-signature/SKILL.md`（风格与合规是硬约束）。
2. 解释复杂机制时读 `skills/ak/accessible-explainer/SKILL.md`。
3. 做深度研究与故事结构时读 `skills/ak/investigative-storytelling/SKILL.md`。
4. 处理 PDF / Excel 文件读 `skills/vendor/anthropic/{pdf,xlsx}/SKILL.md`，但**优先走**
   `backend/app/capabilities/adapters/markitdown_adapter.py` 的统一入口，避免每个脚本各写一套解析。
5. 第三方技能的许可证与来源见 `skills/vendor/anthropic/PROVENANCE.md`；新增第三方技能必须同格式登记。
