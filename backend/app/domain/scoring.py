"""Baseline relevance scorer for event x company candidates (ADR-007, SPEC FR-M2).

Each component c_i in [0, 1] says *why* an event may matter to this company; components are combined
with noisy-OR (several independent reasons raise the score, none can exceed 1), weighted by role.
The result keeps an additive breakdown (component -> contribution, summing to the score) and the
feature paths that matched — the input for ``why_it_matters[].feature_refs`` and for the trace.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.ports import ScoreResult
from app.domain.tagging import GENERIC_TAGS

SCORER_NAME = "baseline@1"

BASE_WEIGHTS: dict[str, float] = {
    "group": 0.6,
    "rate": 0.55,
    "fx_country": 0.5,
    "counterparty": 0.45,
    "industry": 0.45,
    "commodity": 0.45,
    "user_provided": 0.4,
    "demand": 0.35,
    "regulation": 0.2,
    "region": 0.2,
    "bank_analytics": 0.15,
}

# Role personalization (SPEC §4): multipliers on component weights.
ROLE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "owner": {"industry": 1.3, "counterparty": 1.3, "group": 1.2, "demand": 1.2},
    "ceo": {"industry": 1.3, "counterparty": 1.3, "group": 1.2, "demand": 1.2},
    "cfo": {"rate": 1.5, "fx_country": 1.3, "bank_analytics": 1.3, "commodity": 1.1},
    "treasurer": {"fx_country": 1.5, "rate": 1.4, "bank_analytics": 1.2},
    "chief_accountant": {"regulation": 2.0, "rate": 1.1},
    "other": {},
}

RECENCY_HALF_LIFE_DAYS = 7.0
RECENCY_SHARE = 0.15
MAX_WEIGHT = 0.95
CONSUMER_TAGS = frozenset({"retail", "consumer_goods", "food", "hospitality"})


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    company_id: str
    industry_tags: frozenset[str]
    counterparty_shares: dict[str, tuple[float, str]]  # tag -> (max share, feature path)
    top_counterparties: frozenset[str]
    fts_countries: frozenset[str]
    commodities: frozenset[str]
    user_commodities: frozenset[str]
    user_currencies: frozenset[str]
    products: frozenset[str]
    region: str | None
    rate_sensitive: bool
    rate_sensitive_industry: bool
    fx_sensitive: bool
    consumer_demand: bool


@dataclass(frozen=True, slots=True)
class EventProfile:
    event_id: str
    event_type: str
    direction: str
    industry_tags: frozenset[str]
    countries: frozenset[str]
    commodities: frozenset[str]
    regions: frozenset[str]
    currencies: frozenset[str]
    company_ids: frozenset[str]
    urgent: bool
    first_seen_at: datetime


@dataclass
class _Component:
    value: float = 0.0
    features: list[str] = field(default_factory=list)

    def offer(self, value: float, *features: str) -> None:
        if value > self.value:
            self.value = value
            self.features = list(features)
        elif value == self.value and value > 0:
            self.features.extend(f for f in features if f not in self.features)


def _feature_value(features: dict[str, Any], path: str, default: Any = None) -> Any:
    feat = features.get(path)
    return feat.get("value", default) if isinstance(feat, dict) else default


def profile_from_context(company_id: str, features: dict[str, Any]) -> CompanyProfile:
    """Build the scorer's view of a company from ``company_context.features``."""
    shares: dict[str, tuple[float, str]] = {}
    for path in features:
        for direction in ("in", "out"):
            prefix = f"counterparties.industry_share_{direction}."
            if path.startswith(prefix):
                tag = path[len(prefix) :]
                value = float(_feature_value(features, path, 0.0) or 0.0)
                if value > shares.get(tag, (0.0, ""))[0]:
                    shares[tag] = (value, path)
    top = {
        cp["counterparty_id"]
        for d in ("in", "out")
        for cp in (_feature_value(features, f"counterparties.top_{d}", []) or [])
        if cp.get("known")
    }
    user_materials = {
        str(x).lower() for x in (_feature_value(features, "user_provided.raw_materials", []) or [])
    }
    user_currencies = {
        str(x).upper() for x in (_feature_value(features, "user_provided.currencies", []) or [])
    }
    products = {
        path.split(".", 1)[1]
        for path in features
        if path.startswith("products.") and _feature_value(features, path) is True
    }
    return CompanyProfile(
        company_id=company_id,
        industry_tags=frozenset(_feature_value(features, "industry_tags", []) or []),
        counterparty_shares=shares,
        top_counterparties=frozenset(top),
        fts_countries=frozenset(_feature_value(features, "fts_countries", []) or []),
        commodities=frozenset(_feature_value(features, "sensitivities.commodity_exposure", []) or []),
        user_commodities=frozenset(user_materials),
        user_currencies=frozenset(user_currencies - {"RUB"}),
        products=frozenset(products),
        region=_feature_value(features, "region"),
        rate_sensitive=bool(_feature_value(features, "sensitivities.rate_sensitive", False)),
        rate_sensitive_industry=bool(
            _feature_value(features, "sensitivities.rate_sensitive_industry", False)
        ),
        fx_sensitive=bool(_feature_value(features, "sensitivities.fx_sensitive", False)),
        consumer_demand=bool(_feature_value(features, "sensitivities.consumer_demand_dependent", False)),
    )


def components(company: CompanyProfile, event: EventProfile) -> dict[str, _Component]:
    comp = {name: _Component() for name in BASE_WEIGHTS}
    specific_tags = event.industry_tags - GENERIC_TAGS

    if company.company_id in event.company_ids:
        comp["group"].offer(1.0, "group.id")
    hit_cps = event.company_ids & company.top_counterparties
    if hit_cps:
        comp["counterparty"].offer(1.0, "counterparties.top_in", "counterparties.top_out")

    if specific_tags & company.industry_tags:
        comp["industry"].offer(1.0, "industry_tags", "okved.code")
    for tag in sorted(specific_tags):
        if tag in company.counterparty_shares:
            share, path = company.counterparty_shares[tag]
            comp["counterparty"].offer(min(1.0, 2 * share), path)

    countries = event.countries & company.fts_countries
    if countries:
        comp["fx_country"].offer(1.0, "fts_countries")
    elif event.event_type in {"fx", "sanctions"} and company.fx_sensitive:
        comp["fx_country"].offer(0.7, "sensitivities.fx_sensitive")

    if event.event_type == "monetary_policy":
        rate_products = sorted(company.products & {"loan", "leasing", "factoring"})
        if company.rate_sensitive:
            comp["rate"].offer(1.0, "sensitivities.rate_sensitive", *[f"products.{p}" for p in rate_products])
        elif company.rate_sensitive_industry:
            comp["rate"].offer(0.6, "sensitivities.rate_sensitive_industry")
        elif "deposit" in company.products:
            comp["rate"].offer(0.6, "products.deposit")
        else:
            comp["rate"].offer(0.2)

    if event.commodities & company.commodities:
        comp["commodity"].offer(1.0, "sensitivities.commodity_exposure")
    if event.commodities & company.user_commodities:
        comp["user_provided"].offer(1.0, "user_provided.raw_materials")
    if event.currencies & company.user_currencies:
        comp["user_provided"].offer(1.0, "user_provided.currencies")

    if company.region and company.region in event.regions:
        comp["region"].offer(1.0, "region")

    if company.consumer_demand and (
        event.industry_tags & CONSUMER_TAGS or event.event_type in {"macro_stat", "labor"}
    ):
        comp["demand"].offer(
            0.8 if event.industry_tags & CONSUMER_TAGS else 0.5, "sensitivities.consumer_demand_dependent"
        )

    if event.event_type == "regulation_tax" and any(
        c.value > 0 for n, c in comp.items() if n not in {"regulation", "region"}
    ):
        comp["regulation"].offer(1.0)
    return comp


def recency(first_seen_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - first_seen_at).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)


def score_candidate(
    company: CompanyProfile,
    event: EventProfile,
    role: str,
    now: datetime,
    has_bank_analytics: bool = False,
) -> ScoreResult:
    comp = components(company, event)
    if has_bank_analytics and any(c.value > 0 for c in comp.values()):
        comp["bank_analytics"].offer(1.0)
    mult = ROLE_MULTIPLIERS.get(role, {})
    logs: dict[str, float] = {}
    for name, c in comp.items():
        if c.value <= 0:
            continue
        w = min(MAX_WEIGHT, BASE_WEIGHTS[name] * mult.get(name, 1.0))
        logs[name] = -math.log(1 - w * c.value)
    total = sum(logs.values())
    if total == 0:
        return ScoreResult(score=0.0, breakdown={}, matched_features=[], scorer=SCORER_NAME)
    content = 1 - math.exp(-total)
    rec = recency(event.first_seen_at, now)
    score = content * ((1 - RECENCY_SHARE) + RECENCY_SHARE * rec)
    breakdown = {name: round(content * (1 - RECENCY_SHARE) * x / total, 4) for name, x in logs.items()}
    breakdown["recency"] = round(content * RECENCY_SHARE * rec, 4)
    features: list[str] = []
    for name in sorted(logs, key=lambda n: -logs[n]):
        features.extend(f for f in comp[name].features if f not in features)
    return ScoreResult(
        score=round(score, 4), breakdown=breakdown, matched_features=features, scorer=SCORER_NAME
    )


COMPACT_VERSION = 1


def compact_profile(profile: CompanyProfile) -> dict[str, Any]:
    """The scorer's view stored in company_context.scoring_features (~1 KB, no feature metadata): matching
    rebuilds it without parsing the full context. profile_from_stored(compact_profile(p)) == p."""
    return {
        "v": COMPACT_VERSION,
        "it": sorted(profile.industry_tags),
        # tag -> [share, direction]; the feature path is counterparties.industry_share_<direction>.<tag>
        "cs": {
            tag: [share, path.split(".")[1].removeprefix("industry_share_")]
            for tag, (share, path) in sorted(profile.counterparty_shares.items())
        },
        "tc": sorted(profile.top_counterparties),
        "fc": sorted(profile.fts_countries),
        "co": sorted(profile.commodities),
        "um": sorted(profile.user_commodities),
        "uc": sorted(profile.user_currencies),
        "pr": sorted(profile.products),
        "rg": profile.region,
        "fl": [
            profile.rate_sensitive,
            profile.rate_sensitive_industry,
            profile.fx_sensitive,
            profile.consumer_demand,
        ],
    }


def profile_from_stored(company_id: str, stored: dict[str, Any]) -> CompanyProfile:
    """company_context.scoring_features: the compact profile, or (older rows) a features dict."""
    if stored.get("v") != COMPACT_VERSION:
        return profile_from_context(company_id, stored)
    shares = {
        tag: (float(share), f"counterparties.industry_share_{direction}.{tag}")
        for tag, (share, direction) in stored["cs"].items()
    }
    rate, rate_industry, fx, demand = stored["fl"]
    return CompanyProfile(
        company_id=company_id,
        industry_tags=frozenset(stored["it"]),
        counterparty_shares=shares,
        top_counterparties=frozenset(stored["tc"]),
        fts_countries=frozenset(stored["fc"]),
        commodities=frozenset(stored["co"]),
        user_commodities=frozenset(stored["um"]),
        user_currencies=frozenset(stored["uc"]),
        products=frozenset(stored["pr"]),
        region=stored["rg"],
        rate_sensitive=bool(rate),
        rate_sensitive_industry=bool(rate_industry),
        fx_sensitive=bool(fx),
        consumer_demand=bool(demand),
    )


def component_values(company: CompanyProfile, event: EventProfile) -> dict[str, float]:
    return {name: c.value for name, c in components(company, event).items() if c.value > 0}
