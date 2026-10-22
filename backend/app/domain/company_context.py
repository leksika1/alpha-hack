"""Deterministic company context: personalization features with provenance + completeness.

Feature paths are the vocabulary of ``feature_refs`` in the insight contract and of the
Tools API ``get_company_context`` (contracts/tools-api.openapi.yaml). Unknown data stays
``None`` and is reported in ``completeness`` — nothing is invented (CLAUDE.md §7).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.domain.cleaning import ALL_PRODUCTS, segment_by_revenue

Provenance = Literal["csv", "derived", "user", "external"]

RATE_PRODUCTS = ("loan", "leasing", "factoring")
COUNTERPARTY_TAG_MIN_SHARE = 0.05  # counterparty industry becomes an index tag above this share
TOP_N = 10
USER_FIELDS = ("suppliers", "customers", "raw_materials", "currencies", "credit_plans", "notes")


@dataclass(frozen=True, slots=True)
class Counterparty:
    counterparty_id: str
    amount_rub: float
    tx_count: int
    share: float
    known: bool
    okved_code: str | None = None
    okved_name: str | None = None
    industry_tags: tuple[str, ...] = ()
    region: str | None = None
    same_group: bool = False


@dataclass(frozen=True, slots=True)
class DirectionStats:
    total_rub: float = 0.0
    known_rub: float = 0.0
    n_known: int = 0
    n_unknown: int = 0
    hhi: float | None = None
    top: tuple[Counterparty, ...] = ()
    industry_shares: dict[str, float] = field(default_factory=dict)

    @property
    def n_total(self) -> int:
        return self.n_known + self.n_unknown

    @property
    def unknown_share(self) -> float | None:
        if self.total_rub <= 0:
            return None
        return round(1 - self.known_rub / self.total_rub, 4)


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    company_id: str
    is_valid: bool
    client_type: str | None
    okved_code: str | None
    okved_name: str | None
    okved_section: str | None
    okved_section_name: str | None
    industry_tags: tuple[str, ...]
    okved_commodities: tuple[str, ...]
    okved_flags: dict[str, bool]
    region: str | None
    city: str | None
    age_bucket: str | None
    staff_bucket: str | None
    eco_stage: str | None
    okfs_name: str | None
    foreign_ownership: bool | None
    owner_info_reliable: bool | None
    revenue_rub: Decimal | None
    revenue_stub: bool
    group_id: str | None
    group_size: int | None
    group_revenue_rub: Decimal | None
    group_client_count: int | None
    products: tuple[str, ...]
    fts_flag: bool | None
    fts_countries: tuple[str, ...]
    incoming: DirectionStats = field(default_factory=DirectionStats)
    outgoing: DirectionStats = field(default_factory=DirectionStats)
    self_transfer_rub: float = 0.0
    shared_person_companies: tuple[str, ...] = ()
    user_provided: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextResult:
    features: dict[str, dict[str, Any]]
    completeness: dict[str, Any]
    index: dict[str, Any]


def _f(value: Any, source: Provenance, note: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "source": source}
    if note:
        out["note"] = note
    return out


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _cp_dict(cp: Counterparty) -> dict[str, Any]:
    return {
        "counterparty_id": cp.counterparty_id,
        "known": cp.known,
        "amount_rub": round(cp.amount_rub, 2),
        "tx_count": cp.tx_count,
        "share": round(cp.share, 4),
        "okved_code": cp.okved_code,
        "okved_name": cp.okved_name,
        "industry_tags": list(cp.industry_tags),
        "region": cp.region,
        "same_group": cp.same_group,
    }


def _user_list(user: dict[str, Any], key: str) -> list[str]:
    value = user.get(key)
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_context(c: CompanyFacts) -> ContextResult:
    feats: dict[str, dict[str, Any]] = {}
    user = c.user_provided or {}

    # identity
    feats["okved.code"] = _f(c.okved_code, "csv")
    feats["okved.name"] = _f(c.okved_name, "csv")
    feats["okved.section"] = _f(c.okved_section, "derived", "секция ОКВЭД2 по коду класса")
    feats["okved.section_name"] = _f(c.okved_section_name, "derived")
    feats["industry_tags"] = _f(list(c.industry_tags), "derived", "по справочнику data/reference/okved_*.csv")
    feats["region"] = _f(c.region, "csv")
    feats["city"] = _f(c.city, "csv")
    feats["age_bucket"] = _f(c.age_bucket, "csv")
    feats["staff_bucket"] = _f(c.staff_bucket, "csv", "численность по данным ФНС")
    feats["eco_stage"] = _f(c.eco_stage, "csv", "стадия жизненного цикла (ECO_MARK_LABEL)")
    feats["ownership_form"] = _f(c.okfs_name, "csv")
    feats["foreign_ownership"] = _f(c.foreign_ownership, "derived", "по ОКФС")
    feats["owner_info_reliable"] = _f(
        c.owner_info_reliable, "csv", "у банка есть достоверные сведения о ключевом собственнике"
    )
    feats["client_type"] = _f(c.client_type, "csv")

    # scale
    revenue_note = (
        "заглушка 1000 ₽ в исходных данных — выручка неизвестна" if c.revenue_stub else "2025 г., ₽"
    )
    feats["revenue_rub"] = _f(_num(c.revenue_rub), "csv", revenue_note)
    feats["group.id"] = _f(c.group_id, "csv")
    feats["group.size"] = _f(c.group_size, "derived")
    feats["group.revenue_rub"] = _f(
        _num(c.group_revenue_rub), "derived", "сумма известной выручки юрлиц группы"
    )
    feats["group.client_count"] = _f(c.group_client_count, "derived")
    scale_base = c.group_revenue_rub if c.group_revenue_rub is not None else c.revenue_rub
    segment = segment_by_revenue(scale_base)
    feats["segment"] = _f(segment, "derived", "по выручке группы: >15 млрд — large, ≥0,8 млрд — mid")

    # products
    for p in ALL_PRODUCTS:
        feats[f"products.{p}"] = _f(p in c.products, "csv")

    # user-provided facts
    user_materials = _user_list(user, "raw_materials")
    user_currencies = [x.upper() for x in _user_list(user, "currencies")]
    user_credit_plans = user.get("credit_plans")
    for key in USER_FIELDS:
        if key in user and user[key] not in (None, "", []):
            feats[f"user_provided.{key}"] = _f(user[key], "user")

    # sensitivities
    rate_products = [p for p in RATE_PRODUCTS if p in c.products]
    rate_sensitive = bool(rate_products) or bool(user_credit_plans)
    rate_note = (
        "есть продукты: " + ", ".join(rate_products)
        if rate_products
        else (
            "пользователь указал планы по кредитам"
            if user_credit_plans
            else "нет кредита, лизинга, факторинга"
        )
    )
    feats["sensitivities.rate_sensitive"] = _f(
        rate_sensitive, "user" if not rate_products and user_credit_plans else "derived", rate_note
    )
    feats["sensitivities.rate_sensitive_industry"] = _f(
        c.okved_flags.get("rate_sensitive_industry", False),
        "derived",
        "отрасль чувствительна к стоимости денег",
    )
    foreign_currencies = [x for x in user_currencies if x != "RUB"]
    fx = bool(c.fts_flag) or bool(foreign_currencies)
    feats["sensitivities.fx_sensitive"] = _f(
        fx,
        "derived",
        "ВЭД по данным ФТС"
        if c.fts_flag
        else ("валюты указаны пользователем" if foreign_currencies else None),
    )
    commodities = list(dict.fromkeys([*c.okved_commodities, *[m.lower() for m in user_materials]]))
    feats["sensitivities.commodity_exposure"] = _f(
        commodities,
        "user" if user_materials and not c.okved_commodities else "derived",
        "по ОКВЭД и данным пользователя",
    )
    feats["sensitivities.import_dependent"] = _f(
        bool(c.okved_flags.get("import_dependent")) and bool(c.fts_flag),
        "derived",
        "отрасль зависит от импорта и у компании есть ВЭД",
    )
    feats["sensitivities.export_oriented"] = _f(bool(c.okved_flags.get("export_oriented")), "derived")
    feats["sensitivities.consumer_demand_dependent"] = _f(
        bool(c.okved_flags.get("consumer_demand")), "derived"
    )
    feats["fts_countries"] = _f(list(c.fts_countries), "csv", "страны ВЭД по данным ФТС (без направления)")

    # counterparties (payments without self-transfers)
    for prefix, d in (("in", c.incoming), ("out", c.outgoing)):
        feats[f"counterparties.n_{prefix}"] = _f(d.n_total, "derived")
        feats[f"counterparties.amount_{prefix}_rub"] = _f(round(d.total_rub, 2), "derived")
        feats[f"counterparties.hhi_{prefix}"] = _f(
            round(d.hhi, 1) if d.hhi is not None else None, "derived", "концентрация контрагентов, 0–10 000"
        )
        feats[f"counterparties.unknown_share_{prefix}"] = _f(
            d.unknown_share, "derived", "доля оборота с контрагентами вне базы (профиль неизвестен)"
        )
        feats[f"counterparties.top_{prefix}"] = _f([_cp_dict(x) for x in d.top[:TOP_N]], "derived")
        for tag, share in sorted(d.industry_shares.items()):
            feats[f"counterparties.industry_share_{prefix}.{tag}"] = _f(round(share, 4), "derived")
    feats["counterparties.self_transfer_rub"] = _f(
        round(c.self_transfer_rub, 2), "derived", "исключены из анализа"
    )
    feats["links.shared_person_companies"] = _f(
        list(c.shared_person_companies[:TOP_N]),
        "derived",
        f"компании с общими физлицами: {len(c.shared_person_companies)} (ID физлиц не раскрываются)",
    )
    feats["links.shared_person_count"] = _f(len(c.shared_person_companies), "derived")

    # completeness
    checks: dict[str, bool] = {
        "okved.code": c.okved_code is not None,
        "region": c.region is not None,
        "revenue_rub": c.revenue_rub is not None,
        "eco_stage": c.eco_stage is not None,
        "staff_bucket": c.staff_bucket is not None,
        "group.id": c.group_id is not None,
        "counterparties": (c.incoming.n_total + c.outgoing.n_total) > 0,
    }
    missing = [k for k, ok in checks.items() if not ok]
    score = round(sum(checks.values()) / len(checks), 3)
    completeness = {
        "score": score,
        "missing": missing,
        "stubs": ["revenue_rub"] if c.revenue_stub else [],
        "user_fields": sorted(k for k in USER_FIELDS if f"user_provided.{k}" in feats),
    }

    cp_tags = sorted(
        {
            t
            for d in (c.incoming, c.outgoing)
            for t, s in d.industry_shares.items()
            if s >= COUNTERPARTY_TAG_MIN_SHARE
        }
    )
    index = {
        "client_type": c.client_type,
        "okved_section": c.okved_section,
        "region": c.region,
        "segment": segment,
        "industry_tags": list(c.industry_tags),
        "counterparty_tags": cp_tags,
        "fts_countries": list(c.fts_countries),
        "commodities": commodities,
        "products": list(c.products),
        "rate_sensitive": rate_sensitive,
        "fx_sensitive": fx,
        "completeness_score": score,
    }
    return ContextResult(features=feats, completeness=completeness, index=index)
