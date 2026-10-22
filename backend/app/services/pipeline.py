"""Glue for user-triggered refreshes: context rebuild -> matching -> queued generation."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.services.company_context import rebuild_contexts
from app.services.matching import match

Enqueue = Callable[[list[str]], Awaitable[None]]


async def rematch_company(
    conn: Any, settings: Settings, company_id: str, rebuild_context: bool = False
) -> int:
    """Returns the number of candidates waiting for generation for this company."""
    if rebuild_context:
        await rebuild_contexts(conn, [company_id])
    await match(
        conn, [company_id], min_score=settings.match_min_score, max_per_company=settings.match_max_per_company
    )
    pending: int = await conn.fetchval(
        "SELECT count(*) FROM core.candidate_matches WHERE company_id = $1 AND status = 'pending'", company_id
    )
    return pending


async def match_new_events(conn: Any, settings: Settings, event_ids: list[str]) -> list[str]:
    """After a news poll: score new/updated events against companies that have users.
    Returns companies with candidates waiting for generation (to enqueue)."""
    if not event_ids:
        return []
    company_ids = [r["company_id"] for r in await conn.fetch(
        "SELECT DISTINCT company_id FROM core.user_company_access ORDER BY company_id"
    )]  # fmt: skip
    if not company_ids:
        return []
    await match(
        conn,
        company_ids,
        event_ids=event_ids,
        min_score=settings.match_min_score,
        max_per_company=settings.match_max_per_company,
    )
    rows = await conn.fetch(
        """SELECT DISTINCT company_id FROM core.candidate_matches
           WHERE status = 'pending' AND company_id = ANY($1::text[]) ORDER BY company_id""",
        company_ids,
    )
    return [r["company_id"] for r in rows]
