"""Glue for user-triggered refreshes: context rebuild -> matching -> queued generation."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.integrations.scorers import get_scorer
from app.services.company_context import rebuild_contexts
from app.services.matching import match, match_events

Enqueue = Callable[[list[str]], Awaitable[None]]


async def rematch_company(
    conn: Any, settings: Settings, company_id: str, rebuild_context: bool = False
) -> int:
    """Returns the number of candidates waiting for generation for this company."""
    if rebuild_context:
        await rebuild_contexts(conn, [company_id])
    await match(
        conn,
        [company_id],
        min_score=settings.match_min_score,
        max_per_company=settings.match_max_per_company,
        scorer=get_scorer(settings),
    )
    pending: int = await conn.fetchval(
        "SELECT count(*) FROM core.candidate_matches WHERE company_id = $1 AND status = 'pending'", company_id
    )
    return pending


async def match_new_events(conn: Any, settings: Settings, event_ids: list[str]) -> list[str]:
    """After a news poll: pre-filter + score new/updated events (MATCH_SCOPE: companies with users or the
    whole client base). Returns companies with users that have candidates waiting for generation."""
    if not event_ids:
        return []
    await match_events(
        conn,
        event_ids,
        settings.match_scope,
        settings.eligible_client_types,
        min_score=settings.match_min_score,
        max_per_company=settings.match_max_per_company,
        scorer=get_scorer(settings),
    )
    rows = await conn.fetch(
        """SELECT DISTINCT m.company_id FROM core.candidate_matches m
           WHERE m.status = 'pending'
             AND EXISTS (SELECT 1 FROM core.user_company_access a WHERE a.company_id = m.company_id)
           ORDER BY m.company_id"""
    )
    return [r["company_id"] for r in rows]
