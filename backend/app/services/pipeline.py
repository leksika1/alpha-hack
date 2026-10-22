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
