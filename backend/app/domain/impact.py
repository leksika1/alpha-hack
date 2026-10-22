"""Impact scale v1 (docs/IMPACT_SCALE.md): score = 100 * exposure * magnitude * proximity."""

from app.domain.contracts.insight import ImpactLevel

SCALE_VERSION = 1
LEVEL_BOUNDS: tuple[tuple[int, ImpactLevel], ...] = (
    (75, ImpactLevel.critical),
    (50, ImpactLevel.high),
    (25, ImpactLevel.medium),
    (0, ImpactLevel.low),
)

MAGNITUDE_BY_TYPE: dict[str, float] = {
    "monetary_policy": 0.6,
    "sanctions": 0.8,
    "fx": 0.6,
    "regulation_tax": 0.6,
    "trade_logistics": 0.55,
    "commodities": 0.5,
    "company": 0.6,
    "industry": 0.4,
    "macro_stat": 0.35,
    "labor": 0.35,
    "other": 0.3,
}

# Proximity of the strongest reason (docs/IMPACT_SCALE.md).
PROXIMITY_BY_COMPONENT: dict[str, float] = {
    "group": 1.0,
    "rate": 0.9,  # own credit/leasing: the event changes the company's cost of debt directly
    "fx_country": 0.9,  # the company's own foreign-trade country
    "user_provided": 0.9,
    "counterparty": 0.8,
    "commodity": 0.7,
    "industry": 0.6,
    "demand": 0.6,
    "region": 0.4,
    "regulation": 0.4,
    "bank_analytics": 0.3,
}


def level_for_score(score: int) -> ImpactLevel:
    for bound, level in LEVEL_BOUNDS:
        if score >= bound:
            return level
    return ImpactLevel.low


def score_range(level: ImpactLevel) -> tuple[int, int]:
    bounds = {lvl: b for b, lvl in LEVEL_BOUNDS}
    upper = {ImpactLevel.low: 24, ImpactLevel.medium: 49, ImpactLevel.high: 74, ImpactLevel.critical: 100}
    return bounds[level], upper[level]


def magnitude(event_type: str, direction: str, urgent: bool, max_percent: float | None) -> float:
    m = MAGNITUDE_BY_TYPE.get(event_type, 0.3)
    if event_type == "monetary_policy" and direction == "up":
        m = 0.9
    elif event_type == "monetary_policy" and direction == "down":
        m = 0.7
    if max_percent is not None and event_type in {"commodities", "fx", "regulation_tax"}:
        m = max(m, min(0.9, 0.4 + max_percent / 25))
    if urgent:
        m = max(m, 0.8)
    return m


def impact_score(component_values: dict[str, float], magn: float) -> tuple[int, str | None]:
    """Returns (score 0..100, strongest component)."""
    if not component_values:
        return 0, None
    strongest = max(
        component_values, key=lambda n: (component_values[n] * PROXIMITY_BY_COMPONENT.get(n, 0.3), n)
    )
    exposure = component_values[strongest]
    proximity = PROXIMITY_BY_COMPONENT.get(strongest, 0.3)
    return max(0, min(100, round(100 * exposure * magn * proximity))), strongest
