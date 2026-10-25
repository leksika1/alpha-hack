import json

import httpx
import pytest

from alfa_ml import main
from alfa_ml.profile import counterparty_strength
from tests.fakes import RATE_EVENT, LLMScript, generate_request


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    main._RESULTS.clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://ml")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_health_and_info(client) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get("/health")).json()["status"] == "ok"
    info = (await client.get("/v1/info")).json()
    assert info["prompts"]["analyst"] == "analyst@3"


async def test_generate_endpoint_and_idempotency(backend, client) -> None:  # type: ignore[no-untyped-def]
    req = generate_request(RATE_EVENT["event_id"], allow_llm=False)
    r1 = (await client.post("/v1/insights/generate", json=req)).json()
    n_steps = len(backend)
    r2 = (await client.post("/v1/insights/generate", json=req)).json()
    assert r1["status"] == "completed" and r1 == r2
    assert len(backend) == n_steps  # second call served from the idempotency cache


async def test_generate_validation_and_auth(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert (await client.post("/v1/insights/generate", json={"request_id": "x"})).status_code == 422
    monkeypatch.setenv("ML_SERVICE_TOKEN", "secret")
    from alfa_ml.config import get_settings

    get_settings.cache_clear()
    assert (await client.post("/v1/insights/generate", json={})).status_code == 401


@pytest.mark.parametrize("with_llm", [False, True])
async def test_chat_sse_passes_backend_check(backend, client, llm_script: LLMScript, with_llm: bool) -> None:  # type: ignore[no-untyped-def]
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    try:
        from app.domain.chat_validator import check_chat_final
    except Exception as exc:
        pytest.skip(str(exc))
    if with_llm:
        llm_script.handlers = {
            "chat": {
                "answer": "Если ставка останется 14%, платежи по плавающему кредиту не снизятся.",
                "claims": [
                    {
                        "text": "Банк ждёт ставку 14% до конца года.",
                        "kind": "bank_analytics",
                        "confidence": 0.9,
                        "sources": ["qa1"],
                    },
                    {"text": "Рубль вырастет вдвое.", "kind": "fact", "sources": ["zz"]},
                ],
                "missing_data": ["сумма плавающего долга"],
            }
        }
    gen = generate_request(RATE_EVENT["event_id"], allow_llm=False)
    insight = (await client.post("/v1/insights/generate", json=gen)).json()["insight"]
    req = {
        "request_id": "chat1",
        "company_id": "U_110108",
        "role": "cfo",
        "insight_id": None,
        "insight": insight,
        "intent": "other_slice",
        "messages": [
            {"role": "user", "content": "А как это повлияет на другое юрлицо группы и на поставщиков?"}
        ],
        "tools": gen["tools"],
    }
    resp = await client.post("/v1/chat", json=req)
    events = parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "step" and kinds[-1] == "done" and "token" in kinds
    final = events[-1][1]
    check = check_chat_final(final)
    assert check.ok, check.problems
    assert "".join(d["delta"] for e, d in events if e == "token") == final["answer"]
    if with_llm:
        assert final["statements"][1]["kind"] == "model_inference"  # unsupported fact downgraded
        assert "сумма плавающего долга" in final["answer"]
    else:
        assert "групп" in final["answer"]


async def test_score_recalibrates_weak_matches(client) -> None:  # type: ignore[no-untyped-def]
    req = {
        "request_id": "s1",
        "items": [
            {
                "company_id": "U_1",
                "event_id": RATE_EVENT["event_id"],
                "baseline": {"score": 0.4, "breakdown": {"regulation": 0.3, "recency": 0.1}},
            },
            {
                "company_id": "U_2",
                "event_id": RATE_EVENT["event_id"],
                "baseline": {"score": 0.8, "breakdown": {"rate": 0.6, "industry": 0.1, "recency": 0.1}},
            },
        ],
    }
    scores = (await client.post("/v1/score", json=req)).json()["scores"]
    assert scores[0]["score"] < 0.4 and scores[1]["score"] == 0.8


def test_counterparty_strength_calibration() -> None:
    assert counterparty_strength(0.0035) == 0.0  # 0.35% of payments is noise
    assert 0.2 <= counterparty_strength(0.05) < 0.5
    assert counterparty_strength(0.3) == 1.0
