"""Planning: which data the agent still needs for this company x event x role.

A deterministic rule plan always exists (cheap, explainable, works without LLM). When the LLM is available, the
planner model may add search queries and hypotheses; it cannot remove what the rules require.
"""

from dataclasses import dataclass, field
from typing import Any

from alfa_ml.profile import ExposureResult, fv

# Bank analytics topics (backend tags of search_bank_analytics) and default queries by event type.
ANALYTICS_BY_TYPE: dict[str, tuple[list[str], list[str]]] = {
    "monetary_policy": (["key_rate", "inflation"], ["прогноз ключевой ставки", "инфляция ожидания ЦБ"]),
    "fx": (["macro"], ["прогноз курса рубля", "курс юаня рубль"]),
    "commodities": (["steel", "gold", "macro"], ["цены на сырьё прогноз"]),
    "sanctions": (["macro"], ["санкции внешняя торговля платежи"]),
    "regulation_tax": (["macro"], ["налоговая нагрузка бюджет"]),
    "trade_logistics": (["macro"], ["импорт экспорт торговый баланс"]),
    "industry": (["macro"], []),
    "company": ([], []),
    "macro_stat": (["macro", "consumption", "inflation"], ["потребительский спрос динамика", "инфляция"]),
    "labor": (["macro", "consumption"], ["рынок труда зарплаты"]),
    "other": ([], []),
}
COMMODITY_QUERIES = {
    "steel": "рынок стали цены прогноз",
    "iron_ore": "железная руда цены",
    "gold": "золото цена прогноз",
    "oil": "нефть цены прогноз",
    "gas": "газ цены",
    "grain": "зерно экспорт цены",
    "fertilizers": "удобрения экспорт",
    "coal": "уголь цены",
    "nonferrous": "цветные металлы цены",
}
SECOND_ORDER_TYPES = {"commodities", "industry", "trade_logistics", "sanctions", "company", "regulation_tax"}


@dataclass
class Plan:
    analytics_queries: list[str] = field(default_factory=list)
    analytics_tags: list[str] = field(default_factory=list)
    news_queries: list[str] = field(default_factory=list)
    need_counterparties: bool = False
    need_group: bool = False
    focus: str = ""
    hypotheses: list[str] = field(default_factory=list)
    source: str = "rules@1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "analytics_queries": self.analytics_queries,
            "analytics_tags": self.analytics_tags,
            "news_queries": self.news_queries,
            "need_counterparties": self.need_counterparties,
            "need_group": self.need_group,
            "focus": self.focus,
            "hypotheses": self.hypotheses,
            "source": self.source,
        }

    def steps(self) -> list[str]:
        out = ["get_company_context", "get_event"]
        out += [f"search_bank_analytics: {q}" for q in self.analytics_queries]
        out += [f"search_news: {q}" for q in self.news_queries]
        if self.need_counterparties:
            out.append("get_counterparties")
        if self.need_group:
            out.append("get_group")
        out += ["get_bank_products", "draft", "self_check", "critic"]
        return out


def rule_plan(event: dict[str, Any], features: dict[str, Any], exposure: ExposureResult) -> Plan:
    etype = str(event.get("event_type") or "other")
    tags, queries = ANALYTICS_BY_TYPE.get(etype, ([], []))
    plan = Plan(analytics_tags=list(tags), analytics_queries=list(queries))
    ev_tags = event.get("tags") or {}
    for c in ev_tags.get("commodities") or []:
        if c in COMMODITY_QUERIES and COMMODITY_QUERIES[c] not in plan.analytics_queries:
            plan.analytics_queries.append(COMMODITY_QUERIES[c])
    title = str(event.get("title") or "")
    if title and len(plan.analytics_queries) < 3:
        plan.analytics_queries.append(title[:200])
    plan.analytics_queries = plan.analytics_queries[:3]

    names = set(exposure.items)
    plan.need_counterparties = "counterparty" in names or etype in SECOND_ORDER_TYPES
    group_size = fv(features, "group.size") or 0
    plan.need_group = isinstance(group_size, int | float) and group_size > 1 and etype in SECOND_ORDER_TYPES
    if len(event.get("news") or []) < 2 and title:
        plan.news_queries = [title[:200]]
    strongest = exposure.strongest
    plan.focus = strongest.reason if strongest else "Проверить, есть ли у компании связь с событием."
    return plan


def merge_llm_plan(plan: Plan, answer: dict[str, Any]) -> Plan:
    """Add LLM suggestions to the rule plan (bounded; never removes required steps)."""

    def strings(key: str, limit: int) -> list[str]:
        value = answer.get(key) or []
        if not isinstance(value, list):
            return []
        return [str(x).strip()[:200] for x in value if isinstance(x, str | int | float) and str(x).strip()][
            :limit
        ]

    for q in strings("analytics_queries", 3):
        if q not in plan.analytics_queries and len(plan.analytics_queries) < 4:
            plan.analytics_queries.append(q)
    for q in strings("news_queries", 2):
        if q not in plan.news_queries and len(plan.news_queries) < 2:
            plan.news_queries.append(q)
    plan.need_counterparties = plan.need_counterparties or bool(answer.get("need_counterparties"))
    plan.need_group = plan.need_group or bool(answer.get("need_group"))
    focus = answer.get("focus")
    if isinstance(focus, str) and focus.strip():
        plan.focus = focus.strip()[:300]
    plan.hypotheses = strings("hypotheses_to_check", 3)
    plan.source = "rules@1+llm"
    return plan
