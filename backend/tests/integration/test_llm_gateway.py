"""LLM Gateway on Postgres + Redis: allowlist, masking, cache, budget hard stop (also under concurrency),
usage log, streaming, provider errors, run scoping and rate limiting."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis

from app.core.config import Settings, get_settings
from app.core.pg import pg_connection
from app.core.ratelimit import RateLimiter
from app.core.security import hash_password, issue_run_token
from app.integrations.llm import FakeLlmProvider, LlmProviderError
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

MODEL = "glm-5.3-flash"


class RecordingProvider(FakeLlmProvider):
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.delay = 0.0
        self.fail: LlmProviderError | None = None

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return await super().complete(payload)


@pytest.fixture(scope="module")
async def env(infra: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings().model_copy(
        update={"llm_prices": {MODEL: (100.0, 300.0)}, "llm_model_map": {MODEL: "zai/glm-5.3-flash"}}
    )
    holder = {"settings": settings}
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: holder["settings"]
    provider = RecordingProvider()
    app.state.llm_provider = provider
    async with pg_connection(infra["DATABASE_URL"]) as conn:
        await conn.execute("DELETE FROM core.llm_usage")
        await conn.execute("DELETE FROM core.llm_cache")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            yield {
                "client": client,
                "conn": conn,
                "provider": provider,
                "holder": holder,
                "app": app,
                "service": {"Authorization": f"Bearer {settings.ml_service_token.get_secret_value()}"},
            }


def body(text: str = "Почему ставка важна?", **kw: Any) -> dict[str, Any]:
    return {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": 100, **kw}


async def test_auth_and_models(env: dict[str, Any]) -> None:
    c = env["client"]
    assert (await c.get("/internal/llm/models")).status_code == 401
    bad = await c.get("/internal/llm/models", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.headers["content-type"].startswith("application/problem+json")
    models = (await c.get("/internal/llm/models", headers=env["service"])).json()["data"]
    ids = {m["id"] for m in models}
    assert len(ids) == 6 and MODEL in ids
    assert next(m for m in models if m["id"] == "qwen3-vl-30b-a3b-instruct")["vision"] is True
    assert next(m for m in models if m["id"] == MODEL)["price_rub_per_1m_out"] == 300.0


async def test_model_outside_allowlist_is_refused_and_logged(env: dict[str, Any]) -> None:
    resp = await env["client"].post(
        "/internal/llm/chat/completions", headers=env["service"], json=body(model="gpt-4o")
    )
    assert resp.status_code == 403 and resp.json()["type"].endswith("/model-not-allowed")
    row = await env["conn"].fetchrow(
        "SELECT status, error, cost_rub FROM core.llm_usage ORDER BY id DESC LIMIT 1"
    )
    assert (row["status"], row["error"], float(row["cost_rub"])) == ("refused", "model-not-allowed", 0.0)
    assert not env["provider"].payloads


async def test_masking_usage_log_and_cache(env: dict[str, Any]) -> None:
    c, conn, provider = env["client"], env["conn"], env["provider"]
    text = "Директор F_123456, почта boss@firm.ru, тел. +7 912 345-67-89: что со ставкой?"
    first = await c.post(
        "/internal/llm/chat/completions",
        headers={**env["service"], "X-Purpose": "generation"},
        json=body(text, temperature=0),
    )
    assert first.status_code == 200, first.text
    assert first.headers["x-cache"] == "miss" and float(first.headers["x-budget-remaining-rub"]) < 450
    sent = provider.payloads[-1]
    assert sent["model"] == "zai/glm-5.3-flash"  # alias -> provider id
    sent_text = sent["messages"][0]["content"]
    assert "F_123456" not in sent_text and "boss@firm.ru" not in sent_text and "[PHONE]" in sent_text
    assert first.json()["model"] == MODEL
    row = await conn.fetchrow("SELECT * FROM core.llm_usage ORDER BY id DESC LIMIT 1")
    assert row["status"] == "ok" and row["purpose"] == "generation" and row["caller"] == "service"
    assert row["tokens_in"] > 0 and float(row["cost_rub"]) > 0 and "F_123456" not in row["prompt_preview"]
    assert '"person_id": 1' in row["masked"]

    calls = len(provider.payloads)
    second = await c.post(
        "/internal/llm/chat/completions", headers=env["service"], json=body(text, temperature=0)
    )
    assert second.headers["x-cache"] == "hit" and second.json() == first.json()
    assert len(provider.payloads) == calls  # served from the cache
    cached = await conn.fetchrow(
        "SELECT status, cache_hit, cost_rub FROM core.llm_usage ORDER BY id DESC LIMIT 1"
    )
    assert cached["status"] == "cached" and cached["cache_hit"] and float(cached["cost_rub"]) == 0
    hot = await c.post(
        "/internal/llm/chat/completions", headers=env["service"], json=body(text, temperature=0.7)
    )
    assert hot.headers["x-cache"] == "miss" and len(provider.payloads) == calls + 1


async def test_run_token_scoping(env: dict[str, Any]) -> None:
    c, settings = env["client"], env["holder"]["settings"]
    token = issue_run_token(settings, "run_llm_test", "U_700001", 60)
    auth = {"Authorization": f"Bearer {token}"}
    wrong = await c.post(
        "/internal/llm/chat/completions", headers={**auth, "X-Run-Id": "run_other"}, json=body()
    )
    assert wrong.status_code == 403
    ok = await c.post(
        "/internal/llm/chat/completions", headers={**auth, "X-Run-Id": "run_llm_test"}, json=body()
    )
    assert ok.status_code == 200
    row = await env["conn"].fetchrow("SELECT run_id, caller FROM core.llm_usage ORDER BY id DESC LIMIT 1")
    assert (row["run_id"], row["caller"]) == ("run_llm_test", "run")


async def test_provider_errors_are_problems_and_logged(env: dict[str, Any]) -> None:
    provider = env["provider"]
    provider.fail = LlmProviderError(504, "llm-timeout", "slow upstream")
    try:
        resp = await env["client"].post(
            "/internal/llm/chat/completions", headers=env["service"], json=body("x")
        )
    finally:
        provider.fail = None
    assert resp.status_code == 504 and resp.json()["type"].endswith("/llm-timeout")
    row = await env["conn"].fetchrow(
        "SELECT status, error, cost_rub FROM core.llm_usage ORDER BY id DESC LIMIT 1"
    )
    assert (row["status"], row["error"], float(row["cost_rub"])) == ("error", "llm-timeout", 0.0)


async def test_streaming_relays_sse_and_records_usage(env: dict[str, Any]) -> None:
    async with env["client"].stream(
        "POST", "/internal/llm/chat/completions", headers=env["service"], json=body("Поток", stream=True)
    ) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        lines = [line async for line in resp.aiter_lines() if line]
    assert lines[-1] == "data: [DONE]" and any('"delta"' in x for x in lines)
    row = await env["conn"].fetchrow(
        "SELECT status, stream, tokens_out, cost_rub FROM core.llm_usage ORDER BY id DESC LIMIT 1"
    )
    assert row["status"] == "ok" and row["stream"] and row["tokens_out"] > 0 and float(row["cost_rub"]) > 0


async def test_budget_hard_stop_even_under_concurrency(env: dict[str, Any]) -> None:
    c, conn, holder, provider = env["client"], env["conn"], env["holder"], env["provider"]
    base: Settings = holder["settings"]
    spent = float(await conn.fetchval("SELECT coalesce(sum(cost_rub), 0) FROM core.llm_usage"))
    # each call reserves ~100 out tokens * 300 RUB/1M = 0.03 RUB (+ input): room for about 5 calls
    holder["settings"] = base.model_copy(update={"llm_daily_budget_rub": spent + 0.16})
    provider.delay = 0.2
    try:
        results = await asyncio.gather(
            *[
                c.post("/internal/llm/chat/completions", headers=env["service"], json=body(f"вопрос {i}"))
                for i in range(12)
            ]
        )
    finally:
        provider.delay = 0.0
    codes = [r.status_code for r in results]
    assert 0 < codes.count(200) <= 5 and codes.count(429) >= 7, codes
    refused = next(r for r in results if r.status_code == 429).json()
    assert refused["type"].endswith("/budget-exhausted") and "remaining_rub" in refused
    total = float(await conn.fetchval("SELECT sum(cost_rub) FROM core.llm_usage"))
    assert total <= spent + 0.16
    budget = (await c.get("/internal/llm/budget", headers=env["service"])).json()
    assert budget["limit_rub"] == pytest.approx(spent + 0.16) and budget["spent_rub"] == pytest.approx(
        total, abs=1e-6
    )
    holder["settings"] = base.model_copy(update={"llm_daily_budget_rub": 0.0})
    budget = (await c.get("/internal/llm/budget", headers=env["service"])).json()
    assert budget["exhausted"] is True
    holder["settings"] = base


async def test_questions_cannot_eat_the_generation_budget(env: dict[str, Any]) -> None:
    c, conn, holder = env["client"], env["conn"], env["holder"]
    base: Settings = holder["settings"]
    for run_id, kind in (("run_chat_share_qa", "qa"), ("run_chat_share_gen", "insight")):
        await conn.execute(
            """INSERT INTO core.agent_runs (id, kind, request_id, company_id, generator)
               VALUES ($1, $2, 'chat-share', 'U_700001', 'test') ON CONFLICT (id) DO NOTHING""",
            run_id,
            kind,
        )
    spent = float(await conn.fetchval("SELECT coalesce(sum(cost_rub), 0) FROM core.llm_usage"))
    # plenty for the day, but questions get 0.01 RUB: less than one call reserves (~0.03 RUB)
    limit = spent + 10.0
    holder["settings"] = base.model_copy(
        update={"llm_daily_budget_rub": limit, "llm_chat_budget_share": 0.01 / limit}
    )

    async def call(run_id: str, text: str) -> httpx.Response:
        token = issue_run_token(holder["settings"], run_id, "U_700001", 60)
        headers = {"Authorization": f"Bearer {token}", "X-Run-Id": run_id}
        return await c.post("/internal/llm/chat/completions", headers=headers, json=body(text))

    try:
        refused = await call("run_chat_share_qa", "вопрос 1")
        assert refused.status_code == 429 and refused.json()["type"].endswith("/budget-exhausted")
        assert "questions share" in refused.json()["detail"]
        assert (await call("run_chat_share_gen", "генерация")).status_code == 200  # the feed keeps its budget
        holder["settings"] = holder["settings"].model_copy(update={"llm_chat_budget_share": 0.3})
        assert (await call("run_chat_share_qa", "вопрос 2")).status_code == 200
        from app.services.llm_gateway import usage_summary

        summary = await usage_summary(conn, holder["settings"])
        assert 0 < summary["budget"]["chat_spent_rub"] <= summary["budget"]["chat_limit_rub"]
    finally:
        holder["settings"] = base


async def test_admin_llm_usage(env: dict[str, Any]) -> None:
    c, conn = env["client"], env["conn"]
    for login, role in (("llm_admin", "admin"), ("llm_user", "user")):
        await conn.execute(
            """INSERT INTO core.users (login, password_hash, display_name, platform_role)
               VALUES ($1::text, $2, $1::text, $3) ON CONFLICT (login) DO NOTHING""",
            login,
            hash_password("secret-pass-1"),
            role,
        )

    async def auth(login: str) -> dict[str, str]:
        resp = await c.post("/api/v1/auth/login", json={"login": login, "password": "secret-pass-1"})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    data = (await c.get("/api/v1/admin/llm-usage", headers=await auth("llm_admin"))).json()
    statuses = {r["status"] for r in data["rows"]}
    assert {"ok", "cached", "refused", "error"} <= statuses and data["budget"]["limit_rub"] > 0
    errors = {r["error"] for r in data["rows"] if r["status"] == "error"}
    assert "llm-timeout" in errors  # the reason of a failed call is visible to the admin
    assert (await c.get("/api/v1/admin/llm-usage", headers=await auth("llm_user"))).status_code == 403


async def test_abandoned_reservation_stops_holding_the_budget(env: dict[str, Any]) -> None:
    from app.services import llm_gateway

    conn, settings = env["conn"], env["holder"]["settings"]
    usage_id = await conn.fetchval(
        """INSERT INTO core.llm_usage (day, caller, purpose, model, status, tokens_in, tokens_out,
               estimated_cost_rub, created_at)
           VALUES ($1, 'run', 'planning', $2, 'pending', 5000, 1000, 1.5, now() - interval '1 hour')
           RETURNING id""",
        llm_gateway._today(settings),
        MODEL,
    )
    before = await llm_gateway.budget(conn, settings)
    assert await llm_gateway.close_abandoned(conn, settings) >= 1
    after = await llm_gateway.budget(conn, settings)
    row = await conn.fetchrow(
        "SELECT status, error, estimated_cost_rub FROM core.llm_usage WHERE id = $1", usage_id
    )
    assert row["status"] == "error" and row["error"] == "abandoned"
    assert (
        after.spent_rub == pytest.approx(before.spent_rub - 1.5)
        and after.tokens_in == before.tokens_in - 5000
    )


async def test_cancelled_call_releases_its_reservation(env: dict[str, Any]) -> None:
    from app.services import llm_gateway

    conn, settings, provider = env["conn"], env["holder"]["settings"], env["provider"]
    provider.delay = 5.0
    try:
        task = asyncio.create_task(
            llm_gateway.chat(
                conn,
                settings,
                provider,
                llm_gateway.Caller("service"),
                body("отмена", temperature=0.7),
                "other",
            )
        )
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        provider.delay = 0.0
    row = await conn.fetchrow("SELECT status, error FROM core.llm_usage ORDER BY id DESC LIMIT 1")
    assert (row["status"], row["error"]) == ("error", "cancelled")


async def test_rate_limit_token_bucket(env: dict[str, Any]) -> None:
    c, holder = env["client"], env["holder"]
    app = env["app"]
    base: Settings = holder["settings"]
    middleware_settings = next(m.kwargs["settings"] for m in app.user_middleware if "settings" in m.kwargs)
    old = middleware_settings.rate_limit_internal_per_min
    object.__setattr__(middleware_settings, "rate_limit_internal_per_min", 3)
    try:
        headers = {"Authorization": "Bearer rate-limit-probe"}
        codes = [(await c.get("/internal/llm/models", headers=headers)).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
        limited = await c.get("/internal/llm/models", headers=headers)
        assert int(limited.headers["retry-after"]) >= 1 and limited.json()["type"].endswith("/rate-limited")
        other = await c.get("/internal/llm/models", headers=env["service"])
        assert other.status_code == 200  # separate bucket per caller
        broken = RateLimiter(aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2))
        good = app.state.rate_limiter
        app.state.rate_limiter = broken
        try:
            assert (await c.get("/internal/llm/models", headers=headers)).status_code == 401  # fail open
        finally:
            app.state.rate_limiter = good
    finally:
        object.__setattr__(middleware_settings, "rate_limit_internal_per_min", old)
    holder["settings"] = base
