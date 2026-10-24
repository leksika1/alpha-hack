"""Impact scale v1 (docs/IMPACT_SCALE.md): score = 100 · exposure · magnitude · proximity, level from score."""

import re
from typing import Any

from alfa_ml.profile import ExposureResult

LEVELS = (("critical", 75), ("high", 50), ("medium", 25), ("low", 0))
LEVEL_RANGE = {"low": (0, 24), "medium": (25, 49), "high": (50, 74), "critical": (75, 100)}
MAGNITUDE_BY_TYPE = {
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
_POLICY_CHANGE = re.compile(
    r"(ужесточ|смягч|отмен|ввод[яиу]т|введ[её]н|повыш[еа]н|снижен|продлен|продлён).{0,60}"
    r"(ипотек|льгот|субсиди|пошлин|сбор|налог|квот|лимит)|"
    r"(ипотек|льгот|субсиди|пошлин|сбор|налог|квот|лимит).{0,60}"
    r"(ужесточ|смягч|отмен|ввод[яиу]т|введ[её]н|повыш[еа]н|снижен|продлен|продлён)",
    re.IGNORECASE,
)
# The model may move the deterministic score towards its own level by at most this many points.
MAX_LLM_SHIFT = 15


def level_for_score(score: int) -> str:
    for level, bound in LEVELS:
        if score >= bound:
            return level
    return "low"


def magnitude(event: dict[str, Any]) -> float:
    etype = str(event.get("event_type") or "other")
    direction = str(event.get("direction") or "none")
    m = MAGNITUDE_BY_TYPE.get(etype, 0.3)
    if etype == "monetary_policy" and direction == "up":
        m = 0.9
    elif etype == "monetary_policy" and direction == "down":
        m = 0.7
    numbers = (event.get("tags") or {}).get("numbers") or []
    pct = max(
        (float(n["value"]) for n in numbers if isinstance(n, dict) and n.get("kind") == "percent"),
        default=None,
    )
    if pct is not None and etype in {"commodities", "fx", "regulation_tax"}:
        m = max(m, min(0.9, 0.4 + pct / 25))
    if etype in {"industry", "macro_stat", "other"} and _POLICY_CHANGE.search(
        f"{event.get('title') or ''} {event.get('summary') or ''}"
    ):
        m = max(m, MAGNITUDE_BY_TYPE["regulation_tax"])  # a rule/subsidy change typed as an industry story
    if event.get("urgent"):
        m = max(m, 0.8)
    return m


def base_score(exposure: ExposureResult, event: dict[str, Any]) -> tuple[int, str]:
    strongest = exposure.strongest
    magn = magnitude(event)
    if strongest is None:
        return 0, f"Нет прямой подверженности компании; сила события {magn:.2f}."
    score = max(0, min(100, round(100 * strongest.weight * magn)))
    rationale = (
        f"Сила события {magn:.2f} × подверженность по фактору «{strongest.name}» {strongest.value:.2f} × "
        f"близость влияния {strongest.weight / max(strongest.value, 1e-9):.1f}; шкала v1."
    )
    return score, rationale


def reconcile(score: int, llm_level: str | None) -> tuple[int, bool]:
    """Move the score towards the model's level (bounded), keep level consistent with score."""
    if llm_level not in LEVEL_RANGE:
        return score, False
    lo, hi = LEVEL_RANGE[llm_level]
    if lo <= score <= hi:
        return score, False
    target = lo if score < lo else hi
    if abs(target - score) > MAX_LLM_SHIFT:
        target = score + MAX_LLM_SHIFT if target > score else score - MAX_LLM_SHIFT
    return max(0, min(100, target)), True
