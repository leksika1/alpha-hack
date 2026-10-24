"""News collection pipeline (SPEC §5.2): catalog sync -> polite fetch -> raw body to S3 -> parse -> store
(dedup, clustering) -> source health with automatic fallback to reserve sources.

Raw bodies are stored once per distinct content (sha256) and can be re-processed from S3 without downloading
again (``reprocess_raw``). Fetching is concurrent; database writes are sequential on one connection.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.s3 import ObjectStore, S3ObjectStore
from app.integrations.news.connectors import SourceInfo, connector_for
from app.integrations.news.http import FetchResult, PoliteFetcher
from app.services.news_store import FIXTURE_SOURCE_ID, StoreResult, ensure_source, store_news

HEALTHY_OUTCOMES = {"ok", "not_modified", "unchanged"}
__all__ = ["ObjectStore", "S3ObjectStore", "ingest_manual", "poll", "reprocess_raw", "sync_sources"]
MANUAL_SOURCE_ID = "manual"


@dataclass
class SourceRun:
    source_id: str
    outcome: str
    status_code: int | None = None
    n_items: int = 0
    n_new: int = 0
    n_duplicates: int = 0
    latency_ms: int = 0
    error: str | None = None
    health: str = "unknown"
    fallback_used: bool = False


@dataclass
class PollResult:
    sources: list[SourceRun] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    new_event_ids: list[str] = field(default_factory=list)
    urgent_event_ids: list[str] = field(default_factory=list)

    def merge(self, store: StoreResult) -> None:
        for attr in ("event_ids", "new_event_ids", "urgent_event_ids"):
            target: list[str] = getattr(self, attr)
            target.extend(e for e in getattr(store, attr) if e not in target)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": [s.__dict__ for s in self.sources],
            "events": len(self.event_ids),
            "new_events": len(self.new_event_ids),
            "urgent_events": len(self.urgent_event_ids),
        }


async def sync_sources(conn: Any, catalog_path: Path, live_enabled: bool, fixture_path: Path | None) -> int:
    """Upsert the catalog; live sources are enabled only when NEWS_LIVE_SOURCES_ENABLED and not switched off
    by an admin. Sources an admin added (origin = admin, ADR-036) are not in the file and keep their
    settings."""
    data = json.loads(await asyncio.to_thread(catalog_path.read_text, encoding="utf-8"))
    rows = [
        (s["id"], s["name"], s["kind"], s["url"], int(s.get("priority", 100)), s.get("fallback_for"),
         live_enabled, False, list(s.get("topics", [])))
        for s in data["sources"]
    ]  # fmt: skip
    if fixture_path is not None:
        rows.append((FIXTURE_SOURCE_ID, "Демо-лента (синтетические новости)", "fixture", str(fixture_path), 1,
                     None, True, True, ["demo"]))  # fmt: skip
    await conn.executemany(
        """INSERT INTO core.sources (id, name, kind, url, priority, fallback_for, enabled, is_fixture, topics)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, url = EXCLUDED.url,
               priority = EXCLUDED.priority, fallback_for = EXCLUDED.fallback_for,
               enabled = EXCLUDED.enabled AND NOT core.sources.disabled_by_admin,
               is_fixture = EXCLUDED.is_fixture, topics = EXCLUDED.topics""",
        rows,
    )
    await conn.execute(
        "UPDATE core.sources SET enabled = $1 AND NOT disabled_by_admin WHERE origin = 'admin'", live_enabled
    )
    if fixture_path is None:
        await conn.execute("UPDATE core.sources SET enabled = false WHERE id = $1", FIXTURE_SOURCE_ID)
    return len(rows)


def _raw_key(source_id: str, at: datetime, sha: str, content_type: str | None) -> str:
    ext = "json" if content_type and "json" in content_type else "xml"
    return f"{source_id}/{at:%Y/%m/%d}/{at:%H%M%S}_{sha[:16]}.{ext}"


async def _fetch_one(fetcher: PoliteFetcher, src: Any) -> FetchResult:
    if src["kind"] == "fixture":
        try:
            body = await asyncio.to_thread(Path(src["url"]).read_bytes)
            return FetchResult(src["url"], "ok", 200, body, "application/json")
        except OSError as exc:
            return FetchResult(src["url"], "network_error", error=str(exc))
    return await fetcher.fetch(src["url"], src["etag"], src["last_modified"])


async def _record_health(
    conn: Any, src: Any, run: SourceRun, fetch: FetchResult, failures_to_down: int
) -> str:
    if run.outcome in HEALTHY_OUTCOMES:
        row = await conn.fetchrow(
            """UPDATE core.sources SET health_status = 'healthy', consecutive_failures = 0,
                   last_success_at = now(),
                   last_checked_at = now(), last_error = NULL, last_latency_ms = $2,
                   etag = coalesce($3, etag), last_modified = coalesce($4, last_modified)
               WHERE id = $1 RETURNING health_status""",
            src["id"],
            run.latency_ms,
            fetch.etag,
            fetch.last_modified,
        )
    else:
        row = await conn.fetchrow(
            """UPDATE core.sources SET consecutive_failures = consecutive_failures + 1,
                   last_checked_at = now(),
                   last_error = $2, last_latency_ms = $3,
                   health_status = CASE WHEN $4 = 'robots_disallowed' THEN 'down'
                                        WHEN consecutive_failures + 1 >= $5 THEN 'down' ELSE 'degraded' END
               WHERE id = $1 RETURNING health_status""",
            src["id"],
            run.error,
            run.latency_ms,
            run.outcome,
            failures_to_down,
        )
    health: str = row["health_status"]
    return health


async def process_raw(
    conn: Any, src: Any, raw_id: Any, body: bytes, now: datetime
) -> tuple[int, StoreResult]:
    """Parse a raw body with the source's connector and store the news; updates the news_raw row."""
    info = SourceInfo(src["id"], src["name"], src["kind"], src["url"])
    drafts = connector_for(src["kind"]).parse(body, info, now)
    stored = await store_news(conn, src["id"], drafts, raw_id)
    await conn.execute(
        """UPDATE core.news_raw SET n_items = $2, n_new = $3, n_duplicates = $4, processed_at = now(),
               outcome = CASE WHEN outcome = 'parse_error' THEN 'ok' ELSE outcome END
           WHERE id = $1""",
        raw_id,
        len(drafts),
        stored.inserted,
        stored.duplicates + stored.near_duplicates,
    )
    return len(drafts), stored


async def _handle(
    conn: Any, store: ObjectStore, src: Any, fetch: FetchResult, now: datetime, failures_to_down: int
) -> tuple[SourceRun, StoreResult | None]:
    run = SourceRun(
        src["id"], fetch.outcome, fetch.status_code, latency_ms=fetch.latency_ms, error=fetch.error
    )
    stored: StoreResult | None = None
    sha = hashlib.sha256(fetch.body).hexdigest() if fetch.outcome == "ok" else None
    s3_key = None
    if sha is not None:
        previous = await conn.fetchrow(
            "SELECT s3_key FROM core.news_raw WHERE source_id = $1 AND sha256 = $2 AND s3_key IS NOT "
            "NULL LIMIT 1",
            src["id"],
            sha,
        )
        if previous is not None:
            run.outcome, s3_key = "unchanged", previous["s3_key"]
        else:
            s3_key = _raw_key(src["id"], now, sha, fetch.content_type)
            await asyncio.to_thread(store.put, s3_key, fetch.body, fetch.content_type)
    raw_id = await conn.fetchval(
        """INSERT INTO core.news_raw (source_id, status_code, outcome, s3_key, sha256, content_type, bytes,
               latency_ms, error)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
        src["id"],
        fetch.status_code,
        run.outcome,
        s3_key,
        sha,
        fetch.content_type,
        len(fetch.body) if fetch.body else None,
        fetch.latency_ms,
        fetch.error,
    )
    if run.outcome == "ok":
        try:
            run.n_items, stored = await process_raw(conn, src, raw_id, fetch.body, now)
            run.n_new = stored.inserted
            run.n_duplicates = stored.duplicates + stored.near_duplicates
        except Exception as exc:  # malformed feed: keep the raw body for later re-processing
            run.outcome, run.error = "parse_error", f"{type(exc).__name__}: {exc}"[:500]
            await conn.execute(
                "UPDATE core.news_raw SET outcome = 'parse_error', error = $2 WHERE id = $1",
                raw_id,
                run.error,
            )
    run.health = await _record_health(conn, src, run, fetch, failures_to_down)
    return run, stored


async def poll(
    conn: Any,
    store: ObjectStore,
    fetcher: PoliteFetcher,
    *,
    concurrency: int = 4,
    failures_to_down: int = 3,
    source_ids: list[str] | None = None,
    now: datetime | None = None,
) -> PollResult:
    """Poll enabled primary sources; a reserve source (fallback_for = X) is polled when X is down."""
    now = now or datetime.now(UTC)
    sources = await conn.fetch(
        """SELECT * FROM core.sources WHERE enabled AND ($1::text[] IS NULL OR id = ANY($1::text[]))
           ORDER BY priority, id""",
        source_ids,
    )
    primaries = [s for s in sources if s["fallback_for"] is None or source_ids is not None]
    reserves: dict[str, list[Any]] = {}
    for s in sources:
        if s["fallback_for"] is not None:
            reserves.setdefault(s["fallback_for"], []).append(s)
    result = PollResult()
    sem = asyncio.Semaphore(concurrency)

    async def fetch(src: Any) -> FetchResult:
        async with sem:
            return await _fetch_one(fetcher, src)

    async def run_batch(batch: list[Any], fallback: bool) -> list[str]:
        fetched = await asyncio.gather(*(fetch(s) for s in batch))
        down: list[str] = []
        for src, f in zip(batch, fetched, strict=True):
            run, stored = await _handle(conn, store, src, f, now, failures_to_down)
            run.fallback_used = fallback
            result.sources.append(run)
            if stored is not None:
                result.merge(stored)
            if run.health == "down":
                down.append(src["id"])
        return down

    down = await run_batch(primaries, fallback=False)
    if source_ids is None:
        polled = {r.source_id for r in result.sources}
        backup = [r for d in down for r in reserves.get(d, []) if r["id"] not in polled]
        if backup:
            await run_batch(backup, fallback=True)
    return result


async def reprocess_raw(
    conn: Any, store: ObjectStore, raw_ids: list[Any], now: datetime | None = None
) -> PollResult:
    """Re-parse stored raw bodies from S3 (e.g. after a parser or tagger fix) without re-downloading."""
    now = now or datetime.now(UTC)
    result = PollResult()
    rows = await conn.fetch(
        """SELECT r.id AS raw_id, r.s3_key, s.id, s.name, s.kind, s.url
           FROM core.news_raw r JOIN core.sources s ON s.id = r.source_id
           WHERE r.id = ANY($1::uuid[]) AND r.s3_key IS NOT NULL""",
        raw_ids,
    )
    for row in rows:
        body = await asyncio.to_thread(store.get, row["s3_key"])
        n_items, stored = await process_raw(conn, row, row["raw_id"], body, now)
        result.sources.append(SourceRun(row["id"], "reprocessed", n_items=n_items, n_new=stored.inserted))
        result.merge(stored)
    return result


async def ingest_manual(
    conn: Any, store: ObjectStore, items: list[dict[str, Any]], now: datetime | None = None
) -> PollResult:
    """News submitted by hand go through the same steps as a polled feed: raw JSON to S3, parse, dedup,
    clustering, source health. The manual source is never polled itself (enabled = false)."""
    now = now or datetime.now(UTC)
    await ensure_source(conn, MANUAL_SOURCE_ID, "Ручной ввод (аналитики банка)", "manual", None, False)
    await conn.execute("UPDATE core.sources SET enabled = false WHERE id = $1", MANUAL_SOURCE_ID)
    src = await conn.fetchrow("SELECT * FROM core.sources WHERE id = $1", MANUAL_SOURCE_ID)
    body = json.dumps({"items": items}, ensure_ascii=False, default=str).encode()
    fetch = FetchResult("manual", "ok", 200, body, "application/json")
    result = PollResult()
    run, stored = await _handle(conn, store, src, fetch, now, failures_to_down=3)
    result.sources.append(run)
    if stored is not None:
        result.merge(stored)
    return result
