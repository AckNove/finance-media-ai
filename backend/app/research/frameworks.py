"""行业 / 产业链 / 公司三类研究框架（V6.0 模块 E）。

为什么要有这个东西
------------------
没有骨架时，模型会给一份"什么都说一点、什么都说不到点上"的通用介绍：
市场规模有、竞争格局也有，但缺"这个行业的钱是怎么分的""瓶颈在哪一节"。
框架把**研究目标**变成一组必须回答的问题，每条结论都要落到某一节上，
模板填空式的通篇复述就无处可藏。

三条纪律
--------
1. **只定义"问什么"，不预置"答什么"**：这里没有任何行业数据、公司名或数字，
   所有内容必须来自抓取到的证据。
2. **缺证据就留空**：``checklist()`` 明确标出哪几节没有结论、哪几节只有推断，
   报告里照实写"本节缺证据"，不补漂亮话。
3. **事实/推断/假设分开**：每节允许的 ``kinds`` 不同（例如"市场规模"要事实，
   "潜在机会"允许推断），越界的结论在报告里会被单独标出来。

用法
----
    kind = detect_kind("光伏产业链上游硅料价格为什么下跌")
    framework = framework_for(kind)
    framework.prompt_block()      # 塞进分析提示词
    framework.checklist(findings) # 报告里标出缺口
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence


class FrameworkKind(StrEnum):
    INDUSTRY = "industry"
    CHAIN = "chain"
    COMPANY = "company"


@dataclass(frozen=True)
class Section:
    """一节要回答的核心问题。"""

    key: str
    title: str
    question: str
    kinds: tuple[str, ...] = ("fact", "inference")
    evidence_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "question": self.question,
            "kinds": list(self.kinds),
            "evidence_hint": self.evidence_hint,
        }


@dataclass(frozen=True)
class Framework:
    kind: FrameworkKind
    title: str
    sections: tuple[Section, ...]
    rules: tuple[str, ...] = field(default=())

    def section(self, key: str) -> Section | None:
        for item in self.sections:
            if item.key == key:
                return item
        return None

    def keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.sections)

    def prompt_block(self) -> str:
        """给模型看的骨架：每条结论必须挂到其中一节。"""

        lines = [f"【报告骨架：{self.title}】", "每条结论必须用 section 字段说明它回答的是哪一节："]
        for index, item in enumerate(self.sections, start=1):
            kinds = "/".join(item.kinds)
            hint = f"；证据要求：{item.evidence_hint}" if item.evidence_hint else ""
            lines.append(f"{index}. {item.key}｜{item.title}｜要回答：{item.question}（允许类型 {kinds}{hint}）")
        if self.rules:
            lines.append("额外要求：")
            lines += [f"- {rule}" for rule in self.rules]
        lines.append("如果某一节证据不足，就不要硬写：把它写进 data_gaps，并在 section 里留空。")
        return "\n".join(lines)

    def checklist(self, findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """按节统计：有几条结论、有没有事实级证据、缺哪些节。

        ``findings`` 元素需含 ``section`` / ``kind``（缺省按空处理），本函数**不猜**——
        没有 section 的结论进 ``unsectioned``，不会被偷偷归到某一节里。
        """

        per_section: dict[str, dict[str, Any]] = {
            item.key: {"title": item.title, "claims": 0, "facts": 0, "inferences": 0, "sources": []}
            for item in self.sections
        }
        unsectioned: list[str] = []
        for raw in findings:
            claim = str(raw.get("claim") or raw.get("text") or "").strip()
            key = str(raw.get("section") or "").strip()
            kind = str(raw.get("kind") or "").strip().lower()
            if key not in per_section:
                if claim:
                    unsectioned.append(claim)
                continue
            bucket = per_section[key]
            bucket["claims"] += 1
            if kind == "fact":
                bucket["facts"] += 1
            elif kind in {"inference", "hypothesis"}:
                bucket["inferences"] += 1
            for source in raw.get("source_ids") or []:
                text = str(source)
                if text and text not in bucket["sources"]:
                    bucket["sources"].append(text)
        empty = [item.key for item in self.sections if per_section[item.key]["claims"] == 0]
        inference_only = [
            item.key
            for item in self.sections
            if per_section[item.key]["claims"] > 0 and per_section[item.key]["facts"] == 0
        ]
        return {
            "framework": str(self.kind),
            "title": self.title,
            "sections": per_section,
            "empty_sections": empty,
            "inference_only_sections": inference_only,
            "unsectioned": unsectioned,
            "coverage": round((len(self.sections) - len(empty)) / max(len(self.sections), 1), 3),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "title": self.title,
            "sections": [item.as_dict() for item in self.sections],
            "rules": list(self.rules),
        }


INDUSTRY = Framework(
    kind=FrameworkKind.INDUSTRY,
    title="行业研究",
    sections=(
        Section("definition", "行业定义与边界", "这个行业到底做什么、边界在哪、常被混淆的近邻行业是什么",
                evidence_hint="官方行业分类、协会口径、招股书业务描述"),
        Section("stage", "发展阶段", "处于导入/成长/成熟/衰退的哪一段，依据是什么",
                evidence_hint="渗透率、增速变化、产能利用率"),
        Section("market_size", "市场规模", "规模多大、按什么口径统计、增速多少",
                evidence_hint="统计口径必须写明（收入/出货量/零售额）与年份", kinds=("fact",)),
        Section("structure", "产业结构", "由哪些环节/玩家构成，集中度如何",
                evidence_hint="份额数据、CR5/CR10"),
        Section("supply_demand", "供需关系", "供给与需求的强弱、缺口或过剩有多大",
                evidence_hint="产能、产量、需求量的同期数据"),
        Section("competition", "竞争格局", "谁在打、靠什么打、价格战还是差异化",
                evidence_hint="主要玩家的量价与策略证据"),
        Section("drivers", "驱动因素", "真正推动这个行业变化的因素（需求、技术、政策、成本）",
                evidence_hint="驱动因素与行业指标之间的因果链证据"),
        Section("cycle", "周期位置", "当前处于周期什么位置，先行指标指向哪",
                evidence_hint="库存、开工率、价格的历史序列"),
        Section("policy", "政策与监管", "有哪些硬约束与补贴，影响传导到哪一环",
                evidence_hint="政策原文与生效时间"),
        Section("risks", "主要风险", "什么情况下这个判断会失效", kinds=("fact", "inference", "hypothesis")),
    ),
    rules=(
        "区分事实、推断、观点：fact 必须能在证据里找到原文，inference 必须写清推理依据",
        "市场规模必须写明统计口径与年份，不同口径的数字不能混着比",
    ),
)

CHAIN = Framework(
    kind=FrameworkKind.CHAIN,
    title="产业链研究",
    sections=(
        Section("upstream", "上游", "上游提供什么、议价能力如何",
                evidence_hint="上游产品价格、集中度"),
        Section("midstream", "中游", "中游做什么加工、附加值在哪",
                evidence_hint="加工费、良率、产能"),
        Section("downstream", "下游", "下游是谁、需求变化的来源是什么",
                evidence_hint="下游装机/销量/订单"),
        Section("raw_materials", "核心原材料", "关键原材料是什么、占成本多少、供给是否刚性",
                evidence_hint="成本占比、价格序列", kinds=("fact",)),
        Section("equipment", "关键设备", "卡点在哪些设备/工艺上，谁能供",
                evidence_hint="设备国产化率、交期"),
        Section("tech_routes", "技术路线", "有哪几条路线在竞争，谁在押注哪条",
                evidence_hint="转换效率/良率等可比指标"),
        Section("cost_structure", "成本结构", "成本由哪几块构成，哪块在变",
                evidence_hint="分项成本占比与变化", kinds=("fact",)),
        Section("supply_relations", "供应链关系", "谁给谁供货、绑定有多深",
                evidence_hint="长单、合资、认证壁垒"),
        Section("value_split", "价值分配", "利润在链条上怎么分、为什么这么分",
                evidence_hint="各环节毛利率对比"),
        Section("bottlenecks", "关键瓶颈", "真正卡住整个链条的是哪一环",
                evidence_hint="产能利用率、扩产周期"),
        Section("opportunities", "潜在机会", "格局变化可能带来什么机会",
                kinds=("inference", "hypothesis")),
    ),
    rules=(
        "上下游必须落到具体环节与产品，不要只写'上游原材料'这种空话",
        "价值分配必须有各环节的毛利/价格数据支撑，不能用感觉代替",
    ),
)

COMPANY = Framework(
    kind=FrameworkKind.COMPANY,
    title="公司研究",
    sections=(
        Section("profile", "公司概况", "主营什么、在哪个环节、股权与治理结构如何",
                evidence_hint="年报/公告"),
        Section("business_mix", "业务构成", "收入与利润分别来自哪块业务，占比多少",
                evidence_hint="分部收入与毛利", kinds=("fact",)),
        Section("model", "商业模式", "靠什么赚钱、单位经济性如何",
                evidence_hint="量价拆分、客户结构"),
        Section("products", "产品与服务", "核心产品是什么、竞争力体现在哪",
                evidence_hint="产品指标、认证"),
        Section("moat", "竞争优势", "护城河是什么、能不能被绕过",
                evidence_hint="份额、成本、技术或渠道证据"),
        Section("customers_suppliers", "客户与供应商", "客户与供应商集中度、议价关系",
                evidence_hint="前五大占比"),
        Section("financials", "财务表现", "营收与利润的规模、增速与结构",
                evidence_hint="财报口径的营收/净利/毛利率", kinds=("fact",)),
        Section("profitability", "盈利能力", "毛利率、净利率、ROE 的水平与变化原因",
                evidence_hint="同比与环比要分清", kinds=("fact",)),
        Section("cashflow", "现金流", "经营现金流与利润是否匹配，钱去哪了",
                evidence_hint="经营/投资/筹资现金流", kinds=("fact",)),
        Section("balance", "资产负债结构", "杠杆水平、有息负债、偿债压力",
                evidence_hint="资产负债率、到期结构", kinds=("fact",)),
        Section("efficiency", "经营效率", "周转率、库存与应收的变化",
                evidence_hint="存货/应收周转", kinds=("fact",)),
        Section("peers", "同业比较", "和谁比、比什么、差在哪",
                evidence_hint="同业同口径指标"),
        Section("risks", "重大风险", "什么会让这个判断失效",
                kinds=("fact", "inference", "hypothesis")),
        Section("updates", "最新动态", "最近发生了什么变化（订单、扩产、监管、人事）",
                evidence_hint="公告与新闻的发布时间"),
    ),
    rules=(
        "财务数字必须写明年份与报告期（年度/半年/单季），同比与环比不得混用",
        "个案不能推广成行业结论：拿一家公司的数字说整个行业要先说明代表性",
    ),
)

FRAMEWORKS: dict[str, Framework] = {
    FrameworkKind.INDUSTRY.value: INDUSTRY,
    FrameworkKind.CHAIN.value: CHAIN,
    FrameworkKind.COMPANY.value: COMPANY,
}

_CHAIN_HINTS: dict[str, float] = {
    "产业链": 3.0,
    "上游": 2.0,
    "中游": 2.0,
    "下游": 2.0,
    "供应链": 2.0,
    "链条": 2.0,
    "卡脖子": 2.0,
    "环节": 1.0,
    "原材料": 1.0,
}
_COMPANY_HINTS: dict[str, float] = {
    "上市公司": 3.0,
    "财报": 3.0,
    "年报": 3.0,
    "招股书": 3.0,
    "公司": 2.0,
    "营收": 2.0,
    "净利": 2.0,
    "毛利率": 1.5,
    "现金流": 1.5,
    "估值": 2.0,
    "股价": 2.0,
    "市值": 2.0,
    "分红": 2.0,
}
_INDUSTRY_HINTS: dict[str, float] = {
    "行业": 2.5,
    "赛道": 2.0,
    "竞争格局": 2.0,
    "渗透率": 2.0,
    "供需": 2.0,
    "产能": 2.0,
    "价格战": 2.0,
    "市场": 1.0,
    "政策": 1.0,
    "周期": 1.0,
    "机会": 0.5,
}


def detect_kind(text: str) -> FrameworkKind:
    """按关键词加权判断该用哪套框架；判不出来时按行业处理（覆盖面最广）。

    加权而不是数命中个数：「价格战 + 现金流」这种研究目标同时命中两套框架，
    但问的是行业格局，所以产业侧的词权重要高于财务侧的单点词。
    """

    raw = str(text or "")

    def score(hints: dict[str, float]) -> float:
        return sum(weight for word, weight in hints.items() if word in raw)

    scores = {
        FrameworkKind.CHAIN: score(_CHAIN_HINTS),
        FrameworkKind.COMPANY: score(_COMPANY_HINTS),
        FrameworkKind.INDUSTRY: score(_INDUSTRY_HINTS),
    }
    best = max(scores.values())
    if best <= 0:
        return FrameworkKind.INDUSTRY
    for kind in (FrameworkKind.CHAIN, FrameworkKind.COMPANY, FrameworkKind.INDUSTRY):
        if scores[kind] == best:
            return kind
    return FrameworkKind.INDUSTRY  # pragma: no cover - 上面的循环必然命中


def framework_for(target: FrameworkKind | str) -> Framework:
    """按 kind 或研究目标文本取框架。"""

    if isinstance(target, FrameworkKind):
        return FRAMEWORKS[target.value]
    text = str(target or "").strip()
    if text in FRAMEWORKS:
        return FRAMEWORKS[text]
    return FRAMEWORKS[detect_kind(text).value]


_SECTION_TOKEN = re.compile(r"[a-z_]+")


def normalize_section(raw: str, framework: Framework | None) -> str:
    """把模型给的 section 归一：只接受骨架里出现过的 key，其余一律空串。"""

    if framework is None:
        return ""
    match = _SECTION_TOKEN.match(str(raw or "").strip().lower())
    key = match.group(0) if match else ""
    return key if key in framework.keys() else ""


def sections_of(framework: Framework, findings: Iterable[Mapping[str, Any]]) -> list[tuple[Section, list[dict[str, Any]]]]:
    """把结论按骨架顺序分好组；无 section 的结论不塞进任何一节。"""

    grouped: dict[str, list[dict[str, Any]]] = {item.key: [] for item in framework.sections}
    for raw in findings:
        key = str(raw.get("section") or "").strip()
        if key in grouped:
            grouped[key].append(dict(raw))
    return [(item, grouped[item.key]) for item in framework.sections]


__all__ = [
    "CHAIN",
    "COMPANY",
    "FRAMEWORKS",
    "Framework",
    "FrameworkKind",
    "INDUSTRY",
    "Section",
    "detect_kind",
    "framework_for",
    "normalize_section",
    "sections_of",
]
