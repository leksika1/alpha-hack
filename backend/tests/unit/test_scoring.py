from datetime import UTC, datetime, timedelta

import pytest

from app.domain.impact import impact_score, level_for_score, magnitude
from app.domain.scoring import CompanyProfile, EventProfile, component_values, score_candidate

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def company(**kw: object) -> CompanyProfile:
    base = {
        "company_id": "U_1",
        "industry_tags": frozenset({"wholesale", "trade", "metals"}),
        "counterparty_shares": {"construction": (0.3, "counterparties.industry_share_in.construction")},
        "top_counterparties": frozenset({"U_9"}),
        "fts_countries": frozenset({"КИТАЙ"}),
        "commodities": frozenset({"steel"}),
        "user_commodities": frozenset(),
        "user_currencies": frozenset(),
        "products": frozenset({"loan", "rko"}),
        "region": "Свердловская область",
        "rate_sensitive": True,
        "rate_sensitive_industry": True,
        "fx_sensitive": True,
        "consumer_demand": False,
    }
    base.update(kw)
    return CompanyProfile(**base)  # type: ignore[arg-type]


def event(**kw: object) -> EventProfile:
    base = {
        "event_id": "e1",
        "event_type": "commodities",
        "direction": "up",
        "industry_tags": frozenset({"metals", "construction"}),
        "countries": frozenset(),
        "commodities": frozenset({"steel"}),
        "regions": frozenset({"Свердловская область"}),
        "currencies": frozenset(),
        "company_ids": frozenset(),
        "urgent": False,
        "first_seen_at": NOW - timedelta(hours=5),
    }
    base.update(kw)
    return EventProfile(**base)  # type: ignore[arg-type]


def test_breakdown_sums_to_score_and_features_reported() -> None:
    res = score_candidate(company(), event(), "ceo", NOW)
    assert 0 < res.score <= 1
    assert sum(res.breakdown.values()) == pytest.approx(res.score, abs=2e-3)
    assert {"industry", "commodity", "counterparty", "region", "recency"} <= set(res.breakdown)
    assert "sensitivities.commodity_exposure" in res.matched_features
    assert "counterparties.industry_share_in.construction" in res.matched_features


def test_unrelated_event_scores_zero() -> None:
    res = score_candidate(
        company(),
        event(industry_tags=frozenset({"it"}), commodities=frozenset(), regions=frozenset()),
        "ceo",
        NOW,
    )
    assert res.score == 0 and res.breakdown == {} and res.matched_features == []


def test_rate_event_personalized_by_products() -> None:
    rate = event(
        event_type="monetary_policy",
        direction="hold",
        industry_tags=frozenset(),
        commodities=frozenset(),
        regions=frozenset(),
    )
    with_loan = score_candidate(company(), rate, "cfo", NOW)
    no_loan = score_candidate(
        company(products=frozenset({"rko"}), rate_sensitive=False, rate_sensitive_industry=False),
        rate,
        "cfo",
        NOW,
    )
    assert with_loan.score > 0.5 > no_loan.score
    assert "products.loan" in with_loan.matched_features


def test_role_changes_weights() -> None:
    rate = event(
        event_type="monetary_policy",
        direction="hold",
        industry_tags=frozenset({"metals"}),
        commodities=frozenset(),
        regions=frozenset(),
    )
    cfo = score_candidate(company(), rate, "cfo", NOW)
    ceo = score_candidate(company(), rate, "ceo", NOW)
    assert cfo.breakdown["rate"] > ceo.breakdown["rate"]
    assert ceo.breakdown["industry"] > cfo.breakdown["industry"]


def test_recency_decays() -> None:
    fresh = score_candidate(company(), event(), "ceo", NOW)
    old = score_candidate(company(), event(first_seen_at=NOW - timedelta(days=30)), "ceo", NOW)
    assert fresh.score > old.score


def test_different_companies_get_different_scores_for_same_event() -> None:
    retail = company(
        industry_tags=frozenset({"retail", "food"}),
        counterparty_shares={},
        commodities=frozenset(),
        fts_countries=frozenset(),
        region="Москва",
        consumer_demand=True,
    )
    steel = event()
    assert (
        score_candidate(company(), steel, "ceo", NOW).score > score_candidate(retail, steel, "ceo", NOW).score
    )


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0, "low"),
        (24, "low"),
        (25, "medium"),
        (49, "medium"),
        (50, "high"),
        (74, "high"),
        (75, "critical"),
        (100, "critical"),
    ],
)
def test_impact_levels(score: int, level: str) -> None:
    assert level_for_score(score).value == level


def test_rate_hold_for_company_with_loan_is_high_impact() -> None:
    rate = event(
        event_type="monetary_policy",
        direction="hold",
        industry_tags=frozenset(),
        commodities=frozenset(),
        regions=frozenset(),
    )
    values = component_values(company(), rate)
    score, strongest = impact_score(values, magnitude("monetary_policy", "hold", False, None))
    assert strongest == "rate" and level_for_score(score).value == "high"


def test_magnitude_rules() -> None:
    assert magnitude("monetary_policy", "up", False, None) == 0.9
    assert magnitude("commodities", "up", False, 12.0) > magnitude("commodities", "up", False, None)
    assert magnitude("other", "none", True, None) == 0.8
