"""Actual ML application and backend talking over HTTP (`make e2e-ml`), without a paid LLM."""

import os
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.e2e.support import BASE_URL, METALS, ML_SERVICE_TOKEN, feed, login, sse, wait_for

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    assert BASE_URL, "Run via make e2e-ml"
    with httpx.Client(base_url=BASE_URL, timeout=100) as client:
        yield client


@pytest.fixture(scope="module")
def user(api: httpx.Client) -> dict[str, str]:
    return login(api, "demo", "demo12345")


@pytest.fixture(scope="module")
def insight(api: httpx.Client, user: dict[str, str]) -> dict[str, Any]:
    cards = wait_for(lambda: feed(api, user, METALS), "insights from the real ML service")
    response = api.get(f"/api/v1/insights/{cards[0]['insight_id']}", headers=user)
    assert response.status_code == 200, response.text
    return response.json()["insight"]


def test_real_ml_generates_validated_insight_and_trace(
    api: httpx.Client, user: dict[str, str], insight: dict[str, Any]
) -> None:
    assert insight["company_id"] == METALS
    assert insight["sources"] and insight["why_it_matters"]
    assert any(d["code"] == "not_investment_advice" for d in insight["disclaimers"])
    response = api.get(f"/api/v1/insights/{insight['insight_id']}/trace", headers=user)
    assert response.status_code == 200, response.text
    trace = response.json()
    assert trace["run"]["generator"] == "remote:ml-service", trace
    assert trace["candidate"]["scorer"].startswith("ml-ranker@"), trace["candidate"]
    assert trace["tool_calls"] and trace["company_features"]
    names = {step["name"] for step in trace["steps"]}
    assert "backend.validate" in names and "backend.persist" in names
    assert any(not name.startswith("backend.") for name in names), names


def test_real_ml_chat_stream_passes_backend_validation(
    api: httpx.Client, user: dict[str, str], insight: dict[str, Any]
) -> None:
    response = api.post(
        f"/api/v1/insights/{insight['insight_id']}/ask",
        headers=user,
        json={"question": "Как изменение ставки влияет на компанию и какие данные стоит проверить?"},
    )
    assert response.status_code == 200, response.text
    events = sse(response.text)
    assert "error" not in {kind for kind, _ in events}, events
    final = [data for kind, data in events if kind == "done"]
    assert len(final) == 1 and final[0]["answer"]
    tokens = "".join(data["delta"] for kind, data in events if kind == "token")
    assert tokens == final[0]["answer"]
    assert final[0]["sources"] and final[0]["statements"]


def test_real_ml_score_and_service_auth() -> None:
    url = os.environ["E2E_ML_URL"]
    body = {
        "request_id": "e2e-real-ml-score",
        "items": [
            {
                "company_id": METALS,
                "event_id": "00000000-0000-0000-0000-000000000001",
                "baseline": {"score": 0.8, "breakdown": {"rate": 0.6, "industry": 0.1, "recency": 0.1}},
            }
        ],
    }
    with httpx.Client(base_url=url, timeout=60) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/score", json=body).status_code == 401
        response = client.post(
            "/v1/score", json=body, headers={"Authorization": f"Bearer {ML_SERVICE_TOKEN}"}
        )
    assert response.status_code == 200, response.text
    scores = response.json()["scores"]
    assert len(scores) == 1 and scores[0]["company_id"] == METALS
    assert 0 <= scores[0]["score"] <= 1
