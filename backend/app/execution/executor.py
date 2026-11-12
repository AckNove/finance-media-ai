"""Agent 执行器：把"抢到的一个子任务"真的跑出结果。

分工很清楚：

* :mod:`backend.app.execution.runtime` 只管状态机（谁在跑、跑到哪、能不能重试）；
* 本模块只管"这一个子任务要做什么"，做完返回 :class:`ExecutionOutcome`。

三类处理器，诚实区分：

1. **真实可跑、不依赖密钥**：``research.collect``（真搜索 + 真抓取 + 快照）、
   ``research.extract``（真解析，带页码/行号）。这些现在就产出真数据。
2. **依赖模型的**：``topic.discover`` / ``research.plan`` / ``research.analyze`` /
   ``research.synthesize`` / ``verify.claims`` / ``content.script`` /
   ``content.visual`` / ``content.produce``。没有 API Key 时返回
   ``blocked`` + ``model_not_configured``，**绝不编一段假报告糊过去**。
3. **依赖外部能力的**：视频渲染、付费 TTS 等由后续里程碑接入，当前同样返回
   ``blocked`` 并写明原因。

每个子任务都会在 ``agent_runs`` 里留一行执行记录（输入、输出、工具调用、耗时），
这样"Agent 真的执行了 10 步"是可查的，不是口头声明。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Literal

from sqlalchemy.orm import Session, sessionmaker

from backend.app.capabilities.tools import ToolRegistry, get_tool_registry
from backend.app.core.caller import CallerContext
from backend.app.database.session import get_session_factory
from backend.app.execution.runtime import Claim
from backend.app.execution.task_types import TaskType
from backend.app.models.agent import AgentRun
from backend.app.models.enums import AgentStatus

if TYPE_CHECKING:
    from backend.app.capabilities.search.engine import SearchHit

OutcomeStatus = Literal["completed", "blocked", "failed", "retry"]

#: 需要模型才能推进的 task_type（没有密钥就只能阻塞）
MODEL_TASK_TYPES: frozenset[str] = frozenset(
    {
        TaskType.TOPIC_DISCOVER.value,
        TaskType.RESEARCH_PLAN.value,
        TaskType.RESEARCH_ANALYZE.value,
        TaskType.RESEARCH_SYNTHESIZE.value,
        TaskType.VERIFY_CLAIMS.value,
        TaskType.CONTENT_SCRIPT.value,
        TaskType.CONTENT_VISUAL.value,
        TaskType.CONTENT_PRODUCE.value,
    }
)

#: 抓取上限：一个查询抓几篇正文（多了既慢又容易触发对方限流）
FETCH_PER_QUERY = 3
#: 一次采集最多查几个词
MAX_QUERIES = 3
#: 单篇正文截断长度（保存全文会导致输出 JSON 过大）
EXCERPT_CHARS = 1200
#: 一个查询最多试几个搜索结果（前几条常被 robots 拒或撞上人机校验）
MAX_FETCH_ATTEMPTS = 8
#: 正文短于这个长度就当成"没抓到内容"（反爬拦截页通常只有几十个字）
MIN_PAGE_CHARS = 200
#: 人机校验/拦截页的典型文案——出现了就不算抓到了正文
BOT_WALL_MARKERS = (
    "安全验证",
    "人机验证",
    "滑动验证",
    "验证浏览器环境",
    "请稍候",
    "访问验证",
    "Just a moment",
    "Enable JavaScript and cookies",
    "Access Denied",
    "Attention Required",
    "captcha",
)


def page_quality(text: str, title: str = "") -> str:
    """判断抓到的东西是不是正文；返回空串表示可用，否则返回问题代码。

    为什么要在执行器里判：抓取器只负责"服务器返回了什么"，人机校验页也是 200 的正常响应。
    但把一页"正在验证浏览器环境，请稍候…"记成"抓到了 1 篇资料"，
    就等于用假数据往下游喂——宁可在这一步就把它标成失败。
    """
    body = (text or "").strip()
    if len(body) < MIN_PAGE_CHARS:
        return f"thin_content:{len(body)}"
    head = f"{title}\n{body[:600]}"
    for marker in BOT_WALL_MARKERS:
        if marker.lower() in head.lower():
            return "bot_wall"
    return ""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    status: OutcomeStatus
    output: dict[str, Any] | None = None
    reason: str = ""
    calls: int = 0
    tokens: int = 0
    cost_cny: Decimal = Decimal("0")
    agent_run_id: int | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "calls": self.calls,
            "tokens": self.tokens,
            "cost_cny": str(self.cost_cny),
            "agent_run_id": self.agent_run_id,
            "tool_calls": list(self.tool_calls),
            "output_keys": sorted(self.output) if self.output else [],
        }


def derive_queries(question: str, payload: dict[str, Any] | None = None) -> list[str]:
    """从子任务问题里推出检索词。

    没有模型可用时这就是全部"规划"能力：它**不做语义扩展**，只把问题拆成
    几个可检索的短语，并在 ``output`` 里如实标明 ``planner="keywords"``。
    想要真正的多角度问题拆解，需要模型（见 MODEL_TASK_TYPES）。
    """
    payload = payload or {}
    explicit = payload.get("queries")
    if isinstance(explicit, list) and explicit:
        return [str(item).strip() for item in explicit if str(item).strip()][:MAX_QUERIES]

    text = question.strip().rstrip("？?。.")
    chunks = [chunk.strip() for chunk in text.replace("，", "|").replace("、", "|").split("|")]
    queries = [chunk for chunk in chunks if len(chunk) >= 2]
    if not queries:
        queries = [text]
    if len(queries) == 1 and len(text) > 12:
        # 长问题取前半段做补充检索词，避免只查一次导致漏面
        queries.append(text[:12])
    return queries[:MAX_QUERIES]


class AgentExecutor:
    """执行一个 claim。构造时注入工具注册表与会话工厂，便于测试替换。"""

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        session_factory: sessionmaker[Session] | None = None,
        fetch_per_query: int = FETCH_PER_QUERY,
        clock: Callable[[], float] = time.monotonic,
        artifacts_root: Path | None = None,
    ) -> None:
        self._tools = tools or get_tool_registry()
        self._session_factory = session_factory or get_session_factory()
        self.fetch_per_query = fetch_per_query
        self._clock = clock
        #: 研究产物落盘根目录（默认 content/research，测试里换成临时目录）
        self.artifacts_root = artifacts_root
        self._handlers: dict[str, Callable[[Claim, CallerContext], ExecutionOutcome]] = {
            TaskType.RESEARCH_COLLECT.value: self._collect,
            TaskType.RESEARCH_EXTRACT.value: self._extract,
            TaskType.RESEARCH_ANALYZE.value: self._analyze,
            TaskType.RESEARCH_SYNTHESIZE.value: self._synthesize,
            TaskType.VERIFY_CLAIMS.value: self._verify_claims,
        }

    # ------------------------------------------------------------------ 入口

    def execute(self, claim: Claim, *, worker: str = "worker") -> ExecutionOutcome:
        """执行一个已抢占的子任务，并在 ``agent_runs`` 留痕。"""
        import time as _time

        started = _time.perf_counter()
        caller = self._caller(claim)
        try:
            handler = self._handlers.get(claim.task_type)
            if handler is not None:
                outcome = handler(claim, caller)
            elif claim.task_type in MODEL_TASK_TYPES:
                outcome = self._model_blocked(claim)
            else:
                outcome = ExecutionOutcome(
                    status="failed",
                    reason=f"no_handler:{claim.task_type}",
                )
        except Exception as error:  # noqa: BLE001 - 执行器不能把异常抛给 worker 循环
            outcome = ExecutionOutcome(
                status="retry",
                reason=f"exception:{type(error).__name__}: {error}"[:300],
            )

        duration_ms = int((_time.perf_counter() - started) * 1000)
        run_id = self._record_run(claim, caller, outcome, duration_ms=duration_ms, worker=worker)
        return ExecutionOutcome(
            status=outcome.status,
            output=outcome.output,
            reason=outcome.reason,
            calls=outcome.calls,
            tokens=outcome.tokens,
            cost_cny=outcome.cost_cny,
            agent_run_id=run_id,
            tool_calls=outcome.tool_calls,
        )

    # ------------------------------------------------------------------ 处理器

    def _collect(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """真实搜索 + 真实抓取。没有模型参与，结果可直接进证据库。"""
        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        queries = derive_queries(claim.question, payload)
        site = str(payload.get("site", ""))
        time_range = str(payload.get("time_range", ""))
        results: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        errors: list[str] = []
        fetched_total = 0

        for query in queries:
            search_result = self._tools.dispatch(
                caller, "web_search", keywords=query, site=site, time_range=time_range, limit=10
            )
            tool_calls.append(
                {
                    "tool": "web_search",
                    "query": query,
                    "status": search_result.status,
                    "error": search_result.error_code or "",
                }
            )
            if not search_result.ok:
                errors.append(f"search:{search_result.error_code or search_result.status}")
                continue
            hits: list[dict[str, Any]] = list((search_result.data or {}).get("hits", []))
            fetched: list[dict[str, Any]] = []
            attempts = 0
            for hit in hits:
                if len(fetched) >= self.fetch_per_query or attempts >= MAX_FETCH_ATTEMPTS:
                    break
                attempts += 1
                page_result = self._tools.dispatch(caller, "fetch_url", url=hit["url"])
                tool_calls.append(
                    {
                        "tool": "fetch_url",
                        "url": hit["url"],
                        "status": page_result.status,
                        "error": page_result.error_code or "",
                    }
                )
                if not page_result.ok:
                    errors.append(
                        f"fetch:{page_result.error_code or page_result.status}:{hit['url']}"
                    )
                    continue
                data = page_result.data or {}
                text = data.get("text") or ""
                problem = page_quality(text, data.get("title") or "")
                if problem:
                    errors.append(f"fetch:{problem}:{hit['url']}")
                    continue
                fetched_total += 1
                fetched.append(
                    {
                        "url": data.get("final_url") or hit["url"],
                        "title": data.get("title") or hit.get("title", ""),
                        "published_at": data.get("published_at", ""),
                        "author": data.get("author", ""),
                        "site_name": data.get("site_name", ""),
                        "sha256": data.get("sha256", ""),
                        "snapshot_path": data.get("snapshot_path", ""),
                        "char_count": data.get("char_count", 0),
                        "excerpt": text[:EXCERPT_CHARS],
                    }
                )
            results.append(
                {
                    "query": query,
                    "hit_count": len(hits),
                    "hits": hits[:10],
                    "fetched": fetched,
                }
            )

        if fetched_total == 0:
            return ExecutionOutcome(
                status="retry",
                reason="no_page_fetched:" + "; ".join(errors[:3]),
                tool_calls=tuple(tool_calls),
            )

        output = {
            "planner": "keywords",
            "queries": queries,
            "results": results,
            "fetched_pages": fetched_total,
            "errors": errors,
            "note": "检索词由关键词规则拆分，未使用模型；如需多角度拆解请配置模型后走 research.plan",
        }
        return ExecutionOutcome(status="completed", output=output, tool_calls=tuple(tool_calls))

    def _extract(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """对已登记的本地文件做带定位的结构化抽取。"""
        from pathlib import Path

        from backend.app.capabilities.adapters.structured import (
            extract_structured,
            locate_quote,
        )

        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        files = payload.get("files") or []
        if not isinstance(files, list) or not files:
            return ExecutionOutcome(
                status="failed",
                reason="bad_arguments:input_payload.files 为空（需要已登记的文件路径）",
            )
        documents: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for entry in files:
            path = Path(str(entry.get("path", entry) if isinstance(entry, dict) else entry))
            if not path.exists():
                tool_calls.append({"tool": "extract_structured", "path": str(path), "status": "error"})
                continue
            document = extract_structured(path)
            locations: dict[str, Any] = {}
            quote = str(payload.get("quote", "")).strip()
            if quote:
                locations["quote"] = locate_quote(document, quote)
            documents.append(
                {
                    "path": str(path),
                    "kind": document.kind,
                    "extractor": document.extractor,
                    "page_count": document.page_count,
                    "sheet_names": list(document.sheet_names),
                    "char_count": len(document.text),
                    "table_count": len(document.tables),
                    "span_count": len(document.spans),
                    "locations": locations,
                    "tables": [
                        {"locator": table.locator, "header": table.header, "rows": len(table.rows)}
                        for table in document.tables[:20]
                    ],
                }
            )
            tool_calls.append(
                {
                    "tool": "extract_structured",
                    "path": str(path),
                    "status": "ok",
                    "kind": document.kind,
                    "extractor": document.extractor,
                }
            )
        if not documents:
            return ExecutionOutcome(
                status="failed",
                reason="no_document_extracted",
                tool_calls=tuple(tool_calls),
            )
        return ExecutionOutcome(
            status="completed",
            output={"documents": documents},
            tool_calls=tuple(tool_calls),
        )

    def _model_blocked(self, claim: Claim) -> ExecutionOutcome:
        """需要模型但当前没有可用模型时的诚实结果。"""
        from backend.app.capabilities.model.providers import provider_status

        status = provider_status()
        # provider_status() 返回 {名字: {"status": "ready"|原因, ...}}，
        # 只有 status == "ready" 才算这个 provider 真的能用。
        ready = [
            name
            for name, entry in status.items()
            if isinstance(entry, dict) and entry.get("status") == "ready"
        ]
        if ready:
            reason = "model_handler_not_implemented:" + ",".join(ready)
        else:
            reason = "model_not_configured"
        return ExecutionOutcome(
            status="blocked",
            reason=reason,
            output={
                "blocked": True,
                "task_type": claim.task_type,
                "providers": status,
                "next_step": (
                    "在 .env 里配置 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）并把 "
                    "config/models.yaml 里对应 provider/model 的 enabled 改为 true，"
                    "再补上价目；也可以在 config/models.yaml 里启用 local 指向本机模型服务"
                ),
            },
        )

    # ------------------------------------------------------------ 研究与核查

    def _digest_for(self, claim: Claim) -> tuple[Any, dict[str, Any], dict[str, Path]]:
        """按上游真实输出汇编研究底稿并落盘（**规则汇编，不用模型**）。"""
        from backend.app.core.config import PROJECT_ROOT
        from backend.app.research.digest import build_digest, write_digest

        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        upstream = payload.get("_upstream") or []
        queries: list[str] = []
        for item in upstream:
            output = item.get("output") if isinstance(item, dict) else None
            if isinstance(output, dict):
                queries.extend(str(q) for q in (output.get("queries") or []))
        digest = build_digest(
            title=claim.question or "研究底稿",
            objective=str(payload.get("objective") or claim.question or ""),
            upstream=[item for item in upstream if isinstance(item, dict)],
            queries=queries,
        )
        directory = (self.artifacts_root or (PROJECT_ROOT / "content" / "research")) / f"task-{claim.task_id}"
        paths = write_digest(digest, directory)
        return digest, payload, paths

    def _analyze(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """把上游资料汇编成"候选数据点表"，并在没有模型时如实标 blocked。"""
        digest, _payload, paths = self._digest_for(claim)
        blocked = self._model_blocked(claim)
        output = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "source_count": digest.source_count,
            "data_point_count": len(digest.data_points),
            "data_points": [point.as_dict() for point in digest.data_points],
            "sources": [source.as_dict() for source in digest.sources],
            "analysis_done": False,
            "note": (
                "这一步只做了资料整理与数据点抽取，**没有做分析**"
                "（对比、归因、趋势判断都需要模型）。"
            ),
        }
        if blocked.status == "blocked":
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(
                status="blocked",
                reason=blocked.reason,
                output=output,
            )
        return ExecutionOutcome(status="completed", output=output)

    def _synthesize(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """汇总成研究底稿；分析小结需要模型，缺模型时标 blocked 并保留底稿。"""
        digest, _payload, paths = self._digest_for(claim)
        blocked = self._model_blocked(claim)
        output = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "source_count": digest.source_count,
            "data_point_count": len(digest.data_points),
            "queries": list(digest.queries),
            "sources": [source.as_dict() for source in digest.sources],
            "report_ready": False,
            "note": (
                "底稿已落盘（来源清单 + 候选数据点），但**报告的结论段没有生成**："
                "结论必须由模型基于这些证据写，脚本化模板不算研究报告。"
            ),
        }
        if blocked.status == "blocked":
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(
                status="blocked",
                reason=blocked.reason,
                output=output,
            )
        return ExecutionOutcome(status="completed", output=output)

    def _verify_claims(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """在原文里核对候选数据点是否真的出现过（可自动做的那部分）。"""
        from backend.app.research.verify import verify_points

        digest, _payload, paths = self._digest_for(claim)
        report = verify_points(digest)
        blocked = self._model_blocked(claim)
        output = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "checked": report["checked"],
            "found": report["found"],
            "missing": report["missing"],
            "results": report["results"],
            "semantic_check_done": False,
            "note": (
                "只核对了『这句带数字的原话是否出现在该来源的正文里』——"
                "**没有判断它说得对不对**（口径、时效、样本都要模型或人来判）。"
            ),
        }
        if blocked.status == "blocked":
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(status="blocked", reason=blocked.reason, output=output)
        return ExecutionOutcome(status="completed", output=output)

    # ------------------------------------------------------------------ 记录

    def _caller(self, claim: Claim) -> CallerContext:
        from backend.app.core.config import get_agent_routing

        assignee = claim.assignee or ""
        scopes: set[str] = set()
        try:
            routing = get_agent_routing()
            scopes = set(routing.db_scopes_for(assignee)) if assignee else set()
        except Exception:  # noqa: BLE001 - 配置异常不应让执行器崩溃
            scopes = set()
        return CallerContext.for_executor(
            agent=assignee or "unassigned",
            task_id=str(claim.task_id),
            attempt_token=claim.attempt_token,
            subtask_id=str(claim.subtask_id),
            db_scopes=scopes,
        )

    def _record_run(
        self,
        claim: Claim,
        caller: CallerContext,
        outcome: ExecutionOutcome,
        *,
        duration_ms: int,
        worker: str,
    ) -> int | None:
        status_map = {
            "completed": AgentStatus.SUCCEEDED,
            # agent_runs.status 的取值域里没有 blocked（见 models/enums.AgentStatus）：
            # 一次"没拿到输出"的执行就是 failed，"为什么"写在 error_message 里。
            # 任务/子任务层面的 blocked、paused 状态由 runtime 负责，这里不重复表达。
            "blocked": AgentStatus.FAILED,
            "failed": AgentStatus.FAILED,
            "retry": AgentStatus.FAILED,
        }
        error_text = outcome.reason or None
        if outcome.status == "blocked" and error_text:
            error_text = f"blocked:{error_text}"
        try:
            with self._session_factory() as session:
                run = AgentRun(
                    task_id=f"rt-{claim.task_id}-{claim.subtask_id}-{claim.attempt}",
                    agent_name=caller.name,
                    task_type=claim.task_type,
                    status=status_map.get(outcome.status, AgentStatus.FAILED),
                    input_payload={
                        "question": claim.question,
                        "input": claim.input_payload,
                        "worker": worker,
                    },
                    output_payload=_jsonable(outcome.output),
                    error_message=error_text,
                    error_payload=(
                        _jsonable(list(outcome.tool_calls)) if outcome.tool_calls else None
                    ),
                    research_task_id=claim.task_id,
                    subtask_id=claim.subtask_id,
                    cost_estimate=outcome.cost_cny,
                    started_at=None,
                    finished_at=None,
                    duration_ms=duration_ms,
                )
                session.add(run)
                session.commit()
                return run.id
        except Exception:  # noqa: BLE001 - 记录失败不能掩盖执行结果
            return None


def _jsonable(value: Any) -> Any:
    """把结果压成能进 JSON 列的结构（截断正文，保留可追溯字段）。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


__all__ = [
    "AgentExecutor",
    "EXCERPT_CHARS",
    "ExecutionOutcome",
    "FETCH_PER_QUERY",
    "MAX_QUERIES",
    "MODEL_TASK_TYPES",
    "OutcomeStatus",
    "derive_queries",
]
