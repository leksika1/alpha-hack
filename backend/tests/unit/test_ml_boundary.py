"""Failures and retries at the backend ↔ ML boundary must finish as recorded outcomes."""

import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.core.security import decode_token
from app.domain.contracts.insight import Role
from app.domain.ports import GenerationOutcome, GenerationRequest
from app.integrations.ml_client import HttpInsightGenerator, MlProtocolError
from app.services import generation, qa


def request() -> GenerationRequest:
    return GenerationRequest("request-1", "U_1", str(uuid.uuid4()), Role.ceo, "run_1")


@pytest.mark.parametrize(
    "content",
    [
        b"upstream returned HTML",
        b"[]",
        b"{}",
        b'{"request_id":"request-1","status":[]}',
        b'{"request_id":"request-2","status":"skipped"}',
        b'{"request_id":"request-1","status":"completed","insight":[]}',
        b'{"request_id":"request-1","status":"skipped","usage":[]}',
        b'{"request_id":"request-1","status":"skipped","skip_reason":{}}',
        b'{"request_id":"request-1","status":"failed","error":{"detail":[]}}',
    ],
)
async def test_malformed_generation_response_becomes_failed_outcome(content: bytes) -> None:
    client = HttpInsightGenerator(
        "http://ml", "token", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    outcome = await client.generate(request())
    assert outcome.status == "failed" and outcome.error.startswith("Invalid ML response:")


async def test_transport_retries_reuse_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if len(bodies) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"request_id": bodies[-1]["request_id"], "status": "skipped"})

    monkeypatch.setattr("app.integrations.ml_client.asyncio.sleep", AsyncMock())
    client = HttpInsightGenerator("http://ml", "token", transport=httpx.MockTransport(handle))
    assert (await client.generate(request())).status == "skipped"
    assert len(bodies) == 2 and bodies[0] == bodies[1]


class Connection:
    def __init__(self) -> None:
        self.execute = AsyncMock()
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(return_value=1)


def test_generation_run_token_covers_all_transport_attempts() -> None:
    settings = Settings(ml_request_timeout_s=60, ml_max_retries=2)
    access = generation.tools_access(settings, "run_1", "U_1")
    claims = decode_token(settings, access["token"], "run")
    assert claims["exp"] - claims["iat"] >= 3 * 60 + 1 + 4 + generation.RUN_TOKEN_GRACE_S


async def test_regeneration_uses_new_ml_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generation, "budget", AsyncMock(return_value=SimpleNamespace(exhausted=False)))
    requests: list[GenerationRequest] = []

    class Skipping:
        name = "skipping"

        async def generate(self, req: GenerationRequest) -> GenerationOutcome:
            requests.append(req)
            return GenerationOutcome(status="skipped", skip_reason="not_relevant")

    candidate = {
        "id": 1,
        "company_id": "U_1",
        "event_id": uuid.uuid4(),
        "role": "ceo",
        "context_version": 1,
        "score": 0.5,
        "score_breakdown": "{}",
        "matched_features": [],
        "scorer": "baseline@1",
    }
    conn = Connection()
    for _ in range(2):
        await generation.generate_one(conn, Settings(), Skipping(), candidate, generation.GenerationStats())
    assert requests[0].run_id != requests[1].run_id
    assert requests[0].request_id != requests[1].request_id


@pytest.mark.parametrize("trailing", [False, True])
async def test_bad_sse_json_raises_protocol_error_with_or_without_final_separator(trailing: bool) -> None:
    async def lines() -> AsyncIterator[str]:
        yield "event: done"
        yield 'data: {"answer":'
        if trailing:
            yield ""

    with pytest.raises(MlProtocolError, match="Invalid JSON"):
        _ = [event async for event in qa._parse_sse(lines())]


async def test_bad_sse_is_recorded_and_returned_as_error() -> None:
    conn = Connection()

    class Acquire:
        async def __aenter__(self) -> Connection:
            return conn

        async def __aexit__(self, *_: Any) -> None:
            return None

    pool = SimpleNamespace(acquire=Acquire)
    settings = Settings(ml_request_timeout_s=10, qa_timeout_s=180)
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text='event: done\ndata: {"answer":')

    client = HttpInsightGenerator("http://ml", "token", timeout_s=10, transport=httpx.MockTransport(handle))
    chunks = [
        chunk
        async for chunk in qa.ask_stream(
            pool, settings, client, uuid.uuid4(), "U_1", "ceo", None, "Вопрос?", "free"
        )
    ]
    assert "event: error" in chunks[-1] and "/invalid-answer" in chunks[-1]
    assert seen[0].extensions["timeout"]["read"] == 180
    claims = decode_token(settings, json.loads(seen[0].content)["tools"]["token"], "run")
    assert claims["exp"] - claims["iat"] >= 240
    writes = conn.execute.call_args_list
    message = next(call.args for call in writes if "'assistant'" in call.args[0])
    assert message[-1] == "error"
    finished = next(call.args for call in writes if "UPDATE core.agent_runs" in call.args[0])
    assert finished[2:4] == ("error", "failed")
