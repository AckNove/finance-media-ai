# Agent 团队（第一阶段）

本目录定义九个角色的**角色文件、统一协议与可扩展调度接口**。
第一阶段**不接入任何大模型**，也不联网抓取，因此这里没有任何"跑起来"的研究能力——
只有可被人或未来的执行器读取的规范。

## 文件结构

```text
agents/
├── roster.yaml               # 唯一机器可读名册（后端 agent_registry.load_roster() 读取）
├── PROTOCOL.md               # 统一输入输出协议（10 字段，对应 agent_runs 表）
├── README.md                 # 本文件
├── orchestrator/PROMPT.md
├── financial_researcher/PROMPT.md
├── football_researcher/PROMPT.md
├── industry_researcher/PROMPT.md
├── case_analyst/PROMPT.md
├── fact_checker/PROMPT.md
├── scriptwriter/PROMPT.md
├── visual_designer/PROMPT.md
└── video_producer/PROMPT.md
```

## 九个角色

| key | 名称 | 一句话职责 |
| --- | --- | --- |
| `orchestrator` | Research Orchestrator | 任务拆解、分派、整合、冲突收口 |
| `financial_researcher` | Financial Researcher | 金融工具/产品/机制/风险结构 |
| `football_researcher` | Football Business Researcher | 俱乐部财务、转会、足球金融 + 类比素材 |
| `industry_researcher` | Industry Researcher | 产业链、上下游、利润分配 |
| `case_analyst` | Case Analyst | 金融事件、企业案例、资金流向 |
| `fact_checker` | Fact Checker | 事实/数字/公式/来源/类比边界/合规审核 |
| `scriptwriter` | Scriptwriter | 研究报告 → 视频脚本 |
| `visual_designer` | Visual Designer | 知识图解、产业链图、资金流向图、分镜 |
| `video_producer` | Video Producer | 配音、字幕、合成与质检（当前仅规格与质检） |

## 流程位置

```text
orchestrator.plan_research
   ├─> financial_researcher / football_researcher / industry_researcher / case_analyst
   ├─> orchestrator.integrate_results
   ├─> fact_checker.verify_claims + review_analogy + review_compliance   ← 不通过不得进脚本
   ├─> scriptwriter.write_script
   └─> visual_designer.design_storyboard → video_producer.build_render_spec + run_qc
                                                    ↓
                                              人工终审 → 人工发布
```

`fact_checker` 是唯一的强制闸门：任何 `blocker` 级问题都必须修复后才能继续。

## 代码侧接口

| 位置 | 作用 |
| --- | --- |
| `backend/app/services/agent_registry.py::load_roster()` | 读 `roster.yaml`，返回 `AgentRoleSpec` 列表 |
| `...::roster_keys_missing_prompts()` | 校验提示词文件是否存在（`/api/system/status` 用它报告 `agents)` |
| `...::AgentTaskEnvelope` | 协议对象的 Python 形态，`to_dict()` 供持久化 |
| `...::TaskDispatcher` | 抽象调度接口，未来接入模型时实现子类 |
| `...::RecordingDispatcher` | 当前实现：只登记 `agent_runs`（`pending`），不执行、不编造 |
| `...::UnavailableDispatcher` | 执行器不可用时抛 503，明确失败而不是返回假结果 |

## 一致规则（九个角色共同遵守）

1. 不编造来源、数据、案例、引文。
2. 区分事实 / 推断 / 观点。
3. 重要事实必须关联证据。
4. 不确定内容显式标记。
5. 足球类比必须说明成立与不成立的部分。
6. 不自动发布任何内容。
7. 不输出面向个人的具体投资买卖建议。

完整版见 `PROTOCOL.md` 第 3 节。
