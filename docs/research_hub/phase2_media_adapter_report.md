# Phase 2 · 媒体引擎适配层报告

范围：`backend/app/capabilities/media/`（`engine.py`、`openkatana.py`）与它依赖的
`backend/app/core/job_paths.py`。本文件只讲「我们怎么把一个外部渲染引擎包成可以放心调用的能力」，
不讲引擎自己怎么实现。

## 1. 为什么要一层适配

OpenKatana 0.1.3 是命令行工具（`python -m openkatana call <tool> --workspace <ws> --arguments <file>`），
它的 75 个工具、参数契约、错误语义、路径约定都只存在于它自己的代码与文档里。如果业务代码直接
`subprocess` 调它，会出现三类必然事故：

1. **它会用错 ffmpeg**。`openkatana/agent_api.py:60` 与 `cli.py:181` 用 `shutil.which('ffmpeg')`，
   全项目大量裸 `'ffmpeg'` 调用 → 默认命中 PATH 上的 4.2.2（不支持 `-fps_mode`，视频导入直接失败）。
2. **错误没有类型**。引擎抛的是 `ContractError` / 普通 traceback，业务侧无法区分「我参数写错了
   （不该重试）」和「进程超时（可以重试）」。
3. **路径是相对的、隐式的**。`import_media` 产出的是项目内相对路径（`plates/<id>/%05d.png`），
   渲染产物是 `.openkatana/jobs/<job>/render/video.mp4`，都要按项目目录解析。

适配层就是解决这三件事，**不做像素运算**。

## 2. 契约（`engine.py`）

| 类型 | 作用 |
| --- | --- |
| `MediaEngineError(code, message, *, tool, detail, attempts)` | 统一错误。`code ∈ {usage, unavailable, contract, timeout, tool_error, bad_output, not_ready}`；`describe()` 输出一行可读文本 |
| `EngineVersion(name, version, ffmpeg, ffprobe, tool_count, extra)` | 引擎自述 |
| `EngineHealth(engine, ok, version, problems, warnings)` | `health()` 的返回，`problems` 非空即不可用 |
| `ImportResult(kind, path, sha256, width, height, fps, duration, has_audio, raw)` | 导入结果 |
| `VideoEngine`（Protocol） | `health / create_project / get_project / import_media / validate_timeline / save_timeline / render` |

错误码的语义是硬约定，`RETRYABLE_CODES = {"timeout", "tool_error"}` 只包含这两种；`contract`
永远不重试——参数错了重试一百次还是错。

## 3. 实现要点（`openkatana.py`，527 行）

- **固定版本**：`PINNED_ENGINE_VERSION="0.1.3"`、`PINNED_ENGINE_COMMIT="f3b09107a9828ef770f5bf63116cd418197c15c2"`。
  引擎版本不一致只记 warning（不阻断），但 `health()` 会把它写进报告，避免「悄悄换了引擎」。
- **ffmpeg 前置 PATH**：`environment()` 把 `<repo>/tools/ffmpeg/bin` 插到 `PATH` 最前面，并设
  `PYTHONIOENCODING=utf-8`，同时**删除 `PYTHONPATH`**（否则子进程可能 import 到半成品模块）。
  `health()` 校验引擎自述的 ffmpeg 路径必须**等于**项目内的 8.1.2，版本必须 ≥5.1，否则记 problem。
- **参数文件**：`_call()` 把参数写成 `<workspace>/.openkatana_io/<tool>.json`，**UTF-8 无 BOM**
  （PowerShell 写 BOM 会让引擎报 `JSONDecodeError: Unexpected UTF-8 BOM`，V2.1 踩过）。
- **重试**：`retries=1`，只对 `timeout/tool_error` 生效；`ContractError` 直接判 `contract` 并抛。
- **调用日志**：每次调用追加一行 JSON 到 `engine_calls.jsonl`（`tool/ok/duration_ms/attempt/error`），
  Phase 2 全流程共 **277 次调用**（其中 215 次是渲染轮询 `get_render`），失败 11 次全部来自契约错误
  （见第 6 节）。
- **两层封装**：`render()`（阻塞，内部轮询 `get_render` 到终态）与 `start_render()`/`get_render()`
  （手动控制）；`save_composition_reloading()` 在 `revision=None` 新建失败时自动 `inspect_composition`
  取 revision 再存。

## 4. 路径白名单（`job_paths.py` 的扩展）

Phase 2 给白名单补了三样东西，都服务于「素材可溯源才能进片子」：

1. `ApprovedSource` 增 `license_name` / `commercial_use` / `credit`，注册表 CSV 增三列；
   `commercial_use ∈ {allowed, not_allowed, unclear, unknown}`，写错抛 `invalid_license`。
2. `assert_renderable(source_id)`：`commercial_use != "allowed"` 或 licence 名为空 → 抛
   `rights_unclear`。渲染前必过这道闸，**授权不明等于不能渲染**，不是「记录一下继续用」。
3. `load_register()`：后置阶段（另一个进程）重新读 `source_register.csv` 并**逐个文件重算 SHA-256**
   与登记值比对，不一致抛 `not_registered`。这样「登记时是一个文件、渲染时是另一个文件」会被发现，
   而不是靠人记。

## 5. 实测（真机，非模拟）

```
OpenKatanaEngine(workspace).health()
  ok: true
  version: 0.1.3
  ffmpeg: C:\Users\25803\finance-media-ai\tools\ffmpeg\bin\ffmpeg.EXE
  tool_count: 75
  problems: []   warnings: []
ffmpeg_version(): ffmpeg version 8.1.2-full_build-www.gyan.dev
```

导入五条素材（3 个视觉 + 2 个不同入点 + 1 个音频）全部成功，详见 `phase2_import_test_report.md`。

## 6. 过程中被引擎纠正的六次（都是真错，不是环境问题）

| # | 报错原文 | 真实原因 | 修法 |
| --- | --- | --- | --- |
| 1 | `create_project: unsupported arguments: title, workspace (supported: name, project_id)` | 把内部字段名当引擎参数 | `create_project(title)` 只发 `{"name": title}` |
| 2 | `synthesize_score: unsupported arguments: duration, notes, sample_rate` | 配乐参数要包在一个 `document` 里 | 发 `{"project_id":…, "document": {duration, sample_rate, seed, notes}}` |
| 3 | `chart.style: unsupported fields: border-bottom` | 组合 style 白名单只有 `border/border-color/border-width/border-style` | 改成独立 3px 分隔元素 |
| 4 | `clips.S01: unsupported fields: loop` | `loop` 是**镜头**字段不是 clip 字段 | `SUPPORTED_CLIP_KEYS` 收紧，单帧 clip 在 shot 层写 `loop:"loop"` |
| 5 | `Composition changed; reload before saving` | `revision=None` 是「仅新建」 | 先试新建，失败则取 revision 再存 |
| 6 | `Project changed since it was opened. Reload before saving` | 渲染后项目 revision 变了 | `save_timeline_with_retry()` 重取 `timeline_revision` 重试 |

另外两个**不是引擎报错、但更贵**的问题写在这里，因为它们是适配层的使用语义：

- **`transform: scale(1,0)` 与 `scale` 轨道会叠加**：元素 style 写了 `scale(1,0)`，再挂一条
  `scale` 轨道，结果高度恒为 0（柱子永远看不见，但数字照常显示，非常容易误判成「画板没问题」）。
  用 540×960 的 A/B/C/D 四案探针确认：**只留轨道、不写 style transform** 才正确。修法写进了
  `build_phase2.py` 的注释，探针脚本留在 `raw/_work/probe_bar.py` 作为证据。
- **渲染输出 key 是 `video` 且相对项目目录**：`render()` 返回 `.openkatana/jobs/<id>/render/video.mp4`，
  第一次渲染其实**成功了**（1200 帧、sha256 已生成），但业务侧只读 `output/path` 才报
  「render finished without an output file」。现在的解析顺序是 `video → output → path`，再按项目目录拼绝对路径。

## 7. 测试（`tests/test_media_engine.py`，11 例）

| 用例 | 断言 |
| --- | --- |
| `test_environment_puts_project_ffmpeg_first_on_path` | PATH 首位是 `tools/ffmpeg/bin`，且 `PYTHONPATH` 被移除 |
| `test_argument_file_has_no_bom` | 参数文件首字节不是 `EF BB BF`，且能被 `json.loads` 解析 |
| `test_contract_error_is_not_retried` | `ContractError` → `code="contract"`，`attempts == 1` |
| `test_tool_error_is_retried_once` | 普通非零退出 → 重试一次后抛 `tool_error` |
| `test_timeout_is_reported_as_timeout` | 超时 → `code="timeout"` |
| `test_missing_output_is_bad_output` | 返回体没有产物字段 → `bad_output` |
| `test_calls_are_logged_as_jsonl` | 每次调用一行 JSON，字段齐全 |
| `test_health_reports_foreign_ffmpeg_as_problem` | 引擎自述的是系统 ffmpeg → `problems` 非空 |
| `test_health_warns_on_unexpected_engine_version` | 版本不是 0.1.3 → 只 warning |
| 其余两例 | 未安装时的 `unavailable`、`require_ready()` 抛 `not_ready` |

合计全库回归：**354 passed, 1 skipped, 1 warning**（唯一 warning 是既有的 FK 环 SAWarning）。
