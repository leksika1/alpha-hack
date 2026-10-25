"""Optional ML recalibration of the baseline shortlist (contracts/ml-service.openapi.yaml /v1/score)."""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import TypeGuard

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.ports import ScoreResult
from app.integrations.ml_client import HttpInsightGenerator, MlServiceError

log = get_logger(__name__)
SCORE_BATCH_SIZE = 500  # contract limit


@dataclass(frozen=True, slots=True)
class ScoringItem:
    company_id: str
    event_id: str
    role: str
    baseline: ScoreResult
    urgent: bool = False


def _number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


class HttpRelevanceScorer(HttpInsightGenerator):
    name = "remote"

    async def score_batch(self, items: list[ScoringItem]) -> list[ScoreResult]:
        # ScoreResponse has no role field. Keep each company/event pair unique within a role's batch.
        by_role: dict[str, list[int]] = defaultdict(list)
        results = [item.baseline for item in items]
        for index, item in enumerate(items):
            by_role[item.role].append(index)
        for indices in by_role.values():
            for offset in range(0, len(indices), SCORE_BATCH_SIZE):
                batch_indices = indices[offset : offset + SCORE_BATCH_SIZE]
                batch = [items[index] for index in batch_indices]
                try:
                    scored = await self._score(batch)
                except (MlServiceError, httpx.HTTPError, ValueError, TypeError) as exc:
                    # Candidate discovery remains available if the optional scorer is down or incompatible.
                    log.warning("ml_score_degraded", count=len(batch), error=str(exc)[:200])
                    continue
                for index, result in zip(batch_indices, scored, strict=True):
                    results[index] = result
        return results

    async def _score(self, items: list[ScoringItem]) -> list[ScoreResult]:
        request_id = "score_" + uuid.uuid4().hex
        body = {
            "request_id": request_id,
            "items": [
                {
                    "company_id": item.company_id,
                    "event_id": item.event_id,
                    "role": item.role,
                    "baseline": {
                        "score": item.baseline.score,
                        "breakdown": item.baseline.breakdown,
                        "matched_features": item.baseline.matched_features,
                        "urgent": item.urgent,
                    },
                }
                for item in items
            ],
        }
        response = await self._post("/v1/score", body)
        if response.status_code != 200:
            raise MlServiceError(f"/v1/score: HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or data.get("request_id") != request_id:
            raise ValueError("Invalid score response request_id")
        rows = data.get("scores")
        if not isinstance(rows, list) or len(rows) != len(items):
            raise ValueError("Incomplete score response")
        scorer = data.get("scorer", self.name)
        if not isinstance(scorer, str) or not scorer.strip() or len(scorer) > 255:
            raise ValueError("Invalid scorer name")
        expected = {(item.company_id, item.event_id): item for item in items}
        scores: dict[tuple[str, str], ScoreResult] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid score row")
            company_id, event_id = row.get("company_id"), row.get("event_id")
            if not isinstance(company_id, str) or not isinstance(event_id, str):
                raise ValueError("Invalid score row identity")
            key = (company_id, event_id)
            if key not in expected or key in scores:
                raise ValueError("Unexpected or duplicate score row")
            value = row.get("score")
            if not _number(value) or not 0 <= value <= 1:
                raise ValueError("Score must be finite and between 0 and 1")
            baseline = expected[key].baseline
            breakdown = row.get("breakdown")
            if breakdown is None:
                factor = value / baseline.score if baseline.score else 0
                breakdown = {name: round(part * factor, 4) for name, part in baseline.breakdown.items()}
            if not isinstance(breakdown, dict) or any(not _number(part) for part in breakdown.values()):
                raise ValueError("Invalid score breakdown")
            scores[key] = ScoreResult(
                score=float(value),
                breakdown={name: float(part) for name, part in breakdown.items()},
                matched_features=list(baseline.matched_features),
                scorer=scorer,
            )
        return [scores[(item.company_id, item.event_id)] for item in items]


def get_scorer(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> HttpRelevanceScorer | None:
    if settings.scorer_provider != "remote":
        return None  # baseline/fake use the deterministic domain scorer
    return HttpRelevanceScorer(
        settings.ml_service_url,
        settings.ml_service_token.get_secret_value(),
        timeout_s=settings.ml_request_timeout_s,
        max_retries=settings.ml_max_retries,
        transport=transport,
    )
