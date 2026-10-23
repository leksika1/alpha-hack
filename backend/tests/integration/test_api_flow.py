"""End-to-end flow in-process: real Postgres, backend API and ml-stub over ASGI (the stub calls the backend
Tools API with a run-scoped token exactly as in docker-compose). Covers SPEC §11.2 scenarios 1-8, 10, 11."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import ml_stub.main as stub
from app.core.config import get_settings
from app.core.pg import pg_connection
from app.core.security import issue_run_token
from app.domain.scoring import profile_from_context, score_candidate
from app.integrations.generators import get_generator
from app.integrations.llm import FakeLlmProvider
from app.main import create_app
from app.services.analytics_run import run_analytics_ingest
from app.services.generation import generate_pending
from app.services.ingest_companies import ingest_companies
from app.services.matching import affected_companies, event_profile, load_profiles, match_events
from app.services.metrics import snapshot_day
from app.services.notifications import create_digests, notify_insight
from app.services.seed import ADMIN_PASSWORD, DEMO_PASSWORD, seed_demo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

ROOT = Path(__file__).resolve().parents[2]
SYNTH = ROOT / "data" / "synthetic"
REF = ROOT / "data" / "reference"
METALS, RETAIL, LOGISTICS, AGRO = "U_700001", "U_700002", "U_700003", "U_700004"


@pytest.fixture(scope="module")
async def env(infra: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings().model_copy(update={"news_fixtures_enabled": True})
    gen_settings = settings.model_copy(update={"internal_api_url": "http://backend"})
    app = create_app()
    app.state.llm_provider = FakeLlmProvider()  # gateway calls in the test act as the agent's
    stub.TOOLS_TRANSPORT = httpx.ASGITransport(app=app)
    app.state.ml_transport = httpx.ASGITransport(app=stub.app)  # questions -> ml-stub in-process
    generator = get_generator(gen_settings, transport=httpx.ASGITransport(app=stub.app))
    stats: list[dict[str, int]] = []

    async def enqueue(company_ids: list[str]) -> None:
        async with pg_connection(infra["DATABASE_URL"]) as c:
            stats.append((await generate_pending(c, gen_settings, generator, company_ids)).as_dict())

    app.state.enqueue = enqueue
    async with pg_connection(infra["DATABASE_URL"]) as conn:
        await ingest_companies(conn, SYNTH, REF)
        await run_analytics_ingest(conn, settings, SYNTH / "analytics", "synthetic")
        summary = await seed_demo(conn, settings, SYNTH, REF, ROOT)
    async with app.router.lifespan_context(app):
        await enqueue(summary["demo_companies"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield {
                "client": client,
                "url": infra["DATABASE_URL"],
                "summary": summary,
                "stats": stats,
                "app": app,
                "settings": settings,
            }
    stub.TOOLS_TRANSPORT = None


async def login(client: httpx.AsyncClient, user: str = "demo") -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"login": user, "password": DEMO_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def feed(client: httpx.AsyncClient, h: dict[str, str], company: str, **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/v1/feed", headers=h, params={"company_id": company, **params})
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def test_seed_and_pregeneration(env: dict[str, Any]) -> None:
    s = env["summary"]
    assert s["demo_companies"][:4] == [METALS, RETAIL, LOGISTICS, AGRO]
    assert s["events"] >= 14 and s["candidates"] > 0
    first = env["stats"][0]
    assert first["generated"] > 0 and first["failed"] == 0 and first["rejected"] == 0, first


async def test_login_me_and_profile(env: dict[str, Any]) -> None:
    c = env["client"]
    bad = await c.post("/api/v1/auth/login", json={"login": "demo", "password": "nope"})
    assert bad.status_code == 401 and bad.headers["content-type"].startswith("application/problem+json")
    h = await login(c)
    me = (await c.get("/api/v1/me", headers=h)).json()
    roles = {x["company_id"]: x["role"] for x in me["companies"]}
    assert roles == {METALS: "ceo", RETAIL: "cfo", LOGISTICS: "owner"}
    prof = (await c.get(f"/api/v1/companies/{METALS}/profile", headers=h)).json()
    sections = {s["key"]: s for s in prof["sections"]}
    assert {"identity", "products", "sensitivities", "counterparties"} <= set(sections)
    sources = {f["source"] for s in prof["sections"] for f in s["features"]}
    assert {"csv", "derived"} <= sources
    assert prof["completeness"]["score"] == 1.0
    graph = (await c.get(f"/api/v1/companies/{METALS}/graph", headers=h)).json()
    assert any(e["is_hypothesis"] and 0 < e["confidence"] < 1 for e in graph["edges"])
    assert graph["group"]["size"] == 6


async def test_feed_sorted_valid_and_personalized(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    metals = await feed(c, h, METALS)
    retail = await feed(c, h, RETAIL)
    assert metals["items"] and retail["items"]
    scores = [i["relevance_score"] for i in metals["items"]]
    assert scores == sorted(scores, reverse=True)
    m_events = [i["event_id"] for i in metals["items"]]
    r_events = [i["event_id"] for i in retail["items"]]
    assert m_events != r_events  # same events, different companies -> different feed
    for card in metals["items"][:5]:
        full = (await c.get(f"/api/v1/insights/{card['insight_id']}", headers=h)).json()["insight"]
        assert any(d["code"] == "not_investment_advice" for d in full["disclaimers"])
        assert full["sources"] and all(
            s["kind"] in {"fact", "bank_analytics", "model_inference", "hypothesis"}
            for s in full["statements"]
        )
        assert all(r["feature_refs"] for r in full["why_it_matters"])


async def test_pagination_cursor(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    page1 = await feed(c, h, METALS, limit=2)
    assert page1["next_cursor"]
    page2 = await feed(c, h, METALS, limit=2, cursor=page1["next_cursor"])
    ids1 = {i["insight_id"] for i in page1["items"]}
    assert ids1.isdisjoint({i["insight_id"] for i in page2["items"]})
    bad = await c.get("/api/v1/feed", headers=h, params={"company_id": METALS, "cursor": "garbage"})
    assert bad.status_code == 422


async def test_role_switch_changes_variant(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    ceo = await feed(c, h, METALS)
    resp = await c.put(f"/api/v1/me/companies/{METALS}/role", headers=h, json={"role": "cfo"})
    assert resp.status_code == 200 and resp.json()["regeneration"] == "queued", resp.text
    cfo = await feed(c, h, METALS)
    assert cfo["role"] == "cfo" and all(i["role_match"] for i in cfo["items"])
    ceo_view = [(i["event_id"], i["title"]) for i in ceo["items"]]
    cfo_view = [(i["event_id"], i["title"]) for i in cfo["items"]]
    assert ceo_view != cfo_view
    await c.put(f"/api/v1/me/companies/{METALS}/role", headers=h, json={"role": "ceo"})


async def test_trace_has_features_sources_and_agent_steps(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    card = (await feed(c, h, METALS))["items"][0]
    tr = (await c.get(f"/api/v1/insights/{card['insight_id']}/trace", headers=h)).json()
    assert tr["company_features"] and all(v is not None for v in tr["company_features"].values())
    names = [s["name"] for s in tr["steps"]]
    assert "plan_insight" in names and "backend.validate" in names and "insight_ready" in names
    assert {"get_company_context", "get_event", "search_bank_analytics"} <= {
        t["tool"] for t in tr["tool_calls"]
    }
    assert tr["sources"] and tr["candidate"]["breakdown"] and tr["validation"]["status"] == "passed"


async def test_hide_feedback_source_and_cta(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    items = (await feed(c, h, LOGISTICS))["items"]
    target = items[0]
    iid = target["insight_id"]
    assert (
        await c.post(f"/api/v1/insights/{iid}/state", headers=h, json={"action": "hide"})
    ).status_code == 204
    assert iid not in {i["insight_id"] for i in (await feed(c, h, LOGISTICS))["items"]}
    assert iid in {i["insight_id"] for i in (await feed(c, h, LOGISTICS, filter="hidden"))["items"]}
    fb = await c.post(
        f"/api/v1/insights/{iid}/feedback", headers=h, json={"useful": False, "reason": "irrelevant"}
    )
    assert fb.status_code == 201
    src = await c.post(f"/api/v1/insights/{iid}/source-click", headers=h, json={"source_id": "s1"})
    assert src.status_code == 200 and src.json()["url"].startswith("/api/v1/news/")
    news = await c.get(src.json()["url"], headers=h)
    assert news.status_code == 200 and news.json()["is_fixture"] is True
    cta = await c.post(
        f"/api/v1/insights/{iid}/actions",
        headers=h,
        json={"type": "callback", "product_id": "leasing", "preferred_time": "завтра утром"},
    )
    assert cta.status_code == 201 and cta.json()["external_ref"].startswith("CRM-")
    bad = await c.post(
        f"/api/v1/insights/{iid}/actions", headers=h, json={"type": "callback", "product_id": "nope"}
    )
    assert bad.status_code == 422
    async with pg_connection(env["url"]) as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM core.crm_requests WHERE insight_id = $1::uuid", iid)
            == 1
        )
        assert (
            await conn.fetchval("SELECT reason FROM core.feedback WHERE insight_id = $1::uuid", iid)
            == "irrelevant"
        )
        acts = {r["action_type"] for r in await conn.fetch("SELECT action_type FROM core.user_actions")}
        assert {"source_click", "cta"} <= acts


async def test_feed_summary_widget(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    s = (await c.get("/api/v1/feed/summary", headers=h)).json()
    assert s["unread"] > 0 and {x["company_id"] for x in s["companies"]} == {METALS, RETAIL, LOGISTICS}
    top = next(x for x in s["companies"] if x["company_id"] == RETAIL)["top"]
    assert (
        await c.post(f"/api/v1/insights/{top['insight_id']}/state", headers=h, json={"action": "open"})
    ).status_code == 204
    s2 = (await c.get("/api/v1/feed/summary", headers=h)).json()
    assert s2["unread"] == s["unread"] - 1


async def test_profile_update_changes_relevance(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    before = {i["event_id"]: i for i in (await feed(c, h, RETAIL))["items"]}
    resp = await c.patch(
        f"/api/v1/companies/{RETAIL}/profile",
        headers=h,
        json={"currencies": ["CNY"], "credit_plans": "Кредит 300 млн на новые магазины в 2027"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["user_provided"]["currencies"] == ["CNY"] and body["regeneration"] == "queued"
    after = {i["event_id"]: i for i in (await feed(c, h, RETAIL))["items"]}
    assert set(after) - set(before), "new events must become relevant (key rate, yuan)"


async def test_access_control_and_tools_scope(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    assert (await c.get(f"/api/v1/companies/{AGRO}/profile", headers=h)).status_code == 403  # demo2's company
    assert (await c.get("/api/v1/feed", params={"company_id": AGRO}, headers=h)).status_code == 403
    assert (await c.get("/api/v1/me")).status_code == 401
    h2 = await login(c, "demo2")
    card = (await feed(c, h, METALS))["items"][0]
    assert (await c.get(f"/api/v1/insights/{card['insight_id']}", headers=h2)).status_code == 403
    # Tools API: no token -> 401; token of another company -> 403; mismatched run id -> 403
    tools = "/internal/tools/get_company_context"
    assert (await c.post(tools, json={"company_id": METALS})).status_code == 401
    token = issue_run_token(env["settings"], "run_scope_test", AGRO, 60)
    hdr = {"Authorization": f"Bearer {token}", "X-Run-Id": "run_scope_test"}
    resp = await c.post(tools, headers=hdr, json={"company_id": METALS})
    assert resp.status_code == 403 and resp.json()["type"].endswith("out-of-scope")
    assert (await c.post(tools, headers={**hdr, "X-Run-Id": "other"}, json={})).status_code == 403
    async with pg_connection(env["url"]) as conn:
        audited = await conn.fetchval(
            "SELECT count(*) FROM core.tool_audit_log WHERE run_id = 'run_scope_test' AND status_code = 403"
        )
        assert audited == 1


async def test_prompt_injection_news_is_flagged_and_contained(env: dict[str, Any]) -> None:
    async with pg_connection(env["url"]) as conn:
        flags = await conn.fetchval("SELECT flags FROM core.news_items WHERE external_id = 'fx-015'")
        assert "suspected_prompt_injection" in flags
        payloads = await conn.fetch(
            """SELECT i.payload FROM core.insights i JOIN core.event_news en ON en.event_id = i.event_id
               JOIN core.news_items n ON n.id = en.news_id WHERE n.external_id = 'fx-015'"""
        )
    for p in payloads:
        doc = json.loads(p["payload"])
        assert "Игнорируй" not in doc["summary"] and doc["impact"]["level"] != "critical"


async def test_no_rejected_insights_and_runs_closed(env: dict[str, Any]) -> None:
    async with pg_connection(env["url"]) as conn:
        assert await conn.fetchval("SELECT count(*) FROM core.insights_rejected") == 0
        open_runs = await conn.fetchval("SELECT count(*) FROM core.agent_runs WHERE finished_at IS NULL")
        assert open_runs == 0


async def test_bank_analytics_source_links_to_pdf_page(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    for card in (await feed(c, h, METALS))["items"]:
        full = (await c.get(f"/api/v1/insights/{card['insight_id']}", headers=h)).json()["insight"]
        src = next((s for s in full["sources"] if s["type"] == "bank_analytics"), None)
        if src:
            break
    else:
        pytest.fail("no insight cites bank analytics")
    assert src["ref_id"] and src["page"] >= 1 and "синтетика" in src["publisher"].lower()
    resp = await c.post(
        f"/api/v1/insights/{card['insight_id']}/source-click", headers=h, json={"source_id": src["id"]}
    )
    body = resp.json()
    assert resp.status_code == 200 and body["url"].endswith(f"#page={src['page']}"), body
    assert "не аналитика Альфа-Банка" in body["note"]
    docs = (await c.get("/api/v1/analytics/documents", headers=h)).json()
    assert {d["file_name"] for d in docs} >= {"demo_key_rate_outlook.pdf", "demo_steel_market.pdf"}
    doc_id = docs[0]["document_id"]
    dl = await c.get(f"/api/v1/analytics/documents/{doc_id}/download", headers=h, params={"page": 1})
    assert dl.status_code == 200 and dl.json()["url"].endswith("#page=1")
    assert (
        await c.get(f"/api/v1/analytics/documents/{doc_id}/download", headers=h, params={"page": 999})
    ).status_code == 422
    assert (await c.get("/api/v1/analytics/documents")).status_code == 401


async def test_agent_llm_call_through_gateway_is_traced(env: dict[str, Any]) -> None:
    """The agent (ML) calls the gateway with its run token; the call shows up in the insight trace."""
    c, settings = env["client"], env["settings"]
    h = await login(c)
    card = (await feed(c, h, METALS))["items"][0]
    run_id = (await c.get(f"/api/v1/insights/{card['insight_id']}/trace", headers=h)).json()["trace_id"]
    token = issue_run_token(settings, run_id, METALS, 60)
    resp = await c.post(
        "/internal/llm/chat/completions",
        headers={"Authorization": f"Bearer {token}", "X-Run-Id": run_id, "X-Purpose": "generation"},
        json={
            "model": "glm-5.3-flash",
            "messages": [{"role": "user", "content": "тест F_123"}],
            "max_tokens": 20,
        },
    )
    assert resp.status_code == 200, resp.text
    tr = (await c.get(f"/api/v1/insights/{card['insight_id']}/trace", headers=h)).json()
    assert [x["status"] for x in tr["llm_calls"]] == ["ok"] and tr["llm_calls"][0]["model"] == "glm-5.3-flash"
    assert tr["llm_calls"][0]["purpose"] == "generation" and tr["llm_calls"][0]["cost_rub"] > 0
    assert "F_123" not in tr["llm_calls"][0]["prompt_preview"]


async def test_exhausted_budget_is_passed_to_the_agent(env: dict[str, Any]) -> None:
    """NFR-9: with the daily budget spent the backend asks the agent to work without LLM (allow_llm=false)."""
    settings = env["settings"].model_copy(
        update={"llm_daily_budget_rub": 0.0, "internal_api_url": "http://backend"}
    )
    inner = get_generator(settings, transport=httpx.ASGITransport(app=stub.app))
    seen: list[bool] = []

    class Recording:
        name = inner.name

        async def generate(self, request: Any) -> Any:
            seen.append(request.allow_llm)
            return await inner.generate(request)

    async with pg_connection(env["url"]) as conn:
        await conn.execute(
            "UPDATE core.candidate_matches SET status = 'pending' WHERE company_id = $1 AND role = 'ceo'",
            METALS,
        )
        stats = await generate_pending(conn, settings, Recording(), [METALS])
    assert stats.generated > 0 and seen and not any(seen)


async def test_prefilter_is_a_superset_of_scored_candidates(env: dict[str, Any]) -> None:
    settings = env["settings"]
    async with pg_connection(env["url"]) as conn:
        contexts = await conn.fetch(
            "SELECT company_id, features FROM core.company_context WHERE client_type = ANY($1::text[])",
            settings.eligible_client_types,
        )
        profiles = [profile_from_context(r["company_id"], json.loads(r["features"])) for r in contexts]
        trimmed = await load_profiles(conn, [r["company_id"] for r in contexts])
        assert {p.company_id: p for p in profiles} == {cid: prof for cid, (prof, _) in trimmed.items()}
        events = await conn.fetch("SELECT * FROM core.events")
        now = max(e["last_seen_at"] for e in events)
        checked = 0
        for event in events:
            affected = set(await affected_companies(conn, event, settings.eligible_client_types))
            ev = event_profile(event)
            for prof in profiles:
                best = max(
                    score_candidate(prof, ev, role, now).score
                    for role in ("owner", "ceo", "cfo", "chief_accountant", "treasurer", "other")
                )
                if best >= settings.match_min_score:
                    checked += 1
                    assert prof.company_id in affected, (str(event["id"]), prof.company_id, best)
        assert checked > 0
        assert len(affected) < len(profiles) or len(events) == 0


async def test_full_base_scope_scores_companies_without_users(env: dict[str, Any]) -> None:
    settings = env["settings"]
    async with pg_connection(env["url"]) as conn:
        event_ids = [str(r["id"]) for r in await conn.fetch("SELECT id FROM core.events")]
        users_only = await conn.fetchval("SELECT count(DISTINCT company_id) FROM core.candidate_matches")
        stats = await match_events(
            conn, event_ids, "all", settings.eligible_client_types, settings.match_min_score
        )
        assert stats.candidates > 0
        rows = await conn.fetch(
            """SELECT DISTINCT m.company_id, m.role FROM core.candidate_matches m
               WHERE NOT EXISTS (SELECT 1 FROM core.user_company_access a
                                 WHERE a.company_id = m.company_id)"""
        )
        assert rows and {r["role"] for r in rows} == {"owner"}
        assert (
            await conn.fetchval("SELECT count(DISTINCT company_id) FROM core.candidate_matches") > users_only
        )


def sse_events(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = [x for x in block.splitlines() if x and not x.startswith(":")]
        event = next((x[6:].strip() for x in lines if x.startswith("event:")), None)
        data = "\n".join(x[5:].strip() for x in lines if x.startswith("data:"))
        if event:
            out.append((event, json.loads(data) if data else None))
    return out


async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"login": "admin", "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_cards_explain_priority_and_why_for_you(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    summary = (await c.get("/api/v1/feed/summary", headers=h)).json()
    top = summary["companies"][0]["top"]
    assert top["why_for_you"] and top["priority_reasons"], top
    assert "updated" in top["state"]


async def test_urgent_insights_notify_once_and_stream(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    data = (await c.get("/api/v1/notifications", headers=h)).json()
    assert data["items"] and {n["kind"] for n in data["items"]} == {"urgent_insight"}, data
    unread = data["unread"]
    async with pg_connection(env["url"]) as conn:
        insight_id = data["items"][0]["insight_id"]
        assert await notify_insight(conn, insight_id) == 0  # dedup per user and event
        last = await conn.fetchval("SELECT max(id) FROM core.notifications")
        await conn.execute("UPDATE core.insights SET created_at = now()")
        assert await create_digests(conn, "day", datetime.now(UTC)) > 0
        assert await create_digests(conn, "day", datetime.now(UTC)) == 0
    first = data["items"][0]
    assert (await c.post(f"/api/v1/notifications/{first['id']}/read", headers=h)).status_code == 204
    after = (await c.get("/api/v1/notifications", headers=h, params={"unread_only": True})).json()
    assert after["unread"] >= unread  # + digest, - the one read
    assert all(n["id"] != first["id"] for n in after["items"])
    resp = await c.get(
        "/api/v1/notifications/stream", headers={**h, "Last-Event-ID": str(last)}, params={"max_s": 1}
    )
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    events = sse_events(resp.text)
    assert events[0][0] == "ready"
    digests = [d for e, d in events if e == "notification" and d["kind"] == "digest"]
    assert digests and digests[0]["title"].startswith("Дайджест за день")
    assert (await c.post("/api/v1/notifications/read-all", headers=h)).status_code == 204
    assert (await c.get("/api/v1/notifications", headers=h)).json()["unread"] == 0


async def test_digest(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    d = (await c.get("/api/v1/digest", headers=h, params={"company_id": METALS, "period": "week"})).json()
    assert d["counts"]["new"] > 0 and 0 < len(d["top"]) <= 5
    assert d["counts"]["risks"] + d["counts"]["opportunities"] >= 1
    assert all(step["text"] and step["insight_title"] for step in d["next_steps"])
    forbidden = await c.get("/api/v1/digest", headers=h, params={"company_id": AGRO})
    assert forbidden.status_code == 403


async def test_question_to_insight_streams_checked_answer_and_keeps_history(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    card = (await feed(c, h, METALS))["items"][0]
    resp = await c.post(
        f"/api/v1/insights/{card['insight_id']}/ask",
        headers=h,
        json={"question": "Что будет с нашим кредитом, если ставка не снизится?", "intent": "clarify"},
    )
    assert resp.status_code == 200, resp.text
    events = sse_events(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "run" and names[-1] == "done" and "token" in names, names
    final = events[-1][1]
    assert final["answer"] and any(d["code"] == "not_investment_advice" for d in final["disclaimers"])
    run_id = events[0][1]["run_id"]
    history = (await c.get(f"/api/v1/insights/{card['insight_id']}/questions", headers=h)).json()
    assert [m["role"] for m in history[-2:]] == ["user", "assistant"] and history[-1]["status"] == "ok"
    async with pg_connection(env["url"]) as conn:
        run = await conn.fetchrow("SELECT kind, status, outcome FROM core.agent_runs WHERE id = $1", run_id)
        tools = await conn.fetchval("SELECT count(*) FROM core.tool_audit_log WHERE run_id = $1", run_id)
    assert (run["kind"], run["status"], run["outcome"]) == ("chat", "ok", "answered") and tools > 0
    company = await c.post(
        "/api/v1/ask", headers=h, json={"company_id": METALS, "question": "Что с ключевой ставкой?"}
    )
    assert sse_events(company.text)[-1][0] == "done"
    assert len((await c.get("/api/v1/ask/history", headers=h, params={"company_id": METALS})).json()) == 2


async def test_question_when_ml_is_down_is_an_error_event(env: dict[str, Any]) -> None:
    c, app_transport = env["client"], env["app"].state.ml_transport
    env["app"].state.ml_transport = httpx.MockTransport(lambda _: httpx.Response(503))
    try:
        h = await login(c)
        resp = await c.post("/api/v1/ask", headers=h, json={"company_id": METALS, "question": "Курс юаня?"})
    finally:
        env["app"].state.ml_transport = app_transport
    events = sse_events(resp.text)
    assert events[-1][0] == "error" and events[-1][1]["type"].endswith("/ml-unavailable")
    history = (await c.get("/api/v1/ask/history", headers=h, params={"company_id": METALS})).json()
    assert history[-1]["status"] == "error"


async def test_feedback_is_acknowledged_and_watch_is_a_next_step(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    items = (await feed(c, h, RETAIL))["items"]
    useful, irrelevant = items[0], items[1]
    ok = (
        await c.post(f"/api/v1/insights/{useful['insight_id']}/feedback", headers=h, json={"useful": True})
    ).json()
    assert ok["hidden"] is False and ok["applied"][0].startswith("Отзыв сохранён")
    assert (
        await c.post(f"/api/v1/insights/{useful['insight_id']}/state", headers=h, json={"action": "watch"})
    ).status_code == 204
    no = (
        await c.post(
            f"/api/v1/insights/{irrelevant['insight_id']}/feedback",
            headers=h,
            json={"useful": False, "reason": "irrelevant"},
        )
    ).json()
    assert no["hidden"] is True and len(no["applied"]) == 2
    visible = {i["insight_id"] for i in (await feed(c, h, RETAIL))["items"]}
    assert irrelevant["insight_id"] not in visible
    saved = {i["insight_id"] for i in (await feed(c, h, RETAIL, filter="saved"))["items"]}
    assert useful["insight_id"] in saved


async def test_request_status_is_visible_and_notified(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    card = (await feed(c, h, METALS))["items"][0]
    created = (
        await c.post(
            f"/api/v1/insights/{card['insight_id']}/actions", headers=h, json={"type": "contact_manager"}
        )
    ).json()
    mine = (await c.get("/api/v1/me/requests", headers=h)).json()
    assert mine[0]["external_ref"] == created["external_ref"] and mine[0]["insight_title"] == card["title"]
    ah = await admin_headers(c)
    patched = await c.patch(
        f"/api/v1/admin/crm-requests/{created['request_id']}", headers=ah, json={"status": "done"}
    )
    assert patched.status_code == 200 and patched.json()["notified"] == 1
    assert (
        await c.patch(
            f"/api/v1/admin/crm-requests/{created['request_id']}", headers=h, json={"status": "done"}
        )
    ).status_code == 403
    mine = (await c.get("/api/v1/me/requests", headers=h)).json()
    assert mine[0]["status"] == "done" and mine[0]["updated_at"]
    notes = (await c.get("/api/v1/notifications", headers=h, params={"unread_only": True})).json()["items"]
    assert any(n["kind"] == "request_update" and created["external_ref"] in n["title"] for n in notes)


async def test_ui_events_and_admin_metrics_follow_the_cjm(env: dict[str, Any]) -> None:
    c = env["client"]
    h = await login(c)
    card = (await feed(c, h, METALS))["items"][0]
    for event in ("widget_shown", "widget_click"):
        r = await c.post("/api/v1/events", headers=h, json={"type": event, "company_id": METALS})
        assert r.status_code == 204
    assert (
        await c.post("/api/v1/events", headers=h, json={"type": "hack", "company_id": METALS})
    ).status_code == 422
    assert (
        await c.post("/api/v1/events", headers=h, json={"type": "widget_click", "company_id": AGRO})
    ).status_code == 403
    await c.get(f"/api/v1/insights/{card['insight_id']}", headers=h, params={"via": "feed", "position": 1})
    await c.post(f"/api/v1/insights/{card['insight_id']}/feedback", headers=h, json={"useful": True})
    await c.post(f"/api/v1/insights/{card['insight_id']}/actions", headers=h, json={"type": "callback"})
    ah = await admin_headers(c)
    m = (await c.get("/api/v1/admin/metrics", headers=ah, params={"days": 1})).json()
    assert m["sessions"] > 0 and m["north_star"]["value"] > 0
    stages = ["01_notice", "02_context", "03_select", "04_understand", "05_verify", "06_clarify", "07_act"]
    assert set(m["cjm"]) == {*stages, "08_return"}
    assert m["cjm"]["01_notice"]["value"] > 0 and m["cjm"]["03_select"]["value"] is not None
    assert m["cjm"]["05_verify"]["value"] == 1.0  # the validator lets no unsourced key statement through
    assert m["cjm"]["06_clarify"]["questions"] >= 2 and m["quality"]["feedback"] > 0
    assert m["generation"]["completed"] > 0
    assert (await c.get("/api/v1/admin/metrics", headers=h)).status_code == 403
    async with pg_connection(env["url"]) as conn:
        snap = await snapshot_day(conn, datetime.now(UTC).date())
    assert snap["sessions"] > 0
    m = (await c.get("/api/v1/admin/metrics", headers=ah, params={"days": 2})).json()
    assert m["daily"] and m["daily"][-1]["sessions"] > 0


async def test_admin_operations(env: dict[str, Any]) -> None:
    c = env["client"]
    ah = await admin_headers(c)
    dq = (await c.get("/api/v1/admin/data-quality", headers=ah)).json()
    assert dq["run"]["status"] and dq["checks"]
    health = (await c.get("/api/v1/admin/sources/health", headers=ah)).json()
    assert any(s["is_fixture"] for s in health)
    queued = await c.post("/api/v1/admin/ingest/news", headers=ah)
    assert queued.status_code == 202 and queued.json()["task_id"]
    assert (await c.post("/api/v1/admin/ingest/companies", headers=ah)).status_code == 422
    before = len(env["stats"])
    regen = await c.post(
        "/api/v1/admin/insights/regenerate",
        headers=ah,
        json={"company_ids": [LOGISTICS], "only_failed": False},
    )
    assert regen.status_code == 202 and regen.json()["candidates"] > 0
    assert len(env["stats"]) == before + 1  # app.state.enqueue ran the generation
