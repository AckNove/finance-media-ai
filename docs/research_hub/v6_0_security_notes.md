# V6.0 安全说明

> 这份文件只写**已经落在代码里、能被测试或命令复核**的边界。
> 计划做但没做的，会在最后一节如实列出来。

## 1. 这个服务是什么、不是什么

**是**：给一个人在自己电脑上跑的自媒体研究工作台（单用户、本机、`127.0.0.1`）。
**不是**：多租户 SaaS、公网服务、对外开放的 API。

因此：`scripts/start.ps1` 默认绑 `127.0.0.1`；把端口暴露到公网前请自己加反向代理与鉴权——
当前**没有账号体系**，任何人能访问端口就等于能以"人"的身份建任务、确认结论。这一点写在
`docs/research_hub/v6_0_operator_manual.md` 的五条铁律里。

## 2. 调用者身份（谁能做什么）

| 机制 | 位置 | 说明 |
| --- | --- | --- |
| 身份只有三种 | `backend/app/core/caller.py` | `human`（人）/ `dev`（本地调试）/ `agent`（内部执行器） |
| HTTP 请求只能是 human/dev | `build_http_caller()` | **`X-Caller` 只用于标记"这是本地调试"**；写 `agent` 之类会被记一条 warning 并置 `forged_header=True`，身份不升级 |
| agent 只能由服务端创建 | `CallerContext.for_executor()` | 必须带 `task_id` + `attempt_token`，外部请求构造不出来 |
| 每个端点都要声明允许谁 | `backend/app/api/router.py` 的 `ROUTE_CALLERS` | 目前 71 个 API 操作，只有 `GET /health` 是公开的 |
| 声明必须与挂载一致 | `backend/app/core/startup_checks.py` 的 `_check_route_callers` | 启动时走查真实路由，**清单说 human 但端点忘了挂依赖 → 直接拒绝启动**；反之亦然 |
| dev 端点在正式环境消失 | `require_caller(CallerKind.DEV)` | 非 development 环境返回 404（不暴露"这里有个调试口"） |

越权时返回 422 + `details.reason=permission_denied`（不是 500，也不假装成功）。

## 3. 密钥

- 只从**环境变量**读（`DEEPSEEK_API_KEY` 等），`.env` 由 `load_dotenv_into_environ()` 注入进程环境；
  `.gitignore` 已忽略 `.env`、`.env.*`，`git ls-files .env` 恒为空。
- 展示一律走 `mask_secret()`：只留 3 位前缀 + 长度，**不回显尾部**。
- 服务端错误体会过 `_redact()`：整串密钥替换成 `[服务端响应含密钥，已隐去]`、尾 4 位替换成 `****`
  （DeepSeek 的 401 会把密钥尾 4 位回显出来，这是写这段代码的直接原因）。
- `model_calls.prompt_preview` 只存提问前几十字；验收脚本每次都断言
  `SELECT COUNT(*) … WHERE prompt_preview LIKE '%sk-%'` = 0。
- `tests/conftest.py` 顶部清空所有 provider 密钥并设 `FM_SKIP_DOTENV=1`，**跑测试永不真调用**。

## 4. 数据库

- 真实开发库 `data/finance_media.db` 全程**只读**：不做迁移、不开 WAL、不 seed、不回填。
- 七处代码会主动拒绝它（都以路径后缀判定，不靠人记）：
  `run_worker.py`（不许在只读库上跑任务）、`seed_collections.py`、`import_digest.py`、
  `model_smoke.py`（不许把审计行写进去）、`produce_video.py`、`restore_db.py`（不许覆盖）、
  `enable_wal.py`（默认拒绝，需显式 `--allow-development-db`）。
- 备份允许读它（`backup_db.py` 以 `mode=ro` 打开），产物只写到 `data/backups/`（已被 `.gitignore`）。
- 恢复是破坏性操作：先校验备份文件 SHA-256 与完整性，再**自动留一份 `.pre-restore-<时间戳>.db`**，
  恢复后复核表数与 `integrity_check`。
- 迁移只在独立临时库演练；`alembic check` 必须报 "No new upgrade operations detected."。

## 5. 内容出口（最容易被越过的地方）

| 规矩 | 实现 | 效果 |
| --- | --- | --- |
| 只有**人工确认**的结论能进成稿 | `backend/app/research/draft_gate.py` | 模型判定 `supported` **不等于**人工确认；两个状态分开存 |
| 三处出口都拦 | 建视频任务 / 建脚本（带 `research_report_id`）/ `produce_video` CLI | 未确认 → 409 + `details.reason`（`no_checks` / `not_confirmed`） |
| 视频任务锁定证据 | `video/handoff.py` 写 `job.json` 的 `draft_gate.check_ids` | 交接稿分两节：可进成稿 / 尚未确认（后者明写"不得进成稿"） |
| 撤回确认 → 旧任务失效 | `handoff.invalidate_jobs()` | 写 `INVALIDATED.md` 并把 `usable` 置 false，**不删任何产物** |
| 不自动发布 | `operations_service.py` | `publish_state=published` 必须带 `published_url`，只是**留痕**；没有任何平台 API 调用 |

## 6. 素材与路径

- 一切外部素材先进白名单：`backend/app/core/job_paths.py` 只允许任务目录下 `input/approved_sources/`
  里的文件，登记时记 SHA-256、检查符号链接、拒绝 `../` 穿越，**注册后改文件会被哈希不符拦下**。
- 素材授权：`assets_manifest.csv` 里 `commercial_use != allowed` 的一律拒绝渲染，不伪造来源。
- 自绘图表、数据卡、产业链图登记为 B 类（原创）并写明 credit；第三方 Skill / 演示素材在
  `skills/vendor/anthropic/PROVENANCE.md` 与 `tools/ffmpeg/SOURCE.md` 记了固定版本与许可证。
- 联网抓取：SSRF 逐 IP 校验、robots 最长匹配、手动逐跳重定向、字节上限、404/反爬页判
  `thin_content`/`bot_wall` 不当作内容。

## 7. 工具链固定版本

| 组件 | 版本 | 为什么固定 |
| --- | --- | --- |
| FFmpeg | `8.1.2-full_build-www.gyan.dev`（归档 sha256 `0fff18…7059`） | `-fps_mode` 等滤镜需要 ≥5.1；不进 PATH、不覆盖系统那份 |
| OpenKatana | `0.1.3`（commit `f3b0910…`，Apache-2.0） | 独立 venv 放 `tools/openkatana`，不入库 |
| Anthropic Skills | commit `2c5958e…`（Apache-2.0） | 只取 `pdf`/`xlsx` 两个目录，附 PROVENANCE |

## 8. 没做/做不到的（别当成有）

1. **没有账号与权限分级**：能在本机打开 8000 端口的人，就是"人"这个身份。
2. **没有审计日志文件**：审计在数据库（`model_calls`、`tool_calls`、`task_events`），
   没有单独的不可篡改日志。
3. **没有限流防护**（对外维度）：只有对模型服务商的本地限流（60 次/分、并发 2）。
4. **没有加密**：密钥在 `.env` 明文，数据库明文；本机单人场景可接受，别放共享机器。
5. **没有自动发布**，也不打算做——平台账号 API 需要企业资质（需求矩阵里标 NOT_IMPLEMENTED）。
