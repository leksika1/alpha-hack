"""The optional remote scorer recalibrates bounded, role-separated baseline batches."""

import json
import uuid
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.domain.ports import ScoreResult
from app.integrations.scorers import HttpRelevanceScorer, ScoringItem, get_scorer


def item(index: int = 1, role: str = "ceo") -> ScoringItem:
    return ScoringItem(
        company_id=f"U_{index}",
        event_id=str(uuid.UUID(int=index)),
        role=role,
        baseline=ScoreResult(0.6, {"rate": 0.5, "recency": 0.1}, ["products.loan"], "baseline@1"),
        urgent=True,
    )


def response(body: dict[str, Any], score: float = 0.8) -> dict[str, Any]:
    return {
        "request_id": body["request_id"],
        "scorer": "ml-ranker@1:recalibrated-baseline",
        "scores": [
            {"company_id": it["company_id"], "event_id": it["event_id"], "score": score}
            for it in reversed(body["items"])
        ],
    }


async def test_batches_are_bounded_separate_roles_and_restore_response_order() -> None:
    requests: list[dict[str, Any]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer service-token"
        body = json.loads(req.content)
        requests.append(body)
        data = response(body)
        for row in data["scores"]:
            row["score"] = int(row["company_id"][2:]) / 1000
        return httpx.Response(200, json=data)

    scorer = HttpRelevanceScorer("http://ml", "service-token", transport=httpx.MockTransport(handle))
    items = [item(n) for n in range(1, 502)] + [item(1, "cfo")]
    results = await scorer.score_batch(items)
    assert [len(req["items"]) for req in requests] == [500, 1, 1]
    assert len({req["request_id"] for req in requests}) == 3
    assert all(len({it["role"] for it in req["items"]}) == 1 for req in requests)
    assert requests[0]["items"][0]["baseline"]["urgent"] is True
    assert [res.score for res in results] == [n / 1000 for n in range(1, 502)] + [0.001]
    assert all(res.matched_features == ["products.loan"] for res in results)
    assert all(res.scorer == "ml-ranker@1:recalibrated-baseline" for res in results)
    assert sum(results[299].breakdown.values()) == pytest.approx(results[299].score)


@pytest.mark.parametrize(
    "problem",
    ["json", "request_id", "missing", "duplicate", "foreign", "nan", "range", "bool", "breakdown", "scorer"],
)
async def test_bad_response_falls_back_to_original_baseline(problem: str) -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        if problem == "json":
            return httpx.Response(200, text="not JSON")
        data = response(json.loads(req.content))
        if problem == "request_id":
            data["request_id"] = "another-request"
        elif problem == "missing":
            data["scores"].pop()
        elif problem == "duplicate":
            data["scores"][1] = data["scores"][0]
        elif problem == "foreign":
            data["scores"][0]["company_id"] = "U_99999"
        elif problem in {"nan", "range", "bool"}:
            data["scores"][0]["score"] = {"nan": float("nan"), "range": 1.1, "bool": True}[problem]
        elif problem == "breakdown":
            data["scores"][0]["breakdown"] = {"rate": "high"}
        elif problem == "scorer":
            data["scorer"] = "s" * 256
        return httpx.Response(200, content=json.dumps(data), headers={"Content-Type": "application/json"})

    scorer = HttpRelevanceScorer("http://ml", "token", max_retries=0, transport=httpx.MockTransport(handle))
    items = [item(1), item(2)]
    assert await scorer.score_batch(items) == [it.baseline for it in items]


async def test_failed_batch_falls_back_without_discarding_successful_batches() -> None:
    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body["items"][0]["role"] == "cfo":
            raise httpx.ReadTimeout("scorer timeout")
        data = response(body)
        data["scores"][0]["breakdown"] = {"rate": 0.7, "recency": 0.1}
        return httpx.Response(200, json=data)

    scorer = HttpRelevanceScorer("http://ml", "token", max_retries=0, transport=httpx.MockTransport(handle))
    ceo, cfo = item(1), item(1, "cfo")
    results = await scorer.score_batch([ceo, cfo])
    assert results[0].score == 0.8 and results[0].breakdown == {"rate": 0.7, "recency": 0.1}
    assert results[1] is cfo.baseline


def test_scorer_provider_selects_remote_explicitly() -> None:
    assert get_scorer(Settings(scorer_provider="baseline")) is None
    assert get_scorer(Settings(scorer_provider="fake")) is None
    assert isinstance(get_scorer(Settings(scorer_provider="remote")), HttpRelevanceScorer)
