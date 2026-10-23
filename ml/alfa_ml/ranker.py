"""ml-ranker@1: relevance of an event to a company (orders the feed via insight.relevance.score).

Differences from the backend baseline (baseline@1), each motivated by the EDA (ml/reports/eda_ml.md):
* counterparty exposure is calibrated by turnover share (<1% = no exposure, saturates at 25%), so a tiny
  payment share does not lift the event (docs/BACKLOG.md, ADR-029);
* proximity of influence (docs/IMPACT_SCALE.md) weighs components: own product > counterparty > industry > region;
* event strength (magnitude) modulates the score: a decision on the key rate outranks routine statistics;
* role multipliers keep the role personalisation of the baseline (SPEC §4);
* bank analytics on the topic adds a small, explainable bonus.
The score keeps an additive breakdown with the backend keys, so the feed can show "why this is high".
"""

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from alfa_ml.impact import magnitude
from alfa_ml.profile import PROXIMITY, ExposureResult

if TYPE_CHECKING:
    from alfa_ml.agent.evidence import Evidence

RANKER_NAME = "ml-ranker@1"
ROLE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "owner": {"industry": 1.3, "counterparty": 1.3, "group": 1.2, "demand": 1.2},
    "ceo": {"industry": 1.3, "counterparty": 1.3, "group": 1.2, "demand": 1.2},
    "cfo": {"rate": 1.4, "fx_country": 1.25, "bank_analytics": 1.3, "commodity": 1.1},
    "treasurer": {"fx_country": 1.4, "rate": 1.3, "bank_analytics": 1.2},
    "chief_accountant": {"regulation": 1.8, "rate": 1.1},
    "other": {},
}
# breakdown keys understood by the feed (backend app/services/feed.py PRIORITY_LABELS)
KEY_OF = {"labor": "demand"}
RECENCY_HALF_LIFE_DAYS = 7.0
RECENCY_SHARE = 0.15
MAX_W = 0.95
BANK_ANALYTICS_W = 0.12


def _recency(first_seen: Any, now: datetime) -> float:
    try:
        ts = datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age = max(0.0, (now - ts).total_seconds() / 86400)
    return math.exp(-math.log(2) * age / RECENCY_HALF_LIFE_DAYS)


def score_exposure(
    exposure: ExposureResult,
    event: dict[str, Any],
    role: str,
    *,
    has_bank_analytics: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    mult = ROLE_MULTIPLIERS.get(role, {})
    logs: dict[str, float] = {}
    for e in exposure.items.values():
        key = KEY_OF.get(e.name, e.name)
        w = min(MAX_W, e.value * PROXIMITY.get(e.name, 0.3) * mult.get(key, 1.0))
        if w <= 0:
            continue
        logs[key] = logs.get(key, 0.0) - math.log(1 - w)
    if has_bank_analytics and logs:
        w = min(MAX_W, BANK_ANALYTICS_W * mult.get("bank_analytics", 1.0))
        logs["bank_analytics"] = -math.log(1 - w)
    total = sum(logs.values())
    if total <= 0:
        return {
            "score": 0.0,
            "breakdown": {},
            "matched_features": exposure.feature_refs(),
            "scorer": RANKER_NAME,
        }
    content = 1 - math.exp(-total)
    strength = 0.7 + 0.3 * min(1.0, magnitude(event) / 0.9)
    rec = _recency(event.get("first_seen_at"), now)
    score = content * strength * ((1 - RECENCY_SHARE) + RECENCY_SHARE * rec)
    base = content * strength * (1 - RECENCY_SHARE)
    breakdown = {k: round(base * v / total, 4) for k, v in sorted(logs.items(), key=lambda kv: -kv[1])}
    breakdown["recency"] = round(content * strength * RECENCY_SHARE * rec, 4)
    return {
        "score": round(min(1.0, score), 4),
        "breakdown": breakdown,
        "matched_features": exposure.feature_refs(),
        "scorer": RANKER_NAME,
    }


def relevance_for(ev: "Evidence", impact_score: int | None = None) -> dict[str, Any]:
    """insight.relevance: ml-ranker@1 score, with the backend baseline kept in the breakdown for comparison."""
    res = score_exposure(ev.exposure, ev.event, ev.role, has_bank_analytics=bool(ev.analytics))
    return res


def rescore_baseline(item: dict[str, Any]) -> dict[str, Any]:
    """/v1/score without company data: recalibrate the baseline breakdown (no tool calls, 500 items < 10 ms).

    Only what the breakdown tells us is used: weak single-component scores are damped (the baseline's noisy-OR
    gives 0.2-0.4 to events that match one weak signal), multi-reason matches are kept.
    """
    baseline = item.get("baseline") or {}
    score = float(baseline.get("score") or 0.0)
    breakdown = {
        k: float(v) for k, v in (baseline.get("breakdown") or {}).items() if isinstance(v, int | float)
    }
    reasons = {k: v for k, v in breakdown.items() if k != "recency" and v > 0}
    strong = {"group", "rate", "fx_country", "counterparty", "commodity", "user_provided"}
    if not reasons:
        factor = 0.5
    elif len(reasons) == 1 and not (set(reasons) & strong):
        factor = 0.75  # only industry/region/demand/regulation: background news
    elif set(reasons) <= {"regulation", "region", "bank_analytics"}:
        factor = 0.6
    else:
        factor = 1.0
    new = round(min(1.0, score * factor), 4)
    scaled = {k: round(v * factor, 4) for k, v in breakdown.items()}
    return {"company_id": item["company_id"], "event_id": item["event_id"], "score": new, "breakdown": scaled}
