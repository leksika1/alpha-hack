"""Idempotent demo seed: bank products, demo users with access to demo companies, fixture news, matching.

Demo companies: `<DATA_DIR>/demo_companies.json` when present (synthetic set), otherwise picked from the
loaded data by ``select_demo_companies`` (organizers' data). Generation itself is queued to the worker.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.demo_selection import select_demo_companies
from app.services.matching import MatchStats, match
from app.services.news_store import StoreResult, load_fixture_news
from app.services.users import DemoAccess, DemoUser, upsert_user

# Demo-only accounts, documented in README for the jury (case: "передать учётные данные", FAQ #63).
DEMO_PASSWORD = "demo12345"  # noqa: S105
ADMIN_PASSWORD = "admin12345"  # noqa: S105


async def seed_products(conn: Any, path: Path) -> int:
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    await conn.executemany(
        """INSERT INTO core.bank_products (id, name, description, tags, url, source)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
               tags = EXCLUDED.tags, url = EXCLUDED.url, source = EXCLUDED.source""",
        [
            (p["id"], p["name"], p["description"], p["tags"], p.get("url"), data["source"])
            for p in data["products"]
        ],
    )
    return len(data["products"])


async def demo_company_ids(conn: Any, data_dir: Path) -> list[str]:
    manifest = data_dir / "demo_companies.json"
    if manifest.exists():
        ids = [c["company_id"] for c in json.loads(manifest.read_text(encoding="utf-8"))]
    else:
        ids = [c["company_id"] for c in await select_demo_companies(conn)]
    valid = await conn.fetch(
        "SELECT id FROM core.companies WHERE id = ANY($1::text[]) AND is_valid AND client_type = 'CLIENT'",
        ids,
    )
    valid_ids = {r["id"] for r in valid}
    return [i for i in ids if i in valid_ids]


def demo_users(company_ids: list[str]) -> list[DemoUser]:
    """User 1: several companies with different roles (CEO in one,
    CFO in another); user 2: two others; admin."""
    c = company_ids + [None] * (5 - len(company_ids))
    first = [DemoAccess(c[0], "ceo", True)] if c[0] else []
    first += [DemoAccess(cid, role) for cid, role in ((c[1], "cfo"), (c[2], "owner")) if cid]
    second = [
        DemoAccess(cid, role, i == 0)
        for i, (cid, role) in enumerate(((c[3], "ceo"), (c[4], "treasurer")))
        if cid
    ]
    return [
        DemoUser("demo", DEMO_PASSWORD, "Демо: ЛПР нескольких компаний", "user", tuple(first)),
        DemoUser("demo2", DEMO_PASSWORD, "Демо: гендиректор и казначей", "user", tuple(second)),
        DemoUser("admin", ADMIN_PASSWORD, "Администратор сервиса", "admin", ()),
    ]


async def seed_demo(
    conn: Any, settings: Settings, data_dir: Path, reference_dir: Path, base_dir: Path
) -> dict[str, Any]:
    products = await seed_products(conn, reference_dir / "bank_products.json")
    company_ids = await demo_company_ids(conn, data_dir)
    for user in demo_users(company_ids):
        await upsert_user(conn, user)
    news: StoreResult | None = None
    if settings.news_fixtures_enabled:
        fixture = Path(settings.fixture_news_path)
        if not fixture.is_absolute():
            fixture = base_dir / fixture
        news = await load_fixture_news(conn, fixture)
    stats: MatchStats = await match(
        conn, company_ids, min_score=settings.match_min_score, max_per_company=settings.match_max_per_company
    )
    return {
        "products": products,
        "demo_companies": company_ids,
        "news_inserted": news.inserted if news else 0,
        "events": len(news.event_ids) if news else 0,
        "candidates": stats.candidates,
        "pending": stats.pending,
    }
