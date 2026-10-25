"""Company context -> exposure of the company to an event, plus compact texts for prompts.

Exposure is computed deterministically from company features and event tags. It decides relevance (skip or
not), feeds the impact score (docs/IMPACT_SCALE.md) and gives the LLM the *allowed* feature paths to cite.
The LLM explains; it does not decide which company facts exist ("classifier decides, LLM explains").
"""

import re
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


_RUB_STRONGER = re.compile(
    r"рубл\w*\s+(\w+\s+){0,3}(вырос|укрепил|подорожал|обновил максимум)|"
    r"(юан|доллар|евро|валют)\w*\s+(\w+\s+){0,3}(снизил|упал|подешевел|опустил|просел)",
    re.IGNORECASE,
)
_RUB_WEAKER = re.compile(
    r"рубл\w*\s+(\w+\s+){0,3}(ослаб|упал|подешевел|снизил|обновил минимум)|"
    r"(юан|доллар|евро|валют)\w*\s+(\w+\s+){0,3}(вырос|подорожал|укрепил)",
    re.IGNORECASE,
)
_MORTGAGE = re.compile(r"ипотек", re.IGNORECASE)


# --- event screening (ADR-ML-010): cues are matched on whole words of title + lead + news body ----------------
_ECON = re.compile(
    r"цен[аыеу]?\b|цены|стоимост|рын(ок|ка|ке|ку)|спрос|выручк|прибыл|убыт|продаж|экспорт|импорт|поставк|"
    r"производств|выпуск|инвест|ставк|курс|кредит|ипотек|налог|пошлин|тариф|бюджет|компани|предприят|бизнес|"
    r"отрасл|банк|рубл|доллар|юан|евро\b|млн|млрд|трлн|тонн|баррел|акци[ия]|облигац|инфляц|ввп|санкц|"
    r"перевоз|груз|логист|склад|урожа|топлив|бензин|дизел|нефт|газ\b|газа\b|сырь|"
    r"price|market|demand|revenue|profit|sales|export|import|supply|production|invest|rate|tariff|budget|"
    r"inflation|freight|cargo|shipping|earnings|oil|diesel|fuel|grain|wheat|steel|\$|%",
    re.IGNORECASE,
)
_RUSSIA = re.compile(
    r"росси|\bрф\b|рубл|российск|отечествен|москв|петербург|минфин|минэк|минпромторг|правительств|госдум|"
    r"банк росси|\bцб\b|центробанк(?!\w* (турци|кита|сша|инди|япони))|набиуллин|новак|силуанов|ржд|"
    r"russia|russian|ruble|rouble|moscow",
    re.IGNORECASE,
)
FOREIGN_FACTOR = 0.35  # a story about another country with no link to Russia or the company's trade
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
# market conditions of an industry (prices, tariffs, demand, shortages) — what reaches a company via counterparties;
# a single firm's incident or expansion does not
_MARKET = re.compile(
    r"цен[аыеу]?\b|цены|стоимост|тариф|ставк\w* (фрахт|перевоз)|дефицит|спрос|подорож|подешев|рост\w* цен|"
    r"сниж\w* цен|пошлин|сбор\b|сбора\b|санкц|ограничени|запрет|price|rate[sd]?\b|tariff|shortage|demand|cost",
    re.IGNORECASE,
)
_FOREIGN_GEO = re.compile(
    r"\bЕС\b|евросоюз|европ|\bсша\b|американ|британ|нидерланд|германи|франц|париж|индии|индия|япони|канад|"
    r"кени|пакистан|венесуэл|оаэ|гонконг|вьетнам|бразил|норвеги|осло|кипр",
    re.IGNORECASE,
)
# the Bank of Russia's policy: key rate / monetary policy without a foreign central bank in the headline
_RU_RATE = re.compile(
    r"ключев\w*\s+ставк|денежно-кредитн|\bдкп\b|ставк\w*\s+(цб|банка россии)", re.IGNORECASE
)
# the headline is about a rate at all ("снизить ставку", "прогноз по средней ставке"); a market wrap is not
_RATE_WORD = re.compile(r"\bставк\w*|\bдкп\b|\bдку\b", re.IGNORECASE)
_FOREIGN_CB = re.compile(
    r"фрс|\bfed\b|ецб|банк англии|банк японии|центробанк\w* (турци|кита|сша|инди|япони|казахстан)|"
    r"народн\w* банк кита|турци|индии|филиппин|гонконг|hkma",
    re.IGNORECASE,
)
_TRADE = re.compile(
    r"импорт|экспорт|поставк|груз|таможн|пошлин|торгов|перевоз|логист|порт[ауе]?\b|портов|транзит|платеж|платёж|"
    r"расчет|расчёт|валют|контракт|закупк|import|export|cargo|customs|tariff|trade|shipping|freight|payment",
    re.IGNORECASE,
)
# backend tags that come from homonyms or substrings ("в прокат выходят", "Чугунный мост", "медики")
_TAG_CUES: dict[tuple[str, str], re.Pattern[str]] = {
    ("commodities", "nonferrous"): re.compile(
        r"\bмед(ь|и|ью)\b|алюмини|никел|\bцинк|цветн\w* металл|цветмет|copper|alumin|nickel|zinc", re.I
    ),
    ("commodities", "steel"): re.compile(
        r"\bсталь|\bстальн|сталелит|металлопрокат|арматур|горячекат|холоднокат|сортов\w* прокат|"
        r"(производств|выпуск|экспорт|импорт|потреблени|цен\w*|тонн|рын\w*|спрос\w*|поставк\w*)( на)? стали\b|"
        r"прокат\w*(?=[^.]{0,60}(стал|металл|арматур|сортов))|steel|rebar",
        re.I,
    ),
    ("industry_tags", "metals"): re.compile(
        r"металл|\bсталь|\bстальн|сталелит|арматур|горячекат|(производств|выпуск|экспорт|импорт|потреблени|"
        r"цен\w*|тонн|рын\w*)( на)? стали\b|прокат\w*(?=[^.]{0,60}(стал|металл|арматур))|steel|metal",
        re.I,
    ),
    ("industry_tags", "steel"): re.compile(
        r"\bсталь|\bстальн|сталелит|металлопрокат|арматур|горячекат|(производств|выпуск|экспорт|импорт|"
        r"потреблени|цен\w*|тонн|рын\w*)( на)? стали\b|прокат\w*(?=[^.]{0,60}(стал|металл|арматур))|steel",
        re.I,
    ),
    ("industry_tags", "retail"): re.compile(
        r"ритейл|розничн|торгов\w* сет|продаж|товарооборот|средн\w* чек|покупательск|retail|sales|store sales",
        re.I,
    ),
}


def _event_text(event: dict[str, Any]) -> str:
    parts = [str(event.get("title") or ""), str(event.get("summary") or "")]
    for n in (event.get("news") or [])[:2]:
        body = n.get("body") if isinstance(n, dict) else None
        if isinstance(body, dict):
            parts.append(str(body.get("content") or "")[:1500])
    return ". ".join(parts)


def screen_event(event: dict[str, Any]) -> dict[str, Any]:
    """Cheap deterministic screening of an event: is it business news, about Russia, about the key rate, trade."""
    text = _event_text(event)
    head = str(event.get("title") or "")
    return {
        "business": bool(_ECON.search(text)),
        "russia": bool(_RUSSIA.search(text)),
        "cyrillic": bool(_CYRILLIC.search(head)),
        "ru_rate": bool(_RU_RATE.search(text))
        and bool(_CYRILLIC.search(head))
        and not _FOREIGN_CB.search(head),
        "trade": bool(_TRADE.search(text)),
        "market": bool(_MARKET.search(text)),
        "rate_in_title": bool(_RU_RATE.search(head) or _RATE_WORD.search(head)),
        "foreign_geo": bool(_FOREIGN_GEO.search(head)),
    }


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """ML-side fixes on top of the backend tagger (a copy; the backend's event is not changed).

    - FX direction is about the foreign currency: "рубль вырос" means the yuan/dollar went down.
    - Mortgage news concerns developers and construction even if tagged only as real estate.
    - Tags that come from homonyms or substrings are dropped when the text has no whole-word cue.
    - `ml_screen`: business / Russia / key-rate / trade cues used by compute_exposure.
    """
    ev = dict(event)
    tags = dict(ev.get("tags") or {})
    text = _event_text(ev)
    dropped = []
    for (kind, tag), cue in _TAG_CUES.items():
        vals = list(tags.get(kind) or [])
        if tag in vals and not cue.search(text):
            tags[kind] = [v for v in vals if v != tag]
            dropped.append(f"{kind}:{tag}")
    head = str(ev.get("title") or "")
    if ev.get("event_type") in {"other", "macro_stat", "commodities"} and (
        _RUB_STRONGER.search(head) or _RUB_WEAKER.search(head)
    ):
        ev["event_type"] = "fx"  # "Рубль вырос… в паре с юанем" is an FX move whatever the tagger said
    if ev.get("event_type") == "fx":
        stronger, weaker = bool(_RUB_STRONGER.search(head)), bool(_RUB_WEAKER.search(head))
        if stronger != weaker:
            ev["direction"] = "down" if stronger else "up"
    if _MORTGAGE.search(f"{head}. {ev.get('summary') or ''}"):
        tags["industry_tags"] = sorted(
            set(tags.get("industry_tags") or []) | {"real_estate", "construction", "developer"}
        )
    ev["tags"] = tags
    ev["ml_screen"] = {**screen_event(ev), "dropped_tags": dropped}
    return ev


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
    ev_countries = {c.upper() for c in tags["countries"]} - {"РОССИЯ"}
    fts_upper = {str(c).upper() for c in (fv(features, "fts_countries", []) or [])}
    for tag in sorted(match_tags):
        for d, who in (("in", "покупателей"), ("out", "поставщиков")):
            path = f"counterparties.industry_share_{d}.{tag}"
            share = fv(features, path)
            strength = counterparty_strength(share) * SECOND_ORDER_DISCOUNT
            if d == "in" and etype == "commodities":
                strength *= 0.5  # a price move of the customers' goods barely changes what they buy from you
            if ev_countries and not ev_countries & fts_upper:
                strength *= 0.5  # a foreign-route story reaches the company's carriers only partly
            if isinstance(share, int | float) and strength > 0:
                res.offer(
                    "counterparty",
                    round(strength, 4),
                    [path],
                    f"{float(share) * 100:.0f}% платежей {who} компании приходится на затронутую отрасль.",
                )

    industry = set(fv(features, "industry_tags", []) or [])
    if match_tags & industry:
        foreign = bool(ev_countries) and not ev_countries & fts_upper
        res.offer(
            "industry",
            0.4 if foreign else 1.0,
            ["industry_tags", "okved.code"],
            "Событие касается отрасли компании" + (" за рубежом (косвенно)." if foreign else "."),
        )

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
        or (
            etype == "trade_logistics"
            # a foreign price story counts the country only with an own exposure, not via counterparties
            and bool(match_tags & (industry if tags["commodities"] else industry | _cp_tags(features)))
        )
    )
    if overlap_countries and trade_type:
        res.offer(
            "fx_country",
            1.0,
            ["fts_countries"],
            "Компания ведёт ВЭД со страной события: " + ", ".join(sorted(overlap_countries)) + ".",
        )
    elif ev_ccy and ev_ccy & own_ccy and etype in FX_COUNTRY_TYPES:
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
    return _apply_screen(res, event, etype)


def _apply_screen(res: "ExposureResult", event: dict[str, Any], etype: str) -> "ExposureResult":
    """Gates from the event screening (ADR-ML-010). Direct links (group, named counterparty) always stay."""
    scr = event.get("ml_screen") or screen_event(event)
    cp = res.items.get("counterparty")
    direct = {"group"} | (
        {"counterparty"}
        if cp is not None and any(f.startswith("counterparties.top_") for f in cp.features)
        else set()
    )
    items = res.items
    if not scr["business"]:
        for k in list(items):
            if k not in direct:
                del items[k]
        return res
    cp_items = items.get("counterparty")
    if cp_items is not None and "counterparty" not in direct and not scr.get("market", True):
        del items[
            "counterparty"
        ]  # an industry incident or a firm's news, not a change in the industry's terms
    if etype == "monetary_policy" and "rate" in items:
        if not scr["ru_rate"]:
            del items[
                "rate"
            ]  # a foreign central bank, an earnings story with "rates", "FedEx": not the key rate
        elif not scr.get("rate_in_title", True):
            r = items["rate"]  # a market wrap or a digest that mentions the rate in passing
            items["rate"] = Exposure(r.name, round(r.value * 0.25, 4), r.features, r.reason)
    fx = items.get("fx_country")
    if fx is not None:
        broad = fx.features == ["sensitivities.fx_sensitive"]
        by_country = "fts_countries" in fx.features and etype not in {"fx"}
        if broad and not (scr["russia"] and scr["trade"]):
            del items["fx_country"]  # a sanctions/FX story that is not about Russian trade or payments
        elif by_country and not scr["trade"]:
            del items["fx_country"]  # the company's trade country is mentioned, but not its trade
    if not scr["russia"] and (not scr["cyrillic"] or scr.get("foreign_geo")):
        for k, e in items.items():
            if k not in direct:
                items[k] = Exposure(
                    e.name, round(e.value * FOREIGN_FACTOR, 4), e.features, e.reason
                )  # foreign-only story
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
