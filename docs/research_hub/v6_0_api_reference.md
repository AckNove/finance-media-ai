# finance-media-ai API 参考

- 版本：0.1.0
- 路径数：55

> 这份文档由 `python -m backend.scripts.dump_api_doc --out <文件>` 从 `create_app().openapi()` 现取生成，
> 不要手改——改了下次生成会被覆盖。权限列来自 `backend/app/api/router.py` 的 `ROUTE_CALLERS`。

## 调用者身份

- **人**：浏览器/脚本以人类身份调用（默认入口 `/app` 的前端就是这样调的）。
- **开发调试**：只在非生产环境存在，生产环境一律 404（不暴露端点存在性）。
- **Agent**：只能由服务端内部执行器构造，**外部请求无法靠请求头自报身份**。
- **公开**：没有身份要求（目前只有健康检查）。

## 认证与环境变量

本机自用，没有账号体系：不发送任何 `Authorization` 头也能用。
模型密钥只从环境变量读（`.env` 里的 `DEEPSEEK_API_KEY`），不会出现在任何响应里。

## 操作清单

| 方法 | 路径 | 摘要 | 允许调用者 | 参数 | 请求体 | 响应 |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/calendar` | 排期列表 | 人 | `platform`（query，可选，string | null）、`status`（query，可选，string | null）、`publish_state`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_CalendarOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/calendar` | 新建排期 | 人 | — | application/json → `CalendarCreate` | 201 `CalendarOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/calendar/analytics` | 按平台汇总 | 人 | — | — | 200 `CalendarAnalyticsOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/calendar/{entry_id}` | 排期详情 | 人 | `entry_id`（path，必填，integer） | — | 200 `CalendarOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| PATCH | `/api/calendar/{entry_id}` | 改排期 | 人 | `entry_id`（path，必填，integer） | application/json → `CalendarUpdate` | 200 `CalendarOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/calendar/{entry_id}/export` | 导出成文件并留记录（导出不是发布，发布仍要人工填链接） | 人 | `entry_id`（path，必填，integer） | application/json → `CalendarExportIn | null` | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/calendar/{entry_id}/metrics` | 一次排期的所有快照 | 人 | `entry_id`（path，必填，integer） | — | 200 `MetricOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/calendar/{entry_id}/metrics` | 回填一次平台数据 | 人 | `entry_id`（path，必填，integer） | application/json → `MetricIn` | 201 `MetricOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/calendar/{entry_id}/metrics/import` | 批量导入平台 CSV（撞时间戳的跳过，坏行带行号报错） | 人 | `entry_id`（path，必填，integer） | application/json → `MetricsImportIn` | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/evidence` | 按主张目标查询证据（附来源摘要） | 人 | `knowledge_item_id`（query，可选，integer | null）、`research_report_id`（query，可选，integer | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_EvidenceDetailOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/evidence` | 登记事实证据 | 人 | — | application/json → `EvidenceCreate` | 201 `EvidenceOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/knowledge` | 知识条目列表 | 人 | `knowledge_domain`（query，可选，string | null）、`verification_status`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_KnowledgeItemOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/knowledge` | 创建知识条目 | 人 | — | application/json → `KnowledgeItemCreate` | 201 `KnowledgeItemOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/knowledge/relations` | 创建知识关联 | 人 | — | application/json → `KnowledgeRelationCreate` | 201 `KnowledgeRelationOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/knowledge/search` | 知识检索（关键词多字段匹配） | 人 | `q`（query，必填，string）、`knowledge_domain`（query，可选，string | null）、`verification_status`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `KnowledgeSearchResult`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/knowledge/{item_id}` | 知识条目详情 | 人 | `item_id`（path，必填，integer） | — | 200 `KnowledgeItemOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| PATCH | `/api/knowledge/{item_id}` | 更新知识条目 | 人 | `item_id`（path，必填，integer） | application/json → `KnowledgeItemUpdate` | 200 `KnowledgeItemOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/knowledge/{item_id}/relations` | 知识条目的关联列表 | 人 | `item_id`（path，必填，integer） | — | 200 `KnowledgeRelationOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-reports` | 研究报告列表 | 人 | `topic_id`（query，可选，integer | null）、`verification_status`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_ResearchReportOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-reports` | 创建研究报告 | 人 | — | application/json → `ResearchReportCreate` | 201 `ResearchReportOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-reports/{report_id}` | 研究报告详情 | 人 | `report_id`（path，必填，integer） | — | 200 `ResearchReportOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-reports/{report_id}/checks` | 结论核查明细（模型判定 + 人工复核，两条状态线） | 人 | `report_id`（path，必填，integer）、`verdict`（query，可选，string | null）、`human_review_status`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_FindingCheckOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-reports/{report_id}/checks/summary` | 核查覆盖情况（按判定与人工状态分组） | 人 | `report_id`（path，必填，integer） | — | 200 `FindingCheckSummaryOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-reports/{report_id}/checks/{check_id}/review` | 人工复核一条核查（只能人写） | 人 | `report_id`（path，必填，integer）、`check_id`（path，必填，integer） | application/json → `FindingCheckReviewIn` | 200 `FindingCheckOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-reports/{report_id}/draft-readiness` | 这份报告能不能成稿（只有人工确认过的结论才允许） | 人 | `report_id`（path，必填，integer） | — | 200 `DraftReadinessOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-reports/{report_id}/video-job` | 从报告开一个视频任务目录 | 人 | `report_id`（path，必填，integer） | application/json → `VideoJobIn | null` | 201 `VideoJobOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-tasks` | 研究任务列表 | 人 | `status`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_ResearchTaskOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-tasks` | 建研究任务（按流水线拆解） | 人 | — | application/json → `ResearchTaskCreate` | 201 `ResearchTaskDetailOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-tasks/{task_id}` | 研究任务详情 | 人 | `task_id`（path，必填，integer） | — | 200 `ResearchTaskDetailOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-tasks/{task_id}/cancel` | 取消任务 | 人 | `task_id`（path，必填，integer） | — | 200 `ResearchTaskDetailOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/research-tasks/{task_id}/events` | 任务事件流（审计） | 人 | `task_id`（path，必填，integer）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_TaskEventOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-tasks/{task_id}/pause` | 暂停任务 | 人 | `task_id`（path，必填，integer） | — | 200 `ResearchTaskDetailOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-tasks/{task_id}/resume` | 恢复任务 | 人 | `task_id`（path，必填，integer） | — | 200 `ResearchTaskDetailOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/research-tasks/{task_id}/run` | 同步推进执行（最多几格） | 人 | `task_id`（path，必填，integer） | application/json → `RunRequest` | 200 `RunReportOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/reviews` | 审核流水 | 人 | `target_type`（query，可选，ReviewTargetType | null）、`target_id`（query，可选，integer | null）、`verdict`（query，可选，ReviewVerdict | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_ReviewOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/reviews` | 记一次审核（结论回写到目标对象） | 人 | — | application/json → `ReviewCreate` | 201 `ReviewOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/reviews/{review_id}` | 审核记录详情 | 人 | `review_id`（path，必填，integer） | — | 200 `ReviewOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/scripts` | 脚本列表 | 人 | `topic_id`（query，可选，integer | null）、`status`（query，可选，ScriptStatus | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_ScriptOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/scripts` | 创建脚本（版本号按选题+模板自增） | 人 | — | application/json → `ScriptCreate` | 201 `ScriptOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/scripts/{script_id}` | 脚本详情 | 人 | `script_id`（path，必填，integer） | — | 200 `ScriptOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/scripts/{script_id}/adapt` | 一稿三版（抖音 / 小红书 / 公众号） | 人 | `script_id`（path，必填，integer） | application/json → `ScriptAdaptIn | null` | 200 `ScriptAdaptOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| PATCH | `/api/scripts/{script_id}/status` | 迁移脚本状态 | 人 | `script_id`（path，必填，integer） | application/json → `ScriptStatusUpdate` | 200 `ScriptOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/sources` | 资料来源列表 | 人 | `source_type`（query，可选，string | null）、`credibility_level`（query，可选，string | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_SourceOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/sources` | 登记资料来源 | 人 | — | application/json → `SourceCreate` | 201 `SourceOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/system/agents` | Agent 名册（agents/roster.yaml + 路由表） | 人 | — | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/system/costs` | 成本统计（真实调用记录） | 人 | — | — | 200 `CostSummaryOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/system/history` | 按天的运行历史（调用/任务/失败） | 人 | `days`（query，可选，integer） | — | 200 `HistoryOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/system/providers` | 模型服务商状态 | 人 | — | — | 200 `ProviderStatusOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/system/status` | 系统运行状态 | 人 | — | — | 200 `SystemStatus` |
| GET | `/api/topics` | 选题列表 | 人 | `knowledge_domain`（query，可选，string | null）、`research_status`（query，可选，string | null）、`content_status`（query，可选，string | null）、`tag_id`（query，可选，integer | null）、`tag`（query，可选，string | null）、`tag_namespace`（query，可选，TagNamespace | null）、`limit`（query，可选，integer）、`offset`（query，可选，integer） | — | 200 `Page_TopicOut_`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/topics` | 创建选题 | 人 | — | application/json → `TopicCreate` | 201 `TopicOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/topics/columns` | 栏目列表 | 人 | — | — | 200 `TopicColumnOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/topics/suggestions` | 选题建议（规则推荐，不联网） | 人 | `window_days`（query，可选，integer）、`limit`（query，可选，integer）、`min_score`（query，可选，integer） | — | 200 `TopicSuggestionsOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/topics/{topic_id}` | 选题详情 | 人 | `topic_id`（path，必填，integer） | — | 200 `TopicOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| PATCH | `/api/topics/{topic_id}` | 更新选题流程状态 | 人 | `topic_id`（path，必填，integer） | application/json → `TopicUpdate` | 200 `TopicOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/topics/{topic_id}/collections` | 选题所在的合集 | 人 | `topic_id`（path，必填，integer） | — | 200 `TopicCollectionOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/topics/{topic_id}/collections` | 把选题挂进一个合集 | 人 | `topic_id`（path，必填，integer） | application/json → `TopicCollectionCreate` | 201 `TopicCollectionOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| DELETE | `/api/topics/{topic_id}/collections/{column_id}` | 把选题从合集里摘下来 | 人 | `topic_id`（path，必填，integer）、`column_id`（path，必填，integer） | — | 204 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/topics/{topic_id}/tags` | 选题上的标签 | 人 | `topic_id`（path，必填，integer） | — | 200 `TopicTagOut[]`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/topics/{topic_id}/tags` | 给选题打标签（标签不存在则按名字新建） | 人 | `topic_id`（path，必填，integer） | application/json → `TopicTagCreate` | 201 `TopicTagOut`；404 `object`；409 `object`；422 `object`；503 `object` |
| DELETE | `/api/topics/{topic_id}/tags/{tag_id}` | 摘掉选题上的标签 | 人 | `topic_id`（path，必填，integer）、`tag_id`（path，必填，integer） | — | 204 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/config` | 配置快照（只读，供设置页展示） | 人 | — | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/jobs` | 视频任务目录（只读枚举 content/jobs） | 人 | — | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/jobs/{job_id}` | 视频任务详情（分镜/素材/质检/脚本） | 人 | `job_id`（path，必填，string） | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/workspace/jobs/{job_id}/content-qc` | 内容质检（数字出处 / 来源覆盖 / 字幕来源 / 素材授权） | 人 | `job_id`（path，必填，string） | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/jobs/{job_id}/file` | 读取任务产物文件（?path=output/xxx.mp4） | 人 | `job_id`（path，必填，string）、`path`（query，必填，string） | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/workspace/jobs/{job_id}/pipeline` | 分镜 → 时间轴（可选：真渲染 + 技术质检） | 人 | `job_id`（path，必填，string） | application/json → `object | null` | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/jobs/{job_id}/render` | 看后台渲染进度（读 qc/render_run.json） | 人 | `job_id`（path，必填，string） | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| POST | `/api/workspace/jobs/{job_id}/render` | 后台渲染成片（立刻返回，用 GET 看进度） | 人 | `job_id`（path，必填，string） | application/json → `object | null` | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/api/workspace/search` | 真实网络搜索（搜狗/360/bing 降级） | 人 | `q`（query，必填，string）、`site`（query，可选，string）、`limit`（query，可选，integer） | — | 200 `object`；404 `object`；409 `object`；422 `object`；503 `object` |
| GET | `/health` | 存活探针 | 公开 | — | — | 200 `HealthResponse` |

## 错误格式

所有业务错误都是同一个信封（`backend/app/core/errors.py`）：

```json
{
  "error": {
    "code": "domain_validation_error",
    "message": "人话说明",
    "details": {
      "reason": "稳定原因码",
      "field": "出问题的字段"
    }
  }
}
```

`details.reason` 是稳定码，脚本可以按它判断；`message` 会变，别拿来做逻辑。

## 一份最小调用示例

```bash
# 建一个研究任务
curl -s -X POST http://127.0.0.1:8000/api/research-tasks \
  -H "Content-Type: application/json" \
  -d '{"title":"储能行业为什么价格战","objective":"说清供需与价格的关系","budget_max_calls":8}'

# 推进它（每个 worker 进程推进若干格）
python -m backend.scripts.run_worker --task-id 1 --max-iterations 3

# 看语义核查结果
curl -s http://127.0.0.1:8000/api/research-reports/1/checks/summary
```
