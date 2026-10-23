"""Shared helpers for the e2e suite (compose stack, `make e2e`)."""

import json
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = os.environ.get("E2E_BASE_URL", "")
ML_SERVICE_TOKEN = os.environ.get("E2E_ML_SERVICE_TOKEN", "")
METALS, RETAIL, LOGISTICS, AGRO = "U_700001", "U_700002", "U_700003", "U_700004"
WAIT_S = 180


def login(api: httpx.Client, user: str, password: str) -> dict[str, str]:
    resp = api.post("/api/v1/auth/login", json={"login": user, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def wait_for(probe: Callable[[], Any], what: str, timeout_s: float = WAIT_S, every_s: float = 3) -> Any:
    """Poll until probe() returns something truthy (the worker processes queues asynchronously)."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout_s:.0f}s waiting for {what}")
        time.sleep(every_s)


def feed(api: httpx.Client, auth: dict[str, str], company: str, **params: Any) -> list[dict[str, Any]]:
    resp = api.get("/api/v1/feed", headers=auth, params={"company_id": company, **params})
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


def sse(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [x for x in block.splitlines() if x and not x.startswith(":")]
        event = next((x[6:].strip() for x in lines if x.startswith("event:")), None)
        data = "\n".join(x[5:].strip() for x in lines if x.startswith("data:"))
        if event:
            out.append((event, json.loads(data) if data else None))
    return out
