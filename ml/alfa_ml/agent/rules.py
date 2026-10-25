"""Rule-based analyst (rules@1): a complete, grounded draft without LLM.

Used when the LLM is unavailable (no key, budget exhausted, allow_llm=false, invalid answers) and as the
safety net the LLM draft is compared against. Every company claim cites `sc`/`sk`/`sg`, every event claim cites
news sources, every bank claim cites an analytics chunk — so the result passes the backend validator.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from alfa_ml.agent import market
from alfa_ml.agent.draft import Action, BankPosition, Claim, Consequence, Draft, Fact, Quant, Reason, Scenario
from alfa_ml.agent.evidence import Evidence, is_demo_material
from alfa_ml.agent.textutil import (
    best_sentences,
    clip,
    first_sentences,
    fmt_pct,
    rate_change_pp,
    sentences,
    stems,
)
from alfa_ml.profile import GENERIC_TAGS, PRODUCT_LABELS, fv, products_of
from alfa_ml.ranker import ROLE_MULTIPLIERS

TAG_LABELS = {
    "metals": "металлы и металлопрокат",
    "metallurgy": "металлургия",
    "steel": "сталь",
    "construction": "строительство",
    "developer": "девелопмент",
    "real_estate": "недвижимость",
    "construction_materials": "стройматериалы",
    "logistics": "логистика",
    "trucking": "автоперевозки",
    "warehousing": "складская логистика",
    "shipping": "морские перевозки",
    "rail": "железнодорожные перевозки",
    "agriculture": "сельское хозяйство",
    "grain_trade": "торговля зерном",
    "food": "продукты питания",
    "beverages": "напитки",
    "retail": "розничная торговля",
    "consumer_goods": "потребительские товары",
    "oil_gas": "нефтегаз",
    "fuel": "топливо",
    "automotive": "автомобили",
    "automotive_parts": "автокомпоненты",
    "electronics": "электроника",
    "it": "ИТ",
    "chemicals": "химия и удобрения",
    "machinery": "машиностроение",
    "energy": "электроэнергетика",
    "hospitality": "гостиницы и общепит",
    "wholesale": "оптовая торговля",
    "leasing": "лизинг",
    "pharma": "фармацевтика",
    "telecom": "телеком",
    "rental": "аренда",
}
COMMODITY_LABELS = {
    "steel": "сталь и прокат",
    "iron_ore": "железную руду",
    "coal": "уголь",
    "oil": "нефть",
    "gas": "газ",
    "fuel": "топливо",
    "grain": "зерно",
    "fertilizers": "удобрения",
    "gold": "золото",
    "nonferrous": "цветные металлы",
    "cement": "цемент",
    "timber": "лесоматериалы",
    "sugar": "сахар",
    "fish": "рыбу",
}
FINANCE_ROLES = {"cfo", "treasurer", "chief_accountant"}
# OKVED prefixes of companies that SELL a commodity (producers, wholesale traders); everyone else exposed to it buys it.
# Without this a developer would read "steel is up: good for your revenue" (feedback 24.10, P0).
COMMODITY_SELLERS: dict[str, tuple[str, ...]] = {
    "steel": ("24", "25", "46.72", "46.77"),
    "nonferrous": ("24", "46.72", "46.77"),
    "iron_ore": ("07", "46.72"),
    "gold": ("07.29", "24.41", "46.72"),
    "cement": ("23", "46.73"),
    "construction_materials": ("23", "46.73"),
    "timber": ("02", "16", "46.73"),
    "fertilizers": ("20", "46.75"),
    "chemicals": ("20", "46.75"),
    "grain": ("01", "46.21"),
    "food": ("01", "03", "10", "46.3"),
    "fish": ("03", "10.2", "46.38"),
    "sugar": ("10.81", "46.36"),
    "fuel": ("06", "19", "46.71", "47.3"),
    "oil": ("06", "09", "19", "46.71"),
    "gas": ("06", "35.2"),
    "coal": ("05", "46.71"),
}


def _okved_match(code: str, prefix: str) -> bool:
    return code == prefix or code.startswith(prefix + ".") or (len(prefix) > 2 and code.startswith(prefix))


def market_side(okved_code: str | None, commodities: list[str]) -> str:
    """'seller' if the company produces or trades one of the commodities, 'buyer' otherwise, 'none' if no commodity."""
    if not commodities:
        return "none"
    code = str(okved_code or "")
    for c in commodities:
        if any(_okved_match(code, p) for p in COMMODITY_SELLERS.get(c, ())):
            return "seller"
    return "buyer"


def _plural(label: str) -> bool:
    return " и " in label


ROLE_ORDER = {
    "cfo": [
        "rate",
        "fx_country",
        "user_provided",
        "counterparty",
        "commodity",
        "industry",
        "demand",
        "group",
    ],
    "treasurer": ["fx_country", "rate", "user_provided", "counterparty", "commodity", "industry", "demand"],
    "chief_accountant": ["rate", "fx_country", "industry", "counterparty", "commodity", "demand", "labor"],
    "ceo": ["group", "industry", "counterparty", "demand", "commodity", "fx_country", "rate", "region"],
    "owner": ["group", "industry", "counterparty", "demand", "commodity", "rate", "fx_country", "region"],
}
FACTOR_KEYWORDS = {
    "monetary_policy": "ключевая ставка инфляция прогноз ЦБ снижение повышение",
    "fx": "курс рубль юань доллар валюта",
    "commodities": "цены сырьё металл сталь нефть золото",
    "sanctions": "санкции ограничения платежи импорт экспорт",
    "macro_stat": "потребление спрос инфляция рост ВВП",
    "labor": "зарплаты рынок труда безработица",
}


@dataclass
class _Writer:
    ev: Evidence
    claims: list[Claim] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)
    risks: list[Consequence] = field(default_factory=list)
    opportunities: list[Consequence] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    quants: list[Quant] = field(default_factory=list)
    positions: list[BankPosition] = field(default_factory=list)
    prefix: str = "c"
    headline: str = ""
    ordered: list[Consequence] = field(default_factory=list)

    def claim(
        self, text: str, kind: str, conf: float, sources: list[str], features: list[str] | None = None
    ) -> str:
        cid = f"{self.prefix}{len(self.claims) + 1}"
        self.claims.append(
            Claim(id=cid, text=text, kind=kind, confidence=conf, sources=sources, features=features or [])
        )
        return cid

    def risk(self, text: str, horizon: str, drivers: list[str], basis: list[str], conf: float = 0.65) -> None:
        cid = self.claim(text, "model_inference", conf, basis)
        self.risks.append(Consequence(text=text, horizon=horizon, drivers=drivers, claims=[cid]))
        self.ordered.append(self.risks[-1])

    def opp(self, text: str, horizon: str, drivers: list[str], basis: list[str], conf: float = 0.6) -> None:
        cid = self.claim(text, "model_inference", conf, basis)
        self.opportunities.append(Consequence(text=text, horizon=horizon, drivers=drivers, claims=[cid]))
        self.ordered.append(self.opportunities[-1])

    def head(self, text: str) -> None:
        if not self.headline:
            self.headline = text

    def scenario(
        self, name: str, logic: str, plausibility: str, signals: list[str], basis: list[str]
    ) -> None:
        cid = self.claim(logic, "hypothesis", 0.3 if plausibility == "low" else 0.5, basis)
        self.scenarios.append(
            Scenario(name=name, logic=logic, plausibility=plausibility, signals=signals, claims=[cid])
        )

    def action(self, type_: str, text: str, product_id: str | None = None, source: str | None = None) -> None:
        pid = product_id if product_id and product_id in self.ev.product_ids else None
        if any(a.text == text for a in self.actions):
            return
        self.actions.append(Action(type=type_, text=text, product_id=pid, source=source))


def _event_text(ev: Evidence) -> str:
    return " ".join(
        [str(ev.event.get("title") or ""), str(ev.event.get("summary") or "")] + [n.text for n in ev.news]
    )


def _tag_label(tags: list[str]) -> str:
    return ", ".join(TAG_LABELS.get(t, t) for t in tags)


def _commodity_label(items: list[str]) -> str:
    return ", ".join(COMMODITY_LABELS.get(c, c) for c in items)


def _demand_signal(ev: Evidence) -> bool:
    """Large purchases / interventions on the company's market: support for prices, not a cost."""
    text = _event_text(ev).lower()
    return any(k in text for k in ("госзакуп", "закупки зерна", "интервенц", "рост спроса"))


def _affected_counterparties(ev: Evidence) -> list[dict[str, Any]]:
    """Known counterparties whose industry the event touches (second-order effect)."""
    if not ev.counterparties:
        return []
    tags = set((ev.event.get("tags") or {}).get("industry_tags") or []) - GENERIC_TAGS
    out = []
    for cp in ev.counterparties.get("items") or []:
        if not cp.get("known") or cp.get("same_group"):
            continue
        if tags & set(cp.get("industry_tags") or []) and float(cp.get("share") or 0) >= 0.01:
            out.append(cp)
    return sorted(out, key=lambda c: -float(c.get("share") or 0))[:3]


def _company_reasons(w: _Writer, news_refs: list[str]) -> None:
    ev = w.ev
    feats = ev.features
    order = ROLE_ORDER.get(ev.role, list(ev.exposure.items))
    mult = ROLE_MULTIPLIERS.get(ev.role, {})
    items = sorted(
        ev.exposure.ordered(),
        key=lambda e: (
            -round(e.weight * mult.get(e.name, 1.0), 2),
            order.index(e.name) if e.name in order else 50,
        ),
    )
    okved_name = fv(feats, "okved.name") or "основной вид деятельности"
    ev_tags = ev.event.get("tags") or {}
    for e in items:
        text = e.reason
        source = "sc"
        if e.name == "industry":
            overlap = sorted(
                (set(ev_tags.get("industry_tags") or []) & set(fv(feats, "industry_tags", []) or []))
                - GENERIC_TAGS
            )
            text = f"Событие касается отрасли компании: {_tag_label(overlap) or okved_name} (ОКВЭД: {clip(okved_name, 120)})."
        elif e.name == "counterparty" and "sk" in ev.all_sources():
            source = "sk"
        cid = w.claim(text, "fact", 0.95 if e.name != "counterparty" else 0.9, [source], e.features)
        mechanism = _mechanism(e.name, ev)
        reason_text = f"{text} {mechanism}".strip()
        w.reasons.append(
            Reason(text=clip(reason_text, 600), features=e.features, claims=[cid], factor=e.name)
        )
    # second-order: named counterparties in the affected industry
    affected = _affected_counterparties(ev)
    if affected:
        parts = []
        hyp_ids = []
        for cp in affected:
            label = "получатель платежей" if cp.get("direction") == "out" else "плательщик"
            parts.append(
                f"{cp['counterparty_id']} ({clip(str(cp.get('okved_name') or 'отрасль не указана'), 60)}, {label}, {fmt_pct(float(cp['share']))} оборота)"
            )
            hyp = cp.get("relation_hypothesis") or {}
            if hyp.get("label"):
                rel = {
                    "likely_supplier": "ключевой поставщик",
                    "likely_customer": "ключевой покупатель",
                    "key_supplier": "ключевой поставщик",
                    "key_customer": "ключевой покупатель",
                }.get(str(hyp["label"]), str(hyp["label"]))
                hyp_ids.append(
                    w.claim(
                        f"По структуре платежей {cp['counterparty_id']}, вероятно, {rel} компании (гипотеза, уверенность {float(hyp.get('confidence') or 0.5):.1f}).",
                        "hypothesis",
                        min(0.6, float(hyp.get("confidence") or 0.5)),
                        ["sk"],
                        [
                            "counterparties.top_out"
                            if cp.get("direction") == "out"
                            else "counterparties.top_in"
                        ],
                    )
                )
        feature = (
            "counterparties.top_out" if affected[0].get("direction") == "out" else "counterparties.top_in"
        )
        cid = w.claim(
            "Контрагенты компании из затронутой отрасли: " + "; ".join(parts) + ".",
            "fact",
            0.9,
            ["sk"],
            [feature],
        )
        if not any(r.factor == "counterparty" for r in w.reasons):
            w.reasons.append(
                Reason(
                    text=clip(
                        "Событие затрагивает отрасль ваших контрагентов: "
                        + "; ".join(parts)
                        + ". Изменения у них отразятся на сроках оплаты, объёмах и ценах сделок.",
                        600,
                    ),
                    features=[feature],
                    claims=[cid, *hyp_ids],
                    factor="counterparty",
                )
            )
        else:
            for r in w.reasons:
                if r.factor == "counterparty":
                    r.claims += [cid, *hyp_ids]
                    if feature not in r.features:
                        r.features.append(feature)
    # group context
    g = ev.group
    if g and g.get("size", 0) > 1 and ev.role in {"owner", "ceo"}:
        tags = set(ev_tags.get("industry_tags") or []) - GENERIC_TAGS
        members = [m for m in g.get("members") or [] if m.get("company_id") != ev.company_id]
        raw_ind = g.get("industries")
        industries: dict[str, Any] = raw_ind if isinstance(raw_ind, dict) else {}
        own_section = str(fv(ev.features, "okved.section", "") or "")
        same_industry = (
            int(industries.get(own_section, 0) or 0) >= 2
            or int(industries.get(str(fv(ev.features, "okved.code", ""))[:2], 0) or 0) >= 2
        )
        # only when the event hits the group itself or an industry several group members share
        # (otherwise "the company is in a group" was boilerplate on every card)
        if (
            tags
            and members
            and ("group" in ev.exposure.items or ("industry" in ev.exposure.items and same_industry))
        ):
            cid = w.claim(
                f"Компания входит в группу из {g.get('size')} юрлиц (сегмент {g.get('segment')}); эффект события стоит оценивать на уровне группы.",
                "fact",
                0.95,
                ["sg"] if "sg" in ev.all_sources() else ["sc"],
                ["group.size"] if "group.size" in ev.features else ["group.id"],
            )
            if len(w.reasons) < 6 and not any(r.factor == "group" for r in w.reasons):
                w.reasons.append(
                    Reason(
                        text="Компания входит в группу: последствия события для других юрлиц группы (общие контрагенты, внутригрупповое финансирование) влияют и на неё.",
                        features=["group.size"] if "group.size" in ev.features else ["group.id"],
                        claims=[cid],
                        factor="group_context",
                    )
                )


def _mechanism(factor: str, ev: Evidence) -> str:
    etype = str(ev.event.get("event_type") or "other")
    direction = str(ev.event.get("direction") or "none")
    if factor == "rate":
        if direction == "down":
            return "Снижение ставки постепенно удешевит плавающий и новый долг."
        if direction == "up":
            return "Рост ставки быстро переходит в стоимость плавающего и нового долга."
        return "Решение по ставке определяет стоимость обслуживания и рефинансирования долга."
    if factor == "fx_country":
        return "Изменения в расчётах и курсе по этим направлениям сразу отражаются на стоимости контрактов и сроках платежей."
    if factor == "counterparty":
        return "Изменения у контрагентов этой отрасли отразятся на ваших поставках, оплатах и ценах."
    if factor == "commodity":
        return "Цена сырья напрямую входит в себестоимость или выручку компании."
    if factor == "industry":
        return (
            "Отраслевые изменения влияют на спрос, цены и условия работы всей отрасли, включая вас."
            if etype != "company"
            else ""
        )
    if factor == "demand":
        return "Динамика доходов и потребления населения быстро отражается на выручке."
    if factor == "labor":
        return "Ситуация на рынке труда влияет на фонд оплаты труда и найм."
    if factor == "region":
        return "Региональные решения затрагивают логистику, спрос и регуляторную среду компании."
    return ""


def _consequences(w: _Writer, news_refs: list[str]) -> str:
    ev = w.ev
    e = ev.event
    t = str(e.get("event_type") or "other")
    direction = str(e.get("direction") or "none")
    up, down = direction == "up", direction == "down"
    products = set(products_of(ev.features))
    names = set(ev.exposure.items)
    has_debt = bool(products & {"loan", "leasing", "factoring"}) or bool(
        fv(ev.features, "sensitivities.rate_sensitive")
    )
    tags = e.get("tags") or {}
    comm = sorted(
        set(tags.get("commodities") or [])
        & {str(c) for c in fv(ev.features, "sensitivities.commodity_exposure", []) or []}
    )
    ev_comm = sorted(set(tags.get("commodities") or []))
    side = market_side(fv(ev.features, "okved.code"), comm or ev_comm)
    # without a commodity in the event the company "sells" what its industry sells
    seller = side == "seller" or (side == "none" and "industry" in names)
    exporter = bool(fv(ev.features, "sensitivities.export_oriented"))
    importer = bool(fv(ev.features, "sensitivities.import_dependent")) or bool(
        fv(ev.features, "fts_countries")
    )
    basis = [*news_refs[:1], "sc"]
    analytics_ref = [a.sid for a in ev.analytics[:1] if not is_demo_material(a.hit)]
    fx_link = ev.exposure.items.get("fx_country")
    strongest = ev.exposure.strongest
    if t == "sanctions" and (fx_link is None or (strongest is not None and strongest.name != "fx_country")):
        # the story reaches the company through fuel, its industry or counterparties, not through its payments
        t = "other"

    if t == "monetary_policy":
        debt_label = (
            " и ".join(PRODUCT_LABELS[p] for p in ("loan", "leasing", "factoring") if p in products)
            or "заёмное финансирование"
        )
        if down:
            if has_debt:
                w.head(f"окно для рефинансирования ({debt_label})")
                w.opp(
                    f"Снижение ставки удешевит рефинансирование и новые транши ({debt_label}); эффект для плавающих ставок — уже со следующего процентного периода.",
                    "quarters",
                    ["следующие решения ЦБ", "условия действующих договоров"],
                    basis,
                )
                w.action(
                    "contact_manager",
                    "Обсудить с менеджером рефинансирование или пересмотр ставки по действующему долгу.",
                    "loan_refinancing",
                )
            if "deposit" in products:
                w.head("доходность депозитов компании снизится")
                w.risk(
                    "Доходность новых депозитов снизится вслед за ставкой — стоит пересмотреть сроки размещения.",
                    "months",
                    ["ставки по депозитам"],
                    basis,
                )
            if not has_debt:
                w.opp(
                    "Удешевление кредита поддержит спрос клиентов компании, зависящих от заёмных денег.",
                    "quarters",
                    ["ставки по кредитам", "инвестиционная активность"],
                    basis,
                    0.5,
                )
        else:
            if has_debt:
                verb = "не подешевеют" if _plural(debt_label) else "не подешевеет"
                w.head(f"{debt_label} компании {verb} в ближайшие кварталы")
                w.risk(
                    f"Стоимость обслуживания долга ({debt_label}) останется высокой ближайшие 1–2 квартала; рефинансирование под более низкую ставку откладывается.",
                    "quarters",
                    ["следующее заседание ЦБ", "динамика инфляции", "бюджетная политика"],
                    basis + analytics_ref,
                )
                w.action(
                    "analyze",
                    "Пересчитать бюджет процентных расходов на ближайшие 2 квартала при текущей ставке.",
                )
                w.action(
                    "contact_manager",
                    "Обсудить с менеджером фиксацию ставки или изменение графика платежей.",
                    "loan_refinancing",
                )
            if "deposit" in products or not has_debt:
                w.head("свободные средства компании сохраняют высокую доходность")
                w.opp(
                    "Высокие ставки сохраняют доходность размещения свободной ликвидности на короткие сроки.",
                    "months",
                    ["ставки по депозитам", "потребность в оборотном капитале"],
                    basis,
                )
                w.action("contact_manager", "Уточнить условия размещения свободных средств.", "deposit")
            if fv(ev.features, "sensitivities.rate_sensitive_industry"):
                w.risk(
                    "Дорогие деньги сдерживают спрос и инвестиции в отрасли компании, зависящей от кредита.",
                    "quarters",
                    ["ставки для конечных покупателей", "ипотека и проектное финансирование"],
                    basis,
                    0.55,
                )
        w.scenario(
            "Ставка сохраняется до конца года",
            "Регулятор держит паузу из-за инфляционных рисков; условия финансирования остаются жёсткими, процентные расходы не снижаются.",
            "medium",
            ["инфляционные ожидания", "риторика ЦБ"],
            basis + analytics_ref,
        )
        w.scenario(
            "Возобновление снижения",
            "При замедлении устойчивой инфляции регулятор возвращается к снижению ставки, кредит дешевеет в течение 1–2 кварталов.",
            "low",
            ["замедление базовой инфляции", "смягчение риторики"],
            basis + analytics_ref,
        )
        w.action("monitor", "Отслеживать решения Банка России и прогноз банка по ставке.")
        if up or direction == "hold":
            return "risk" if has_debt else "mixed"
        return "opportunity" if has_debt else "mixed"

    if t == "fx":
        if up:
            if exporter:
                w.head("рублёвая выручка от экспорта вырастет")
                w.opp(
                    "Ослабление рубля увеличит рублёвую выручку от экспорта.",
                    "weeks",
                    ["курс валюты", "экспортные цены"],
                    basis,
                )
            if importer or "fx_country" in names:
                w.head("импорт и валютные платежи компании подорожают")
                w.risk(
                    "Ослабление рубля удорожит импортные закупки и платежи в валюте в рублёвом выражении.",
                    "weeks",
                    ["курс валюты расчётов", "условия оплаты с поставщиками"],
                    basis,
                )
        elif down:
            if exporter:
                w.head("рублёвая выручка от экспорта снизится")
                w.risk(
                    "Укрепление рубля снизит рублёвую выручку от экспорта.", "weeks", ["курс валюты"], basis
                )
            if importer or "fx_country" in names:
                w.head("импорт для компании дешевеет — окно для закупок")
                w.opp(
                    "Укрепление рубля снизит рублёвую стоимость импорта — окно для закупок и предоплат.",
                    "weeks",
                    ["курс валюты расчётов"],
                    basis,
                )
        if not w.risks and not w.opportunities:
            w.risk(
                "Волатильность курса повышает неопределённость платежей и маржи по валютным контрактам.",
                "weeks",
                ["курс валюты"],
                basis,
            )
        w.scenario(
            "Курс остаётся волатильным",
            "Колебания курса сохраняются; без хеджирования маржа по валютным контрактам становится непредсказуемой.",
            "medium",
            ["цены на нефть", "платёжный баланс", "решения по ставке"],
            basis,
        )
        w.action("contact_manager", "Обсудить инструменты управления валютным риском.", "fx_hedging")
        w.action("analyze", "Оценить долю закупок и выручки в валюте на ближайший квартал.")
        return "mixed" if w.risks and w.opportunities else ("risk" if w.risks else "opportunity")

    if t == "sanctions":
        w.head("расчёты компании по ВЭД могут задерживаться")
        w.risk(
            "Расчёты с контрагентами в затронутых странах могут задерживаться или потребовать смены маршрута платежа.",
            "weeks",
            ["практика банков-корреспондентов", "новые ограничения"],
            basis,
        )
        w.risk(
            "Задержки платежей сдвинут поставки и потребуют дополнительного оборотного капитала.",
            "months",
            ["сроки поставок", "условия оплаты"],
            basis,
        )
        w.scenario(
            "Ужесточение комплаенса",
            "Банки в затронутых странах расширяют проверки; сроки расчётов растут, часть платежей возвращается.",
            "medium",
            ["новые отказы в проведении платежей"],
            basis,
        )
        w.action(
            "contact_manager",
            "Проконсультироваться по маршрутам расчётов по внешнеторговым контрактам.",
            "ved_support",
        )
        w.action("analyze", "Проверить, какие платежи на ближайший месяц идут через затронутые страны.")
        return "risk"

    comm_label = _commodity_label(comm) or "продукцию отрасли"
    fts_overlap = sorted(
        {str(c).upper() for c in tags.get("countries") or []}
        & {str(c).upper() for c in fv(ev.features, "fts_countries", []) or []}
    )
    if (
        t == "regulation_tax"
        and down
        and (comm or seller)
        and (
            "export" in str(e.get("summary", "")).lower()
            or "экспорт" in str(e.get("summary", "")).lower() + str(e.get("title", "")).lower()
        )
    ):
        w.head("экспорт компании становится выгоднее")
        w.opp(
            f"Снижение экспортной нагрузки на {comm_label} повышает выручку компании с каждой тонны экспорта и конкурентоспособность поставок.",
            "months",
            ["вступление решения в силу", "мировые цены", "курс рубля"],
            basis,
        )
        if fts_overlap:
            w.opp(
                f"Рост закупок со стороны {', '.join(c.title() for c in fts_overlap)} — ваших направлений ВЭД — поддержит спрос на экспорт компании.",
                "months",
                ["объёмы закупок импортёров", "условия оплаты"],
                [*basis, "sc"],
            )
        w.risk(
            "Рост экспорта может обострить конкуренцию за логистику (порты, вагоны) и поднять ставки перевозки.",
            "weeks",
            ["загрузка портов", "ставки фрахта"],
            basis,
            0.5,
        )
        w.action("analyze", "Пересчитать экономику экспортных контрактов с учётом новой пошлины.")
        w.action(
            "contact_manager",
            "Обсудить финансирование экспортных поставок и валютные расчёты.",
            "ved_support",
        )
    elif t == "regulation_tax":
        if down:
            w.head("нагрузка на компанию снижается")
            w.opp(
                "Снижение нагрузки улучшит экономику операций компании в затронутом сегменте.",
                "months",
                ["вступление решения в силу", "реакция цен"],
                basis,
            )
        else:
            w.head("новые затраты для компании")
            w.risk(
                "Новые требования или платежи увеличат затраты компании в затронутом сегменте.",
                "months",
                ["сроки вступления в силу", "возможность переложить рост затрат в цены"],
                basis,
            )
        w.action("analyze", "Оценить влияние изменения на себестоимость, цены и налоговую нагрузку.")
    elif t == "trade_logistics" and comm and down:
        if seller:
            w.head("дешёвый импорт давит на цены и маржу компании")
            w.risk(
                f"Снижение цен на импортные {comm_label} потянет вниз внутренние цены: маржа по текущим запасам сократится.",
                "weeks",
                ["импортные цены", "курс юаня", "пошлины"],
                basis,
            )
            w.opp(
                "Если компания сама закупает импорт, это окно для закупки по более низкой цене.",
                "weeks",
                ["условия поставщиков", "курс"],
                basis,
                0.5,
            )
        else:
            w.head("закупки компании могут подешеветь")
            w.opp(
                f"Снижение цен на импортные {comm_label} уменьшит затраты на закупки компании.",
                "months",
                ["импортные цены", "курс", "логистика"],
                basis,
            )
        w.action("analyze", "Сравнить текущие закупочные цены с импортными предложениями.")
    elif t == "trade_logistics" and ev_comm and not comm and not {"fuel", "oil"} & set(ev_comm):
        # a foreign price story about goods the company neither sells nor buys: it matters through flows
        own = set(fv(ev.features, "industry_tags", []) or [])
        ev_label = _commodity_label(ev_comm) or "товары"
        if {"logistics", "trucking", "shipping", "rail", "transport"} & own:
            w.head("объём перевозок импорта для компании может измениться")
            w.opp(
                f"Изменение цен на импортные {ev_label} меняет объём ввоза: у перевозчика растёт или падает грузопоток клиентов.",
                "months",
                ["объём импорта", "ставки перевозки", "загрузка маршрутов"],
                basis,
                0.5,
            )
        else:
            w.head("заказы и оплаты контрагентов компании могут измениться")
            w.risk(
                f"Изменение цен на импортные {ev_label} отразится на выручке и закупках контрагентов компании.",
                "months",
                ["финансовое положение контрагентов", "импортные цены"],
                basis + (["sk"] if "sk" in ev.all_sources() else []),
                0.5,
            )
        w.action("monitor", "Следить за объёмами и оплатами клиентов из затронутой отрасли.")
    elif t == "trade_logistics":
        text = _event_text(ev).lower()
        fuel = bool({"fuel", "oil"} & set(ev_comm)) or "топлив" in text or "дизел" in text
        delays = any(k in text for k in ("задерж", "очеред", "контрол", "границ", "пропуск", "перегруж"))
        if fuel and up:
            w.head("логистика компании подорожает")
            w.risk(
                "Рост цен на топливо увеличит тарифы перевозчиков и транспортные расходы компании.",
                "weeks",
                ["цены на дизель", "тарифы перевозчиков"],
                basis,
            )
        elif "водител" in text:
            w.head("перевозки для компании подорожают")
            w.risk(
                "Дефицит водителей поднимает ставки перевозок и увеличивает риск срыва сроков доставки.",
                "months",
                ["ставки перевозчиков", "доступность транспорта"],
                basis,
            )
        else:
            w.head(
                "поставки компании замедлятся и подорожают"
                if delays
                else "логистика компании может подорожать"
            )
            w.risk(
                "Задержки и рост ставок перевозки увеличат сроки поставок и затраты на логистику."
                if delays
                else "Изменения в логистике увеличат затраты на доставку и сроки поставок.",
                "weeks",
                ["пропускная способность маршрутов", "ставки фрахта"],
                basis,
            )
        if {"logistics", "trucking", "shipping", "rail"} & set(fv(ev.features, "industry_tags", []) or []):
            w.opp(
                "Спрос на альтернативные маршруты и перевозки может вырасти.",
                "months",
                ["перераспределение грузопотоков"],
                basis,
            )
        w.action(
            "contact_manager",
            "Обсудить финансирование увеличенных запасов и сроков оборота.",
            "loan_working_capital",
        )
    elif t == "labor":
        w.head("расходы компании на персонал вырастут")
        w.risk(
            "Рост зарплат и дефицит персонала увеличат операционные расходы.",
            "quarters",
            ["ситуация на рынке труда", "индексация зарплат"],
            basis,
        )
    elif "ипотек" in _event_text(ev).lower() and {"construction", "developer", "real_estate"} & set(
        fv(ev.features, "industry_tags", []) or []
    ):
        w.head("продажи жилья и денежный поток компании под давлением")
        w.risk(
            "Ужесточение ипотеки снизит спрос на жильё: продажи и поступления на эскроу замедлятся, а при проектном финансировании растёт срок и стоимость долга.",
            "quarters",
            ["темп продаж", "ставки по ипотеке", "условия проектного финансирования"],
            basis + analytics_ref,
        )
        if has_debt:
            w.risk(
                "Если продажи отстанут от графика, нагрузка по кредитам компании вырастет относительно выручки.",
                "quarters",
                ["покрытие долга выручкой", "ковенанты"],
                basis,
                0.55,
            )
        w.opp(
            "Компании с запасом ликвидности смогут предложить покупателям собственные программы рассрочки и забрать долю рынка.",
            "months",
            ["ликвидность", "действия конкурентов"],
            basis,
            0.45,
        )
        w.action(
            "analyze",
            "Пересчитать график продаж и потребность в финансировании по проектам при снижении темпа продаж.",
        )
        w.action(
            "contact_manager",
            "Обсудить с менеджером условия проектного финансирования и запас ликвидности.",
            "loan_working_capital",
        )
    elif t == "industry" and seller:
        if down:
            w.head("спрос и цены в отрасли компании снижаются")
            w.risk(
                "Ухудшение условий в отрасли может снизить спрос и цены на продукцию компании.",
                "quarters",
                ["спрос клиентов", "действия конкурентов"],
                basis,
            )
        elif up or _demand_signal(ev):
            w.head(
                "условия в отрасли компании улучшаются"
                if up
                else f"дополнительный спрос поддержит цены на {comm_label}"
            )
            w.opp(
                "Улучшение условий в отрасли поддержит спрос и цены на продукцию компании.",
                "quarters",
                ["спрос клиентов"],
                basis,
            )
        else:
            w.risk(
                "Изменения в отрасли могут повлиять на спрос и условия работы компании.",
                "months",
                ["развитие ситуации в отрасли"],
                basis,
                0.5,
            )
        w.action("analyze", "Оценить влияние на план продаж и цены на ближайший квартал.")
    elif "demand" in names and not comm:
        if up:
            w.head("спрос на продукцию компании растёт")
            w.opp(
                "Рост потребительского спроса поддержит выручку компании.",
                "quarters",
                ["реальные доходы населения"],
                basis,
            )
        else:
            w.head("спрос на продукцию компании может замедлиться")
            w.risk(
                "Замедление потребительского спроса может снизить выручку и оборачиваемость запасов.",
                "quarters",
                ["реальные доходы населения", "инфляция", "ставки по потребкредитам"],
                basis,
            )
            w.action(
                "analyze",
                "Пересмотреть план продаж, закупок и запасов на квартал с учётом замедления спроса.",
            )
    elif not comm and not seller:
        # the company is reached through counterparties or a factor, not through a commodity it trades
        if "counterparty" in names:
            w.head("заказы и оплаты контрагентов компании могут измениться")
            w.risk(
                "Ухудшение условий в отрасли контрагентов может снизить объём их заказов и замедлить оплаты."
                if not up
                else "Изменения в отрасли контрагентов повлияют на объёмы заказов и условия расчётов.",
                "months",
                ["финансовое положение контрагентов", "спрос в их отрасли"],
                basis + (["sk"] if "sk" in ev.all_sources() else []),
            )
        else:
            w.risk(
                "Изменения могут повлиять на условия работы компании.",
                "months",
                ["развитие ситуации"],
                basis,
                0.5,
            )
        w.action("analyze", "Проверить зависимость выручки и закупок от затронутых контрагентов.")
    else:
        if up:
            if seller:
                w.head("рост цен поддержит выручку и маржу компании")
                w.opp(
                    f"Рост цен на {comm_label} поддержит выручку и маржу по текущим запасам.",
                    "weeks",
                    ["устойчивость спроса", "цены конкурентов"],
                    basis,
                )
                w.risk(
                    "Рост цен может снизить спрос покупателей и потребовать больше оборотного капитала.",
                    "months",
                    ["спрос покупателей", "сроки оплаты"],
                    basis,
                )
            else:
                w.head("закупки компании подорожают")
                w.risk(
                    f"Рост цен на {comm_label} увеличит себестоимость и затраты на закупки.",
                    "months",
                    ["динамика цен", "возможность пересмотра цен в контрактах"],
                    basis,
                )
        elif down:
            if seller:
                w.head("давление на выручку и запасы компании")
                w.risk(
                    f"Снижение цен на {comm_label} давит на выручку и стоимость запасов.",
                    "weeks",
                    ["динамика цен", "объём запасов"],
                    basis,
                )
            else:
                w.head("закупки компании подешевеют")
                w.opp(
                    f"Снижение цен на {comm_label} уменьшит затраты на закупки.",
                    "months",
                    ["динамика цен"],
                    basis,
                )
        elif seller and _demand_signal(ev):
            w.head(f"дополнительный спрос поддержит цены на {comm_label}")
            w.opp(
                f"Крупные закупки на рынке поддерживают цены на {comm_label}: выручка и маржа компании выигрывают.",
                "months",
                ["цены на бирже", "объём закупок"],
                basis,
                0.55,
            )
        else:
            w.risk(
                "Изменения в отрасли могут повлиять на спрос и условия работы компании.",
                "months",
                ["развитие ситуации в отрасли"],
                basis,
                0.5,
            )
        w.action("analyze", "Пересчитать закупочный план и цены на ближайший квартал.")
    if "counterparty" in names or _affected_counterparties(ev):
        w.risk(
            "Изменения у значимых контрагентов могут отразиться на сроках платежей и объёмах заказов.",
            "months",
            ["финансовое положение контрагентов"],
            basis + (["sk"] if "sk" in ev.all_sources() else []),
        )
        w.action(
            "contact_manager",
            "Обсудить инструменты снижения рисков расчётов с ключевыми контрагентами.",
            "trade_finance",
        )
    w.scenario(
        "Тенденция сохраняется",
        "Если изменение закрепится, эффект для компании будет нарастать в течение нескольких месяцев.",
        "medium",
        ["повторные сообщения о событии", "динамика цен и спроса"],
        basis,
    )
    w.action("monitor", "Отслеживать развитие события и реакцию контрагентов.")
    if w.risks and w.opportunities:
        return "mixed"
    if w.risks:
        return "risk"
    return "opportunity" if w.opportunities else "neutral"


def _bank_view(w: _Writer) -> None:
    ev = w.ev
    etype = str(ev.event.get("event_type") or "other")
    query = f"{ev.event.get('title', '')} {FACTOR_KEYWORDS.get(etype, '')}"
    topic = stems(f"{ev.event.get('title', '')} {ev.event.get('summary', '')}") | stems(
        FACTOR_KEYWORDS.get(etype, "")
    )
    title_stems = (
        stems(str(ev.event.get("title") or "")) | stems(FACTOR_KEYWORDS.get(etype, ""))
    ) - _GENERIC_STEMS
    for a in ev.analytics[:4]:
        text = " ".join(
            s
            for s in sentences(str(a.hit.get("text") or ""))
            if "ДЕМО (синтетика)" not in s and not _ocr_noise(s)
        )
        best = [b for b in best_sentences(text, query, n=2) if len(stems(b) & title_stems) >= 2]
        if not best:
            continue
        # the RAG always returns top-k: keep a chunk only if it speaks about the event's topic (3+ shared stems)
        if len(stems(" ".join(best)) & topic) < 3:
            continue
        # a verbatim quote must be one contiguous sentence of the chunk (two picked sentences may be apart)
        quote = best[0] if len(best[0]) <= 400 else None  # clipping would add "…" and break verbatim match
        label = "Демо-материал (синтетика)" if is_demo_material(a.hit) else "Аналитики Альфа-Банка"
        doc = clip(str(a.hit.get("doc_title") or ""), 120)
        age = _age_days(a.hit.get("doc_date"), ev.event.get("first_seen_at"))
        if age is not None and age > 365:
            doc += f", материал от {str(a.hit.get('doc_date'))[:10]} — может быть неактуален"
        w.positions.append(
            BankPosition(
                source=a.sid,
                text=f"{label} («{doc}», с. {a.hit.get('page')}): {clip(' '.join(best), 380)}",
                quote=quote,
            )
        )
        w.claim(f"{label}: {clip(' '.join(best), 300)}", "bank_analytics", 0.9, [a.sid])
        if len(w.positions) >= 2:
            break


# stems too common in business news to tie a bank material to an event on their own
_GENERIC_STEMS = frozenset(
    {"росси", "росс", "рынок", "рынка", "рынке", "цены", "цена", "цен", "рост", "выросл", "вырос", "снизи",
     "компа", "банк", "банка", "году", "года", "прогн", "уровн", "конца", "начал"}
)  # fmt: skip


def _age_days(doc_date: Any, event_date: Any) -> int | None:
    try:
        d = date.fromisoformat(str(doc_date)[:10])
        e = datetime.fromisoformat(str(event_date)).date() if event_date else date.today()
    except ValueError:
        return None
    return (e - d).days


def _ocr_noise(sentence: str) -> bool:
    """OCR of tables/charts gives runs of numbers and 1-2 letter fragments, not a statement."""
    tokens = sentence.split()
    if len(tokens) < 5:
        return True
    junk = sum(1 for t in tokens if not any(ch.isalpha() for ch in t))
    caps = sum(1 for t in tokens if t.isupper() and 1 < len(t) <= 4)  # table headers: "TOT", "YoY"
    return (junk + caps) / len(tokens) > 0.4


def _quant(w: _Writer) -> None:
    ev = w.ev
    e = ev.event
    names = ev.exposure.items
    if str(e.get("event_type")) == "monetary_policy" and "rate" in names:
        delta = rate_change_pp(_event_text(ev))
        if delta:
            refs = [f for f in names["rate"].features if f.startswith("products.")] or names["rate"].features
            w.quants.append(
                Quant(
                    metric="interest_cost_delta_per_100m",
                    value=round(delta * 1_000_000),
                    unit="₽/год на 100 млн ₽ плавающего долга",
                    basis=f"Изменение ставки на {delta:g} п.п. × 100 млн ₽. Сумма долга компании банку в данных отсутствует, поэтому оценка нормирована на 100 млн ₽ плавающего долга.",
                    period="12 мес.",
                    features=refs,
                )
            )
        rate = next(
            (
                n.get("value")
                for n in (e.get("tags") or {}).get("numbers") or []
                if isinstance(n, dict) and n.get("kind") == "key_rate"
            ),
            None,
        )
        if rate is not None:
            w.quants.append(
                Quant(
                    metric="key_rate_level",
                    value=float(rate),
                    unit="% годовых",
                    basis="Уровень ключевой ставки по сообщению о решении Банка России.",
                    period=None,
                    features=[],
                )
            )
    cp = names.get("counterparty")
    if cp:
        path = next((f for f in cp.features if ".industry_share_" in f), None)
        share = fv(ev.features, path) if path else None
        if isinstance(share, int | float):
            side = "входящих (покупатели)" if "_in." in (path or "") else "исходящих (поставщики)"
            w.quants.append(
                Quant(
                    metric="counterparty_share_affected",
                    value=round(float(share) * 100, 1),
                    unit="% оборота",
                    basis=f"Доля {side} платежей без самопереводов, приходящаяся на контрагентов затронутой отрасли.",
                    period="за период данных",
                    features=[path] if path else [],
                )
            )
        for d in ("out", "in"):
            hhi = fv(ev.features, f"counterparties.hhi_{d}")
            if isinstance(hhi, int | float) and hhi > 0:
                w.quants.append(
                    Quant(
                        metric="counterparty_hhi",
                        value=round(float(hhi)),
                        unit="HHI (0–10 000)",
                        basis=f"Концентрация {'поставщиков' if d == 'out' else 'покупателей'}: чем выше, тем сильнее компания зависит от отдельных контрагентов.",
                        period=None,
                        features=[f"counterparties.hhi_{d}"],
                    )
                )
                break
    fx = names.get("fx_country")
    if fx and "fts_countries" in fx.features:
        countries = {str(c).upper() for c in (e.get("tags") or {}).get("countries") or []}
        own = [str(c) for c in fv(ev.features, "fts_countries", []) or [] if str(c).upper() in countries]
        if own:
            w.quants.append(
                Quant(
                    metric="fx_countries_affected",
                    value=len(own),
                    unit="стран ВЭД",
                    basis="Страны ВЭД компании по данным ФТС, которых касается событие: "
                    + ", ".join(own)
                    + " (направление торговли в данных не указано).",
                    period=None,
                    features=["fts_countries"],
                )
            )
    w.quants = w.quants[:4]


def _title_summary(w: _Writer, polarity: str) -> tuple[str, str]:
    ev = w.ev
    e = ev.event
    finance = ev.role in FINANCE_ROLES
    short = clip(str(e.get("title") or "Событие"), 80 if w.headline else 90)
    main = w.ordered
    effect = {
        "risk": "давление на финансы компании" if finance else "риск для бизнеса компании",
        "opportunity": "возможность для компании",
        "mixed": "риски и возможности для компании",
        "neutral": "что важно знать компании",
    }[polarity]
    factor = w.reasons[0].factor if w.reasons else None
    factor_phrase = {
        "rate": "стоимость долга",
        "fx_country": "расчёты по ВЭД",
        "counterparty": "ваши контрагенты",
        "commodity": "сырьё и себестоимость",
        "industry": "ваша отрасль",
        "demand": "спрос на продукцию",
        "user_provided": "данные из вашей анкеты",
        "group": "ваша группа",
        "region": "ваш регион",
        "labor": "фонд оплаты труда",
    }.get(factor or "", "")
    if w.headline:
        title = clip(f"{short}: {w.headline}", 160)
    else:
        title = clip(f"{short}: {factor_phrase} — {effect}" if factor_phrase else f"{short}: {effect}", 160)
    why = w.reasons[0].text if w.reasons else ""
    consequence = main[0].text if main else "Событие стоит отслеживать."
    summary = clip(f"{consequence} {why}" if finance else f"{why} {consequence}", 600)
    return title, summary


def _official_figures(w: _Writer, news_refs: list[str], facts: list[Fact]) -> None:
    """Bank of Russia figures (source sm) and an explicit note when the news quotes a different figure."""
    ev = w.ev
    if not ev.market:
        return
    for off in ev.market:
        text = f"По данным Банка России: {off.phrase()}."
        w.claim(text, "fact", 0.99, ["sm"])
        if len(facts) < 4:
            facts.append(Fact(text=text, sources=["sm"]))
    for c in ev.market_checks:
        if c.get("mismatch") and news_refs:
            vals = ", ".join(f"{v:g}".replace(".", ",") for v in c["mismatched_values"])
            official = f"{c['official']:g}".replace(".", ",")
            w.claim(
                f"В новости приведено значение {vals}, официальное значение Банка России на "
                f"{market.ru_date(c['official_date'])} — {official}: в расчётах опирайтесь на официальные данные.",
                "fact",
                0.95,
                [news_refs[0], "sm"],
            )


def rule_draft(ev: Evidence, prefix: str = "c") -> Draft:
    w = _Writer(ev, prefix=prefix)
    news_refs = [n.sid for n in ev.news[:3]]
    summary_src = (
        str(ev.event.get("summary") or "")
        or (ev.news[0].text if ev.news else "")
        or str(ev.event.get("title") or "")
    )
    key_fact = first_sentences(summary_src, 2, 400) or clip(str(ev.event.get("title") or ""), 300)
    facts = [Fact(text=key_fact, sources=news_refs)] if news_refs else []
    if news_refs:
        w.claim(key_fact, "fact", 0.95, news_refs)
        if len(ev.news) > 1:
            w.claim(f"О событии сообщили {len(ev.news)} источника(ов).", "fact", 0.95, news_refs)
    _official_figures(w, news_refs, facts)
    _company_reasons(w, news_refs)
    polarity = _consequences(w, news_refs)
    _bank_view(w)
    _quant(w)
    if w.positions:
        w.action("read_source", "Прочитать материал аналитики банка по теме.", source=w.positions[0].source)
    if news_refs:
        w.action("read_source", "Открыть первоисточник новости.", source=news_refs[0])
    if ev.role in FINANCE_ROLES:
        w.actions.sort(key=lambda a: {"analyze": 0, "contact_manager": 1}.get(a.type, 2))
    title, summary = _title_summary(w, polarity)
    strongest = ev.exposure.strongest
    return Draft(
        relevant=bool(ev.exposure.items),
        skip_reason=None if ev.exposure.items else "not_relevant",
        title=title,
        summary=summary,
        polarity=polarity,
        event_summary=clip(summary_src, 1500),
        event_type=str(ev.event.get("event_type") or "other"),
        participants=[],
        key_facts=facts,
        claims=w.claims,
        why_it_matters=w.reasons[:6],
        risks=w.risks[:5],
        opportunities=w.opportunities[:5],
        scenarios=w.scenarios[:3],
        bank_positions=w.positions,
        model_disagrees_with_bank=False,
        impact_level=None,
        impact_rationale=f"Главный фактор — {strongest.reason}" if strongest else "",
        quantitative=w.quants,
        actions=w.actions[:6],
        author="rules@1",
    )
