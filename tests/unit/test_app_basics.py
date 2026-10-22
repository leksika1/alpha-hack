from fastapi.testclient import TestClient

from app.core.config import ALLOWED_LLM_MODELS
from app.core.logging import mask_pii
from app.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["x-request-id"]


def test_metrics_exposed() -> None:
    client = TestClient(create_app())
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text


def test_unknown_route_is_problem_json() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/v1/does-not-exist", headers={"X-Request-Id": "req-1"})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 404
    assert body["type"].endswith("/not-found")
    assert body["trace_id"] == "req-1"


def test_openapi_served_under_v1() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"].startswith("Alfa Future")


def test_mask_pii_removes_person_ids_emails_phones() -> None:
    text = "F_64255 связан с U_146717, почта ivan@example.com, тел. +7 (912) 345-67-89"
    masked = mask_pii(text)
    assert "F_64255" not in masked
    assert "ivan@example.com" not in masked
    assert "345-67-89" not in masked
    assert "U_146717" in masked  # company ids are not personal data


def test_llm_allowlist_matches_case_appendix_3() -> None:
    assert set(ALLOWED_LLM_MODELS.values()) == {
        "Gemma 4 26B A4B",
        "GLM-5.3-Flash",
        "Qwen3.6 35B A3B",
        "DeepSeek-V4.1-Flash",
        "Qwen3-VL-30B-A3B-Instruct",
        "Kimi-K2.6",
    }
