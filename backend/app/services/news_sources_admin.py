"""News sources managed by an admin (ADR-036): check a feed before adding it, add RSS/Atom sources, switch any
source on or off, remove the ones an admin added.

Catalog sources (`news_sources.json`, origin = catalog) are re-synced on every poll, so an admin can only
switch them off (`disabled_by_admin`, respected by the sync); their other fields live in the file. Admin
sources (origin = admin) exist only in the database. Only http(s) feeds on public addresses, no local files.
"""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.integrations.news.connectors import SourceInfo, connector_for
from app.integrations.news.http import PoliteFetcher
from app.integrations.news.safety import check_url_shape

SAMPLE_ITEMS = 5
CHECK_TIMEOUT_S = 15.0
ADMIN_ID_PREFIX = "adm_"
EDITABLE = {"name", "priority", "fallback_for", "topics", "enabled"}


class SourceConflictError(Exception):
    """The feed is already in the list."""


class CatalogSourceError(Exception):
    """Catalog sources can only be switched on or off (the rest comes from news_sources.json)."""


@dataclass
class FeedCheck:
    ok: bool
    url: str
    outcome: str  # ok | empty | http_error | network_error | robots_disallowed | too_large | not_modified
    status_code: int | None = None
    items: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_id_for(url: str) -> str:
    return ADMIN_ID_PREFIX + hashlib.sha256(url.encode()).hexdigest()[:12]


async def check_feed(settings: Settings, url: str, fetcher: PoliteFetcher | None = None) -> FeedCheck:
    """Fetches the feed politely (robots.txt, public addresses only) and parses it; nothing is stored.
    ValueError for a malformed URL."""
    url = check_url_shape(url)
    own = fetcher is None
    fetcher = fetcher or PoliteFetcher(
        settings.news_user_agent,
        min_interval_s=0.0,
        timeout_s=CHECK_TIMEOUT_S,
        public_only=settings.news_public_hosts_only,
    )
    try:
        res = await fetcher.fetch(url)
    finally:
        if own:
            await fetcher.close()
    if res.outcome != "ok":
        return FeedCheck(False, url, res.outcome, res.status_code, error=res.error)
    drafts = connector_for("rss").parse(res.body, SourceInfo("check", "check", "rss", url), datetime.now(UTC))
    if not drafts:
        return FeedCheck(False, url, "empty", res.status_code, error="no news items: is it an RSS/Atom feed?")
    latest = sorted(drafts, key=lambda d: d.published_at, reverse=True)[:SAMPLE_ITEMS]
    return FeedCheck(
        True,
        url,
        "ok",
        res.status_code,
        items=len(drafts),
        sample=[{"title": d.title, "url": d.url, "published_at": d.published_at} for d in latest],
    )


async def list_sources(conn: Any) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT s.id, s.name, s.kind, s.url, s.origin, s.enabled, s.disabled_by_admin, s.is_fixture,
                  s.priority, s.fallback_for, s.topics, s.health_status, s.last_error, s.last_checked_at,
                  s.last_success_at,
                  (SELECT count(*) FROM core.news_items n WHERE n.source_id = s.id
                     AND n.fetched_at > now() - interval '1 day') AS items_24h
           FROM core.sources s ORDER BY s.origin DESC, s.priority, s.id"""
    )
    return [{**dict(r), "url": None if r["kind"] == "fixture" else r["url"]} for r in rows]


async def get_source(conn: Any, source_id: str) -> dict[str, Any] | None:
    return next((s for s in await list_sources(conn) if s["id"] == source_id), None)


async def _check_fallback(conn: Any, fallback_for: str | None, source_id: str | None = None) -> None:
    if fallback_for is None:
        return
    if fallback_for == source_id or not await conn.fetchval(
        "SELECT 1 FROM core.sources WHERE id = $1", fallback_for
    ):
        raise ValueError(f"fallback_for: unknown source {fallback_for!r}")


async def create_source(
    conn: Any,
    settings: Settings,
    *,
    name: str,
    url: str,
    topics: list[str],
    priority: int,
    fallback_for: str | None,
) -> dict[str, Any]:
    url = check_url_shape(url)
    await _check_fallback(conn, fallback_for)
    if await conn.fetchval("SELECT 1 FROM core.sources WHERE url = $1", url):
        raise SourceConflictError(url)
    source_id = source_id_for(url)
    await conn.execute(
        """INSERT INTO core.sources (id, name, kind, url, priority, fallback_for, enabled, topics, origin,
               health_status)
           VALUES ($1, $2, 'rss', $3, $4, $5, $6, $7, 'admin', 'unknown')""",
        source_id,
        name,
        url,
        priority,
        fallback_for,
        settings.news_live_sources_enabled,
        topics,
    )
    source = await get_source(conn, source_id)
    assert source is not None
    return source


async def update_source(
    conn: Any, settings: Settings, source_id: str, changes: dict[str, Any]
) -> dict[str, Any] | None:
    current = await conn.fetchrow("SELECT origin, is_fixture FROM core.sources WHERE id = $1", source_id)
    if current is None:
        return None
    if current["origin"] != "admin" and set(changes) - {"enabled"}:
        raise CatalogSourceError(source_id)
    if "fallback_for" in changes:
        await _check_fallback(conn, changes["fallback_for"], source_id)
    sets: dict[str, Any] = {k: v for k, v in changes.items() if k in EDITABLE and k != "enabled"}
    if "enabled" in changes:
        wanted = bool(changes["enabled"])
        live = current["is_fixture"] or settings.news_live_sources_enabled
        sets.update(disabled_by_admin=not wanted, enabled=wanted and live)
    if sets:
        assignments = ", ".join(f"{k} = ${i}" for i, k in enumerate(sets, 2))
        await conn.execute(
            f"UPDATE core.sources SET {assignments} WHERE id = $1",  # noqa: S608 - keys from EDITABLE
            source_id,
            *sets.values(),
        )
    return await get_source(conn, source_id)


async def delete_source(conn: Any, source_id: str) -> str | None:
    """deleted | catalog | has-news | None (not found)."""
    row = await conn.fetchrow("SELECT origin FROM core.sources WHERE id = $1", source_id)
    if row is None:
        return None
    if row["origin"] != "admin":
        return "catalog"
    if await conn.fetchval("SELECT 1 FROM core.news_items WHERE source_id = $1 LIMIT 1", source_id):
        return "has-news"
    async with conn.transaction():
        await conn.execute("UPDATE core.sources SET fallback_for = NULL WHERE fallback_for = $1", source_id)
        await conn.execute("DELETE FROM core.news_raw WHERE source_id = $1", source_id)
        await conn.execute("DELETE FROM core.sources WHERE id = $1", source_id)
    return "deleted"
