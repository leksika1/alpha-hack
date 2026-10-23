"""End-to-end flow in-process: real Postgres, backend API and ml-stub over ASGI (the stub calls the backend
Tools API with a run-scoped token exactly as in docker-compose). Covers SPEC §11.2 scenarios 1-8, 10, 11."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import ml_stub.main as stub
from app.core.config import get_settings
from app.core.pg import pg_connection
from app.core.security import issue_run_token
from app.integrations.generators import get_generator
from app.main import create_app
from app.services.analytics_run import run_analytics_ingest
from app.services.generation import generate_pending
from app.services.ingest_companies import ingest_companies
from app.services.seed import DEMO_PASSWORD, seed_demo

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
    stub.TOOLS_TRANSPORT = httpx.ASGITransport(app=app)
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
