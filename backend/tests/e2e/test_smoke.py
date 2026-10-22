"""E2E smoke against the running compose stack (make e2e sets E2E_BASE_URL).

Full scenarios 1-12 (docs/SPEC.md §11.2) are added in phase 7.
"""

import os

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "")
ML_STUB_URL = os.environ.get("E2E_ML_STUB_URL", "")


@pytest.fixture(scope="module")
def api() -> httpx.Client:
    if not BASE_URL:
        pytest.fail("E2E_BASE_URL is not set: run e2e via `make e2e`")
    return httpx.Client(base_url=BASE_URL, timeout=10)


def test_api_health_and_ready(api: httpx.Client) -> None:
    assert api.get("/health").json()["status"] == "ok"
    ready = api.get("/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"] == {"postgres": "ok", "redis": "ok", "s3": "ok"}


def test_openapi_available(api: httpx.Client) -> None:
    assert api.get("/api/v1/openapi.json").status_code == 200


def test_ml_stub_health() -> None:
    if not ML_STUB_URL:
        pytest.fail("E2E_ML_STUB_URL is not set: run e2e via `make e2e`")
    assert httpx.get(f"{ML_STUB_URL}/health", timeout=10).json()["status"] == "ok"
