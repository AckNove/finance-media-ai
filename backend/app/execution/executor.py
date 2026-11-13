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

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from sqlalchemy.orm import Session, sessionmaker

from backend.app.capabilities.tools import ToolRegistry, get_tool_registry
from backend.app.core.caller import CallerContext
from backend.app.core.config import PROJECT_ROOT
from backend.app.database.session import get_session_factory
from backend.app.execution.runtime import Claim
from backend.app.execution.task_types import TaskType
from backend.app.models.agent import AgentRun
from backend.app.models.enums import AgentStatus

if TYPE_CHECKING:
    from backend.app.capabilities.search.engine import SearchHit

logger = logging.getLogger(__name__)

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
#: 研究产物默认落盘根目录。测试里替换这个常量，产物就不会写进仓库。
DEFAULT_ARTIFACTS_ROOT = PROJECT_ROOT / "content" / "research"
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


def _clean_queries(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()][:MAX_QUERIES]


def _safe_digest_dir(directory: Path, digest: Any) -> Path:
    """给底稿挑一个不会覆盖别人的目录。

    产物按 ``task-<id>`` 落盘，而 id 只在**一个库**里唯一：换一个库跑同一个 id，
    或删库重建，就会撞上上一次的目录。早先直接覆盖，实测把一份真实底稿换成了
    另一次实验的内容。这里改成：目录里已有同名底稿但目标不同，就退到
    ``task-<id>-<标题指纹>``，谁也不覆盖谁。
    """
    existing = directory / "digest.json"
    if not existing.exists():
        return directory
    try:
        payload = json.loads(existing.read_text(encoding="utf-8"))
        same = str(payload.get("objective", "")) == str(digest.objective)
    except (OSError, ValueError):
        same = False
    if same:
        return directory
    fingerprint = hashlib.sha256(str(digest.objective).encode("utf-8")).hexdigest()[:8]
    return directory.with_name(f"{directory.name}-{fingerprint}")


def _planned_queries(payload: dict[str, Any]) -> list[str]:
    """上游 ``research.plan`` 排出的检索词。

    规划阶段把问题拆成检索词之后，采集阶段要**直接用**这份结果，否则规划就白做了。
    只认 ``planner="keywords"`` 这种规则产物的结构，读不到就当没有。
    """
    upstream = payload.get("_upstream")
    if not isinstance(upstream, list):
        return []
    for item in upstream:
        if not isinstance(item, dict):
            continue
        if item.get("task_type") != TaskType.RESEARCH_PLAN.value:
            continue
        output = item.get("output")
        if isinstance(output, dict):
            queries = _clean_queries(output.get("queries"))
            if queries:
                return queries
    return []


def derive_queries(question: str, payload: dict[str, Any] | None = None) -> list[str]:
    """从子任务问题里推出检索词。

    优先级：显式 ``payload["queries"]`` > 上游 ``research.plan`` 的产出 > 按标点拆句。

    没有模型可用时这就是全部"规划"能力：它**不做语义扩展**，只把问题拆成
    几个可检索的短语，并在 ``output`` 里如实标明 ``planner="keywords"``。
    想要真正的多角度问题拆解，需要模型（见 MODEL_TASK_TYPES）。
    """
    payload = payload or {}
    explicit = _clean_queries(payload.get("queries"))
    if explicit:
        return explicit

    planned = _planned_queries(payload)
    if planned:
        return planned

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
        model_gateway: Any = None,
    ) -> None:
        self._tools = tools or get_tool_registry()
        self._session_factory = session_factory or get_session_factory()
        self.fetch_per_query = fetch_per_query
        self._clock = clock
        #: 研究产物落盘根目录（默认 content/research，测试里换成临时目录）
        self.artifacts_root = artifacts_root
        #: 模型网关（缺省用全局单例；测试注入 MockTransport，绝不真发请求）
        self._model_gateway = model_gateway
        self._handlers: dict[str, Callable[[Claim, CallerContext], ExecutionOutcome]] = {
            TaskType.TOPIC_DISCOVER.value: self._discover,
            TaskType.RESEARCH_PLAN.value: self._plan,
            TaskType.RESEARCH_COLLECT.value: self._collect,
            TaskType.RESEARCH_EXTRACT.value: self._extract,
            TaskType.RESEARCH_ANALYZE.value: self._analyze,
            TaskType.RESEARCH_SYNTHESIZE.value: self._synthesize,
            TaskType.VERIFY_CLAIMS.value: self._verify_claims,
            TaskType.CONTENT_SCRIPT.value: self._script,
            TaskType.CONTENT_VISUAL.value: self._visual,
            TaskType.CONTENT_PRODUCE.value: self._produce,
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

    def _plan(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """研究规划：有模型就让模型拆，没有模型就用规则拆并**如实标注降级**。

        这里不把"缺模型"当成失败：拆检索词这件事规则也能做，把整条研究链卡死在
        第一格毫无意义。但产出里必须能一眼看出这是谁拆的——
        ``planner`` 只会是 ``model`` 或 ``keywords``，降级时另带 ``degraded_reason``。
        """
        from backend.app.research.planning import plan_research

        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        question = claim.question.strip()
        if question.startswith("拆解研究问题："):
            question = question[len("拆解研究问题：") :].strip()
        explicit = payload.get("queries") or []
        keywords = derive_queries(question, {"queries": explicit})
        base: dict[str, Any] = {
            "objective": question,
            "queries": keywords,
            "query_count": len(keywords),
            "planner": "keywords",
            "model_used": False,
            "proposed_subtasks": [],
            "note": "按标点与长度拆检索词，没有做语义扩展；多角度拆解需要模型",
        }

        blocked = self._model_blocked(claim)
        if blocked.status == "blocked":
            base["degraded_reason"] = blocked.reason
            base["degraded_message"] = (
                "没有可用模型，规划降级为关键词规则拆分（下游照常执行）"
            )
            base["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(status="completed", output=base)

        context = {
            "pipeline": payload.get("pipeline", ""),
            "explicit_queries": list(explicit),
            "note": "尚无已有资料，这是首次规划",
        }
        try:
            plan = plan_research(
                question,
                context=context,
                gateway=self._model_gateway,
                caller=caller,
                budget=self._budget_for(claim),
                purpose="research.plan",
            )
        except Exception as error:  # noqa: BLE001 - 规划失败要降级，但不能假装成功
            reason = str(getattr(error, "reason", "") or type(error).__name__)
            base["degraded_reason"] = f"model_planning_failed:{reason}"
            base["degraded_message"] = str(error)[:300]
            # 失败也是真花过钱的：把这次调用的用量记进任务账，否则
            # research_tasks.cost_spent 会小于 SUM(model_calls)。
            return ExecutionOutcome(
                status="completed", output=base, **self._usage_kwargs(error)
            )

        planned = list(plan.subtasks)
        queries = plan.queries() or keywords
        # 高优先级的子任务可以真的变成额外的采集任务（由 worker 追加，有上限、去重）
        followups = [
            {
                "question": item.question,
                "queries": list(item.all_queries()),
                "plan_subtask": item.id,
                "reason": item.search_intent or "规划里列为高优先级的补充检索",
            }
            for item in planned[1:]
            if item.priority == "high"
        ][:2]
        return ExecutionOutcome(
            status="completed",
            output={
                "objective": plan.objective,
                "core_questions": list(plan.core_questions),
                "subtasks": [item.as_dict() for item in planned],
                "subtask_count": len(planned),
                "proposed_subtasks": followups,
                "queries": queries,
                "query_count": len(queries),
                "risks": list(plan.risks),
                "gaps": list(plan.gaps),
                "planner": "model",
                "model_used": True,
                "plan": plan.as_dict(),
                "note": "规划由模型生成；检索词直接进入采集阶段，高优先级子任务会追加为独立采集任务",
            },
            calls=1,
            tokens=plan.prompt_tokens + plan.completion_tokens,
            cost_cny=Decimal(str(round(plan.cost_cny, 6))),
        )

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
        recorded = self._persist_sources(output)
        if recorded is not None:
            output["recorded_sources"] = recorded
        return ExecutionOutcome(status="completed", output=output, tool_calls=tuple(tool_calls))

    def _persist_sources(self, output: dict[str, Any]) -> dict[str, Any] | None:
        """把这一轮抓到的页面写进 ``sources`` 表，供资料库与证据链引用。"""
        from backend.app.research.digest import sources_from_collect
        from backend.app.research.persistence import record_sources

        try:
            refs = sources_from_collect([output])
            if not refs:
                return None
            with self._session_factory() as session:
                recorded = record_sources(session, refs)
                session.commit()
            return {
                "created": recorded.created,
                "updated": recorded.updated,
                "source_ids": dict(recorded.mapping),
            }
        except Exception as error:  # noqa: BLE001 - 入库失败不该让整轮采集白跑
            logger.warning("来源入库失败（抓取结果仍保留在输出里）：%s", error)
            return None

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
            # 没有本地文件不是错误：这条研究线本来就是"只靠网页资料"。
            # 如实记一笔"跳过了"，让后面的阶段继续跑；真给了路径却抽不出来才失败。
            return ExecutionOutcome(
                status="completed",
                output={
                    "documents": [],
                    "skipped": True,
                    "note": "本次没有已登记的本地文件，抽取阶段空跑（不是失败）",
                },
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
        # 注入了网关时，以**网关自己的配置与密钥**为准：全局 os.environ 与它可能不一致
        # （测试、多账号、CI），两边不一致时"以为有模型、实际没有"会让判断错。
        if self._model_gateway is not None and hasattr(
            self._model_gateway, "ready_providers"
        ):
            ready = list(self._model_gateway.ready_providers())
        if ready:
            # 有可用模型：不再拦在这里，交回各处理器去真的调用模型。
            # （早期这里会返回 model_handler_not_implemented，导致"配了 key 也跑不动"。）
            return ExecutionOutcome(status="completed", output={"ready_providers": ready})
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
            # 目标一律取"任务"的目标，而不是各阶段的阶段问题：否则采集/分析/核查三个阶段
            # 会算出三个不同的目录（曾经的现场是 task-1-4a56b721 / 5f15f8f2 / 89131660），
            # 同一份研究的底稿、分析、核查散在三个文件夹里。
            objective=self._task_objective(claim.task_id)
            or str(payload.get("objective") or claim.question or ""),
            upstream=[item for item in upstream if isinstance(item, dict)],
            queries=queries,
        )
        directory = (self.artifacts_root or DEFAULT_ARTIFACTS_ROOT) / f"task-{claim.task_id}"
        directory = _safe_digest_dir(directory, digest)
        paths = write_digest(digest, directory)
        return digest, payload, paths

    def _cross_check(self, digest: Any) -> dict[str, Any]:
        """底稿里的跨来源比对结果（规则计算；没有就跑一遍）。"""
        from backend.app.research.corroborate import corroborate

        result = getattr(digest, "corroboration", None) or corroborate(digest.data_points)
        return {
            "checked_points": result.checked_points,
            "corroborated_count": result.corroborated_count,
            "conflict_count": result.conflict_count,
            "single_source_count": len(result.single_source),
            "conflicts": [item.as_dict() for item in result.conflicts[:5]],
        }

    def _task_objective(self, task_id: int) -> str:
        """任务的总体研究目标（所有阶段共用，用来给产物目录定 key）。"""
        from backend.app.models.execution import ResearchTask

        try:
            with self._session_factory() as session:
                row = session.get(ResearchTask, task_id)
                return str(getattr(row, "objective", "") or "").strip()
        except Exception as error:  # noqa: BLE001 - 读不到就退回阶段问题
            logger.warning("读取任务目标失败，退回阶段问题：%s", error)
            return ""

    def _usage_kwargs(self, error: BaseException) -> dict[str, Any]:
        """把失败调用真的花掉的用量转成 ``ExecutionOutcome`` 的成本参数。

        模型调用失败 / 输出被截断**不代表没花钱**。这些异常对象上挂着 ``usage``
        （prompt/completion/cost），这里把它带回任务账，保证
        ``research_tasks.cost_spent`` 与 ``SUM(model_calls.cost_estimate)`` 对得上。
        """
        usage = getattr(error, "usage", None)
        if not isinstance(usage, dict) or not usage:
            return {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = float(usage.get("cost_cny") or 0.0)
        if not (prompt_tokens or completion_tokens or cost):
            return {}
        return {
            # 重试过就有多次调用：按实际次数记账，不要一律记 1 次
            "calls": int(usage.get("calls") or 1),
            "tokens": prompt_tokens + completion_tokens,
            "cost_cny": Decimal(str(round(cost, 6))),
        }

    def _budget_for(self, claim: Claim) -> Any:
        """把任务当前的已花费用喂给模型网关，让预算闸门看得见真实进度。"""
        from backend.app.capabilities.model import BudgetContext
        from backend.app.models.execution import ResearchTask

        calls_spent = 0
        cost_spent = 0.0
        found = False
        try:
            with self._session_factory() as session:
                row = session.get(ResearchTask, claim.task_id)
                if row is not None:
                    found = True
                    calls_spent = int(row.calls_spent or 0)
                    cost_spent = float(row.cost_spent or 0)
        except Exception as error:  # noqa: BLE001 - 读不到就按 0 处理，闸门仍会拦单次上限
            logger.warning("读取任务预算进度失败，按 0 处理：%s", error)
        return BudgetContext(
            # 任务行不存在时不要带 id，否则 model_calls 的外键会指空
            task_id=claim.task_id if found else None,
            subtask_id=claim.subtask_id if found else None,
            calls_spent=calls_spent,
            cost_spent_cny=cost_spent,
            max_calls=claim.budget_max_calls,
            max_cost_cny=(
                float(claim.budget_max_cost_cny)
                if claim.budget_max_cost_cny is not None
                else None
            ),
        )

    def _analyse_with_model(
        self, claim: Claim, digest: Any, caller: CallerContext, *, purpose: str
    ) -> Any:
        """调真实模型做一次分析（失败抛 ``AnalysisError``，绝不用模板假装成功）。

        按研究目标自动挑骨架（行业 / 产业链 / 公司）——骨架只决定"该回答哪些问题"，
        不改变模型只能引用给定证据这条硬规则。
        """
        from backend.app.research.analysis import analyze
        from backend.app.research.frameworks import framework_for

        objective = self._task_objective(claim.task_id) or digest.objective
        framework = framework_for(objective)
        return analyze(
            digest,
            gateway=self._model_gateway,
            caller=caller,
            budget=self._budget_for(claim),
            purpose=purpose,
            framework=framework,
            skill_notes=self._skill_notes(caller, claim),
        )

    def _skill_notes(self, caller: CallerContext, claim: Claim) -> tuple[tuple[str, str], ...]:
        """取这次任务该带的 Skill 正文（表达与研究方法要求）。

        只对 Agent 身份加载：``SkillRegistry.load_for`` 会校验"这个角色能不能跑这个
        task_type"，并把加载事件写进审计日志。取不到就返回空——**绝不让 Skill 加载
        失败把整条研究链打断**，但也绝不假装加载成功（output 里的 `skills_used` 会是空）。
        """

        from backend.app.core.config import PROJECT_ROOT
        from backend.app.skills.registry import SkillRegistry, SkillRegistryError

        try:
            registry = SkillRegistry(PROJECT_ROOT / "skills")
            loaded = registry.load_for(caller, claim.task_type)
        except (SkillRegistryError, OSError) as error:  # 缺文件、校验不过、角色不对
            logger.warning("任务 %s 的 Skill 没有加载（%s），本轮不带 Skill 提示词", claim.task_id, error)
            return ()
        return tuple((item.name, item.instructions) for item in loaded)

    def _write_analysis(
        self, paths: dict[str, Path], analysis: Any, *, objective: str
    ) -> dict[str, str]:
        """把分析结果落到与底稿同目录（人看 md，程序读 json）。"""
        directory = Path(paths["markdown"]).parent
        markdown_path = directory / "analysis.md"
        json_path = directory / "analysis.json"
        markdown_path.write_text(
            analysis.to_markdown(objective=objective), encoding="utf-8"
        )
        json_path.write_text(
            json.dumps(analysis.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"markdown": str(markdown_path), "json": str(json_path)}

    def _analysis_failed(
        self, output: dict[str, Any], error: Exception
    ) -> ExecutionOutcome:
        reason = getattr(error, "reason", "") or "analysis_failed"
        output["analysis_done"] = False
        output["analysis_error"] = {"reason": reason, "message": str(error)}
        output["note"] = (
            "模型分析失败；底稿、来源与证据行仍然有效，先看 analysis_error。"
            "本次没有生成任何结论，不会用模板补一段。"
        )
        return ExecutionOutcome(
            status="failed",
            reason=f"analysis_failed: {reason}",
            output=output,
            **self._usage_kwargs(error),
        )

    def _analyze(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """把上游资料汇编成"候选数据点表"，并在没有模型时如实标 blocked。"""
        from backend.app.research.verify import verify_points

        digest, _payload, paths = self._digest_for(claim)
        verification = verify_points(digest)
        # 即使是草稿也先落库：证据链不能等"有模型了"才存在
        persisted = self._persist_report(claim, digest, verification)
        blocked = self._model_blocked(claim)
        output = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "source_count": digest.source_count,
            "data_point_count": len(digest.data_points),
            "data_points": [point.as_dict() for point in digest.data_points],
            "sources": [source.as_dict() for source in digest.sources],
            "cross_check": self._cross_check(digest),
            "analysis_done": False,
            "note": (
                "这一步只做了资料整理、数据点抽取和跨来源比对，**没有做分析**"
                "（对比、归因、趋势判断都需要模型）。"
            ),
        }
        if persisted is not None:
            output["report"] = persisted
        if blocked.status == "blocked":
            output["blocked"] = True
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(
                status="blocked",
                reason=blocked.reason,
                output=output,
            )

        # 有可用模型：真的让它读证据、写结论（每条结论都必须挂来源）
        try:
            analysis = self._analyse_with_model(
                claim, digest, caller, purpose="research.analyze"
            )
        except Exception as error:  # noqa: BLE001 - 分析失败要如实记，不能假装完成
            return self._analysis_failed(output, error)

        files = self._write_analysis(paths, analysis, objective=digest.objective)
        model_report = self._persist_report(claim, digest, verification, analysis=analysis)
        output.update(
            {
                "generated_by": "model+rules",
                "analysis_done": True,
                "analysis": analysis.as_dict(),
                "analysis_files": files,
                "framework": getattr(analysis, "framework_kind", ""),
                "framework_checklist": getattr(analysis, "checklist", {}) or {},
                "note": (
                    "小结与结论由模型基于上述证据生成，每条都挂了来源编号；"
                    "证据不足的部分写在 data_gaps 里，未填。"
                ),
            }
        )
        if model_report is not None:
            output["report"] = model_report
        return ExecutionOutcome(
            status="completed",
            output=output,
            calls=1,
            tokens=analysis.prompt_tokens + analysis.completion_tokens,
            cost_cny=Decimal(str(round(analysis.cost_cny, 6))),
        )

    def _synthesize(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """汇总成研究底稿；分析小结需要模型，缺模型时标 blocked 并保留底稿。"""
        from backend.app.research.verify import verify_points

        digest, _payload, paths = self._digest_for(claim)
        verification = verify_points(digest)
        persisted = self._persist_report(claim, digest, verification)
        blocked = self._model_blocked(claim)
        output = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "source_count": digest.source_count,
            "data_point_count": len(digest.data_points),
            "queries": list(digest.queries),
            "sources": [source.as_dict() for source in digest.sources],
            "cross_check": self._cross_check(digest),
            "report_ready": False,
            "note": (
                "底稿已落盘（来源清单 + 候选数据点），但**报告的结论段没有生成**："
                "结论必须由模型基于这些证据写，脚本化模板不算研究报告。"
            ),
        }
        if persisted is not None:
            output["report"] = persisted
        if blocked.status == "blocked":
            output["blocked"] = True
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(
                status="blocked",
                reason=blocked.reason,
                output=output,
            )

        # 有可用模型：写出结论段，并把结论回写进报告行
        try:
            analysis = self._analyse_with_model(
                claim, digest, caller, purpose="research.synthesize"
            )
        except Exception as error:  # noqa: BLE001 - 同上：失败要记，不补模板
            return self._analysis_failed(output, error)

        files = self._write_analysis(paths, analysis, objective=digest.objective)
        model_report = self._persist_report(claim, digest, verification, analysis=analysis)
        output.update(
            {
                "generated_by": "model+rules",
                "report_ready": True,
                "analysis": analysis.as_dict(),
                "analysis_files": files,
                "framework": getattr(analysis, "framework_kind", ""),
                "framework_checklist": getattr(analysis, "checklist", {}) or {},
                "skills_used": list(getattr(analysis, "skills_used", ()) or ()),
                "note": (
                    "结论段由模型基于已抓取证据写成，每条结论都挂了来源编号；"
                    "data_gaps 里列出还缺什么证据。"
                ),
            }
        )
        if model_report is not None:
            output["report"] = model_report
        return ExecutionOutcome(
            status="completed",
            output=output,
            calls=1,
            tokens=analysis.prompt_tokens + analysis.completion_tokens,
            cost_cny=Decimal(str(round(analysis.cost_cny, 6))),
        )

    def _persist_report(
        self,
        claim: Claim,
        digest: Any,
        verification: dict[str, Any],
        analysis: Any = None,
    ) -> dict[str, Any] | None:
        """把来源、报告骨架与证据行写进库——证据链必须是**库里的数据**。

        ``analysis`` 非空时（模型已经给出分析），报告的摘要与结论用模型的产出，
        但每条结论仍然必须挂 ``source_ids``——挂不上的在 ``analysis`` 里已被丢弃。
        """
        from backend.app.research.persistence import (
            digest_fingerprint,
            record_evidence,
            record_report,
            record_sources,
        )

        summary_override = None
        findings_override = None
        model_label = ""
        if analysis is not None:
            summary_override = analysis.summary or None
            model_label = f"{analysis.provider}/{analysis.model}"
            findings_override = [
                {
                    "text": item.claim,
                    "kind": item.kind,
                    "source_ids": list(item.source_ids),
                    "numbers": list(item.numbers),
                    "verified": False,
                }
                for item in analysis.findings
            ]

        try:
            with self._session_factory() as session:
                recorded = record_sources(session, digest.sources)
                report = record_report(
                    session,
                    digest=digest,
                    research_task_id=claim.task_id,
                    outline=[
                        {"source_id": ref.source_id, "title": ref.title, "url": ref.url}
                        for ref in digest.sources
                    ],
                    summary_override=summary_override,
                    findings_override=findings_override,
                    model_label=model_label,
                )
                rows = record_evidence(
                    session,
                    report=report,
                    digest=digest,
                    source_ids=recorded.mapping,
                    verification=verification,
                )
                session.commit()
                return {
                    "report_id": report.id,
                    "evidence_rows": len(rows),
                    "verified_rows": sum(
                        1 for row in rows if row.verification_status.value == "verified"
                    ),
                    "sources_created": recorded.created,
                    "sources_updated": recorded.updated,
                    "source_ids": dict(recorded.mapping),
                    "fingerprint": digest_fingerprint(digest),
                }
        except Exception as error:  # noqa: BLE001 - 落库失败要如实说，但不能丢底稿
            logger.warning("报告/证据入库失败（底稿已落盘）：%s", error)
            return None

    def _verify_claims(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """逐条语义核查报告里的结论：模型判断"话对不对"，逐字确认"引文真不真"。

        两层证据链缺一不可：

        * 规则层（``verify_points``）只回答"这句话是不是原文里的字"，它证明不了
          口径、时点、因果；
        * 模型层（``check_findings``）回答"这段证据到底支不支持这条结论"，但它给出的
          支持/反对片段**必须能在证据里逐字找到**，找不到就降级。

        两层都不写"已核实"：``human_review_status`` 恒为 ``pending``，人工确认是另一回事。
        """
        from backend.app.models.research import ResearchReport
        from backend.app.research.persistence import record_checks
        from backend.app.research.semantic import check_findings
        from backend.app.research.verify import verify_points

        digest, _payload, paths = self._digest_for(claim)
        baseline = verify_points(digest)
        output: dict[str, Any] = {
            "generated_by": "rules",
            "digest": {key: str(value) for key, value in paths.items()},
            "checked": baseline["checked"],
            "found": baseline["found"],
            "missing": baseline["missing"],
            "results": baseline["results"],
            "semantic_check_done": False,
            "note": (
                "先逐字核对引文是否真出自原文，再由模型判断语义是否成立；"
                "模型判定不等于人工确认，human_review_status 一律 pending。"
            ),
        }
        blocked = self._model_blocked(claim)
        if blocked.status == "blocked":
            output["blocked"] = True
            output["next_step"] = (blocked.output or {}).get("next_step", "")
            return ExecutionOutcome(status="blocked", reason=blocked.reason, output=output)

        report_row, findings, units = self._load_checks_input(claim, digest)
        if not findings:
            output["semantic_skipped_reason"] = "no_findings"
            output["note"] = "报告里没有可核查的结论（findings 为空），语义核查未执行"
            return ExecutionOutcome(status="completed", output=output)
        if not units:
            output["semantic_skipped_reason"] = "no_evidence"
            output["note"] = "这条报告没有入库证据，无法做语义核查（先修证据链）"
            return ExecutionOutcome(status="completed", output=output)

        try:
            checked = check_findings(
                findings,
                units,
                gateway=self._model_gateway,
                caller=caller,
                budget=self._budget_for(claim),
                purpose="research.verify",
            )
        except Exception as error:  # noqa: BLE001 - 核查失败要重试，不能悄悄放过
            reason = str(getattr(error, "reason", "") or type(error).__name__)
            output["semantic_error"] = {"reason": reason, "message": str(error)[:500]}
            output["note"] = "语义核查调用失败，本轮不产出核查结论（不会退回成『已核对』）"
            return ExecutionOutcome(
                status="retry",
                reason=f"semantic_check_failed: {reason}",
                output=output,
                **self._usage_kwargs(error),
            )

        with self._session_factory() as session:
            stored = session.get(ResearchReport, report_row.id)
            if stored is not None:
                record_checks(session, report=stored, checks=checked.checks)
                session.commit()

        files = self._write_semantic(
            paths, checked, objective=str(getattr(digest, "objective", ""))
        )
        output.update(
            {
                "generated_by": "model+rules",
                "semantic_check_done": True,
                "provider": checked.provider,
                "model": checked.model,
                "model_call_id": checked.model_call_id,
                "verdicts": checked.verdict_counts(),
                "sentence_count": len(checked.checks),
                "semantic_files": files,
                "checks": checked.as_dict()["checks"],
                "human_review": "全部待人工复核（human_review_status=pending）",
                "note": (
                    "结论逐条给了判定与逐字片段；判定是模型意见，不是人工核实。"
                    "confirmed/rejected 只能由人工在复核接口里写。"
                ),
            }
        )
        return ExecutionOutcome(
            status="completed",
            output=output,
            calls=1,
            tokens=checked.prompt_tokens + checked.completion_tokens,
            cost_cny=Decimal(str(round(checked.cost_cny, 6))),
        )

    def _load_checks_input(
        self, claim: Claim, digest: Any
    ) -> tuple[Any, list[dict[str, Any]], list[Any]]:
        """取出"这条任务最新的报告 + 它的入库证据"，并建立 S01→数据库 id 的对应。"""
        from sqlalchemy import select

        from backend.app.models.evidence import Evidence
        from backend.app.models.research import ResearchReport
        from backend.app.models.source import Source as SourceRow
        from backend.app.research.semantic import EvidenceUnit

        with self._session_factory() as session:
            report = session.scalars(
                select(ResearchReport)
                .where(ResearchReport.research_task_id == claim.task_id)
                .order_by(ResearchReport.id.desc())
                .limit(1)
            ).first()
            if report is None:
                return None, [], []
            report_id = report.id
            raw_findings = report.findings if isinstance(report.findings, list) else []
            findings: list[dict[str, Any]] = []
            for item in raw_findings:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    kind = str(item.get("kind") or "inference")
                    source_ids = [str(s) for s in (item.get("source_ids") or [])]
                    numbers = [str(n) for n in (item.get("numbers") or [])]
                else:
                    text, kind, source_ids, numbers = str(item).strip(), "inference", [], []
                if text:
                    findings.append(
                        {
                            "text": text,
                            "kind": kind,
                            "source_ids": source_ids,
                            "numbers": numbers,
                        }
                    )
            rows = session.execute(
                select(
                    Evidence.id, Evidence.source_id, Evidence.claim, Evidence.original_excerpt
                )
                .where(Evidence.research_report_id == report_id)
                .order_by(Evidence.id)
            ).all()

        # 底稿里的 S01 是对外的来源编号，证据行只认数据库主键，得先把两者对上。
        #
        # 注意：核查阶段拿到的 `_upstream` 是上游阶段**跑完之后**的输出，里面已经没有
        # 抓取明细了，所以不能只从本次重算的底稿里找对应关系——那份底稿此时通常
        # 一个来源都没有。可靠的做法是以入库时写下的 `report.outline` 为准
        # （它就是当时底稿里的 来源编号 ↔ URL ↔ 标题）。
        by_url: dict[str, str] = {}
        by_title: dict[str, str] = {}
        for ref in getattr(digest, "sources", []) or []:
            url = str(getattr(ref, "url", "") or "")
            title = str(getattr(ref, "title", "") or "")
            label = str(getattr(ref, "source_id", "") or "")
            if not label:
                continue
            if url:
                by_url.setdefault(url, label)
            if title:
                by_title.setdefault(title, label)
        outline = report.outline if isinstance(report.outline, list) else []
        for item in outline:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            label = str(item.get("source_id") or "")
            if not label:
                continue
            if url:
                by_url.setdefault(url, label)
            if title:
                by_title.setdefault(title, label)

        url_map: dict[int, str] = {}
        unmatched = 0
        source_ids = {row[1] for row in rows if row[1] is not None}
        if source_ids:
            with self._session_factory() as session:
                for source in session.scalars(
                    select(SourceRow).where(SourceRow.id.in_(source_ids))
                ).all():
                    label = by_url.get(str(source.url or "")) or by_title.get(
                        str(source.title or "")
                    )
                    if label:
                        url_map[source.id] = label
        units: list[Any] = []
        for evidence_id, source_id, text, excerpt in rows:
            body = (excerpt or text or "").strip()
            if not body:
                continue
            label = url_map.get(source_id)
            if label is None:
                unmatched += 1
                label = f"db-{source_id}"
            units.append(EvidenceUnit(evidence_id=evidence_id, source_id=label, text=body))
        if unmatched:
            logger.warning(
                "核查时有 %d 条证据没能对上底稿里的来源编号（报告 %s），它们的引文会按来源不匹配处理",
                unmatched,
                getattr(report, "id", "?"),
            )
        return report, findings, units

    def _write_semantic(
        self, paths: dict[str, Path], checked: Any, *, objective: str
    ) -> dict[str, str]:
        directory = Path(paths["markdown"]).parent
        markdown_path = directory / "semantic_check.md"
        json_path = directory / "semantic_check.json"
        markdown_path.write_text(checked.to_markdown(objective=objective), encoding="utf-8")
        json_path.write_text(
            json.dumps(checked.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"markdown": str(markdown_path), "json": str(json_path)}

    # -------------------------------------------------------------- 内容阶段

    def _discover(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """``topic.discover``：把研究目标拆成一页"要回答哪些问题"的框架。

        这里**不检索、不调模型**：它给出骨架（行业/产业链/公司三选一）与前几节要回答的
        问题，供人工在立项闸门上判断"这个方向值不值得查"。真正的检索词由
        ``research.plan`` 产出。
        """
        import json as _json

        from backend.app.research.frameworks import framework_for

        payload = claim.input_payload if isinstance(claim.input_payload, dict) else {}
        objective = (
            self._task_objective(claim.task_id)
            or str(payload.get("objective") or "")
            or claim.question
            or ""
        ).strip()
        framework = framework_for(objective)
        questions = [
            {
                "key": section.key,
                "title": section.title,
                "question": section.question,
                "allowed_kinds": list(section.kinds),
            }
            for section in framework.sections[:6]
        ]

        directory = self._stage_dir(claim)
        directory.mkdir(parents=True, exist_ok=True)
        payload_out = {
            "objective": objective,
            "framework": framework.kind,
            "framework_title": framework.title,
            "questions": questions,
            "note": "这一阶段只出研究框架，没有检索、没有调用模型",
        }
        json_path = directory / "discovery.json"
        json_path.write_text(
            _json.dumps(payload_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        markdown_path = directory / "discovery.md"
        lines = [
            f"# 研究框架：{framework.title}",
            "",
            f"> 目标：{objective}",
            "",
            "## 要回答的问题",
            "",
        ]
        lines += [f"{index}. **{item['title']}**：{item['question']}" for index, item in enumerate(questions, start=1)]
        lines += ["", "> 这一步只定框架，检索词由研究规划阶段产出。"]
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return ExecutionOutcome(
            status="completed",
            output={
                "generated_by": "rules",
                "framework": framework.kind,
                "framework_title": framework.title,
                "questions": questions,
                "files": {"markdown": str(markdown_path), "json": str(json_path)},
                "note": "这一阶段只出研究框架，没有检索、没有调用模型",
            },
        )

    def _latest_report(self, claim: Claim) -> Any | None:
        """这条任务最新的一份研究报告（没有就是还没跑到 synthesize）。"""
        from sqlalchemy import select

        from backend.app.models.research import ResearchReport

        with self._session_factory() as session:
            return session.scalars(
                select(ResearchReport)
                .where(ResearchReport.research_task_id == claim.task_id)
                .order_by(ResearchReport.id.desc())
                .limit(1)
            ).first()

    def _stage_dir(self, claim: Claim) -> Path:
        base = self.artifacts_root or DEFAULT_ARTIFACTS_ROOT
        return Path(base) / f"task-{claim.task_id}" / "content"

    def _blocked_stage(self, reason: str, note: str, *, next_step: str) -> ExecutionOutcome:
        return ExecutionOutcome(
            status="blocked",
            reason=reason,
            output={"note": note, "next_step": next_step},
        )

    def _script(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """``content.script``：把**人工确认过的结论**写成口播草稿。

        没确认就不写。这不是提示词里的一句劝告，是这里根本拿不到未确认的结论——
        ``confirmed_findings`` 只返回 ``human_review_status=confirmed`` 的那些行。
        """
        from backend.app.content.stages import StageError, draft_script, write_stage_files
        from backend.app.research.draft_gate import confirmed_findings

        report = self._latest_report(claim)
        if report is None:
            return self._blocked_stage(
                "no_report",
                "这条任务还没有研究报告，写不了脚本",
                next_step="先让 research.synthesize 跑完产出报告",
            )

        with self._session_factory() as session:
            from backend.app.models.research import ResearchReport

            stored = session.get(ResearchReport, report.id)
            confirmed = confirmed_findings(session, stored) if stored is not None else []
            report_id = report.id
            report_title = str(getattr(report, "title", "") or "")

        if not confirmed:
            return self._blocked_stage(
                "no_confirmed_findings",
                "这份报告还没有人工确认过的结论：脚本必须只写确认过的内容",
                next_step="先到「语义核查」页逐条确认，再恢复任务",
            )

        objective = self._task_objective(claim.task_id)
        try:
            draft = draft_script(
                report=report,
                confirmed=confirmed,
                requested_title=objective or report_title,
            )
        except StageError as error:
            return self._blocked_stage(
                error.reason,
                error.message,
                next_step="检查结论内容后再恢复任务",
            )

        directory = self._stage_dir(claim)
        files = write_stage_files(out_dir=directory, draft=draft)

        script_id = self._store_script(claim, draft, report_id)
        output: dict[str, Any] = {
            "generated_by": draft.generated_by,
            "script_id": script_id,
            "title": draft.title,
            "line_count": len(draft.lines),
            "word_count": draft.word_count,
            "estimated_seconds": draft.estimated_seconds,
            "confirmed_used": len(draft.lines),
            "dropped": list(draft.dropped),
            "files": files,
            "note": (
                "草稿只用了人工确认过的结论；未确认的一条都没进来。"
                "机器音与渲染都不在这一步。"
            ),
        }
        if script_id is None:
            output["store_note"] = (
                "任务没有绑定选题（topic_id 为空），脚本只落盘未入库"
                if self._task_topic_id(claim.task_id) is None
                else "脚本入库失败，产物已落盘，详见日志"
            )
        return ExecutionOutcome(status="completed", output=output)

    def _task_topic_id(self, task_id: int) -> int | None:
        from backend.app.models.execution import ResearchTask

        with self._session_factory() as session:
            task = session.get(ResearchTask, task_id)
            return getattr(task, "topic_id", None) if task is not None else None

    def _store_script(self, claim: Claim, draft: Any, report_id: int) -> int | None:
        """把草稿写成 ``scripts`` 表的一行（版本号由 ``ScriptService`` 管）。"""
        topic_id = self._task_topic_id(claim.task_id)
        if topic_id is None:
            return None
        from backend.app.services.script_service import ScriptService

        try:
            with self._session_factory() as session:
                script = ScriptService(session).create(
                    topic_id=topic_id,
                    title=draft.title,
                    body=draft.body,
                    research_report_id=report_id,
                    hook=draft.hook,
                    segments=[line.as_dict() for line in draft.lines],
                    disclaimer=draft.disclaimer,
                    estimated_duration=int(round(draft.estimated_seconds)),
                )
                script_id = script.id
                session.commit()
                return script_id
        except Exception as error:  # noqa: BLE001 - 入库失败不该吞掉已生成的草稿
            logger.warning("脚本入库失败（草稿已落盘）：%s", error)
            return None

    def _visual(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """``content.visual``：把草稿排成镜头计划（只排，不渲染）。"""
        import json as _json

        from backend.app.content.stages import ScriptDraft, ScriptLine, plan_shots, write_stage_files

        directory = self._stage_dir(claim)
        draft_path = directory / "script_draft.json"
        if not draft_path.exists():
            return self._blocked_stage(
                "no_script_draft",
                "还没有脚本草稿，排不了镜头",
                next_step="先让 content.script 产出草稿（需要先人工确认结论）",
            )
        raw = _json.loads(draft_path.read_text(encoding="utf-8"))
        draft = ScriptDraft(
            title=str(raw.get("title") or ""),
            hook=str(raw.get("hook") or ""),
            lines=tuple(
                ScriptLine(
                    text=str(item.get("text") or ""),
                    role=str(item.get("role") or "evidence"),
                    source_ids=tuple(str(x) for x in (item.get("source_ids") or [])),
                    numbers=tuple(str(x) for x in (item.get("numbers") or [])),
                )
                for item in (raw.get("lines") or [])
                if str(item.get("text") or "").strip()
            ),
            disclaimer=str(raw.get("disclaimer") or ""),
            generated_by=str(raw.get("generated_by") or "rules"),
        )
        if not draft.lines:
            return self._blocked_stage(
                "empty_script",
                "草稿里没有可用的句子",
                next_step="回到 content.script 重新生成",
            )
        shots = plan_shots(draft)
        files = write_stage_files(out_dir=directory, shots=shots)
        return ExecutionOutcome(
            status="completed",
            output={
                "generated_by": "rules",
                "shot_count": len(shots),
                "total_seconds": round(sum(float(shot["seconds"]) for shot in shots), 2),
                "asset_types": sorted({str(shot["asset_type"]) for shot in shots}),
                "files": files,
                "note": "这一阶段只排镜头计划，没有任何画面被渲染出来",
            },
        )

    def _produce(self, claim: Claim, caller: CallerContext) -> ExecutionOutcome:
        """``content.produce``：出制作计划（配音/字幕/音乐/渲染参数/质检清单）。"""
        import json as _json

        from backend.app.content.stages import production_plan, write_stage_files

        directory = self._stage_dir(claim)
        script_path = directory / "script_draft.json"
        shots_path = directory / "visual_plan.json"
        if not script_path.exists() or not shots_path.exists():
            return self._blocked_stage(
                "no_visual_plan",
                "脚本或分镜计划还缺一个，出不了制作计划",
                next_step="先跑 content.script 与 content.visual",
            )
        raw_script = _json.loads(script_path.read_text(encoding="utf-8"))
        raw_shots = _json.loads(shots_path.read_text(encoding="utf-8"))
        shots = list(raw_shots.get("shots") or [])

        from backend.app.content.stages import ScriptDraft

        draft = ScriptDraft(
            title=str(raw_script.get("title") or ""),
            hook=str(raw_script.get("hook") or ""),
            lines=(),
            disclaimer=str(raw_script.get("disclaimer") or ""),
            generated_by=str(raw_script.get("generated_by") or "rules"),
        )
        job_id = f"task-{claim.task_id}"
        plan = production_plan(draft=draft, shots=shots, job_id=job_id)
        files = write_stage_files(out_dir=directory, plan=plan)
        return ExecutionOutcome(
            status="completed",
            output={
                "generated_by": "rules",
                "job_id": job_id,
                "shot_count": plan["shot_count"],
                "duration_seconds": plan["duration_seconds"],
                "not_done": plan["not_done"],
                "files": files,
                "note": "制作计划已就绪；渲染、配音、素材下载、发布都还没做（next_step 里有命令）",
                "next_step": plan["render"]["command"],
            },
        )

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
