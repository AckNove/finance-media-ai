# 验收标准：怎么判定"真的做完了"

> 本文档的存在意义只有一个：**防止把"目录建好了""接口占位了""Prompt 写好了"当成功能完成。**
> 任何一条不满足，状态只能是 `PARTIAL` 或 `NOT IMPLEMENTED`。

---

## 1. 术语：DONE / PARTIAL / NOT IMPLEMENTED

| 状态 | 判定标准（全部满足才算） |
|---|---|
| **DONE** | ①代码已写入仓库；②有自动化测试且实跑通过；③测试**不依赖网络与密钥**（真实调用另设 `integration` 标记）；④文档写清怎么用、边界在哪；⑤失败路径有明确行为（不是 500 崩）；⑥能给出复现命令 |
| **PARTIAL** | 有代码或数据结构，但缺①②③中的任意一项；**必须在文档中写明缺什么** |
| **NOT IMPLEMENTED** | 只有文档/目录/Prompt/占位返回 |

### 反面清单（出现任意一条即判定为 NOT IMPLEMENTED，即使代码存在）

| 反模式 | 为什么不算 |
|---|---|
| 端点返回固定 `{"status": "ok"}` 或 `[]` 假装查过数据库 | 没有真实数据路径 |
| `RecordingDispatcher` 登记了 `agent_runs` 就说"Agent 跑过了" | 只写了 pending，没有执行、没有输出 |
| Prompt 里写"你可以使用搜索工具"但没有任何工具注册 | Prompt 不是能力 |
| 把 Fake 网关跑通说成"已接入模型" | 没有真实供应商调用与费用记录 |
| 目录里放了 `README.md` 说"计划支持 PDF" | 计划不是实现 |
| 测试断言 `assert response.status_code == 200` 而不检查内容 | 空断言 |
| 只测 happy path，错误分支从未执行 | 未验证失败行为 |

---

## 2. 通用质量门（每阶段必过，缺一不可）

| # | 门 | 命令 / 判据 |
|---|---|---|
| G1 | 全量测试通过 | `.venv\Scripts\python.exe -m pytest` → 0 failed |
| G2 | 测试离线 | 默认运行无任何外网请求（真实调用需 `-m integration`） |
| G3 | 迁移可逆 | `alembic upgrade head` → `downgrade -1` → `upgrade head` 均 exit 0；`alembic check` 无新操作 |
| G4 | 模型与迁移一致 | `alembic check` 输出 "No new upgrade operations detected." |
| G5 | 无假功能 | 新增能力在 `current_state_audit.md` 里状态与实测一致 |
| G6 | 错误码一致 | 全部错误走 `{"error":{code,message,details}}`；无裸 500 |
| G7 | 成本可见 | 任何产生费用的调用都写 `model_calls`/`tool_calls`，`GET /cost` 可汇总 |
| G8 | 文档同步 | `README.md` 与对应 `docs/` 更新；设计变更先改文档再改代码 |
| G9 | 无敏感信息 | 无 `.env`、无密钥、无 token 进 git（`git diff` 人工复核） |
| G10 | 提交可回溯 | 普通 commit + push，`git status` clean，本地 HEAD 与 origin/main 一致 |

---

## 3. 分阶段验收清单

### 2.0 骨架与配置

- [ ] `config/models.yaml`、`agent_routing.yaml`、`pipelines.yaml` 存在且被 schema 测试校验
- [ ] `backend/app/{execution,capabilities,parsers}/__init__.py` 存在，且有 Protocol 定义（`SearchProvider`/`Fetcher`/`ModelGateway`/`Tool`）
- [ ] `GET /api/system/status` 的 `features` 新增 `model`/`search`/`execution` 三键且默认 `false`
- [ ] `pytest` 全绿；**没有新增任何业务端点**

### 2.1 模型适配层

- [ ] 未配置密钥时 `ModelGateway.complete()` 抛 `DependencyUnavailableError`（503），**不返回编造内容**
- [ ] `FakeModelGateway` 能返回带 `usage` 的结构化输出，测试覆盖 6 个分支（成功/未配置/超时/schema 非法/预算超限/重试成功）
- [ ] 预算闸门三层（单次/任务/日）各有一个失败用例
- [ ] `pricing_unknown: true` 时 `cost_estimate` 为 `null` 且有 `pricing_unknown=true` 标记（不猜数字）
- [ ] `@pytest.mark.integration` 的真实调用测试存在且默认跳过

### 2.2 搜索与抓取

- [ ] `POST /api/sources/{id}/fetch` 成功写入 `source_snapshots`，文件落 `data/raw/snapshots/{sha256}.html`
- [ ] 同一 URL 两次抓取 → 两行快照，旧行不被覆盖
- [ ] 403/404/超时/超限四分支状态正确（`blocked`/`http_error`/`timeout`/`too_large`），且**仍返回 2xx + 记录**
- [ ] `NullSearchProvider` 未配置时返回 `not_configured`，Agent 侧产生 `insufficient_evidence` 而非编造

### 2.3 解析

- [ ] PDF/Excel/HTML 各 3 个用例（正常/损坏/超限）
- [ ] 从 fixture 中抽取的引文满足 `text[char_start:char_end] == quote`
- [ ] 无文本层 PDF → `has_text_layer=false`，不返回空字符串冒充解析成功

### 2.4 编排与执行

- [ ] 3 节点 DAG 在 Fake 环境从 `created` 跑到 `succeeded`，`task_events` 事件完整（≥6 条）
- [ ] DAG 成环 → 创建时 422（不是运行时死锁）
- [ ] 租约过期 → 子任务自动回收并 `attempts += 1`
- [ ] 工具越权（如 Scriptwriter 调 `web_search`）→ `ToolCallStatus.denied` + `task_events`
- [ ] `execution.enabled=false` 时 `/start` 返回 409 并给出 tick 提示；tick 能推进 ≥1 个子任务
- [ ] `GET /api/research-tasks/{id}/cost` 返回模型/工具成本汇总

### 2.5 证据链

- [ ] 任意 `verified_fact` 可解析到 `evidence → source_snapshot → char_start/char_end`
- [ ] 篡改引文后核查 → `quote_mismatch=true` → 结论自动降档为 `unconfirmed`
- [ ] 失效证据（`invalidated_at` 非空）不参与 `tier_stats`
- [ ] `db_write` 幂等键重复 → 返回 `skipped`，不产生重复行

### 2.6 端到端

- [ ] Fake 全链路测试进 CI 且通过（选题→任务→证据→报告→核查）
- [ ] 真实运行（若授权）：报告每条事实有证据、成本 ≤ 上限、`model_calls` 与 `/cost` 一致
- [ ] 产出固定格式的 e2e 报告（含实际成本）

### 2.7 选题中心

- [ ] `PUT /api/topics/{id}/collections`：1–3 个、primary 唯一、未知 code → 422
- [ ] `seed_collections.py` 幂等：连跑两次结果一致
- [ ] 老数据回填：所有 `column_id` 非空的选题都有对应 `topic_collections(is_primary=1)`
- [ ] 合并后原选题 `merged_into_topic_id` 非空且不可再立项；拆分保留来源引用
- [ ] 评分六维权重可配置，人工 `scored_by=human` 覆盖机器分并留快照

### 2.8 知识中心

- [ ] 只有 `verified_fact` 能提议知识条目；`unconfirmed` 被拒（422）
- [ ] 提议需 `confirm=true` 才落库（默认 dry-run）
- [ ] 未使用 `concepts` 枚举值（遵守决策 3）

### 2.9 内容生产

- [ ] 一份报告生成 2 种形式脚本；每段有 `finding_id` 或显式 `background`
- [ ] 未审批脚本 → 建分镜/素材 422
- [ ] 审批只能走 `POST /scripts/{id}/approve`（人工）；Agent 无对应工具
- [ ] 脚本不能改 `findings.tier`（服务层拒绝）
- [ ] `publications` 仍只能人工写；引用必须带 `script_version`

---

## 4. 本次（纯文档阶段）的验收清单

用户可**当场验证**：

- [ ] `git show --stat HEAD` 只含 `docs/research_hub/*`（+ 1 行 README 索引）
- [ ] `git status --porcelain` 为空
- [ ] `git diff --name-only HEAD~1 HEAD -- backend/ config/ data/` → **无输出**（业务代码零改动）
- [ ] 无 `alembic/versions/` 新增文件
- [ ] `pytest` → 96 passed（与改动前一致）
- [ ] `docs/research_hub/` 下 10 份文档可独立阅读：README、current_state_audit、system_architecture、business_workflow、database_design、agent_architecture、tooling_architecture、api_design、state_machine、implementation_roadmap、acceptance_criteria
- [ ] 每份文档含 Mermaid 图或表格（不是纯散文）
- [ ] 文中的表名/字段名能在 `database_design.md` 的 ER 图里找到（自洽）
- [ ] 文中标注为"已实现"的能力，与仓库代码一致（抽查 3 处）

---

## 5. 风险登记表

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **无鉴权**且未来可能绑定公网 | 未授权访问/误发布 | 第二阶段继续 `127.0.0.1`；开公网前必须加 token 鉴权（列为 2.10 前置） |
| R2 | SQLite 并发写 | worker 与 API 同时写可能 `database is locked` | 单 worker、`max_concurrency=1`、写事务短小；如并发上升迁 PostgreSQL（模型层已兼容） |
| R3 | 模型幻觉 | 错误事实进入内容 | 五档结论 + 强制证据 + Fact Checker 门 + 引文 hash 校验 |
| R4 | 成本失控 | 单任务烧钱 | 三层预算闸门 + 调用前拦截 + 日预算 + `/cost` 可见 |
| R5 | 抓取合法性与版权 | 法务与平台风险 | 只存快照用于核查；对外引用标来源与链接；遵守 robots；不整篇转载 |
| R6 | 供应商锁定 | 换模型成本高 | ModelGateway 抽象 + OpenAI 兼容协议 + 价目表外置 |
| R7 | 文件体积膨胀 | 磁盘与备份 | 快照存压缩 HTML；`data/raw` 已被 `.gitignore` 排除；设定 5MB/文件、单任务 200 源上限 |
| R8 | 时区/币种/单位错配 | 结论错误 | 证据与 finding 强制带 `currency/unit/as_of`；跨口径必须显式标注 |
| R9 | 迁移期双写不一致 | `column_id` 与 `topic_collections` 漂移 | 服务层单点写两处 + 迁移期校验脚本；2.10 前保持兼容读 |
| R10 | 设计文档与实现漂移 | 文档变谎话 | 每阶段 G8 强制同步；`current_state_audit.md` 是唯一状态真相 |

---

## 6. 需要用户确认的事项（进入开发前）

| # | 问题 | 为什么必须问 | 默认建议 |
|---|---|---|---|
| Q1 | 2.1 接哪家模型？预算上限多少？ | 直接产生费用 | 先用一家 OpenAI 兼容的国产模型，日预算 ¥5 |
| Q2 | 2.2 接哪家搜索？是否允许联网抓取？ | 费用 + 合规 | 先 `NullSearchProvider`（手动粘贴 URL），确认后再接 |
| Q3 | 2.6 是否允许一次真实付费端到端运行？ | 唯一整链烧钱 | 先 Fake 全流程，再授权 1 次真实运行（≤¥2） |
| Q4 | 迁移批准：38 表方案是否一次性做完，还是按 2.2/2.4/2.5/2.7/2.9 分 5 次迁移？ | 分次更安全但更慢 | **推荐分 5 次**（每次可单独回滚） |
| Q5 | `topics.column_id` 何时删除？ | 影响兼容与回滚 | 2.10 之后，确认回填稳定再删 |
| Q6 | 是否现在就把 `backend/app/main.py` 的 DESCRIPTION 旧定位文案改掉？（上次因"禁止改 API"未动） | 属 API 文案 | 允许改，0 风险 |

---

## 7. 复现命令集合（给验收者）

```powershell
# 1. 环境
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 数据库
Copy-Item .env.example .env
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check

# 3. 测试
.venv\Scripts\python.exe -m pytest

# 4. 起服务
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8010
# 浏览器打开 http://127.0.0.1:8010/docs

# 5. 本次文档阶段的自证
git show --stat HEAD
git diff --name-only HEAD~1 HEAD -- backend/ config/ data/
```

> 任何一条命令失败，对应能力即不得标记为 DONE。
