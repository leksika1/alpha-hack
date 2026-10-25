"""End-to-end flow in-process: real Postgres, backend API and ml-stub over ASGI (the stub calls the backend
Tools API with a run-scoped token exactly as in docker-compose). Covers SPEC §11.2 scenarios 1-8, 10, 11."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fpdf import FPDF

import ml_stub.main as stub
from app.core.config import get_settings
from app.core.pg import pg_connection
from app.core.security import issue_run_token
from app.domain.ports import GenerationOutcome
from app.domain.scoring import profile_from_context, score_candidate
from app.integrations.generators import get_generator
from app.integrations.llm import FakeLlmProvider
from app.main import create_app
from app.services.analytics_run import run_analytics_ingest
from app.services.analytics_upload import process_upload
from app.services.generation import GenerationStats, generate_one, generate_pending
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


def text_pdf(title: str, body: str) -> bytes:
    pdf = FPDF()
    pdf.add_font("Roboto", fname=str(REF / "fonts" / "Roboto-Regular.ttf"))
    pdf.set_font("Roboto", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 8, title + "\n\n" + body)
    return bytes(pdf.output())


async def test_admin_uploads_analytics_pdf_and_worker_indexes_it(env: dict[str, Any]) -> None:
    c = env["client"]
    ah = await admin_headers(c)
    url = "/api/v1/admin/analytics"
    data = text_pdf(
        "Обзор рынка удобрений, октябрь",
        "Экспорт азотных удобрений вырос на фоне высоких цен на газ в Европе. Аналитики Альфа-Банка ждут, "
        "что маржа производителей сохранится до конца года, а внутренние цены останутся стабильными. " * 3,
    )
    pdf_headers = {**ah, "Content-Type": "application/pdf"}

    async def upload(headers: dict[str, str], name: str, body: bytes) -> httpx.Response:
        return await c.post(url, headers=headers, params={"file_name": name}, content=body)

    assert (await upload(ah, "x.pdf", data)).status_code == 415
    assert (await upload(pdf_headers, "x.pdf", b"not a pdf")).status_code == 422
    assert (await upload(pdf_headers, "../x.pdf", data)).status_code == 422
    assert (
        await upload({**await login(c), "Content-Type": "application/pdf"}, "x.pdf", data)
    ).status_code == 403

    resp = await upload(pdf_headers, "fertilizers_oct.pdf", data)
    up = resp.json()
    assert resp.status_code == 202 and up["status"] == "processing" and up["queued"] and up["task_id"], up
    listed = {d["document_id"]: d for d in (await c.get(url, headers=ah)).json()}
    assert listed[up["document_id"]]["status"] == "processing"

    async with pg_connection(env["url"]) as conn:  # what the worker task does
        outcome = await process_upload(conn, env["settings"], uuid.UUID(up["document_id"]))
    assert outcome.status == "ready" and outcome.chunks > 0, outcome
    doc = {d["document_id"]: d for d in (await c.get(url, headers=ah)).json()}[up["document_id"]]
    assert doc["status"] == "ready" and doc["chunks"] > 0 and doc["error"] is None
    assert doc["origin"] == "upload"
    user_docs = (await c.get("/api/v1/analytics/documents", headers=await login(c))).json()
    assert up["document_id"] in {d["document_id"] for d in user_docs}

    again = (await upload(pdf_headers, "copy.pdf", data)).json()
    assert again["status"] == "ready" and again["duplicate"] and not again["queued"]
    assert again["task_id"] is None
    async with pg_connection(env["url"]) as conn:
        await conn.execute("DELETE FROM core.analytics_documents WHERE id = $1::uuid", up["document_id"])


async def test_admin_generation_monitor_and_retry(env: dict[str, Any]) -> None:
    c = env["client"]
    ah = await admin_headers(c)
    async with pg_connection(env["url"]) as conn:
        await conn.execute(  # a run the worker never finished (its process died mid-generation)
            """INSERT INTO core.agent_runs (id, kind, request_id, company_id, role, generator, started_at)
               VALUES ('run_stuck_test', 'insight', 'gen:stuck', $1, 'cfo', 'stub',
                       now() - interval '2 hours')""",
            LOGISTICS,
        )
        failed = await conn.fetchval(
            """UPDATE core.candidate_matches SET status = 'failed', status_reason = 'ml-unavailable (test)'
               WHERE id = (SELECT id FROM core.candidate_matches WHERE company_id = $1 ORDER BY id LIMIT 1)
               RETURNING id""",
            LOGISTICS,
        )
    assert failed
    assert (await c.get("/api/v1/admin/generation", headers=await login(c))).status_code == 403
    view = (await c.get("/api/v1/admin/generation", headers=ah)).json()
    assert "run_stuck_test" in {r["id"] for r in view["stuck_runs"]}
    assert any(f["status_reason"] == "ml-unavailable (test)" for f in view["recent_failures"])
    assert view["candidates"]["failed"] >= 1 and view["runs_24h"] and view["queue_length"] is not None

    before = len(env["stats"])
    retry = await c.post("/api/v1/admin/generation/retry", headers=ah, json={"company_ids": [LOGISTICS]})
    body = retry.json()
    assert retry.status_code == 202 and body["stuck_closed"] == 1 and body["requeued"] >= 1, body
    assert body["companies"] == [LOGISTICS] and len(env["stats"]) == before + 1  # generation ran again
    after = (await c.get("/api/v1/admin/generation", headers=ah)).json()
    assert "run_stuck_test" not in {r["id"] for r in after["stuck_runs"]}
    async with pg_connection(env["url"]) as conn:
        run = await conn.fetchrow("SELECT status, error FROM core.agent_runs WHERE id = 'run_stuck_test'")
        cand = await conn.fetchval("SELECT status FROM core.candidate_matches WHERE id = $1", failed)
    assert run["status"] == "error" and "stuck" in run["error"]
    assert cand != "failed"


async def test_admin_alerts_reflect_system_state(env: dict[str, Any]) -> None:
    from app.services.alerts import evaluate

    c = env["client"]
    ah = await admin_headers(c)
    async with pg_connection(env["url"]) as conn:
        others = [
            r["id"] for r in await conn.fetch("SELECT id FROM core.sources WHERE enabled AND NOT is_fixture")
        ]
        await conn.execute("UPDATE core.sources SET enabled = false WHERE id = ANY($1::text[])", others)
        await conn.executemany(  # one source of three is down: skipped quietly
            """INSERT INTO core.sources (id, name, kind, health_status, last_error)
               VALUES ($1, $2, 'rss', $3, $4)""",
            [
                ("alert-down", "Недоступный RSS", "down", "HTTP 503"),
                ("alert-ok-1", "Рабочий RSS 1", "healthy", None),
                ("alert-ok-2", "Рабочий RSS 2", "healthy", None),
            ],
        )
        await conn.executemany(  # rejected runs in the last hour (the module's own generation ran too)
            """INSERT INTO core.agent_runs
                   (id, kind, request_id, company_id, role, generator, status, outcome, finished_at)
               VALUES ($1, 'insight', 'gen:alert-test', $2, 'cfo', 'stub', 'ok', $3, now())""",
            [(f"run_alert_{i}", LOGISTICS, "rejected" if i else "completed") for i in range(5)],
        )
        unreachable_ml = env["settings"].model_copy(
            update={
                "insight_generator": "remote",
                "ml_service_url": "http://127.0.0.1:9",
                "llm_daily_budget_rub": 0,
                "alert_reject_ratio": 0.001,  # the module generated many runs already
            }
        )
        await conn.executemany(  # the provider unreachable from the server: every call times out
            """INSERT INTO core.llm_usage (day, caller, purpose, model, status, error)
               VALUES (current_date, 'run', 'planning', 'gemma-4-26b-a4b', 'error', $1)""",
            [("llm-timeout",)] * 6,
        )
        keys = {a.key: a for a in await evaluate(conn, unreachable_ml)}
        assert keys["llm-errors"].severity == "critical" and "llm-timeout" in keys["llm-errors"].detail
        await conn.execute("DELETE FROM core.llm_usage WHERE purpose = 'planning' AND error = 'llm-timeout'")
        assert {"insights-rejected", "ml-unavailable", "llm-budget-exhausted"} <= set(keys)
        assert keys["ml-unavailable"].severity == "critical" and "sources-down" not in keys
        # most sources down: a network problem of the server
        await conn.execute("UPDATE core.sources SET health_status = 'down' WHERE id = 'alert-ok-1'")
        mass = {a.key: a for a in await evaluate(conn, env["settings"])}
    assert "sources-down" in mass and "2 из 3" in mass["sources-down"].detail

    assert (await c.get("/api/v1/admin/alerts", headers=await login(c))).status_code == 403
    live = (await c.get("/api/v1/admin/alerts", headers=ah)).json()
    assert "sources-down" in {a["key"] for a in live}
    async with pg_connection(env["url"]) as conn:
        await conn.execute("DELETE FROM core.sources WHERE id LIKE 'alert-%'")
        await conn.execute("DELETE FROM core.agent_runs WHERE id LIKE 'run_alert_%'")
        await conn.execute("UPDATE core.sources SET enabled = true WHERE id = ANY($1::text[])", others)
    after = {a["key"] for a in (await c.get("/api/v1/admin/alerts", headers=ah)).json()}
    assert "sources-down" not in after


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>World business</title>
<item><title>Steel prices rise in Europe</title><link>https://news.example.com/a</link>
<pubDate>Fri, 23 Oct 2026 10:00:00 GMT</pubDate><description>Mills raised prices.</description></item>
<item><title>Wheat exports slow down</title><link>https://news.example.com/b</link></item>
</channel></rss>"""


async def test_admin_manages_news_sources(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.news.http import PoliteFetcher
    from app.services import news_discovery, news_sources_admin
    from app.services.news_ingest import sync_sources

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        is_feed = request.url.host == "world.example.com" and request.url.path == "/rss.xml"
        return httpx.Response(200, content=RSS if is_feed else b"<html>not a feed</html>")

    def fake_fetcher(
        *args: Any, **kwargs: Any
    ) -> PoliteFetcher:  # the network is mocked, the guard stays off
        return PoliteFetcher("test", min_interval_s=0, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_sources_admin, "PoliteFetcher", fake_fetcher)
    monkeypatch.setattr(news_discovery, "PoliteFetcher", fake_fetcher)
    c = env["client"]
    ah = await admin_headers(c)
    url = "/api/v1/admin/sources"
    catalog, fixture = REF / "news_sources.json", ROOT / env["settings"].fixture_news_path
    async with pg_connection(env["url"]) as conn:
        await sync_sources(conn, catalog, True, fixture)
    assert (await c.get(url, headers=await login(c))).status_code == 403

    preview = (
        await c.post(f"{url}/check", headers=ah, json={"url": "https://world.example.com/rss.xml"})
    ).json()
    assert preview["ok"] and preview["items"] == 2 and preview["sample"][0]["title"]
    page = (await c.post(f"{url}/check", headers=ah, json={"url": "https://world.example.com/page"})).json()
    assert not page["ok"] and page["outcome"] == "empty"
    local = await c.post(f"{url}/check", headers=ah, json={"url": "file:///etc/passwd"})
    assert local.status_code == 422

    bad = await c.post(url, headers=ah, json={"url": "https://nofeed.example.com/page", "name": "Не лента"})
    assert bad.status_code == 422 and bad.json()["type"].endswith("/feed-check-failed")
    body = {"url": "https://world.example.com/rss.xml", "name": "World business", "topics": ["world"]}
    created = await c.post(url, headers=ah, json=body, params={"poll_now": "false"})
    assert created.status_code == 201, created.text
    src = created.json()["source"]
    assert src["origin"] == "admin" and src["kind"] == "rss" and src["id"].startswith("adm_")
    assert (await c.post(url, headers=ah, json=body)).status_code == 409

    renamed = await c.patch(f"{url}/{src['id']}", headers=ah, json={"name": "BBC Business", "priority": 50})
    assert renamed.status_code == 200 and renamed.json()["name"] == "BBC Business"
    catalog_id = "rbc"
    named = await c.patch(f"{url}/{catalog_id}", headers=ah, json={"name": "Другое имя"})
    assert named.status_code == 422 and "news_sources.json" in named.json()["detail"]
    off = (await c.patch(f"{url}/{catalog_id}", headers=ah, json={"enabled": False})).json()
    assert off["enabled"] is False and off["disabled_by_admin"] is True
    async with pg_connection(env["url"]) as conn:  # the next poll re-syncs the catalog file
        await sync_sources(conn, catalog, True, fixture)
        assert await conn.fetchval("SELECT enabled FROM core.sources WHERE id = $1", catalog_id) is False
        assert await conn.fetchval("SELECT name FROM core.sources WHERE id = $1", src["id"]) == "BBC Business"
    on = (await c.patch(f"{url}/{catalog_id}", headers=ah, json={"enabled": True})).json()
    assert on["disabled_by_admin"] is False

    listed = {s["id"]: s for s in (await c.get(url, headers=ah)).json()}
    assert listed[src["id"]]["origin"] == "admin" and listed[catalog_id]["origin"] == "catalog"
    assert (await c.delete(f"{url}/{catalog_id}", headers=ah)).status_code == 409
    assert (await c.delete(f"{url}/{src['id']}", headers=ah)).status_code == 204
    assert (await c.delete(f"{url}/{src['id']}", headers=ah)).status_code == 404
    async with pg_connection(env["url"]) as conn:  # live sources are off in the rest of the module
        await sync_sources(conn, catalog, False, fixture)


ARTICLE = """<html><head><meta property="og:title" content="Металлурги подняли цены на арматуру и прокат">
<meta property="og:site_name" content="Металл Инфо"></head><body><article>
<p>Крупнейшие металлургические комбинаты подняли отпускные цены на арматуру и сортовой прокат на 8%.</p>
<p>Металлотрейдеры ожидают дефицита стали до конца квартала: строительный спрос растёт, запасы снижаются.</p>
</article></body></html>"""


async def test_user_sends_link_or_text_and_gets_an_insight_for_their_company(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.news.http import PoliteFetcher
    from app.services import news_submit
    from app.services.tools import search_news

    pages = {"/steel": (200, ARTICLE), "/down": (503, "unavailable"), "/feed.xml": (200, RSS.decode())}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        code, body = pages.get(request.url.path, (404, "not found"))
        return httpx.Response(code, text=body)

    def fake_fetcher(*args: Any, **kwargs: Any) -> PoliteFetcher:
        return PoliteFetcher("test", min_interval_s=0, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_submit, "PoliteFetcher", fake_fetcher)
    c = env["client"]
    h = await login(c)
    url = "/api/v1/news/submit"

    before = len(env["stats"])
    sent = await c.post(url, headers=h, json={"company_id": METALS, "url": "https://metal.example.com/steel"})
    body = sent.json()
    assert sent.status_code == 200 and body["status"] == "processed", body
    assert body["relevant"] and body["generation_queued"] and len(env["stats"]) == before + 1
    async with pg_connection(
        env["url"]
    ) as conn:  # private to the sender's company until a real source has it
        private = await conn.fetchval(
            "SELECT private_company_ids FROM core.events WHERE id = $1", uuid.UUID(body["event_id"])
        )
        assert private == [METALS]
        others = await conn.fetchval(
            "SELECT count(*) FROM core.candidate_matches WHERE event_id = $1 AND company_id <> $2",
            uuid.UUID(body["event_id"]),
            METALS,
        )
        assert others == 0
        hits = await search_news(conn, {"query": "арматура прокат", "limit": 10}, RETAIL)
        assert body["news_id"] not in {h["news_id"] for h in hits}

    again = (
        await c.post(url, headers=h, json={"company_id": METALS, "url": "https://metal.example.com/steel"})
    ).json()
    assert again["status"] == "duplicate" and again["event_id"] == body["event_id"]

    down = (
        await c.post(url, headers=h, json={"company_id": METALS, "url": "https://metal.example.com/down"})
    ).json()
    assert down["status"] == "unavailable" and down["message"] == "Ресурс сейчас недоступен для просмотра"
    assert "503" in down["reason"]
    feed_link = await c.post(
        url, headers=h, json={"company_id": METALS, "url": "https://metal.example.com/feed.xml"}
    )
    assert feed_link.json()["status"] == "not_article"
    registry = await c.post(url, headers=h, json={"company_id": METALS, "url": "https://meduza.io/news/x"})
    assert registry.json()["status"] == "blocked"

    text = {
        "company_id": METALS,
        "title": "Экспортёры стали теряют рынки",
        "text": "Аналитики отмечают, что после вторжения России экспорт стали сократился, "
        "и металлурги ищут новые рынки сбыта в Азии и на Ближнем Востоке.",
    }
    blocked = (await c.post(url, headers=h, json=text)).json()
    assert blocked["status"] == "blocked" and "20.3.3" in blocked["reason"]
    text["text"] = (
        "Металлотрейдеры сообщают о росте закупочных цен на арматуру и листовой прокат у комбинатов на 6%; "
        "склады пустеют."
    )
    plain = (await c.post(url, headers=h, json=text)).json()
    assert plain["status"] == "processed" and plain["url"] is None

    assert (await c.post(url, headers=h, json={"company_id": METALS})).status_code == 422
    assert (
        await c.post(url, headers=h, json={"company_id": AGRO, "url": "https://x.example.com/a"})
    ).status_code == 403


async def test_admin_finds_feeds_by_name_or_site(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.integrations.news.http import PoliteFetcher
    from app.services import news_discovery, news_sources_admin

    home = b'<html><head><link rel="alternate" type="application/rss+xml" href="/news.rss"></head></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path in {"/", ""}:
            return httpx.Response(200, content=home, headers={"content-type": "text/html"})
        if path.endswith((".rss", ".xml", "/feed", "/rss")):
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    def fake_fetcher(*args: Any, **kwargs: Any) -> PoliteFetcher:
        return PoliteFetcher("test", min_interval_s=0, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(news_sources_admin, "PoliteFetcher", fake_fetcher)
    monkeypatch.setattr(news_discovery, "PoliteFetcher", fake_fetcher)
    c = env["client"]
    ah = await admin_headers(c)
    url = "/api/v1/admin/sources/discover"

    by_name = (await c.post(url, headers=ah, json={"query": "Ведомости"})).json()
    assert by_name["kind"] == "name" and by_name["candidates"]
    assert {x["found_by"] for x in by_name["candidates"]} == {"directory"}
    assert all(x["name"] == "Ведомости" for x in by_name["candidates"])

    by_site = (await c.post(url, headers=ah, json={"query": "news.example.com"})).json()
    assert by_site["kind"] == "site"
    page_feed = next(x for x in by_site["candidates"] if x["found_by"] == "page")
    assert (
        page_feed["url"] == "https://news.example.com/news.rss"
        and page_feed["ok"]
        and page_feed["items"] == 2
    )
    assert all(x["ok"] for x in by_site["candidates"] if x["found_by"] == "guess")

    blocked = (await c.post(url, headers=ah, json={"query": "meduza.io"})).json()
    assert blocked["blocked"] and blocked["candidates"] == []

    added = await c.post(
        "/api/v1/admin/sources",
        headers=ah,
        json={"url": "https://news.example.com", "name": "Example News"},
        params={"poll_now": "false"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["source"]["url"] == "https://news.example.com/news.rss"  # the site's feed, found
    assert (
        await c.delete(f"/api/v1/admin/sources/{added.json()['source']['id']}", headers=ah)
    ).status_code == 204


async def _old_rules_event(conn: Any, title: str, body: str, tags: dict[str, Any], flags: list[str]) -> str:
    """A news item and its own event as the previous rules stored them."""
    news_id = await conn.fetchval(
        """INSERT INTO core.news_items
               (source_id, url, canonical_url, title, body, published_at, text_hash, tags, flags, event_type)
           VALUES ('manual', $1, $1, $2, $3, now() - interval '1 hour', md5($1), $4::jsonb, $5,
                   'monetary_policy')
           RETURNING id""",
        f"https://old.example.com/{uuid.uuid4().hex}",
        title,
        body,
        json.dumps(tags, ensure_ascii=False),
        flags,
    )
    event_id = await conn.fetchval(
        """INSERT INTO core.events
               (title, event_type, tags, first_seen_at, last_seen_at, urgent, urgency_reasons)
           VALUES ($1, 'monetary_policy', $2::jsonb, now() - interval '1 hour', now() - interval '1 hour',
                   true, '{cbr_key_rate_decision}')
           RETURNING id""",
        title,
        json.dumps({"currencies": [], "numbers": tags["numbers"]}),
    )
    await conn.execute("INSERT INTO core.event_news (event_id, news_id) VALUES ($1, $2)", event_id, news_id)
    return str(event_id)


async def test_feed_quality_rules_and_cleanup_of_stored_news(env: dict[str, Any]) -> None:
    c = env["client"]
    ah = await admin_headers(c)
    live = {"title": "Военная операция на Украине. Онлайн", "body": "Хроника событий дня, обновляется."}
    sent = (await c.post("/api/v1/admin/news", headers=ah, json={"items": [live]})).json()
    assert sent["new"] == 0 and not sent["events"]  # live blogs are not news of one fact

    story = [
        {
            "title": "Банк России сохранил ключевую ставку на уровне 15% годовых",
            "body": "Решение совета директоров.",
        },
        {
            "title": "Шохин: бизнес не ожидает снижения ключевой ставки ЦБ",
            "body": "Глава РСПП прокомментировал.",
        },
    ]
    one = (await c.post("/api/v1/admin/news", headers=ah, json={"items": story})).json()
    assert one["new"] == 2 and len(one["events"]) == 1  # the decision and the comment: one event

    old_tags = {"numbers": [{"kind": "key_rate", "value": 13.5, "unit": "%"}], "urgency_reasons": []}
    async with pg_connection(env["url"]) as conn:
        forecast = await _old_rules_event(
            conn,
            "ЦБ придется дольше сохранять умеренно жесткую денежно-кредитную политику",
            "Прогноз средней ключевой ставки на 2027 год повышен до 12,5-13,5%.",
            old_tags,
            [],
        )
        comment = await _old_rules_event(
            conn,
            "Минфин связал повышенную ключевую ставку с политикой ЦБ по инфляции",
            "Ведомство прокомментировало решение Банка России сохранить ставку 15%.",
            {"numbers": [], "urgency_reasons": []},
            [],
        )
        blog = await _old_rules_event(
            conn, live["title"], live["body"], {"numbers": [], "urgency_reasons": []}, []
        )
        await conn.execute(
            """INSERT INTO core.candidate_matches (company_id, event_id, role, score, score_breakdown)
               VALUES ($1, $2::uuid, 'cfo', 0.9, '{}'::jsonb)""",
            METALS,
            blog,
        )

    stats = (await c.post("/api/v1/admin/news/cleanup", headers=ah, params={"days": 2})).json()
    assert (
        stats["liveblog_events_retired"] >= 1
        and stats["cbr_events_merged"] >= 2
        and stats["news_retagged"] >= 1
    )
    async with pg_connection(env["url"]) as conn:
        kept = uuid.UUID(one["events"][0])
        row = await conn.fetchrow(
            "SELECT tags, n_news, private_company_ids FROM core.events WHERE id = $1", kept
        )
        # the decision's rate (this one or the demo feed's decision it joined), never the 13.5% forecast
        rates = [n["value"] for n in json.loads(row["tags"])["numbers"] if n["kind"] == "key_rate"]
        assert rates and rates[0] in {14.0, 15.0}
        assert row["n_news"] >= 4 and row["private_company_ids"] is None  # the old events' news moved in
        for retired in (forecast, comment, blog):
            hidden = await conn.fetchval(
                "SELECT private_company_ids FROM core.events WHERE id = $1::uuid", retired
            )
            assert hidden == []
        blog_candidate = await conn.fetchval(
            "SELECT status_reason FROM core.candidate_matches WHERE event_id = $1::uuid", blog
        )
        assert blog_candidate == "liveblog"
        forecast_news = await conn.fetchval(
            """SELECT n.tags FROM core.news_items n JOIN core.event_news en ON en.news_id = n.id
               WHERE n.title LIKE 'ЦБ придется%' AND en.event_id = $1""",
            kept,
        )
        assert [n for n in json.loads(forecast_news)["numbers"] if n["kind"] == "key_rate"] == []

    again = (await c.post("/api/v1/admin/news/cleanup", headers=ah, params={"days": 2})).json()
    assert (
        again["cbr_events_merged"] == 0
        and again["liveblog_events_retired"] == 0
        and again["news_retagged"] == 0
    )


async def test_demo_feed_and_real_news_are_never_one_event(env: dict[str, Any]) -> None:
    from app.services.feed_cleanup import cleanup
    from app.services.news_store import NewsDraft, store_news

    tag = uuid.uuid4().hex[:8]  # unique wording: not a near-duplicate of the module's demo feed
    title = f"Банк России объявил паузу по ключевой ставке, серия {tag}"
    now = datetime.now(UTC)
    async with pg_connection(env["url"]) as conn:
        demo = await store_news(
            conn, "fixture", [NewsDraft("https://fixture.local/cbr-x", title, "Демо.", now)]
        )
        real = await store_news(
            conn,
            "manual",
            [NewsDraft("https://www.cbr.ru/press/x", title + " (пресс-релиз)", "Решение.", now)],
        )
        assert demo.event_ids and real.event_ids and demo.event_ids != real.event_ids
        # an event mixed by the previous rules: the real news sits in the demo event
        await conn.execute(
            "UPDATE core.event_news SET event_id = $1::uuid WHERE event_id = $2::uuid",
            demo.event_ids[0],
            real.event_ids[0],
        )
        stats = await cleanup(conn, env["settings"], days=1)
        assert stats["mixed_events_split"] >= 1
        sources = await conn.fetch(
            """SELECT en.event_id, bool_and(s.is_fixture) AS all_demo, bool_or(s.is_fixture) AS any_demo
               FROM core.event_news en JOIN core.news_items n ON n.id = en.news_id
               JOIN core.sources s ON s.id = n.source_id GROUP BY en.event_id"""
        )
    assert all(r["all_demo"] == r["any_demo"] for r in sources)  # no event mixes demo and real news


async def test_feed_shows_at_most_two_cards_per_macro_theme(env: dict[str, Any]) -> None:
    from app.services.feed import feed

    async with pg_connection(env["url"]) as conn:
        user_id = await conn.fetchval("SELECT id FROM core.users WHERE login = 'demo'")
        full, _ = await feed(conn, user_id, METALS, "ceo", "all", None, 50, None, mark_seen=False)
        capped, _ = await feed(
            conn, user_id, METALS, "ceo", "all", None, 50, None, mark_seen=False, topic_cap=1
        )
        urgent, _ = await feed(
            conn, user_id, METALS, "ceo", "urgent", None, 50, None, mark_seen=False, topic_cap=1
        )
    themes = {"monetary_policy", "fx"}
    for theme in themes:
        total = [c for c in full if c["event_type"] == theme]
        shown = [c for c in capped if c["event_type"] == theme]
        assert len(shown) == min(1, len(total))  # the most relevant card of the theme stays
        if shown:
            assert (
                shown[0]["insight_id"] == total[0]["insight_id"]
                and shown[0]["more_in_topic"] == len(total) - 1
            )
    other = [c["insight_id"] for c in full if c["event_type"] not in themes]
    assert other == [c["insight_id"] for c in capped if c["event_type"] not in themes]  # nothing else hidden
    assert all(c["more_in_topic"] == 0 for c in urgent)  # other filters show everything


async def test_admin_manual_news_goes_through_the_pipeline(env: dict[str, Any]) -> None:
    c = env["client"]
    ah = await admin_headers(c)
    news = {
        "title": "ЕС ввёл новые санкции против российских производителей стального проката",
        "body": "Под ограничения попали поставки арматуры, сортового проката и стали из России; "
        "металлурги ждут сокращения экспорта.",
    }
    before = len(env["stats"])
    async with pg_connection(env["url"]) as conn:  # other tests send manual news too
        raw_before = await conn.fetchval("SELECT count(*) FROM core.news_raw WHERE source_id = 'manual'")
    first = (await c.post("/api/v1/admin/news", headers=ah, json={"items": [news]})).json()
    assert first["outcome"] == "ok" and first["new"] == 1 and first["urgent_events"], first
    assert METALS in first["companies_queued"] and len(env["stats"]) == before + 1
    again = (await c.post("/api/v1/admin/news", headers=ah, json={"items": [news]})).json()
    assert again["outcome"] == "unchanged" and again["new"] == 0  # same body: stored once
    async with pg_connection(env["url"]) as conn:
        src = await conn.fetchrow("SELECT enabled, kind, health_status FROM core.sources WHERE id = 'manual'")
        raw = await conn.fetchval("SELECT count(*) FROM core.news_raw WHERE source_id = 'manual'")
    state = (src["enabled"], src["kind"], src["health_status"])
    assert state == (False, "manual", "healthy") and raw == raw_before + 2  # both submissions kept
    h = await login(c)
    assert (await c.post("/api/v1/admin/news", headers=h, json={"items": [news]})).status_code == 403


class _SkippingGenerator:
    name = "skip-test"

    async def generate(self, request: Any) -> GenerationOutcome:
        return GenerationOutcome(status="skipped", skip_reason="not_relevant")


async def test_skipped_regeneration_retires_the_previous_card(env: dict[str, Any]) -> None:
    async with pg_connection(env["url"]) as conn:
        cand = await conn.fetchrow(
            """SELECT cm.* FROM core.candidate_matches cm
               JOIN core.insights i ON i.company_id = cm.company_id AND i.event_id = cm.event_id
                    AND i.role_variant = cm.role AND (i.expires_at IS NULL OR i.expires_at > now())
               WHERE cm.company_id = $1 AND cm.status = 'generated' ORDER BY cm.id LIMIT 1""",
            RETAIL,
        )
        assert cand is not None
        stats = GenerationStats()
        await generate_one(conn, get_settings(), _SkippingGenerator(), cand, stats)
        live = await conn.fetchval(
            """SELECT count(*) FROM core.insights
               WHERE company_id = $1 AND event_id = $2 AND role_variant = $3
                 AND (expires_at IS NULL OR expires_at > now())""",
            RETAIL,
            cand["event_id"],
            cand["role"],
        )
        status = await conn.fetchval("SELECT status FROM core.candidate_matches WHERE id = $1", cand["id"])
    assert stats.skipped == 1 and status == "skipped" and live == 0


async def test_market_data_of_the_bank_of_russia_for_the_agent_and_the_widget(env: dict[str, Any]) -> None:
    from datetime import date
    from decimal import Decimal

    from app.integrations.cbr import CbrError
    from app.services.market import refresh

    class Source:
        def __init__(self) -> None:
            self.calls: list[tuple[str, date]] = []

        async def series(self, code: str, date_from: date, date_to: date) -> list[tuple[date, Decimal]]:
            self.calls.append((code, date_from))
            if code == "eur_rub":
                raise CbrError("HTTP 503")
            if code == "key_rate":
                return [(date(2026, 9, 12), Decimal("14.50")), (date(2026, 10, 24), Decimal("14.00"))]
            return [(date(2026, 10, 24), Decimal("82.1")), (date(2026, 10, 25), Decimal("81.9"))]

    c = env["client"]
    h = await login(c)
    today = date(2026, 10, 25)
    async with pg_connection(env["url"]) as conn:
        assert (await c.get("/api/v1/market", headers=h)).json() == {"items": []}
        source = Source()
        result = await refresh(conn, source, 400, today=today)
        assert result.errors == {"eur_rub": "HTTP 503"} and result.points["key_rate"] == 2
        assert all(since == date(2025, 9, 20) for _, since in source.calls)  # empty table: full history
        again = Source()
        await refresh(conn, again, 400, today=today)  # idempotent; only the last week of known indicators
        assert dict(again.calls)["key_rate"] == date(2026, 10, 18) and dict(again.calls)["eur_rub"] == date(
            2025, 9, 20
        )

    items = {i["indicator"]: i for i in (await c.get("/api/v1/market", headers=h)).json()["items"]}
    assert set(items) == {"key_rate", "usd_rub", "cny_rub"}
    rate = items["key_rate"]
    assert rate["value"] == 14.0 and rate["previous_value"] == 14.5 and rate["change"] == -0.5
    assert rate["previous_date"] == "2026-09-12" and rate["source"]["title"] == "Банк России"
    assert (await c.get("/api/v1/market")).status_code == 401

    token = issue_run_token(env["settings"], "run_market", METALS, 60)
    hdr = {"Authorization": f"Bearer {token}", "X-Run-Id": "run_market"}
    tool = "/internal/tools/get_market_data"
    resp = await c.post(tool, headers=hdr, json={"indicator": "key_rate", "date_from": "2026-09-01"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [p["value"] for p in body["points"]] == [14.5, 14.0] and body["unit"] == "%"
    assert body["source"]["url"].startswith("https://cbr.ru/")
    missing = await c.post(tool, headers=hdr, json={"indicator": "brent"})
    assert missing.status_code == 404 and missing.json()["type"].endswith("not-available")
    assert (await c.post(tool, headers=hdr, json={"indicator": "eur_rub"})).status_code == 404
    bad = await c.post(tool, headers=hdr, json={"indicator": "usd_rub", "date_from": "вчера"})
    assert bad.status_code == 422


async def test_prometheus_shows_the_pipeline_state_of_the_worker(env: dict[str, Any]) -> None:
    resp = await env["client"].get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    async with pg_connection(env["url"]) as conn:
        generated = await conn.fetchval(
            "SELECT count(*) FROM core.candidate_matches WHERE status = 'generated'"
        )
        news = await conn.fetchval(
            "SELECT count(*) FROM core.news_items WHERE fetched_at > now() - interval '1 day'"
        )
    assert f'generation_candidates{{status="generated"}} {float(generated)}' in text
    assert f"news_items_last_24h {float(news)}" in text
    assert 'news_sources{status="down"}' in text and "news_fetches_last_hour" in text
