"""Deterministic insight builder (templates + rules) used by ml-stub (ADR-013).

Pure function of: company context (Tools API get_company_context), event (get_event), bank analytics hits,
bank products, role. Every claim is tied to sources and company features, so the result passes the backend
validator; the ML team replaces this with an LLM agent behind the same contract.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.contracts.insight import SCHEMA_VERSION
from app.domain.impact import SCALE_VERSION, impact_score, level_for_score, magnitude
from app.domain.scoring import (
    EventProfile,
    components,
    profile_from_context,
    score_candidate,
)
from app.domain.tagging import strip_injection

PRODUCT_LABELS = {
    "loan": "кредит",
    "leasing": "лизинг",
    "factoring": "факторинг",
    "deposit": "депозит",
    "guarantee": "банковская гарантия",
    "rko": "РКО",
    "payroll": "зарплатный проект",
}
TAG_LABELS = {
    "metals": "металлы и металлопрокат",
    "metallurgy": "металлургия",
    "steel": "сталь",
    "construction": "строительство",
    "developer": "девелопмент",
    "real_estate": "недвижимость",
    "logistics": "логистика",
    "trucking": "автоперевозки",
    "warehousing": "складская логистика",
    "agriculture": "сельское хозяйство",
    "grain_trade": "торговля зерном",
    "food": "продукты питания",
    "retail": "розничная торговля",
    "oil_gas": "нефтегаз",
    "fuel": "топливо",
    "automotive": "автомобили",
    "automotive_parts": "автокомпоненты",
    "machinery": "машиностроение",
    "chemicals": "химия и удобрения",
    "wholesale": "оптовая торговля",
    "electronics": "электроника",
    "it": "ИТ",
    "construction_materials": "стройматериалы",
    "beverages": "напитки",
    "meat": "мясная продукция",
    "grain_processing": "переработка зерна",
    "leasing": "лизинг",
    "rental": "аренда",
    "fish": "рыба",
}
COMMODITY_LABELS = {
    "steel": "сталь и прокат",
    "iron_ore": "железная руда",
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
    "fish": "рыба",
}
FINANCE_ROLES = {"cfo", "treasurer", "chief_accountant"}
DISCLAIMER_TEXT = "Материал носит информационный характер и не является инвестиционной рекомендацией."


def is_demo_material(hit: dict[str, Any]) -> bool:
    return str(hit.get("doc_title", "")).startswith("ДЕМО")


def analytics_publisher(hit: dict[str, Any]) -> str:
    """Synthetic demo PDFs must never be presented as real Alfa-Bank analytics."""
    return "Демо-материал (синтетика), не аналитика Альфа-Банка" if is_demo_material(hit) else "Альфа-Банк"


def analytics_position_label(hit: dict[str, Any]) -> str:
    return "Позиция из демо-материала (синтетика)" if is_demo_material(hit) else "Позиция Альфа-Банка по теме"


def _fv(features: dict[str, Any], path: str, default: Any = None) -> Any:
    f = features.get(path)
    return f.get("value", default) if isinstance(f, dict) else default


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip(" ,.;:") + "…"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]


def event_profile(ev: dict[str, Any]) -> EventProfile:
    tags = ev.get("tags", {})
    return EventProfile(
        event_id=ev["event_id"],
        event_type=ev["event_type"],
        direction=ev.get("direction", "none"),
        industry_tags=frozenset(tags.get("industry_tags", [])),
        countries=frozenset(tags.get("countries", [])),
        commodities=frozenset(tags.get("commodities", [])),
        regions=frozenset(tags.get("regions", [])),
        currencies=frozenset(tags.get("currencies", [])),
        company_ids=frozenset(),
        urgent=bool(ev.get("urgent")),
        first_seen_at=datetime.fromisoformat(ev["first_seen_at"]),
    )


@dataclass
class _Draft:
    statements: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (component, reason)
    risks: list[dict[str, Any]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    quantitative: list[dict[str, Any]] = field(default_factory=list)

    def stmt(
        self, text: str, kind: str, conf: float, sources: list[str], features: list[str] | None = None
    ) -> str:
        sid = f"st{len(self.statements) + 1}"
        self.statements.append(
            {
                "id": sid,
                "text": text,
                "kind": kind,
                "confidence": conf,
                "source_refs": sources,
                "feature_refs": features or [],
            }
        )
        return sid


def _consequence(text: str, horizon: str, drivers: list[str], refs: list[str]) -> dict[str, Any]:
    return {"text": text, "horizon": horizon, "drivers": drivers, "statement_refs": refs}


def build_insight(
    *,
    company_id: str,
    role: str,
    run_id: str,
    context: dict[str, Any],
    event: dict[str, Any],
    analytics_hits: list[dict[str, Any]],
    products: list[dict[str, Any]],
    candidate: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Returns the insight payload, or None when the event is not relevant to the company (skip)."""
    now = now or datetime.now(UTC)
    feats: dict[str, Any] = context["features"]
    profile = profile_from_context(company_id, feats)
    ev = event_profile(event)
    comp = components(profile, ev)
    values = {n: c.value for n, c in comp.items() if c.value > 0 and c.features}
    if not values:
        return None
    product_ids = {p["product_id"] for p in products}
    d = _Draft()

    # --- sources
    sources: list[dict[str, Any]] = []
    news_refs: list[str] = []
    for i, n in enumerate(event.get("news", [])[:5], start=1):
        sid = f"s{i}"
        news_refs.append(sid)
        sources.append(
            {
                "id": sid,
                "type": "news",
                "ref_id": n["news_id"],
                "url": n["url"],
                "title": n["title"],
                "publisher": n.get("publisher"),
                "published_at": n.get("published_at"),
                "page": None,
            }
        )
    if not news_refs:
        return None
    sources.append(
        {
            "id": "sc",
            "type": "company_data",
            "ref_id": "company_context",
            "url": None,
            "title": "Данные о компании в Альфа-Банке",
            "publisher": None,
            "published_at": None,
            "page": None,
        }
    )
    analytics_refs: list[tuple[str, dict[str, Any]]] = []
    for i, h in enumerate(analytics_hits[:2], start=1):
        sid = f"sa{i}"
        analytics_refs.append((sid, h))
        sources.append(
            {
                "id": sid,
                "type": "bank_analytics",
                "ref_id": h["chunk_id"],
                "url": None,
                "title": h["doc_title"],
                "publisher": analytics_publisher(h),
                "published_at": None,
                "page": h["page"],
            }
        )

    # --- facts about the event
    summary_src = strip_injection(event.get("summary") or "") or event["title"]
    key_fact_text = _clip(_first_sentence(summary_src), 400)
    d.stmt(key_fact_text, "fact", 0.95, news_refs)
    if len(news_refs) > 1:
        d.stmt(f"О событии сообщили {len(news_refs)} источника.", "fact", 0.95, news_refs)

    # --- why it matters (one reason per matched component, company facts as statements)
    okved_name = _fv(feats, "okved.name") or "основной вид деятельности"
    products_now = sorted(p for p in profile.products if p in PRODUCT_LABELS)
    if "rate" in values:
        rate_products = [p for p in products_now if p in {"loan", "leasing", "factoring"}]
        if rate_products:
            txt = (
                "У компании есть "
                + ", ".join(PRODUCT_LABELS[p] for p in rate_products)
                + " в Альфа-Банке — стоимость обслуживания долга зависит от ключевой ставки."
            )
        elif "deposit" in products_now:
            txt = "У компании размещены депозиты — их доходность следует за ключевой ставкой."
        else:
            txt = f"Отрасль компании ({okved_name}) чувствительна к стоимости заёмных денег."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], comp["rate"].features)
        d.reasons.append(
            ("rate", {"text": txt, "feature_refs": comp["rate"].features, "statement_refs": [sid]})
        )
    if "fx_country" in values:
        countries = sorted(ev.countries & profile.fts_countries)
        if countries:
            txt = (
                "Компания ведёт внешнеэкономическую деятельность со странами, которых касается событие: "
                + ", ".join(c.title() for c in countries)
                + "."
            )
        else:
            txt = "У компании есть внешнеторговые операции — она чувствительна к курсу и условиям расчётов."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], comp["fx_country"].features)
        d.reasons.append(
            (
                "fx_country",
                {"text": txt, "feature_refs": comp["fx_country"].features, "statement_refs": [sid]},
            )
        )
    if "industry" in values:
        overlap = sorted((ev.industry_tags & profile.industry_tags) - {"trade", "manufacturing"})
        label = ", ".join(TAG_LABELS.get(t, t) for t in overlap) or okved_name
        txt = f"Событие касается отрасли компании: {label}."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], comp["industry"].features)
        d.reasons.append(
            ("industry", {"text": txt, "feature_refs": comp["industry"].features, "statement_refs": [sid]})
        )
    if "counterparty" in values:
        path = comp["counterparty"].features[0]
        share = _fv(feats, path)
        tag = path.rsplit(".", 1)[-1]
        direction = "входящих платежей (покупатели)" if "_in." in path else "исходящих платежей (поставщики)"
        if isinstance(share, int | float):
            txt = (
                f"На контрагентов отрасли «{TAG_LABELS.get(tag, tag)}» приходится {share * 100:.0f}% "
                f"{direction} компании."
            )
            d.quantitative.append(
                {
                    "metric": "counterparty_share_affected",
                    "value": round(share * 100, 1),
                    "unit": "% оборота",
                    "basis": f"Доля {direction} без самопереводов, приходящаяся на контрагентов "
                    "затронутой отрасли.",
                    "period": "с 1 января",
                    "feature_refs": [path],
                }
            )
        else:
            txt = "Событие затрагивает крупных контрагентов компании."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], comp["counterparty"].features)
        d.reasons.append(
            (
                "counterparty",
                {"text": txt, "feature_refs": comp["counterparty"].features, "statement_refs": [sid]},
            )
        )
    if "commodity" in values:
        items = sorted(ev.commodities & profile.commodities)
        txt = (
            "Компания работает с сырьём и товарами, цены на которые затрагивает событие: "
            + ", ".join(COMMODITY_LABELS.get(c, c) for c in items)
            + "."
        )
        sid = d.stmt(txt, "fact", 0.9, ["sc"], comp["commodity"].features)
        d.reasons.append(
            ("commodity", {"text": txt, "feature_refs": comp["commodity"].features, "statement_refs": [sid]})
        )
    if "user_provided" in values:
        txt = "Событие касается сведений, которые вы указали в профиле компании (сырьё или валюты расчётов)."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], comp["user_provided"].features)
        d.reasons.append(
            (
                "user_provided",
                {"text": txt, "feature_refs": comp["user_provided"].features, "statement_refs": [sid]},
            )
        )
    if "demand" in values:
        txt = "Выручка компании зависит от потребительского спроса."
        sid = d.stmt(txt, "fact", 0.9, ["sc"], comp["demand"].features)
        d.reasons.append(
            ("demand", {"text": txt, "feature_refs": comp["demand"].features, "statement_refs": [sid]})
        )
    if "region" in values:
        txt = f"Событие затрагивает регион работы компании: {profile.region}."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], ["region"])
        d.reasons.append(("region", {"text": txt, "feature_refs": ["region"], "statement_refs": [sid]}))
    if "group" in values:
        txt = "Событие напрямую касается компании или её группы."
        sid = d.stmt(txt, "fact", 1.0, ["sc"], ["group.id"])
        d.reasons.append(("group", {"text": txt, "feature_refs": ["group.id"], "statement_refs": [sid]}))
    if not d.reasons:
        return None

    # --- consequences by event type, direction and the company's exposure
    t, direction = ev.event_type, ev.direction
    polarity = _consequences(d, t, direction, values, profile.products, profile, ev, news_refs, product_ids)

    # --- bank view (only real analytics chunks; no invented bank positions)
    positions = []
    for sid, h in analytics_refs:
        quote = _clip(h["text"], 300)
        d.stmt(f"{analytics_publisher(h)}: {quote}", "bank_analytics", 0.9, [sid])
        positions.append(
            {
                "text": f"{analytics_position_label(h)}: {_clip(h['text'], 400)}",
                "quote": quote,
                "analytics_chunk_id": h["chunk_id"],
                "document_id": h["document_id"],
                "page": h["page"],
                "source_ref": sid,
            }
        )
    bank_view = {
        "coverage": "related" if positions else "none",
        "positions": positions,
        "model_disagrees_with_bank": False,
        "disagreement_note": None,
    }

    # --- impact
    numbers = event.get("tags", {}).get("numbers", [])
    pct = max((float(n["value"]) for n in numbers if n.get("kind") == "percent"), default=None)
    magn = magnitude(t, direction, ev.urgent, pct)
    score, strongest = impact_score(values, magn)
    level = level_for_score(score)
    rationale = (
        f"Сила события ({magn:.2f}) × подверженность компании по фактору «{strongest}» "
        f"({values.get(strongest or '', 0):.2f}) × близость влияния; шкала v{SCALE_VERSION}."
    )

    # --- relevance
    if candidate and "score" in candidate:
        relevance = {
            "score": float(candidate["score"]),
            "breakdown": candidate.get("breakdown", {}),
            "matched_features": list(candidate.get("matched_features", [])),
            "scorer": "baseline@1",
        }
    else:
        res = score_candidate(profile, ev, role, now)
        relevance = {
            "score": res.score,
            "breakdown": res.breakdown,
            "matched_features": res.matched_features,
            "scorer": res.scorer,
        }

    # --- role variant: order reasons and wording
    finance = role in FINANCE_ROLES
    order = [
        "rate",
        "fx_country",
        "user_provided",
        "commodity",
        "counterparty",
        "industry",
        "demand",
        "region",
        "group",
    ]
    if not finance:
        order = [
            "group",
            "industry",
            "counterparty",
            "demand",
            "commodity",
            "fx_country",
            "user_provided",
            "rate",
            "region",
        ]
    d.reasons.sort(key=lambda r: order.index(r[0]) if r[0] in order else 99)
    reasons = [r for _, r in d.reasons][:6]
    main_effect = (d.risks or d.opportunities or [{"text": "Событие стоит отслеживать."}])[0]["text"]
    lead = "Финансовый эффект" if finance else "Для бизнеса"
    title_tail = {
        "risk": "риск для компании" if not finance else "давление на финансы компании",
        "opportunity": "возможность для компании",
        "mixed": "риски и возможности для компании",
        "neutral": "что важно знать компании",
    }[polarity]
    title = _clip(f"{_clip(event['title'], 100)}: {title_tail}", 160)
    summary = _clip(f"{_first_sentence(summary_src)} {lead}: {reasons[0]['text']} {main_effect}", 600)

    if finance:
        d.actions.sort(key=lambda a: {"analyze": 0, "contact_manager": 1}.get(a["type"], 2))
    d.actions.append(
        {
            "type": "read_source",
            "text": "Открыть первоисточник новости.",
            "product_id": None,
            "source_ref": news_refs[0],
        }
    )

    disclaimers = [
        {"code": "not_investment_advice", "text": DISCLAIMER_TEXT},
        {
            "code": "model_generated",
            "text": "Выводы с пометкой «вывод модели» сформированы автоматически на основе "
            "доступных данных и не являются официальной позицией Альфа-Банка.",
        },
    ]
    if any(s["kind"] == "hypothesis" for s in d.statements):
        disclaimers.append({"code": "hypothesis_present", "text": "Содержит гипотезы, требующие проверки."})
    completeness = float(context.get("completeness", {}).get("score", 1.0))
    if completeness < 0.8:
        disclaimers.append(
            {
                "code": "incomplete_data",
                "text": "Часть данных о компании неизвестна; оценка может быть неполной.",
            }
        )
    if not positions:
        disclaimers[1]["text"] += (
            " По этому событию нет материалов аналитики Альфа-Банка — позиция банка не приводится."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "insight_id": None,
        "company_id": company_id,
        "event_id": event["event_id"],
        "role_variant": role,
        "trace_id": run_id,
        "language": "ru",
        "title": title,
        "summary": summary,
        "polarity": polarity,
        "urgent": bool(event.get("urgent")),
        "event": {
            "title": _clip(event["title"], 300),
            "summary": _clip(summary_src, 1500),
            "event_type": t,
            "occurred_at": event.get("first_seen_at"),
            "participants": [],
            "key_facts": [{"text": key_fact_text, "source_refs": news_refs}],
        },
        "why_it_matters": reasons,
        "consequences": {"risks": d.risks, "opportunities": d.opportunities, "scenarios": d.scenarios},
        "impact": {
            "level": level.value,
            "score": score,
            "scale_version": SCALE_VERSION,
            "rationale": rationale,
            "quantitative": d.quantitative,
        },
        "actions": d.actions[:6],
        "bank_view": bank_view,
        "statements": d.statements,
        "relevance": relevance,
        "sources": sources,
        "disclaimers": disclaimers,
        "models_used": [
            {"name": "rules@1", "purpose": "template"},
            {"name": "baseline@1", "purpose": "scoring"},
        ],
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=14)).isoformat(),
    }


def _action(product_ids: set[str], type_: str, text: str, product_id: str | None = None) -> dict[str, Any]:
    pid = product_id if product_id in product_ids else None
    return {"type": type_, "text": text, "product_id": pid, "source_ref": None}


def _consequences(
    d: _Draft,
    t: str,
    direction: str,
    values: dict[str, float],
    products: frozenset[str],
    profile: Any,
    ev: EventProfile,
    news_refs: list[str],
    product_ids: set[str],
) -> str:
    """Fill risks/opportunities/scenarios/actions (model inferences and hypotheses). Returns polarity."""
    ref = news_refs[:1]

    def risk(text: str, horizon: str, drivers: list[str]) -> None:
        sid = d.stmt(text, "model_inference", 0.65, ref)
        d.risks.append(_consequence(text, horizon, drivers, [sid]))

    def opp(text: str, horizon: str, drivers: list[str]) -> None:
        sid = d.stmt(text, "model_inference", 0.6, ref)
        d.opportunities.append(_consequence(text, horizon, drivers, [sid]))

    def scenario(name: str, logic: str, plausibility: str, signals: list[str]) -> None:
        sid = d.stmt(logic, "hypothesis", 0.3 if plausibility == "low" else 0.5, ref)
        d.scenarios.append(
            {
                "name": name,
                "logic": logic,
                "plausibility": plausibility,
                "signals": signals,
                "statement_refs": [sid],
            }
        )

    up, down = direction == "up", direction == "down"
    has_debt = bool(products & {"loan", "leasing", "factoring"}) or values.get("rate", 0) >= 1.0
    seller = "industry" in values  # the company is in the event's industry (sells / trades the goods)
    exporter = bool(profile.industry_tags & {"agriculture", "grain_trade"}) or "export" in ev.event_type

    if t == "monetary_policy":
        if down:
            if has_debt:
                opp(
                    "Снижение ставки удешевит новые заимствования и рефинансирование кредита и лизинга.",
                    "quarters",
                    ["график следующих решений ЦБ", "условия по действующим договорам"],
                )
                d.actions.append(
                    _action(
                        product_ids,
                        "contact_manager",
                        "Обсудить с менеджером рефинансирование действующего долга.",
                        "loan_refinancing",
                    )
                )
            if "deposit" in products:
                risk(
                    "Доходность новых депозитов снизится вслед за ставкой.", "months", ["ставки по депозитам"]
                )
        else:
            if has_debt:
                risk(
                    "Стоимость обслуживания кредита и лизинга не снизится в ближайшие кварталы; "
                    "рефинансирование под более низкую ставку откладывается.",
                    "quarters",
                    ["следующее заседание ЦБ", "динамика инфляции", "бюджетная политика"],
                )
                d.actions.append(
                    _action(
                        product_ids,
                        "contact_manager",
                        "Обсудить с менеджером график платежей или фиксацию ставки по кредиту.",
                        "loan_refinancing",
                    )
                )
                d.actions.append(
                    _action(
                        product_ids, "analyze", "Пересчитать бюджет процентных расходов при текущей ставке."
                    )
                )
            if "deposit" in products or not has_debt:
                opp(
                    "Высокие ставки сохраняют доходность размещения свободной ликвидности.",
                    "months",
                    ["ставки по депозитам", "потребность в оборотном капитале"],
                )
                d.actions.append(
                    _action(
                        product_ids,
                        "contact_manager",
                        "Уточнить условия размещения свободных средств.",
                        "deposit",
                    )
                )
            if not has_debt and values.get("rate", 0) < 1.0:
                risk(
                    "Дорогие деньги сдерживают спрос со стороны клиентов отрасли, зависящих от кредита.",
                    "quarters",
                    ["ставки по кредитам для клиентов", "инвестиционная активность"],
                )
        scenario(
            "Ставка сохраняется до конца года",
            "Регулятор продолжает паузу из-за инфляционных рисков; условия финансирования остаются жёсткими.",
            "medium",
            ["инфляционные ожидания", "риторика ЦБ"],
        )
        scenario(
            "Возобновление снижения",
            "При замедлении устойчивой инфляции регулятор может вернуться к снижению "
            "ставки, что удешевит кредит.",
            "low",
            ["замедление базовой инфляции", "смягчение риторики"],
        )
        d.actions.append(
            _action(product_ids, "monitor", "Отслеживать решения Банка России и прогнозы банка по ставке.")
        )
        if up or direction == "hold":
            return "risk" if has_debt else "mixed"
        return "opportunity" if has_debt else "mixed"

    if t == "fx":
        import_side = "fx_country" in values or bool(profile.fts_countries)
        if up:
            if import_side:
                risk(
                    "Рост курса удорожит импортные закупки и расчёты в валюте в рублёвом выражении.",
                    "weeks",
                    ["курс валюты расчётов", "условия оплаты с поставщиками"],
                )
            if exporter:
                opp(
                    "Рост курса увеличит рублёвую выручку от экспорта.",
                    "weeks",
                    ["курс валюты", "экспортные цены"],
                )
        else:
            if import_side:
                opp("Укрепление рубля снизит рублёвую стоимость импорта.", "weeks", ["курс валюты расчётов"])
            if exporter:
                risk("Укрепление рубля снизит рублёвую выручку экспортёров.", "weeks", ["курс валюты"])
        if not d.risks and not d.opportunities:
            risk("Волатильность курса повышает неопределённость расчётов в валюте.", "weeks", ["курс валюты"])
        d.actions.append(
            _action(
                product_ids,
                "contact_manager",
                "Обсудить инструменты управления валютным риском.",
                "fx_hedging",
            )
        )
        d.actions.append(
            _action(product_ids, "analyze", "Оценить долю закупок и выручки в валюте на ближайший квартал.")
        )
        return "mixed" if d.risks and d.opportunities else ("risk" if d.risks else "opportunity")

    if t == "sanctions":
        risk(
            "Расчёты с контрагентами в затронутых странах могут задерживаться или требовать смены "
            "маршрута платежа.",
            "weeks",
            ["практика банков-корреспондентов", "новые ограничения"],
        )
        risk(
            "Задержки платежей могут сдвинуть поставки и потребовать дополнительного оборотного капитала.",
            "months",
            ["сроки поставок", "условия оплаты"],
        )
        scenario(
            "Ужесточение комплаенса",
            "Банки в затронутых странах продолжат расширять проверки; сроки расчётов вырастут.",
            "medium",
            ["новые отказы в проведении платежей"],
        )
        d.actions.append(
            _action(
                product_ids,
                "contact_manager",
                "Проконсультироваться по маршрутам расчётов по внешнеторговым контрактам.",
                "ved_support",
            )
        )
        d.actions.append(
            _action(
                product_ids,
                "analyze",
                "Проверить, какие платежи на ближайший месяц идут через затронутые страны.",
            )
        )
        return "risk"

    # commodities, industry, regulation_tax, trade_logistics, labor, macro_stat, company, other
    if t not in {"monetary_policy", "fx", "sanctions"}:
        comm = sorted(ev.commodities & (profile.commodities | profile.user_commodities))
        comm_label = ", ".join(COMMODITY_LABELS.get(c, c) for c in comm) or "продукции отрасли"
        if t == "regulation_tax":
            if down:
                opp(
                    "Снижение нагрузки улучшит экономику операций компании в затронутом сегменте.",
                    "months",
                    ["вступление решения в силу", "реакция цен"],
                )
            else:
                risk(
                    "Новые требования или платежи увеличат затраты компании в затронутом сегменте.",
                    "months",
                    ["сроки вступления в силу", "возможность переложить рост затрат в цены"],
                )
            if exporter and down:
                opp(
                    "Снижение экспортной нагрузки повышает конкурентоспособность поставок на внешние рынки.",
                    "months",
                    ["спрос импортёров", "мировые цены"],
                )
            d.actions.append(
                _action(product_ids, "analyze", "Оценить влияние изменения на себестоимость и цены.")
            )
        elif t == "trade_logistics":
            risk(
                "Задержки и рост ставок перевозки увеличат сроки поставок и затраты на логистику.",
                "weeks",
                ["пропускная способность маршрутов", "ставки фрахта"],
            )
            if "logistics" in profile.industry_tags or "trucking" in profile.industry_tags:
                opp(
                    "Спрос на альтернативные маршруты и складские услуги может вырасти.",
                    "months",
                    ["перераспределение грузопотоков"],
                )
            d.actions.append(
                _action(
                    product_ids,
                    "contact_manager",
                    "Обсудить финансирование увеличенных запасов и сроков оборота.",
                    "loan_working_capital",
                )
            )
        elif t in {"labor"}:
            risk(
                "Рост зарплат и дефицит персонала увеличат операционные расходы.",
                "quarters",
                ["ситуация на рынке труда", "индексация зарплат"],
            )
        elif "demand" in values and not ev.commodities:
            if up:
                opp(
                    "Рост потребительского спроса поддержит выручку компании.",
                    "quarters",
                    ["реальные доходы населения"],
                )
            else:
                risk(
                    "Замедление потребительского спроса может снизить выручку и оборачиваемость запасов.",
                    "quarters",
                    ["реальные доходы населения", "инфляция", "ставки по потребкредитам"],
                )
        else:
            if up:
                if seller:
                    opp(
                        f"Рост цен на {comm_label} поддержит выручку и маржу по текущим запасам.",
                        "weeks",
                        ["устойчивость спроса", "цены конкурентов"],
                    )
                    risk(
                        "Рост цен может снизить спрос со стороны покупателей и потребовать больше "
                        "оборотного капитала.",
                        "months",
                        ["спрос покупателей", "сроки оплаты"],
                    )
                else:
                    risk(
                        f"Рост цен на {comm_label} увеличит себестоимость и затраты на закупки.",
                        "months",
                        ["динамика цен", "возможность пересмотра цен в контрактах"],
                    )
            elif down:
                if seller:
                    risk(
                        f"Снижение цен на {comm_label} давит на выручку и стоимость запасов.",
                        "weeks",
                        ["динамика цен", "объём запасов"],
                    )
                else:
                    opp(
                        f"Снижение цен на {comm_label} уменьшит затраты на закупки.",
                        "months",
                        ["динамика цен"],
                    )
            else:
                risk(
                    "Изменения в отрасли могут повлиять на спрос и условия работы компании.",
                    "months",
                    ["развитие ситуации в отрасли"],
                )
            d.actions.append(
                _action(product_ids, "analyze", "Пересчитать закупочный план и цены на ближайший квартал.")
            )
            if "counterparty" in values:
                d.actions.append(
                    _action(
                        product_ids,
                        "contact_manager",
                        "Обсудить инструменты снижения рисков расчётов с ключевыми контрагентами.",
                        "trade_finance",
                    )
                )
        if "counterparty" in values and not any("контрагент" in r["text"] for r in d.risks):
            risk(
                "Изменения у значимых контрагентов могут отразиться на сроках платежей и объёмах заказов.",
                "months",
                ["финансовое положение контрагентов"],
            )
        scenario(
            "Тенденция сохраняется",
            "Если изменение закрепится, эффект для компании будет нарастать в течение нескольких месяцев.",
            "medium",
            ["повторные сообщения о событии", "динамика цен и спроса"],
        )
        d.actions.append(
            _action(product_ids, "monitor", "Отслеживать развитие события и реакцию контрагентов.")
        )

    if d.risks and d.opportunities:
        return "mixed"
    if d.risks:
        return "risk"
    if d.opportunities:
        return "opportunity"
    return "neutral"
