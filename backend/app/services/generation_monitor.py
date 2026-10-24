"""Generation monitor for admins: queue, candidate states, agent runs, stuck runs and recent failures, plus a
retry that closes stuck runs and puts failed candidates back in the queue.

A run is stuck when it has no result long after the ML timeout: the worker process died mid-generation
(before the broker fix it restarted every few seconds of idling) and nobody will finish the run.
"""

from datetime import timedelta
from typing import Any

import httpx
import redis.asyncio as aioredis

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

CANDIDATE_STATES = ("pending", "generated", "rejected", "failed", "skipped")
RECENT_LIMIT = 20


def stuck_after(settings: Settings) -> timedelta:
    return timedelta(seconds=max(300.0, 3 * max(settings.ml_request_timeout_s, settings.qa_timeout_s)))


async def _queue_length(settings: Settings) -> int | None:
    from app.workers.broker import QUEUE_NAME  # lazily: the module builds the broker from settings

    try:
        async with aioredis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1) as redis:
            return int(await redis.llen(QUEUE_NAME))
    except Exception:
        log.warning("generation_monitor_queue_unavailable", exc_info=True)
        return None


async def ml_status(settings: Settings) -> str:
    if settings.insight_generator != "remote":
        return "not-used"
    try:
        async with httpx.AsyncClient(timeout=2) as http:
            resp = await http.get(f"{settings.ml_service_url.rstrip('/')}/health")
        return "ok" if resp.status_code == 200 else f"http-{resp.status_code}"
    except httpx.HTTPError:
        return "unavailable"


async def overview(conn: Any, settings: Settings) -> dict[str, Any]:
    rows = await conn.fetch("SELECT status, count(*) AS n FROM core.candidate_matches GROUP BY status")
    counts = {r["status"]: r["n"] for r in rows}
    runs = await conn.fetch(
        """SELECT kind, coalesce(outcome, status) AS outcome, count(*) AS n,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at))
                      AS p50_s,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at))
                      AS p95_s
           FROM core.agent_runs WHERE started_at > now() - interval '1 day'
           GROUP BY 1, 2 ORDER BY 1, 3 DESC"""
    )
    stuck = await conn.fetch(
        """SELECT id, kind, company_id, event_id, generator, started_at FROM core.agent_runs
           WHERE status = 'running' AND started_at < now() - $1::interval
           ORDER BY started_at LIMIT $2""",
        stuck_after(settings),
        RECENT_LIMIT,
    )
    failures = await conn.fetch(
        """SELECT company_id, event_id, role, status, status_reason, updated_at FROM core.candidate_matches
           WHERE status IN ('failed', 'rejected') ORDER BY updated_at DESC LIMIT $1""",
        RECENT_LIMIT,
    )
    return {
        "generator": settings.insight_generator,
        "ml_service": await ml_status(settings),
        "queue_length": await _queue_length(settings),
        "candidates": {state: int(counts.get(state, 0)) for state in CANDIDATE_STATES},
        "runs_24h": [
            {
                "kind": r["kind"],
                "outcome": r["outcome"],
                "count": r["n"],
                "p50_s": round(r["p50_s"], 2) if r["p50_s"] is not None else None,
                "p95_s": round(r["p95_s"], 2) if r["p95_s"] is not None else None,
            }
            for r in runs
        ],
        "stuck_after_s": int(stuck_after(settings).total_seconds()),
        "stuck_runs": [dict(r) for r in stuck],
        "recent_failures": [dict(r) for r in failures],
    }


async def retry(
    conn: Any, settings: Settings, company_ids: list[str] | None, include_rejected: bool
) -> dict[str, Any]:
    """Closes stuck runs and returns failed (optionally rejected) candidates to `pending`."""
    async with conn.transaction():
        closed = await conn.fetch(
            """UPDATE core.agent_runs SET status = 'error', outcome = 'failed', finished_at = now(),
                   error = 'stuck: no result (worker restarted?), closed by admin retry'
               WHERE status = 'running' AND started_at < now() - $1::interval
                 AND ($2::text[] IS NULL OR company_id = ANY($2::text[]))
               RETURNING id""",
            stuck_after(settings),
            company_ids,
        )
        states = ["failed", "rejected"] if include_rejected else ["failed"]
        requeued = await conn.fetch(
            """UPDATE core.candidate_matches SET status = 'pending', status_reason = 'admin-retry',
                   updated_at = now()
               WHERE status = ANY($1::text[]) AND ($2::text[] IS NULL OR company_id = ANY($2::text[]))
               RETURNING company_id""",
            states,
            company_ids,
        )
    companies = sorted({r["company_id"] for r in requeued})
    log.info("generation_retry", stuck_closed=len(closed), requeued=len(requeued), companies=len(companies))
    return {"stuck_closed": len(closed), "requeued": len(requeued), "companies": companies}
