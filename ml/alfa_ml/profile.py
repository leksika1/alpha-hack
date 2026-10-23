"""Company context -> exposure of the company to an event, plus compact texts for prompts.

Exposure is computed deterministically from company features and event tags. It decides relevance (skip or
not), feeds the impact score (docs/IMPACT_SCALE.md) and gives the LLM the *allowed* feature paths to cite.
The LLM explains; it does not decide which company facts exist ("classifier decides, LLM explains").
"""

from dataclasses import dataclass, field
from typing import Any

GENERIC_TAGS = frozenset({"trade", "manufacturing", "services", "finance"})
# "оптовые цены" makes the tagger add `wholesale` to any price news: too broad for counterparty/industry matching
MATCH_GENERIC_TAGS = GENERIC_TAGS | {"wholesale"}
# second-order effects (through counterparties of the affected industry) are discounted vs own exposure
SECOND_ORDER_DISCOUNT = 0.6
FX_COUNTRY_TYPES = frozenset({"fx", "sanctions"})
COUNTRY_CURRENCY = {
    "КИТАЙ": "CNY", "ГОНКОНГ": "CNY", "ТУРЦИЯ": "TRY", "ОАЭ": "AED", "ИНДИЯ": "INR", "КАЗАХСТАН": "KZT",
    "БЕЛАРУСЬ": "BYN", "США": "USD", "ГЕРМАНИЯ": "EUR", "ИТАЛИЯ": "EUR", "ФРАНЦИЯ": "EUR", "НИДЕРЛАНДЫ": "EUR",
    "ЮЖНАЯ КОРЕЯ": "KRW", "ЯПОНИЯ": "JPY", "УЗБЕКИСТАН": "UZS", "ИРАН": "IRR", "ЕГИПЕТ": "EGP",
}  # fmt: skip
CONSUMER_TAGS = frozenset({"retail", "consumer_goods", "food", "hospitality"})
RATE_PRODUCTS = ("loan", "leasing", "factoring")

# Proximity of the strongest reason (docs/IMPACT_SCALE.md).
PROXIMITY: dict[str, float] = {
    "group": 1.0,
    "rate": 0.9,
    "fx_country": 0.9,
    "user_provided": 0.9,
    "counterparty": 0.8,
    "commodity": 0.7,
    "industry": 0.6,
    "demand": 0.6,
    "labor": 0.5,
    "region": 0.4,
}

PRODUCT_LABELS = {
    "loan": "кредит",
    "leasing": "лизинг",
    "factoring": "факторинг",
    "deposit": "депозит",
    "guarantee": "банковская гарантия",
    "rko": "РКО",
    "payroll": "зарплатный проект",
}


COUNTERPARTY_MIN_SHARE = 0.01  # below 1% of turnover a counterparty industry is noise, not exposure
COUNTERPARTY_FULL_SHARE = 0.25  # 25%+ of turnover with the affected industry = full exposure


def counterparty_strength(share: Any) -> float:
    """Exposure through counterparties from the turnover share (calibrated, ml-ranker@1).

    The baseline uses min(1, 2·share): 0.35% of payments still opens the regulation boost (docs/BACKLOG.md).
    Here a share below 1% gives no exposure and the curve saturates at 25%.
    """
    if not isinstance(share, int | float) or share < COUNTERPARTY_MIN_SHARE:
        return 0.0
    return round(min(1.0, 0.2 + 0.8 * (share - COUNTERPARTY_MIN_SHARE) / COUNTERPARTY_FULL_SHARE), 4)


def fv(features: dict[str, Any], path: str, default: Any = None) -> Any:
    feat = features.get(path)
    return feat.get("value", default) if isinstance(feat, dict) else default


def feature_known(ref: str, paths: set[str] | frozenset[str]) -> bool:
    return ref in paths or any(p.startswith(ref + ".") for p in paths)


@dataclass
class Exposure:
    name: str
    value: float
    features: list[str]
    reason: str  # short Russian explanation, deterministic

    @property
    def weight(self) -> float:
        return self.value * PROXIMITY.get(self.name, 0.3)


@dataclass
class ExposureResult:
    items: dict[str, Exposure] = field(default_factory=dict)

    def offer(self, name: str, value: float, features: list[str], reason: str) -> None:
        cur = self.items.get(name)
        if cur is None or value > cur.value:
            self.items[name] = Exposure(name, value, features, reason)

    @property
    def strongest(self) -> Exposure | None:
        return max(self.items.values(), key=lambda e: (e.weight, e.name), default=None)

    def ordered(self) -> list[Exposure]:
        return sorted(self.items.values(), key=lambda e: -e.weight)

    def feature_refs(self) -> list[str]:
        out: list[str] = []
        for e in self.ordered():
            out.extend(f for f in e.features if f not in out)
        return out

    def score(self) -> float:
        """Relevance proxy 0..1 (noisy-OR of weighted exposures) — used when no backend candidate is given."""
        p = 1.0
        for e in self.items.values():
            p *= 1 - min(0.95, e.weight)
        return round(1 - p, 4)


def products_of(features: dict[str, Any]) -> list[str]:
    return sorted(
        path.split(".", 1)[1]
        for path in features
        if path.startswith("products.") and fv(features, path) is True
    )


def event_tags(event: dict[str, Any]) -> dict[str, set[str]]:
    tags = event.get("tags") or {}
    return {
        k: {str(x) for x in (tags.get(k) or []) if isinstance(x, str)}
        for k in ("industry_tags", "countries", "commodities", "regions", "currencies", "company_ids")
    }


def _cp_tags(features: dict[str, Any], min_share: float = COUNTERPARTY_MIN_SHARE) -> set[str]:
    out = set()
    for path in features:
        if path.startswith("counterparties.industry_share_"):
            share = fv(features, path)
            if isinstance(share, int | float) and share >= min_share:
                out.add(path.rsplit(".", 1)[-1])
    return out


def compute_exposure(features: dict[str, Any], event: dict[str, Any], company_id: str) -> ExposureResult:
    res = ExposureResult()
    tags = event_tags(event)
    etype = str(event.get("event_type") or "other")
    products = set(products_of(features))
    paths = set(features)

    group_id = fv(features, "group.id")
    if company_id in tags["company_ids"] or (group_id and group_id in tags["company_ids"]):
        res.offer("group", 1.0, ["group.id"], "Событие напрямую касается компании или её группы.")

    for d in ("in", "out"):
        top = fv(features, f"counterparties.top_{d}", []) or []
        hit = [cp for cp in top if isinstance(cp, dict) and cp.get("counterparty_id") in tags["company_ids"]]
        if hit:
            res.offer(
                "counterparty",
                1.0,
                [f"counterparties.top_{d}"],
                "Событие касается одного из крупнейших контрагентов компании.",
            )
    match_tags = tags["industry_tags"] - MATCH_GENERIC_TAGS
    for tag in sorted(match_tags):
        for d, who in (("in", "покупателей"), ("out", "поставщиков")):
            path = f"counterparties.industry_share_{d}.{tag}"
            share = fv(features, path)
            strength = counterparty_strength(share) * SECOND_ORDER_DISCOUNT
            if d == "in" and etype == "commodities":
                strength *= 0.5  # a price move of the customers' goods barely changes what they buy from you
            if isinstance(share, int | float) and strength > 0:
                res.offer(
                    "counterparty",
                    round(strength, 4),
                    [path],
                    f"{float(share) * 100:.0f}% платежей {who} компании приходится на затронутую отрасль.",
                )

    industry = set(fv(features, "industry_tags", []) or [])
    if match_tags & industry:
        res.offer("industry", 1.0, ["industry_tags", "okved.code"], "Событие касается отрасли компании.")

    fts = {str(c).upper() for c in (fv(features, "fts_countries", []) or [])}
    countries = {c.upper() for c in tags["countries"]} - {"РОССИЯ"}
    own_ccy = {COUNTRY_CURRENCY[c] for c in fts if c in COUNTRY_CURRENCY}
    own_ccy |= {str(x).upper() for x in (fv(features, "user_provided.currencies", []) or [])} - {"RUB"}
    ev_ccy = {c.upper() for c in tags["currencies"]}
    overlap_countries = countries & fts
    # trade with the event's country matters for FX/sanctions/logistics news; for a commodity or tax story
    # about that country only together with an own exposure (industry, commodity, counterparty)
    trade_type = (
        etype in FX_COUNTRY_TYPES
        or (etype == "trade_logistics" and not match_tags)
        or (etype == "trade_logistics" and bool(match_tags & (industry | _cp_tags(features))))
    )
    if overlap_countries and trade_type:
        res.offer(
            "fx_country",
            1.0,
            ["fts_countries"],
            "Компания ведёт ВЭД со страной события: " + ", ".join(sorted(overlap_countries)) + ".",
        )
    elif ev_ccy and ev_ccy & own_ccy:
        res.offer(
            "fx_country",
            0.9,
            ["fts_countries"] if fts else ["user_provided.currencies"],
            "Событие касается валюты расчётов по ВЭД компании: " + ", ".join(sorted(ev_ccy & own_ccy)) + ".",
        )
    elif overlap_countries:
        res.offer(
            "fx_country",
            0.35,
            ["fts_countries"],
            "Событие касается страны ВЭД компании: " + ", ".join(sorted(overlap_countries)) + " (косвенно).",
        )
    elif etype in FX_COUNTRY_TYPES and fv(features, "sensitivities.fx_sensitive"):
        broad = not countries and not ev_ccy  # a general FX/sanctions story vs. one about other countries
        res.offer(
            "fx_country",
            0.7 if broad else 0.3,
            ["sensitivities.fx_sensitive"],
            "У компании есть внешнеторговые операции — она чувствительна к курсу и условиям расчётов.",
        )

    if etype == "monetary_policy":
        rate_products = [p for p in RATE_PRODUCTS if p in products]
        if rate_products or fv(features, "sensitivities.rate_sensitive"):
            refs = ["sensitivities.rate_sensitive"] if "sensitivities.rate_sensitive" in paths else []
            refs += [f"products.{p}" for p in rate_products]
            label = ", ".join(PRODUCT_LABELS[p] for p in rate_products) or "заёмное финансирование"
            res.offer("rate", 1.0, refs, f"У компании {label} — стоимость долга зависит от ключевой ставки.")
        elif fv(features, "sensitivities.rate_sensitive_industry"):
            res.offer(
                "rate",
                0.6,
                ["sensitivities.rate_sensitive_industry"],
                "Спрос и финансирование в отрасли компании зависят от стоимости кредита.",
            )
        elif "deposit" in products:
            res.offer(
                "rate", 0.6, ["products.deposit"], "У компании депозиты — доходность следует за ставкой."
            )

    commodities = {str(c) for c in (fv(features, "sensitivities.commodity_exposure", []) or [])}
    if tags["commodities"] & commodities:
        res.offer(
            "commodity",
            1.0,
            ["sensitivities.commodity_exposure"],
            "Компания работает с сырьём, которое затрагивает событие.",
        )
    user_materials = {str(x).lower() for x in (fv(features, "user_provided.raw_materials", []) or [])}
    if tags["commodities"] & user_materials:
        res.offer(
            "user_provided", 1.0, ["user_provided.raw_materials"], "Событие касается сырья из вашей анкеты."
        )
    user_ccy = {str(x).upper() for x in (fv(features, "user_provided.currencies", []) or [])} - {"RUB"}
    if {c.upper() for c in tags["currencies"]} & user_ccy:
        res.offer(
            "user_provided", 1.0, ["user_provided.currencies"], "Событие касается валюты расчётов из анкеты."
        )

    if fv(features, "sensitivities.consumer_demand_dependent") and (
        tags["industry_tags"] & CONSUMER_TAGS or etype in {"macro_stat", "labor"}
    ):
        res.offer(
            "demand",
            0.8 if tags["industry_tags"] & CONSUMER_TAGS else 0.5,
            ["sensitivities.consumer_demand_dependent"],
            "Выручка компании зависит от потребительского спроса.",
        )
    if etype == "labor" and "payroll" in products:
        res.offer(
            "labor", 0.5, ["products.payroll"], "У компании зарплатный проект — она заметный работодатель."
        )

    region = fv(features, "region")
    if region and region in tags["regions"]:
        # the region alone is background (the event happens where the company works); with another exposure
        # it strengthens the case (logistics, regional demand)
        res.offer(
            "region",
            1.0 if res.items else 0.5,
            ["region"],
            f"Событие затрагивает регион работы компании: {region}.",
        )
    # Keep only references that really exist in this company's context (validator rule unknown_feature_ref).
    for e in list(res.items.values()):
        e.features = [f for f in e.features if feature_known(f, paths)]
        if not e.features:
            del res.items[e.name]
    return res


SECTION_ORDER = (
    "okved",
    "industry_tags",
    "segment",
    "revenue_rub",
    "group",
    "region",
    "products",
    "sensitivities",
    "fts_countries",
    "counterparties",
    "links",
    "user_provided",
)


def company_card(features: dict[str, Any], completeness: dict[str, Any] | None, limit: int = 60) -> str:
    """Feature list for prompts: `path = value (source)`; the paths are the only allowed feature_refs."""
    lines: list[str] = []

    def rank(path: str) -> int:
        head = path.split(".", 1)[0]
        return SECTION_ORDER.index(head) if head in SECTION_ORDER else len(SECTION_ORDER)

    for path in sorted(features, key=lambda p: (rank(p), p)):
        feat = features[path]
        if not isinstance(feat, dict):
            continue
        value = feat.get("value")
        if value in (None, [], {}, "") or (value is False and not path.startswith("products.")):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = [
                {k: v for k, v in item.items() if k in {"okved_name", "share", "industry_tags", "relation"}}
                for item in value[:5]
            ]
        text = str(value)
        if isinstance(value, float) and value > 1e6:
            text = f"{value / 1e9:.2f} млрд ₽" if value >= 1e9 else f"{value / 1e6:.0f} млн ₽"
        note = f"; {feat['note']}" if feat.get("note") else ""
        lines.append(f"- {path} = {text[:240]} ({feat.get('source', '?')}{note})")
        if len(lines) >= limit:
            break
    if completeness:
        missing = ", ".join(completeness.get("missing") or []) or "нет"
        lines.append(f"- [полнота данных] {completeness.get('score', '?')}; неизвестно: {missing}")
    return "\n".join(lines)
