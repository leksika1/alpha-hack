"""Live smoke (`make smoke-live`, excluded from CI): one real news source and one real LLM call.

The LLM call goes through the LLM Gateway of a running stack (`make up`), never to the provider directly,
so it is counted in the budget and llm_usage. Skipped when LLM_BASE_URL is not configured or the stack is
not reachable.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.integrations.news.connectors import SourceInfo, connector_for
from app.integrations.news.http import PoliteFetcher

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]

ROOT = Path(__file__).resolve().parents[2]
API_URL = os.environ.get("LIVE_API_URL", "http://localhost:8000")


async def test_one_real_news_source_is_fetched_and_parsed() -> None:
    settings = get_settings()
    catalog = json.loads((ROOT / settings.news_sources_path).read_text(encoding="utf-8"))
    src = min((s for s in catalog["sources"] if s["kind"] == "rss"), key=lambda s: s.get("priority", 100))
    fetcher = PoliteFetcher(settings.news_user_agent, min_interval_s=settings.news_min_interval_s)
    try:
        result = await fetcher.fetch(src["url"], None, None)
    finally:
        await fetcher.close()
    assert result.outcome == "ok", (src["id"], result.outcome, result.error)
    drafts = connector_for("rss").parse(
        result.body, SourceInfo(src["id"], src["name"], "rss", src["url"]), datetime.now(UTC)
    )
    assert drafts and all(d.title and d.url.startswith("http") for d in drafts)


async def test_one_real_llm_call_through_the_gateway() -> None:
    settings = get_settings()
    if not settings.llm_base_url:
        pytest.skip("LLM_BASE_URL is not set")
    headers = {
        "Authorization": f"Bearer {settings.ml_service_token.get_secret_value()}",
        "X-Purpose": "other",
    }
    async with httpx.AsyncClient(base_url=API_URL, timeout=90) as client:
        try:
            models = await client.get("/internal/llm/models", headers=headers)
        except httpx.ConnectError:
            pytest.skip(f"the stack is not running at {API_URL} (make up)")
        assert models.status_code == 200, models.text
        model = os.environ.get("LIVE_LLM_MODEL", models.json()["data"][0]["id"])
        resp = await client.post(
            "/internal/llm/chat/completions",
            headers=headers,
            json={
                "model": model,
                "max_tokens": 16,
                "temperature": 0,
                "messages": [{"role": "user", "content": "Ответь одним словом: столица России?"}],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["choices"][0]["message"]["content"]
        budget = (await client.get("/internal/llm/budget", headers=headers)).json()
        assert budget["spent_rub"] > 0 or resp.headers.get("x-cache") == "hit"
