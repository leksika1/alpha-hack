"""E2E against the compose stack (`make e2e`): seed -> queued pre-generation in the worker -> ml-stub calls
the Tools API -> validated insights in the feed. Remaining scenarios of SPEC §11.2 are added in phase 7."""

import os
import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "")
FEED_WAIT_S = 180
METALS, RETAIL, AGRO = "U_700001", "U_700002", "U_700004"


@pytest.fixture(scope="module")
def api() -> httpx.Client:
    if not BASE_URL:
        pytest.fail("E2E_BASE_URL is not set: run e2e via `make e2e`")
    return httpx.Client(base_url=BASE_URL, timeout=30)


@pytest.fixture(scope="module")
def auth(api: httpx.Client) -> dict[str, str]:
    resp = api.post("/api/v1/auth/login", json={"login": "demo", "password": "demo12345"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def wait_feed(api: httpx.Client, auth: dict[str, str], company: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + FEED_WAIT_S
    while True:
        resp = api.get("/api/v1/feed", headers=auth, params={"company_id": company})
        assert resp.status_code == 200, resp.text
        items: list[dict[str, Any]] = resp.json()["items"]
        if items or time.monotonic() > deadline:
            return items
        time.sleep(3)


def test_scenario_1_login_companies_profile(api: httpx.Client, auth: dict[str, str]) -> None:
    me = api.get("/api/v1/me", headers=auth).json()
    assert {c["company_id"] for c in me["companies"]} >= {METALS, RETAIL}
    prof = api.get(f"/api/v1/companies/{METALS}/profile", headers=auth).json()
    assert prof["sections"] and "score" in prof["completeness"]


def test_scenario_2_feed_is_personalized_and_valid(api: httpx.Client, auth: dict[str, str]) -> None:
    metals = wait_feed(api, auth, METALS)
    retail = wait_feed(api, auth, RETAIL)
    assert metals and retail, "pre-generated insights must appear after the seed"
    assert [i["relevance_score"] for i in metals] == sorted(
        (i["relevance_score"] for i in metals), reverse=True
    )
    assert [i["event_id"] for i in metals] != [i["event_id"] for i in retail]
    full = api.get(f"/api/v1/insights/{metals[0]['insight_id']}", headers=auth).json()["insight"]
    assert any(d["code"] == "not_investment_advice" for d in full["disclaimers"])
    assert full["sources"] and {s["kind"] for s in full["statements"]} <= {
        "fact",
        "bank_analytics",
        "model_inference",
        "hypothesis",
    }


def test_scenario_4_trace(api: httpx.Client, auth: dict[str, str]) -> None:
    card = wait_feed(api, auth, METALS)[0]
    tr = api.get(f"/api/v1/insights/{card['insight_id']}/trace", headers=auth).json()
    assert tr["company_features"] and tr["steps"] and tr["tool_calls"]


def test_scenario_7_callback_goes_to_mock_crm(api: httpx.Client, auth: dict[str, str]) -> None:
    card = wait_feed(api, auth, METALS)[0]
    resp = api.post(f"/api/v1/insights/{card['insight_id']}/actions", headers=auth, json={"type": "callback"})
    assert resp.status_code == 201 and resp.json()["external_ref"].startswith("CRM-")


def test_scenario_11_foreign_company_is_forbidden(api: httpx.Client, auth: dict[str, str]) -> None:
    assert api.get(f"/api/v1/companies/{AGRO}/profile", headers=auth).status_code == 403
